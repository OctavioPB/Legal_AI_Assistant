"""Complete per-query trace (WS7.T02). A trace holds everything needed to REPLAY the query without
the database or a model: session, filters, retrieved texts, the composed prompt, every model call
and every verdict. `replay` re-derives the prompt hash and the verdicts and compares them with what
was recorded: a trace that cannot be replayed is not reconstructible.

Traces are tenant data (they carry prompts and statute fragments): they are tagged with
`tenant_id`, subject to the tenant retention policy, and registered as a store for offboarding.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

from app.prompts.composer import Composer, SessionPrompt
from app.validation.types import RetrievedSet, ValidationConfig, Verdict
from app.validation.validator import validate


class TracedHit(BaseModel):
    model_config = ConfigDict(frozen=True)

    cite_id: str
    citation_string: str
    score: float
    rank: int


class TracedAttempt(BaseModel):
    model_config = ConfigDict(frozen=True)

    user: str
    output: str
    alias: str
    resolved_model_id: str
    verdict: Verdict


class TraceRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    request_id: uuid.UUID
    tenant_id: uuid.UUID
    occurred_at: datetime
    query: str
    session: dict[str, str | int | bool | None]  # language, persona, workflow, jurisdiction...
    filters_sql: str
    filters_params: dict[str, object]
    semantic_ids: tuple[str, ...]
    lexical_ids: tuple[str, ...]
    hits: tuple[TracedHit, ...]
    retrieved_texts: dict[str, tuple[str, ...]]  # cite_id -> every text it may quote from
    gate: dict[str, float | int | str | None]
    jurisdiction_display: str | None = None
    prompt_versions: tuple[str, ...] = ()
    prompt_sha256: str | None = None
    prompt_system: str | None = None  # the full system prompt, exactly as sent
    context: str | None = None  # the rendered <fragmento> blocks (layer 5)
    attempts: tuple[TracedAttempt, ...] = ()
    outcome: Literal["answered", "refused"]
    answer: str | None = None
    refusal_block: str | None = None
    refusal_reason: str | None = None
    versions: dict[str, str | int | float | None]  # embedding/chunker/prompt/alias ids in force
    validator_config: dict[str, int] = {}  # thresholds the verdicts were computed with


class Tracer(Protocol):
    def record(self, trace: TraceRecord) -> None: ...


class InMemoryTracer:
    """Test/dev tracer. Doubles as the erasable store for offboarding (`KeyBackend`)."""

    def __init__(self) -> None:
        self.traces: dict[uuid.UUID, dict[str, TraceRecord]] = defaultdict(dict)

    def record(self, trace: TraceRecord) -> None:
        self.traces[trace.tenant_id][str(trace.request_id)] = trace

    def get(self, tenant_id: uuid.UUID, request_id: uuid.UUID) -> TraceRecord:
        return self.traces[tenant_id][str(request_id)]

    # --- KeyBackend (WS7.T06) ---
    def list_keys(self, tenant_id: uuid.UUID) -> list[str]:
        return list(self.traces[tenant_id])

    def delete_keys(self, tenant_id: uuid.UUID, keys: Iterable[str]) -> None:
        for k in list(keys):
            self.traces[tenant_id].pop(k, None)


class ReplayReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    prompt_matches: bool | None  # None: the query was refused before any prompt existed
    verdicts_match: bool
    outcome_matches: bool

    @property
    def reconstructible(self) -> bool:
        return self.prompt_matches is not False and self.verdicts_match and self.outcome_matches


def replay(trace: TraceRecord, composer: Composer | None = None) -> ReplayReport:
    prompt_ok: bool | None = None
    if trace.prompt_sha256 is not None and trace.context is not None:
        s = trace.session
        session = SessionPrompt(
            workflow=str(s["workflow"]),  # type: ignore[arg-type]
            persona=str(s["persona"]),  # type: ignore[arg-type]
            language=str(s["prompt_language"]),  # type: ignore[arg-type]
            jurisdiction_display=trace.jurisdiction_display or "",
        )
        again = (composer or Composer()).compose(session, trace.context)
        prompt_ok = again.sha256 == trace.prompt_sha256 and again.system == trace.prompt_system
    retrieved = RetrievedSet(texts=trace.retrieved_texts)
    verdicts_ok = all(
        validate(
            a.output,
            retrieved,
            workflow=str(trace.session["workflow"]),
            config=ValidationConfig(**trace.validator_config),
        )
        == a.verdict
        for a in trace.attempts
    )
    if trace.attempts:
        final = trace.attempts[-1].verdict
        outcome = (
            "answered"
            if final.grounded and trace.answer == trace.attempts[-1].output
            else "refused"
        )
    else:
        outcome = "refused"  # no model call: the retrieval gate refused
    return ReplayReport(
        prompt_matches=prompt_ok,
        verdicts_match=verdicts_ok,
        outcome_matches=outcome == trace.outcome,
    )
