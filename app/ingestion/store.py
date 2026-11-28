"""Idempotent, versioned persistence of one document (WS2.T06 core + WS2.T08 guards).

One transaction per document. Unchanged re-run (same source + checksum) costs no embedder call.
A reform (same `official_id`, new checksum) inserts a NEW version and flips `is_current` /
`valid_to` on the previous one; nothing is deleted (invariant 3). The live set never mixes
embedding model/revision, chunker version or thresholds (invariant 5, ADR-006): a mismatch is
refused, and a different generation must be built aside (not implemented here: WS2.T08 swap).
"""

from __future__ import annotations

import hashlib
import json
import math
import time
import uuid
from datetime import date
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict
from sqlalchemy import CursorResult, Engine, text

from app.db.models import EMBEDDING_DIM, DocNode, Document
from app.db.models import Chunk as ChunkRow
from app.db.session import PUBLIC_CORPUS_TENANT_ID, tenant_session
from app.ingestion.chunker import CHUNKER_VERSION, Chunker, report
from app.ingestion.model import ChunkThresholds, DocMeta
from app.ingestion.normalizer_generic import normalize_generic_text
from app.ingestion.normalizer_generic import parse as parse_generic
from app.ingestion.normalizer_mx import normalize_text as normalize_mx
from app.ingestion.normalizer_mx import parse as parse_mx
from app.ingestion.normalizer_us import normalize_us_text
from app.ingestion.normalizer_us import parse as parse_us
from app.ingestion.tokens import TokenCounter

EMBED_BATCH = 96  # Cohere embed: up to 96 texts per request (docs.cohere.com/reference/embed)


class Embedder(Protocol):
    model: str
    revision: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class VersionMismatchError(RuntimeError):
    """Writing would mix generations in the live set (invariant 5)."""


class InvalidEmbeddingError(RuntimeError):
    """The embedder returned a vector that would corrupt or hide a chunk."""


def validate_vectors(cite_ids: list[str], vectors: list[list[float]]) -> None:
    """Reject BEFORE writing: a wrong dimension or non-finite value fails with a cryptic database
    error, and a zero vector is silently accepted yet can never be reached by the cosine HNSW
    index (an invisible chunk)."""
    for cite, v in zip(cite_ids, vectors, strict=True):
        if len(v) != EMBEDDING_DIM:
            raise InvalidEmbeddingError(f"{cite}: {len(v)} dimensions, expected {EMBEDDING_DIM}")
        if not all(math.isfinite(x) for x in v):
            raise InvalidEmbeddingError(f"{cite}: vector has NaN or infinite values")
        if not any(v):
            raise InvalidEmbeddingError(f"{cite}: zero vector (unsearchable by cosine distance)")


class IngestStats(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: str  # added | unchanged
    chunks_written: int = 0
    previous_versions_retired: int = 0
    oversized: tuple[str, ...] = ()
    undersized: tuple[str, ...] = ()
    exceeds_window: tuple[str, ...] = ()
    seconds: float = 0.0
    embed_seconds: float = 0.0
    document_id: uuid.UUID | None = None  # the version written (None when unchanged)
    retired_document_ids: tuple[uuid.UUID, ...] = ()  # versions this one replaced (ADR-017)


def _generation(embedder: Embedder, th: ChunkThresholds) -> dict[str, object]:
    return {
        "embedding_model": embedder.model,
        "embedding_revision": embedder.revision,
        "chunker_version": CHUNKER_VERSION,
        "chunk_thresholds": th.model_dump(),
    }


def write_tree(
    s: Any,
    *,
    tenant_id: uuid.UUID,
    doc_id: uuid.UUID,
    src: str,
    nodes: list[Any],
    chunks: list[Any],
    vectors: list[list[float]],
    gen: dict[str, object],
    generation: int | None = None,
) -> None:
    """Write a document's tree and chunks. `generation` None: the active one (the column default);
    an explicit value only from a rebuild (ADR-006), which runs as the superuser."""
    extra = {} if generation is None else {"generation": generation}
    ids: dict[str, uuid.UUID] = {}
    parents = {n.path: n.parent for n in nodes}
    for n in nodes:  # document order: parents precede children
        ids[n.path] = uuid.uuid4()
        pp = parents[n.path]
        parent_id = ids[pp] if pp is not None else None
        s.add(
            DocNode(
                id=ids[n.path],
                document_id=doc_id,
                tenant_id=tenant_id,
                parent_id=parent_id,
                node_type=n.node_type,
                label=n.label,
                ordinal=n.ordinal,
                path=n.path,
                full_text=src[n.start : n.end],
                **extra,
            )
        )
        s.flush()
    for c, v in zip(chunks, vectors, strict=True):
        s.add(
            ChunkRow(
                tenant_id=tenant_id,
                document_id=doc_id,
                node_id=ids[c.node_path],
                is_current=c.is_current,
                is_navigation=c.is_navigation,
                cite_id=c.cite_id,
                citation_string=c.citation_string,
                text=c.text,
                breadcrumb=c.breadcrumb,
                embedded_text=c.embedded_text,
                char_start=c.char_start,
                char_end=c.char_end,
                token_count=c.token_count,
                embedding=v,
                **extra,
                **gen,
            )
        )
    s.flush()


def ingest_document(
    engine: Engine,
    tenant_id: uuid.UUID,
    *,
    meta: DocMeta,
    doc_type: str,
    jurisdiction_id: int,
    source_uri: str,
    raw_text: str,
    embedder: Embedder,
    counter: TokenCounter,
    thresholds: ChunkThresholds | None = None,
    official_id: str | None = None,
    legal_domain: list[str] | None = None,
    valid_from: date | None = None,
    grammar: Literal["mx", "us", "generic"] = "mx",
    matter_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
) -> IngestStats:
    """`matter_id` (ADR-025): a client file of that matter; `user_id` anchors the transaction so
    the matter's RLS lets its member write it."""
    t0 = time.monotonic()
    public = tenant_id == PUBLIC_CORPUS_TENANT_ID
    th = thresholds or ChunkThresholds(counter=counter.name)
    if matter_id is not None and public:
        raise ValueError("a public document cannot belong to a matter")
    normalize, parse = {
        "us": (normalize_us_text, parse_us),
        "mx": (normalize_mx, parse_mx),
        "generic": (normalize_generic_text, parse_generic),
    }[grammar]
    src = normalize(raw_text)
    checksum = hashlib.sha256(src.encode("utf-8")).hexdigest()

    with tenant_session(engine, tenant_id, allow_public=public, user_id=user_id) as s:
        known = s.execute(
            text(
                "SELECT 1 FROM documents WHERE tenant_id = :t AND source_uri = :u "
                "AND checksum_sha256 = :c"
            ),
            {"t": tenant_id, "u": source_uri, "c": checksum},
        ).first()
        if known:
            return IngestStats(status="unchanged", seconds=time.monotonic() - t0)

    nodes = parse(src)
    chunks = Chunker(counter, th).chunk(src, nodes, meta)
    if not chunks:
        raise ValueError(f"{source_uri}: no chunks produced (empty or unparseable document)")

    e0 = time.monotonic()
    vectors: list[list[float]] = []
    texts = [c.embedded_text for c in chunks]
    for i in range(0, len(texts), EMBED_BATCH):
        vectors.extend(embedder.embed(texts[i : i + EMBED_BATCH]))
    embed_seconds = time.monotonic() - e0
    if len(vectors) != len(chunks):
        raise RuntimeError("embedder returned a different number of vectors than texts")
    validate_vectors([c.cite_id for c in chunks], vectors)

    gen = _generation(embedder, th)
    with tenant_session(engine, tenant_id, allow_public=public, user_id=user_id) as s:
        live = s.execute(
            text(
                "SELECT DISTINCT embedding_model, embedding_revision, chunker_version, "
                "chunk_thresholds::text FROM chunks WHERE tenant_id IN (:t, :pub)"
            ),
            # a firm's file is searched together with the public corpus: one generation for both
            {"t": tenant_id, "pub": PUBLIC_CORPUS_TENANT_ID},
        ).all()
        want = (
            gen["embedding_model"],
            gen["embedding_revision"],
            gen["chunker_version"],
            json.dumps(gen["chunk_thresholds"], sort_keys=True),
        )
        for row in live:
            have = (row[0], row[1], row[2], json.dumps(json.loads(row[3]), sort_keys=True))
            if have != want:
                raise VersionMismatchError(
                    f"live set was built with {have}, this ingestion uses {want}: "
                    "build a new generation aside instead of mixing (ADR-006)"
                )

        retired = 0
        old: list[uuid.UUID] = []
        if official_id is not None:
            old = list(
                s.execute(
                    # ADR-050: the old version is in force until the new one is, not until the day
                    # the reform was detected (that left two versions both "in force" for days);
                    # unknown entry into force keeps the detection day, never before its own start
                    text(
                        "UPDATE documents SET valid_to = GREATEST("
                        "COALESCE(CAST(:vf AS date), current_date), "
                        "COALESCE(valid_from, CAST('-infinity' AS date))) "
                        "WHERE tenant_id = :t AND official_id = :o AND valid_to IS NULL "
                        "RETURNING id"
                    ),
                    {"t": tenant_id, "o": official_id, "vf": valid_from},
                )
                .scalars()
                .all()
            )
            if old:
                res: CursorResult[tuple[()]] = s.execute(  # type: ignore[assignment]
                    text(
                        "UPDATE chunks SET is_current = false "
                        "WHERE tenant_id = :t AND document_id = ANY(:ids) AND is_current"
                    ),
                    {"t": tenant_id, "ids": list(old)},
                )
                retired = res.rowcount

        doc = Document(
            tenant_id=tenant_id,
            jurisdiction_id=jurisdiction_id,
            doc_type=doc_type,
            legal_domain=legal_domain,
            title=meta.title,
            official_id=official_id,
            language=meta.language,
            valid_from=valid_from,
            version_label=meta.version_label,
            source_uri=source_uri,
            checksum_sha256=checksum,
            matter_id=matter_id,
        )
        s.add(doc)
        s.flush()
        doc_id = doc.id
        write_tree(s, tenant_id=tenant_id, doc_id=doc_id, src=src, nodes=nodes, chunks=chunks,
                   vectors=vectors, gen=gen)  # fmt: skip

    rep = report(chunks)
    return IngestStats(
        status="added",
        chunks_written=len(chunks),
        previous_versions_retired=retired,
        oversized=tuple(rep["oversized"]),  # type: ignore[arg-type]
        undersized=tuple(rep["undersized"]),  # type: ignore[arg-type]
        exceeds_window=tuple(rep["exceeds_window"]),  # type: ignore[arg-type]
        seconds=time.monotonic() - t0,
        embed_seconds=embed_seconds,
        document_id=doc_id,
        retired_document_ids=tuple(old),
    )
