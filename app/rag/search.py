"""Hybrid retrieval arms (WS3.T02). Both run inside the caller's tenant transaction (RLS) and
apply the jurisdiction predicate in SQL BEFORE ranking (hard pre-filter). Both also filter on the
ACTIVE embedding generation (invariant 5): query and chunk vectors must share model + revision.
"""

from __future__ import annotations

import math
from typing import Any, Protocol

from sqlalchemy import Connection, text

from app.db.models import EMBEDDING_DIM
from app.rag.context import SessionContext
from app.rag.jurisdiction import build_jurisdiction_predicate
from app.rag.types import Hit

_COLS = (
    "chunks.id AS chunk_id, chunks.cite_id, chunks.citation_string, chunks.document_id, "
    "chunks.node_id, chunks.text, chunks.breadcrumb"
)
_TS_CONFIG = {"es": "legal_es", "en": "legal_en"}  # closed map: user text never names a regconfig


class QueryEmbedder(Protocol):
    model: str
    revision: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class InvalidQueryVectorError(RuntimeError):
    """The query embedder returned a vector that cannot be compared by cosine distance."""


def _vec(v: list[float]) -> str:
    if len(v) != EMBEDDING_DIM or not all(math.isfinite(x) for x in v) or not any(v):
        raise InvalidQueryVectorError(
            f"query vector must have {EMBEDDING_DIM} finite, non-zero components (got {len(v)})"
        )
    return "[" + ",".join(f"{x:.7g}" for x in v) + "]"


def _hits(rows: list[Any]) -> list[Hit]:
    return [
        Hit(
            chunk_id=r.chunk_id,
            cite_id=r.cite_id,
            citation_string=r.citation_string,
            document_id=r.document_id,
            node_id=r.node_id,
            text=r.text,
            breadcrumb=r.breadcrumb,
            score=float(r.score),
            rank=i,
        )
        for i, r in enumerate(rows, 1)
    ]


def semantic_arm(
    conn: Connection, ctx: SessionContext, query: str, embedder: QueryEmbedder, *, top: int
) -> list[Hit]:
    pred = build_jurisdiction_predicate(ctx)
    vec = _vec(embedder.embed([query])[0])
    conn.execute(text("SET LOCAL hnsw.iterative_scan = 'relaxed_order'"))  # ADR-011
    rows = conn.execute(
        text(
            f"SELECT {_COLS}, 1 - (chunks.embedding <=> CAST(:q AS vector)) AS score "
            f"FROM chunks WHERE {pred.sql} AND chunks.embedding_model = :em "
            "AND chunks.embedding_revision = :er "
            "ORDER BY chunks.embedding <=> CAST(:q AS vector), chunks.cite_id LIMIT :k"
        ),
        {**pred.params, "q": vec, "em": embedder.model, "er": embedder.revision, "k": top},
    ).all()
    return _hits(list(rows))


def lexical_arm(conn: Connection, ctx: SessionContext, query: str, *, top: int) -> list[Hit]:
    pred = build_jurisdiction_predicate(ctx)
    cfg = _TS_CONFIG[ctx.language]
    rows = conn.execute(
        text(
            f"SELECT {_COLS}, ts_rank_cd(chunks.fts, q.tsq) AS score "
            f"FROM chunks, (SELECT websearch_to_tsquery('{cfg}', :t) AS tsq) q "
            f"WHERE {pred.sql} AND chunks.fts @@ q.tsq "
            "ORDER BY score DESC, chunks.cite_id LIMIT :k"
        ),
        {**pred.params, "t": query, "k": top},
    ).all()
    return _hits(list(rows))
