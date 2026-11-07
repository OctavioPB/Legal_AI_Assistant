"""LLM client Protocol. App code depends on this, never on LiteLLM or a provider SDK
(architecture.md "Interfaces"). The concrete LiteLLM-proxy client lands with the T01 infra."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict

from app.orchestration.aliases import AliasName


class LLMResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str
    alias: AliasName
    resolved_model_id: str  # stored with the trace and the eval run
    requested_alias: AliasName | None = None  # set when a fallback answered instead
    attempts: tuple[str, ...] = ()  # aliases that failed before this one answered
    prompt_tokens: int = 0
    completion_tokens: int = 0


class LLMClient(Protocol):
    def complete(
        self, alias: AliasName, *, system: str, user: str, max_tokens: int, tenant_id: str
    ) -> LLMResult:
        """`tenant_id` keys budgets and rate limits (team = tenant). `user` is the only place
        free user text may travel; `system` comes from the prompt composer."""
        ...
