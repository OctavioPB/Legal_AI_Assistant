"""Claude through the official Anthropic SDK (ADR-024): the ONLY remote model client.

Used only by the `claude` model profile, which a conversation can use only when the server has an
API key AND the firm's admin opted in. The SDK is not a dependency of the local demo: whoever
enables the profile installs it (`uv add anthropic`); it is imported when the client is built.

Mapping to the app's error contract: connection failures, timeouts, 408/429/5xx/529 raise
`ProviderError`; other 4xx (bad key, unknown model, bad request) are configuration bugs and raise
`ValueError`; a safety decline (`stop_reason == "refusal"`) raises `ModelDeclinedError`, which is
never retried on another model (the pinned model is the one that answers, or nobody does).
"""

from __future__ import annotations

from typing import Any

from app.orchestration.aliases import AliasName, AliasRegistry
from app.orchestration.client import LLMResult
from app.orchestration.resilient import ProviderError

_RETRYABLE_STATUS = {408, 429, 529}
_RETRYABLE_NAMES = {"APIConnectionError", "APITimeoutError"}


class ModelDeclinedError(RuntimeError):
    """The provider's safety layer declined the request (stop_reason "refusal")."""


class MissingSDKError(RuntimeError):
    """The `anthropic` package is not installed (`uv add anthropic`)."""


def _sdk_client(api_key: str, timeout_s: float) -> Any:
    try:
        import anthropic
    except ImportError as exc:  # optional dependency
        raise MissingSDKError("install the Anthropic SDK: uv add anthropic") from exc
    return anthropic.Anthropic(api_key=api_key, timeout=timeout_s, max_retries=2)


def _classify(exc: Exception) -> Exception:
    status = getattr(exc, "status_code", None)
    if status is None:
        if type(exc).__name__ in _RETRYABLE_NAMES:
            return ProviderError(f"anthropic unreachable or too slow: {type(exc).__name__}")
        return exc
    if status >= 500 or status in _RETRYABLE_STATUS:
        return ProviderError(f"anthropic {status}")
    return ValueError(f"anthropic rejected the request ({status}): {type(exc).__name__}")


class AnthropicClient:
    """`LLMClient` over the Messages API. One user turn, system prompt from the composer; no
    sampling parameters (not accepted by current models); depth set with `effort`."""

    def __init__(
        self,
        registry: AliasRegistry,
        *,
        api_key: str,
        effort: str = "medium",
        thinking_headroom_tokens: int = 8_000,
        timeout_s: float = 300.0,
        sdk: Any | None = None,  # tests inject a fake with `.messages.create(**kw)`
    ) -> None:
        if not api_key and sdk is None:
            raise ValueError("an Anthropic API key is required")
        self._registry, self._effort = registry, effort
        self._headroom = thinking_headroom_tokens
        self._sdk = sdk if sdk is not None else _sdk_client(api_key, timeout_s)

    def complete(
        self, alias: AliasName, *, system: str, user: str, max_tokens: int, tenant_id: str
    ) -> LLMResult:
        spec = self._registry.require_pinned(alias)
        assert spec.model_id is not None
        try:
            res = self._sdk.messages.create(
                model=spec.model_id,
                max_tokens=max_tokens + self._headroom,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={"effort": self._effort},
            )
        except Exception as exc:
            raise _classify(exc) from exc
        if res.stop_reason == "refusal":
            raise ModelDeclinedError("the provider declined the request")
        text = "".join(b.text for b in res.content if getattr(b, "type", None) == "text")
        usage = getattr(res, "usage", None)
        return LLMResult(
            text=text,
            alias=alias,
            resolved_model_id=str(getattr(res, "model", None) or spec.model_id),
            prompt_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            completion_tokens=int(getattr(usage, "output_tokens", 0) or 0),
        )
