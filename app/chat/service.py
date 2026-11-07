"""One query, end to end (WS4.T04 core without the SSE transport): retrieval -> tau gate ->
prompt composition -> validated generation -> audit row -> trace.

Order is fixed (architecture.md data flow): predicate BEFORE vector search, validator AFTER
generation. The user text travels ONLY in the user turn: the system prompt is built from closed
catalog values and the retrieved fragments.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import Connection, text

from app.ingestion.chunker import CHUNKER_VERSION
from app.ingestion.tokens import TokenCounter
from app.observability.trace import TracedAttempt, TracedHit, Tracer, TraceRecord
from app.orchestration.aliases import AliasName, AliasRegistry
from app.orchestration.client import LLMClient
from app.prompts.composer import Composer, Persona, SessionPrompt, Workflow
from app.rag.context import SessionContext
from app.rag.expansion import render_context
from app.rag.jurisdiction import build_jurisdiction_predicate
from app.rag.pipeline import Reranker, Retrieval, retrieve
from app.rag.search import QueryEmbedder
from app.settings import Settings
from app.validation.audit import write_audit
from app.validation.loop import ValidatedAnswer, generate_validated
from app.validation.types import RetrievedSet, ValidationConfig

WORKFLOW_ALIAS: dict[str, AliasName] = {
    "dossier_builder": "drafting-primary",
    "adversarial": "adversarial-primary",
}
MAX_QUERY_CHARS = 4000


class ChatRequest(BaseModel):
    """persona and workflow are closed enums; the query is the only free text."""

    model_config = ConfigDict(frozen=True)

    query: str
    persona: Persona
    workflow: Workflow

    @field_validator("query")
    @classmethod
    def _query(cls, v: str) -> str:
        v = v.strip()
        if not v or len(v) > MAX_QUERY_CHARS:
            raise ValueError(f"query must be 1..{MAX_QUERY_CHARS} characters")
        return v


class Citation(BaseModel):
    model_config = ConfigDict(frozen=True)

    cite_id: str
    citation_string: str


class ChatResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    request_id: uuid.UUID
    status: Literal["answered", "refused"]
    answer: str | None
    refusal_block: str | None  # always written by the system
    refusal_reason: str | None  # a catalog key the UI translates
    citations: tuple[Citation, ...]


@dataclass(frozen=True)
class ChatDeps:
    embedder: QueryEmbedder
    reranker: Reranker
    counter: TokenCounter
    settings: Settings
    composer: Composer
    llm: LLMClient
    aliases: AliasRegistry
    tracer: Tracer
    require_pinned_models: bool = True  # False only in tests with a fake client


_PROMPT_LANG = {"es": "es-MX", "en": "en-US"}


def jurisdiction_display(conn: Connection, ctx: SessionContext) -> str:
    """The stacked scope as catalog names, e.g. "Federal (México) + Nuevo León + Monterrey"."""
    parts: list[str] = []
    col = "name_es" if ctx.language == "es" else "name_en"
    fed = conn.execute(
        text(f"SELECT {col} FROM jurisdictions WHERE country = :c AND level = 'federal'"),
        {"c": ctx.country},
    ).scalar_one()
    parts.append(fed)
    if ctx.state_code:
        parts.append(
            conn.execute(
                text(
                    f"SELECT {col} FROM jurisdictions WHERE country = :c AND level = 'state' "
                    "AND state_code = :s"
                ),
                {"c": ctx.country, "s": ctx.state_code},
            ).scalar_one()
        )
    if ctx.municipality:
        parts.append(ctx.municipality)
    return " + ".join(parts)


def _retrieved_set(r: Retrieval) -> RetrievedSet:
    entries: list[tuple[str, list[str]]] = [(h.cite_id, [h.text]) for h in r.hits]
    for f in r.fragments:
        entries.extend((cid, [f.text]) for cid in (f.cite_id, *f.also_cite_ids))
    return RetrievedSet.build(entries)


def _citations(r: Retrieval, cited: tuple[str, ...]) -> tuple[Citation, ...]:
    names = {h.cite_id: h.citation_string for h in r.hits}
    return tuple(Citation(cite_id=c, citation_string=names[c]) for c in cited if c in names)


class TenantMismatchError(RuntimeError):
    """The session context and the database connection anchor disagree about the tenant."""


def _assert_anchored(conn: Connection, ctx: SessionContext) -> None:
    anchored = conn.execute(text("SELECT app_tenant_id()")).scalar_one()
    if anchored != ctx.tenant_id:
        raise TenantMismatchError(
            f"connection is anchored on {anchored} but the session belongs to {ctx.tenant_id}"
        )


def answer_query(
    conn: Connection,
    ctx: SessionContext,
    req: ChatRequest,
    deps: ChatDeps,
    on_stage: Callable[[str], None] | None = None,
) -> ChatResult:
    """`conn` is inside `tenant_transaction(ctx.tenant_id)`: RLS applies to every statement."""
    _assert_anchored(conn, ctx)  # never retrieve for one tenant and audit for another
    stage = on_stage or (lambda _name: None)
    stage("retrieving")
    s = deps.settings
    retrieval = retrieve(
        conn, ctx, req.query, embedder=deps.embedder, reranker=deps.reranker,
        counter=deps.counter, settings=s,
    )  # fmt: skip
    pred = build_jurisdiction_predicate(ctx)
    hit_ids = [h.cite_id for h in retrieval.hits]
    alias = WORKFLOW_ALIAS[req.workflow]
    display = jurisdiction_display(conn, ctx)
    session_info: dict[str, str | int | bool | None] = {
        "language": ctx.language, "prompt_language": _PROMPT_LANG[ctx.language],
        "persona": req.persona, "workflow": req.workflow, "jurisdiction_id": ctx.jurisdiction_id,
        "country": ctx.country, "level": ctx.level, "state_code": ctx.state_code,
        "municipality": ctx.municipality, "include_historical": ctx.include_historical,
    }  # fmt: skip
    versions: dict[str, str | int | float | None] = {
        "embedding_model": deps.embedder.model, "embedding_revision": deps.embedder.revision,
        "chunker_version": CHUNKER_VERSION, "reranker": deps.reranker.name,
        "tau": s.refusal_tau, **{f"alias:{a}": m for a, m in deps.aliases.pinned_ids().items()},
    }  # fmt: skip
    base = {
        "tenant_id": ctx.tenant_id, "occurred_at": datetime.now(UTC), "query": req.query,
        "session": session_info, "filters_sql": pred.sql, "filters_params": dict(pred.params),
        "semantic_ids": retrieval.semantic_ids, "lexical_ids": retrieval.lexical_ids,
        "hits": tuple(
            TracedHit(
                cite_id=h.cite_id,
                citation_string=h.citation_string,
                score=h.score,
                rank=h.rank,
            )
            for h in retrieval.hits
        ),
        "retrieved_texts": dict(_retrieved_set(retrieval).texts),
        "gate": {"tau": s.refusal_tau, "min_fragments": s.refusal_min_fragments,
                 "best_score": max((h.score for h in retrieval.hits), default=None)},
        "jurisdiction_display": display, "versions": versions,
        "validator_config": ValidationConfig.from_settings(s).model_dump(),
    }  # fmt: skip
    filters: dict[str, object] = {"sql": pred.sql, "params": dict(pred.params)}

    if retrieval.refusal is not None:  # refusal short-circuits BEFORE any model call (ADR-005)
        rf = retrieval.refusal
        rid = write_audit(
            conn, tenant_id=ctx.tenant_id, query_text=req.query, filters=filters,
            retrieved_cite_ids=hit_ids, result=None, output_text=rf.block, event_type="refusal",
            extra_verdict={"refusal_reason": rf.reason_key, "best_score": rf.best_score,
                           "tau": rf.tau, "fragments_above_tau": rf.fragments_above_tau},
        )  # fmt: skip
        deps.tracer.record(
            TraceRecord(request_id=rid, outcome="refused", refusal_block=rf.block,
                        refusal_reason=rf.reason_key, **base)  # type: ignore[arg-type]
        )  # fmt: skip
        return ChatResult(request_id=rid, status="refused", answer=None, refusal_block=rf.block,
                          refusal_reason=rf.reason_key, citations=())  # fmt: skip

    stage("generating")
    session = SessionPrompt(
        workflow=req.workflow, persona=req.persona,
        language=_PROMPT_LANG[ctx.language],  # type: ignore[arg-type]
        jurisdiction_display=display,
    )  # fmt: skip
    context = render_context(retrieval.fragments)
    prompt = deps.composer.compose(session, context)
    spec = (
        deps.aliases.require_pinned(alias)
        if deps.require_pinned_models
        else deps.aliases.spec(alias)
    )
    result: ValidatedAnswer = generate_validated(
        deps.llm, alias, system=prompt.system, user=req.query, retrieved=_retrieved_set(retrieval),
        workflow=req.workflow, tenant_id=str(ctx.tenant_id), max_tokens=spec.max_answer_tokens,
        regen_max_tokens=s.validator_regen_max_tokens, config=ValidationConfig.from_settings(s),
    )  # fmt: skip
    answered = result.answer is not None
    output = result.answer if answered else (result.refusal.block if result.refusal else None)
    rid = write_audit(
        conn, tenant_id=ctx.tenant_id, query_text=req.query, filters=filters,
        retrieved_cite_ids=hit_ids, result=result, output_text=output,
        event_type="query" if answered else "refusal", model_alias=alias,
    )  # fmt: skip
    deps.tracer.record(
        TraceRecord(
            request_id=rid,
            outcome="answered" if answered else "refused",
            answer=result.answer,
            refusal_block=result.refusal.block if result.refusal else None,
            refusal_reason=result.refusal.reason_key if result.refusal else None,
            prompt_versions=prompt.versions,
            prompt_sha256=prompt.sha256,
            prompt_system=prompt.system,
            context=context,
            attempts=tuple(
                TracedAttempt(
                    user=a.user,
                    output=a.output,
                    alias=a.alias,
                    resolved_model_id=a.resolved_model_id,
                    verdict=v,
                )
                for a, v in zip(result.attempts, result.verdicts, strict=True)
            ),
            **base,  # type: ignore[arg-type]
        )
    )
    return ChatResult(
        request_id=rid, status="answered" if answered else "refused", answer=result.answer,
        refusal_block=result.refusal.block if result.refusal else None,
        refusal_reason=result.refusal.reason_key if result.refusal else None,
        citations=_citations(retrieval, result.verdicts[-1].cited) if answered else (),
    )  # fmt: skip
