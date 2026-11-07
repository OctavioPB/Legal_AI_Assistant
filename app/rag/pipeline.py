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
from app.rag.search import QueryEmbedder, lexical_arm, semantic_arm
from app.rag.types import Fragment, Hit, Refusal
from app.settings import Settings


class Reranker(Protocol):
    name: str

    def score(self, query: str, texts: list[str]) -> list[float]: ...


class Retrieval(BaseModel):
    model_config = ConfigDict(frozen=True)

    refusal: Refusal | None
    hits: tuple[Hit, ...]  # reranked, best first: THE retrieved set the citation validator uses
    fragments: tuple[Fragment, ...]
    semantic_ids: tuple[str, ...]
    lexical_ids: tuple[str, ...]


def retrieve(
    conn: Connection,
    ctx: SessionContext,
    query: str,
    *,
    embedder: QueryEmbedder,
    reranker: Reranker,
    counter: TokenCounter,
    settings: Settings,
) -> Retrieval:
    sem = semantic_arm(conn, ctx, query, embedder, top=settings.retrieval_arm_top)
    lex = lexical_arm(conn, ctx, query, top=settings.retrieval_arm_top)
    by_cite = {h.cite_id: h for h in (*lex, *sem)}
    fused = rrf(
        [[h.cite_id for h in sem], [h.cite_id for h in lex]],
        k=settings.retrieval_rrf_k,
        top=settings.retrieval_rrf_top,
    )
    cands = [by_cite[c] for c, _ in fused]
    scores = reranker.score(query, [h.text for h in cands]) if cands else []
    if len(scores) != len(cands):
        raise RuntimeError("reranker returned a different number of scores than candidates")
    order = sorted(zip(cands, scores, strict=True), key=lambda p: (-p[1], p[0].cite_id))
    top = [
        h.model_copy(update={"score": float(s), "rank": i})
        for i, (h, s) in enumerate(order[: settings.retrieval_rerank_top], 1)
    ]
    refusal = apply_gate(
        top, tau=settings.refusal_tau, min_fragments=settings.refusal_min_fragments
    )
    sem_ids = tuple(h.cite_id for h in sem)
    lex_ids = tuple(h.cite_id for h in lex)
    if refusal is not None:
        return Retrieval(
            refusal=refusal, hits=tuple(top), fragments=(),
            semantic_ids=sem_ids, lexical_ids=lex_ids,
        )  # fmt: skip
    frags = build_context(
        conn, top, budget_tokens=settings.retrieval_context_budget_tokens, counter=counter
    )
    return Retrieval(
        refusal=None, hits=tuple(top), fragments=tuple(frags),
        semantic_ids=sem_ids, lexical_ids=lex_ids,
    )  # fmt: skip
