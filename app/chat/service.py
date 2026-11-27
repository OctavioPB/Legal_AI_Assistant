"""One query, end to end (WS4.T04 core without the SSE transport): retrieval -> tau gate ->
prompt composition -> validated generation -> audit row -> trace.

Order is fixed (architecture.md data flow): predicate BEFORE vector search, validator AFTER
generation. The user text travels ONLY in the user turn: the system prompt is built from closed
catalog values and the retrieved fragments.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import Connection, text

from app.corpus.regimes import CASE_REGIMES, mismatch, regime_of
from app.ingestion.chunker import CHUNKER_VERSION
from app.ingestion.tokens import TokenCounter
from app.observability.trace import TracedAttempt, TracedHit, Tracer, TraceRecord
from app.orchestration.aliases import AliasName, AliasRegistry
from app.orchestration.client import LLMClient
from app.orchestration.profiles import ProfileCatalog, UnknownProfileError
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
    "conversar": "drafting-primary",
    "adversarial": "adversarial-primary",
    "argumentar": "adversarial-primary",
    "hoja_clave": "drafting-primary",  # ADR-028
}
MAX_QUERY_CHARS = 4000
CaseSide = Literal["actor", "demandado", "defensa", "acusacion", "tercero"]
_SIDE_LABEL: dict[str, dict[str, str]] = {
    "es": {"actor": "parte actora", "demandado": "parte demandada", "defensa": "defensa",
           "acusacion": "acusación", "tercero": "tercero interesado"},
    "en": {"actor": "plaintiff", "demandado": "defendant", "defensa": "defense",
           "acusacion": "prosecution", "tercero": "interested third party"},
}  # fmt: skip
_TURN_LABEL = {
    "es": {"case": "Caso", "side": "Parte que represento", "facts": "Hechos",
           "claim": "Pretensión", "history": "Conversación previa (no es fuente)",
           "user": "Usuario", "assistant": "Asistente", "now": "Mensaje actual"},
    "en": {"case": "Case", "side": "Side I represent", "facts": "Facts", "claim": "Claim",
           "history": "Previous conversation (not a source)", "user": "User",
           "assistant": "Assistant", "now": "Current message"},
}  # fmt: skip


class Turn(BaseModel):
    """One earlier message of the same conversation, as stored (ADR-016)."""

    model_config = ConfigDict(frozen=True)

    role: Literal["user", "assistant"]
    content: str


class CaseBrief(BaseModel):
    """The case a sparring round is about. Free text: it travels in the USER turn only."""

    model_config = ConfigDict(frozen=True)

    side: CaseSide
    facts: str
    claim: str = ""


class ChatRequest(BaseModel):
    """persona and workflow are closed enums; the query is the only free text."""

    model_config = ConfigDict(frozen=True)

    query: str
    persona: Persona
    workflow: Workflow
    history: tuple[Turn, ...] = ()  # earlier turns, oldest first (clipped by `user_turn`)
    carry: tuple[str, ...] = ()  # ids cited earlier in the conversation (ADR-016)
    case: CaseBrief | None = None
    labor_regime: str | None = None  # ADR-031: the matter's declared regime (closed list)

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
    # ADR-031: the legal regime of the cited unit (LFT special regimes, collective law), and a
    # warning key when it contradicts the regime the matter declares. Shown, never used to refuse.
    regime: str | None = None
    regime_label: str | None = None
    applicability: Literal["special_regime", "collective_regime"] | None = None


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
    profiles: ProfileCatalog | None = None  # ADR-024; None = single profile (tests)
    model_profile: str = "gemma"  # the profile `llm`/`aliases` belong to

    def for_profile(self, key: str) -> ChatDeps:
        """The same deps answering through another model profile (ADR-024). Raises
        UnknownProfileError / ProfileUnavailableError; never substitutes another profile."""
        if key == self.model_profile:
            return self
        if self.profiles is None:
            raise UnknownProfileError(key)
        p = self.profiles.serving(key)
        assert p.llm is not None
        return replace(self, llm=p.llm, aliases=p.registry, model_profile=p.key)


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
    """The ids the answer may cite: exactly those rendered into the model's context. A hit that
    did not fit the budget was never shown, so a citation to it cannot come from reading it."""
    shown = {cid for f in r.fragments for cid in (f.cite_id, *f.also_cite_ids)}
    entries: list[tuple[str, list[str]]] = [
        (h.cite_id, [h.text]) for h in r.hits if h.cite_id in shown
    ]
    for f in r.fragments:
        entries.extend((cid, [f.text]) for cid in (f.cite_id, *f.also_cite_ids))
    return RetrievedSet.build(entries)


def _citations(
    r: Retrieval, cited: tuple[str, ...], labor_regime: str | None = None
) -> tuple[Citation, ...]:
    names = {cid: name for f in r.fragments for cid, name in f.unit_names}  # ADR-020
    names.update({h.cite_id: h.citation_string for h in r.hits})
    # A unit rendered inside an expanded article shares that article's Título and Capítulo, so it
    # takes the breadcrumb of the hit the fragment was built from.
    crumbs = {h.cite_id: h.breadcrumb for h in r.hits}
    for f in r.fragments:
        for cid in f.also_cite_ids:
            crumbs.setdefault(cid, crumbs.get(f.cite_id, ""))
    case = labor_regime if labor_regime in CASE_REGIMES else None
    out: list[Citation] = []
    for c in cited:
        if c not in names:
            continue
        reg = regime_of(c, crumbs.get(c, ""))
        out.append(
            Citation(
                cite_id=c,
                citation_string=names[c],
                regime=reg.code if reg else None,
                regime_label=reg.label if reg else None,
                applicability=mismatch(reg, case),
            )
        )
    return tuple(out)


def user_turn(req: ChatRequest, language: str, settings: Settings) -> str:
    """The user message the model receives. Without history or case it is the query itself,
    byte for byte (the single-question path is unchanged). Newest history is kept first when
    clipping; the case has its own limit; the current message is never clipped."""
    if not req.history and req.case is None:
        return req.query
    lab = _TURN_LABEL[language]
    parts: list[str] = []
    if req.case is not None:
        c = req.case
        lines = [
            lab["case"],
            f"{lab['side']}: {_SIDE_LABEL[language][c.side]}",
            f"{lab['facts']}:",
            c.facts,
        ]
        if c.claim:
            lines += [f"{lab['claim']}:", c.claim]
        parts.append("\n".join(lines)[: settings.conversation_case_max_chars])
    turns = req.history[-2 * settings.conversation_history_turns :] if req.history else ()
    kept: list[str] = []
    budget = settings.conversation_history_chars
    for t in reversed(turns):
        line = f"{lab[t.role]}: {t.content}"
        if len(line) > budget:
            break
        kept.append(line)
        budget -= len(line)
    if kept:
        parts.append(lab["history"] + "\n" + "\n\n".join(reversed(kept)))
    parts.append(f"{lab['now']}:\n{req.query}")
    return "\n\n".join(parts)


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
        counter=deps.counter, settings=s, carry=req.carry,
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
        "model_profile": deps.model_profile,
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
                           "tau": rf.tau, "fragments_above_tau": rf.fragments_above_tau,
                           "model_profile": deps.model_profile},
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
        deps.llm, alias, system=prompt.system, user=user_turn(req, ctx.language, s),
        retrieved=_retrieved_set(retrieval),
        workflow=req.workflow, tenant_id=str(ctx.tenant_id), max_tokens=spec.max_answer_tokens,
        regen_max_tokens=s.validator_regen_max_tokens, config=ValidationConfig.from_settings(s),
    )  # fmt: skip
    answered = result.answer is not None
    output = result.answer if answered else (result.refusal.block if result.refusal else None)
    rid = write_audit(
        conn, tenant_id=ctx.tenant_id, query_text=req.query, filters=filters,
        retrieved_cite_ids=hit_ids, result=result, output_text=output,
        event_type="query" if answered else "refusal", model_alias=alias,
        extra_verdict={"model_profile": deps.model_profile},
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
        citations=(
            _citations(retrieval, result.verdicts[-1].cited, req.labor_regime) if answered else ()
        ),
    )  # fmt: skip
