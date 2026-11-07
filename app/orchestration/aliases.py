"""Task-alias registry (WS4.T01). The router picks ALIASES, never providers (ADR-003).

Which model backs an alias is decided by golden-dataset metrics, and its exact id comes from the
provider documentation with the date it was checked (architecture.md: pin, do not trust memory).
Until an alias is pinned it exists but cannot be used for a call: an unpinned alias fails loudly
instead of falling back to a remembered model name.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

AliasName = Literal[
    "drafting-primary", "drafting-heavy", "adversarial-primary", "cheap-utility", "fallback-general"
]
ALL_ALIASES: tuple[AliasName, ...] = (
    "drafting-primary",
    "drafting-heavy",
    "adversarial-primary",
    "cheap-utility",
    "fallback-general",
)


class UnpinnedAliasError(RuntimeError):
    """An LLM call was requested through an alias with no verified model id."""


class AliasSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    alias: AliasName
    model_id: str | None = None  # exact provider id, a dated snapshot where one is offered
    verified_on: date | None = None  # date the id was read from the provider documentation
    source_url: str | None = None
    context_window: int | None = None  # tokens, from the same documentation
    max_answer_tokens: int  # every call has its own cap (never the model default)
    fallbacks: tuple[AliasName, ...] = ()

    @model_validator(mode="after")
    def _pin_is_all_or_nothing(self) -> AliasSpec:
        pin = (self.model_id, self.verified_on, self.source_url, self.context_window)
        if any(p is not None for p in pin) and not all(p is not None for p in pin):
            raise ValueError(
                f"{self.alias}: model_id, verified_on, source_url and context_window are pinned "
                "together or not at all"
            )
        if self.max_answer_tokens < 1:
            raise ValueError(f"{self.alias}: max_answer_tokens must be >= 1")
        return self

    @property
    def pinned(self) -> bool:
        return self.model_id is not None


def default_aliases() -> dict[AliasName, AliasSpec]:
    """Nothing pinned yet: the ids are a golden-suite decision plus a documentation check."""
    return {
        "drafting-primary": AliasSpec(
            alias="drafting-primary", max_answer_tokens=4096, fallbacks=("fallback-general",)
        ),
        "drafting-heavy": AliasSpec(
            alias="drafting-heavy", max_answer_tokens=8192, fallbacks=("drafting-primary",)
        ),
        "adversarial-primary": AliasSpec(
            alias="adversarial-primary", max_answer_tokens=4096, fallbacks=("drafting-primary",)
        ),
        "cheap-utility": AliasSpec(alias="cheap-utility", max_answer_tokens=512),
        "fallback-general": AliasSpec(alias="fallback-general", max_answer_tokens=4096),
    }


class AliasRegistry:
    def __init__(self, specs: dict[AliasName, AliasSpec] | None = None) -> None:
        self._specs = specs or default_aliases()
        missing = set(ALL_ALIASES) - set(self._specs)
        if missing:
            raise ValueError(f"aliases without a spec: {sorted(missing)}")
        for spec in self._specs.values():
            for fb in spec.fallbacks:
                if fb not in self._specs or fb == spec.alias:
                    raise ValueError(f"{spec.alias}: invalid fallback {fb}")
        self._check_no_fallback_cycles()

    def _check_no_fallback_cycles(self) -> None:
        for start in self._specs:
            seen: set[str] = set()
            todo: list[AliasName] = [start]
            while todo:
                cur = todo.pop()
                for fb in self._specs[cur].fallbacks:
                    if fb == start:
                        raise ValueError(f"fallback cycle through {start}")
                    if fb not in seen:
                        seen.add(fb)
                        todo.append(fb)

    def spec(self, alias: AliasName) -> AliasSpec:
        return self._specs[alias]

    def require_pinned(self, alias: AliasName) -> AliasSpec:
        spec = self._specs[alias]
        if not spec.pinned:
            raise UnpinnedAliasError(
                f"{alias} has no model id verified against the provider documentation"
            )
        return spec

    def validate_budget(self, alias: AliasName, context_budget_tokens: int) -> None:
        """Startup check: prompt (context budget) + answer must fit the model window."""
        spec = self.require_pinned(alias)
        assert spec.context_window is not None  # guaranteed by the all-or-nothing pin
        if context_budget_tokens + spec.max_answer_tokens > spec.context_window:
            raise ValueError(
                f"{alias}: context_budget {context_budget_tokens} + max_answer_tokens "
                f"{spec.max_answer_tokens} exceeds the {spec.context_window}-token window"
            )

    def pinned_ids(self) -> dict[str, str | None]:
        """Resolved ids to store with every trace and eval run (a change is a model change)."""
        return {a: s.model_id for a, s in self._specs.items()}
