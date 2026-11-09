"""Query path: predicate -> arms -> RRF -> rerank -> tau gate -> parent expansion (architecture.md
data flow). Stops at the gate: a refusal short-circuits BEFORE any LLM call."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Connection

from app.ingestion.tokens import TokenCounter
from app.rag.context import SessionContext
from app.rag.expansion import build_context
from app.rag.fusion import rrf
from app.rag.gate import apply_gate
from app.rag.search import QueryEmbedder, hits_by_cite, hits_by_stem, lexical_arm, semantic_arm
from app.rag.types import Fragment, Hit, Refusal
from app.settings import Settings


class Reranker(Protocol):
    name: str

    def score(self, query: str, texts: list[str]) -> list[float]: ...


class Retrieval(BaseModel):
    model_config = ConfigDict(frozen=True)

    refusal: Refusal | None
    hits: tuple[Hit, ...]  # reranked, best first; only those rendered into `fragments` are citable
    fragments: tuple[Fragment, ...]
    semantic_ids: tuple[str, ...]
    lexical_ids: tuple[str, ...]


def explicit_units(
    conn: Connection, ctx: SessionContext, query: str, embedder: QueryEmbedder, settings: Settings
) -> list[Hit]:
    """Only references whose law is NAMED in the query (never inferred from context, never an
    ambiguous or unloaded law) are resolved."""
    from app.corpus.verify import extract, stem

    stems = [s for r in extract(query) if r.resolution == "named" and (s := stem(r))]
    if not stems:
        return []
    limit = min(settings.retrieval_explicit_max, settings.retrieval_rerank_top)
    return hits_by_stem(conn, ctx, list(dict.fromkeys(stems)), embedder, limit=limit)


def retrieve(
    conn: Connection,
    ctx: SessionContext,
    query: str,
    *,
    embedder: QueryEmbedder,
    reranker: Reranker,
    counter: TokenCounter,
    settings: Settings,
    carry: tuple[str, ...] = (),
) -> Retrieval:
    """`carry`: ids cited earlier in the same conversation (ADR-016). They never take a slot from
    this query's own hits and never count for the tau gate; they only fill the budget left over."""
    sem = semantic_arm(conn, ctx, query, embedder, top=settings.retrieval_arm_top)
    lex = lexical_arm(
        conn, ctx, query, top=settings.retrieval_arm_top, match=settings.lexical_match
    )
    by_cite = {h.cite_id: h for h in (*lex, *sem)}
    fused = rrf(
        [[h.cite_id for h in sem], [h.cite_id for h in lex]],
        k=settings.retrieval_rrf_k,
        top=settings.retrieval_rrf_top,
    )
    cands = [by_cite[c] for c, _ in fused]
    # ADR-022: units the query names explicitly (resolved by the citation grammar, under the same
    # predicate) are candidates too, and go first whatever their similarity score
    pinned = (
        explicit_units(conn, ctx, query, embedder, settings)
        if settings.retrieval_explicit_reference
        else []
    )
    pinned_ids = [h.cite_id for h in pinned]
    cands = [*pinned, *[h for h in cands if h.cite_id not in set(pinned_ids)]]
    # rerank_breadcrumb: score what was embedded (breadcrumb + text), not the bare text
    texts = [f"{h.breadcrumb}\n{h.text}" if settings.rerank_breadcrumb else h.text for h in cands]
    scores = reranker.score(query, texts) if cands else []
    if len(scores) != len(cands):
        raise RuntimeError("reranker returned a different number of scores than candidates")
    first = {c: i for i, c in enumerate(pinned_ids)}
    order = sorted(
        zip(cands, scores, strict=True),
        key=lambda p: (first.get(p[0].cite_id, len(first)), -p[1], p[0].cite_id),
    )
    top = [
        h.model_copy(update={"score": float(s), "rank": i})
        for i, (h, s) in enumerate(order[: settings.retrieval_rerank_top], 1)
    ]
    # ADR-022: a named unit that exists in scope is evidence enough for the gate; the validator
    # still checks every citation and quotation of the answer
    refusal = (
        None
        if pinned
        else apply_gate(top, tau=settings.refusal_tau, min_fragments=settings.refusal_min_fragments)
    )
    sem_ids = tuple(h.cite_id for h in sem)
    lex_ids = tuple(h.cite_id for h in lex)
    if refusal is not None:
        return Retrieval(
            refusal=refusal, hits=tuple(top), fragments=(),
            semantic_ids=sem_ids, lexical_ids=lex_ids,
        )  # fmt: skip
    have = {h.cite_id for h in top}
    wanted = [c for c in carry if c not in have][: settings.conversation_carry_max]
    carried = [
        h.model_copy(update={"rank": len(top) + i})
        for i, h in enumerate(hits_by_cite(conn, ctx, wanted, embedder), 1)
    ]
    top = [*top, *carried]
    frags = build_context(
        conn, top, budget_tokens=settings.retrieval_context_budget_tokens, counter=counter,
        citable_units=settings.context_citable_units, include_historical=ctx.include_historical,
        apartado=settings.context_unit_apartado,
    )  # fmt: skip
    return Retrieval(
        refusal=None, hits=tuple(top), fragments=tuple(frags),
        semantic_ids=sem_ids, lexical_ids=lex_ids,
    )  # fmt: skip
