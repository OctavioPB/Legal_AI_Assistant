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


def lexical_arm(
    conn: Connection, ctx: SessionContext, query: str, *, top: int, match: str = "all"
) -> list[Hit]:
    """`match="all"`: websearch_to_tsquery (every term, the original behaviour). `match="any"`: the
    same lexemes ORed (plainto_tsquery's AND turned into OR), ranked by ts_rank_cd, so a question
    in natural language still reaches the chunks that share its legal identifiers."""
    pred = build_jurisdiction_predicate(ctx)
    cfg = _TS_CONFIG[ctx.language]
    tsq = (
        f"CAST(replace(CAST(plainto_tsquery('{cfg}', :t) AS text), ' & ', ' | ') AS tsquery)"
        if match == "any"
        else f"websearch_to_tsquery('{cfg}', :t)"
    )
    rows = conn.execute(
        text(
            f"SELECT {_COLS}, ts_rank_cd(chunks.fts, q.tsq) AS score "
            f"FROM chunks, (SELECT {tsq} AS tsq) q "
            f"WHERE {pred.sql} AND chunks.fts @@ q.tsq "
            "ORDER BY score DESC, chunks.cite_id LIMIT :k"
        ),
        {**pred.params, "t": query, "k": top},
    ).all()
    return _hits(list(rows))


def hits_by_stem(
    conn: Connection, ctx: SessionContext, stems: list[str], embedder: QueryEmbedder, *, limit: int
) -> list[Hit]:
    """ADR-022: the units a query names explicitly, by `cite_id` stem ("MX-FED-LFT-ART47" gives the
    article's units, "...-ART47-FRACII" the fracción), under the SAME jurisdiction predicate (so a
    named law outside the session never comes back), the active embedding generation, no navigation
    chunks and no duplicated headings. Order: the stems' order, then document order."""
    if not stems:
        return []
    pred = build_jurisdiction_predicate(ctx)
    rows = conn.execute(
        text(
            f"SELECT {_COLS}, 0.0 AS score, s.ord FROM chunks "
            "JOIN unnest(CAST(:stems AS text[])) WITH ORDINALITY AS s(stem, ord) "
            "ON (regexp_replace(chunks.cite_id, '-v[^-]+$', '') = s.stem "
            "    OR chunks.cite_id LIKE s.stem || '-%') "
            f"WHERE {pred.sql} AND NOT chunks.is_navigation "
            "AND regexp_replace(chunks.cite_id, '-v[^-]+$', '') !~ '-DUP[0-9]+(-|$)' "
            "AND chunks.embedding_model = :em AND chunks.embedding_revision = :er "
            "ORDER BY s.ord, chunks.char_start, chunks.cite_id LIMIT :k"
        ),
        {**pred.params, "stems": stems, "em": embedder.model, "er": embedder.revision, "k": limit},
    ).all()
    out: dict[str, Hit] = {}
    for h in _hits(list(rows)):  # a chunk matched by two stems comes once, at its first stem
        out.setdefault(h.cite_id, h)
    return list(out.values())


def hits_by_cite(
    conn: Connection, ctx: SessionContext, cite_ids: list[str], embedder: QueryEmbedder
) -> list[Hit]:
    """Chunks already cited earlier in the same conversation (ADR-016), re-read under the SAME
    jurisdiction predicate and the active embedding generation: a carried id that is now out of
    scope (or of another generation) simply does not come back. Order follows `cite_ids`."""
    if not cite_ids:
        return []
    pred = build_jurisdiction_predicate(ctx)
    rows = conn.execute(
        text(
            f"SELECT {_COLS}, 0.0 AS score FROM chunks WHERE {pred.sql} "
            "AND chunks.cite_id = ANY(:ids) AND chunks.embedding_model = :em "
            "AND chunks.embedding_revision = :er "
            "ORDER BY array_position(CAST(:ids AS text[]), chunks.cite_id), chunks.cite_id"
        ),
        {**pred.params, "ids": cite_ids, "em": embedder.model, "er": embedder.revision},
    ).all()
    return _hits(list(rows))
