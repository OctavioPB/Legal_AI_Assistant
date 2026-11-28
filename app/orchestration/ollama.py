"""Local model clients over Ollama's HTTP API (ADR-014): LLM chat, embeddings and model pinning.

Everything runs on this machine; nothing here reaches an external provider. Failures that are the
runtime's fault (connection refused, timeout, 5xx, model not loaded) raise `ProviderError`, the
only error that makes `ResilientClient` fall back to another alias.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from datetime import date
from typing import Any

import httpx

from app.orchestration.aliases import AliasName, AliasRegistry, AliasSpec
from app.orchestration.client import LLMResult
from app.orchestration.resilient import ProviderError

DEFAULT_URL = "http://localhost:11434"


def _client(base_url: str, timeout: float, transport: httpx.BaseTransport | None) -> httpx.Client:
    return httpx.Client(base_url=base_url, timeout=timeout, transport=transport)


def _post(client: httpx.Client, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        res = client.post(path, json=payload)
    except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as exc:
        raise ProviderError(f"ollama unreachable or too slow: {type(exc).__name__}") from exc
    if res.status_code >= 500:
        raise ProviderError(f"ollama {res.status_code}: {res.text[:200]}")
    if (
        res.status_code >= 400
    ):  # our request is wrong (unknown model, bad option): a bug, not an outage
        raise ValueError(f"ollama rejected the request ({res.status_code}): {res.text[:300]}")
    data: dict[str, Any] = res.json()
    return data


def model_digest(
    name: str, *, base_url: str = DEFAULT_URL, transport: httpx.BaseTransport | None = None
) -> str:
    """The content digest of a local model: the revision we pin (a tag can be re-pointed)."""
    with _client(base_url, 30, transport) as c:
        res = c.get("/api/tags")
    for m in res.json().get("models", []):
        if m["name"] == name or m["name"] == f"{name}:latest":
            return str(m["digest"])
    raise ValueError(f"model {name!r} is not available in the local runtime")


def show_model(
    name: str, *, base_url: str = DEFAULT_URL, transport: httpx.BaseTransport | None = None
) -> dict[str, Any]:
    with _client(base_url, 30, transport) as c:
        return _post(c, "/api/show", {"model": name})


def pin_from_runtime(
    alias: AliasName,
    model: str,
    *,
    max_answer_tokens: int,
    fallbacks: tuple[AliasName, ...] = (),
    base_url: str = DEFAULT_URL,
    transport: httpx.BaseTransport | None = None,
    today: date | None = None,
) -> AliasSpec:
    """Pin an alias from what the local runtime itself reports (digest and context length), with
    the date it was read. For a local model the runtime is the provider documentation."""
    info = show_model(model, base_url=base_url, transport=transport)
    ctx = next(
        (int(v) for k, v in info.get("model_info", {}).items() if k.endswith(".context_length")),
        None,
    )
    if ctx is None:
        raise ValueError(f"{model}: the runtime did not report a context length")
    digest = model_digest(model, base_url=base_url, transport=transport)
    return AliasSpec(
        alias=alias,
        model_id=f"{model}@{digest[:12]}",
        verified_on=today or date.today(),
        source_url=f"{base_url}/api/show",
        context_window=ctx,
        max_answer_tokens=max_answer_tokens,
        fallbacks=fallbacks,
    )


class OllamaClient:
    supports_schema = True  # ADR-033: Ollama constrains the output to a JSON schema (`format`)
    """`LLMClient` over /api/chat. Deterministic by default (temperature 0, fixed seed) so an eval
    run is reproducible; the context window is set per call (Ollama defaults to 4096 otherwise)."""

    def __init__(
        self,
        registry: AliasRegistry,
        *,
        base_url: str = DEFAULT_URL,
        num_ctx: int = 20_480,
        timeout_s: float = 600.0,
        seed: int = 7,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._registry, self._num_ctx, self._seed = registry, num_ctx, seed
        self._http = _client(base_url, timeout_s, transport)

    def complete(
        self,
        alias: AliasName,
        *,
        system: str,
        user: str,
        max_tokens: int,
        tenant_id: str,
        schema: Mapping[str, Any] | None = None,
    ) -> LLMResult:
        spec = self._registry.require_pinned(alias)
        assert spec.model_id is not None
        name = spec.model_id.split("@", 1)[0]
        t0 = time.monotonic()
        data = _post(
            self._http,
            "/api/chat",
            {
                "model": name,
                "stream": False,
                "think": False,  # reasoning tokens would eat the answer budget and the latency
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                **({"format": dict(schema)} if schema is not None else {}),  # ADR-033
                "options": {
                    "temperature": 0,
                    "seed": self._seed,
                    "num_predict": max_tokens,
                    "num_ctx": self._num_ctx,
                },
            },
        )
        text = (data.get("message") or {}).get("content")
        if not isinstance(text, str):
            raise ProviderError("ollama returned no message content")
        _ = time.monotonic() - t0
        return LLMResult(
            text=text,
            alias=alias,
            resolved_model_id=spec.model_id,
            prompt_tokens=int(data.get("prompt_eval_count", 0)),
            completion_tokens=int(data.get("eval_count", 0)),
        )

    def close(self) -> None:
        self._http.close()
