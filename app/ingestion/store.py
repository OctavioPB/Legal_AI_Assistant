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
import time
import uuid
from datetime import date
from typing import Protocol

from pydantic import BaseModel, ConfigDict
from sqlalchemy import CursorResult, Engine, text

from app.db.models import Chunk as ChunkRow
from app.db.models import DocNode, Document
from app.db.session import PUBLIC_CORPUS_TENANT_ID, tenant_session
from app.ingestion.chunker import CHUNKER_VERSION, Chunker, report
from app.ingestion.model import ChunkThresholds, DocMeta
from app.ingestion.normalizer_mx import normalize_text, parse
from app.ingestion.tokens import TokenCounter

EMBED_BATCH = 96  # Cohere embed: up to 96 texts per request (docs.cohere.com/reference/embed)


class Embedder(Protocol):
    model: str
    revision: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class VersionMismatchError(RuntimeError):
    """Writing would mix generations in the live set (invariant 5)."""


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


def _generation(embedder: Embedder, th: ChunkThresholds) -> dict[str, object]:
    return {
        "embedding_model": embedder.model,
        "embedding_revision": embedder.revision,
        "chunker_version": CHUNKER_VERSION,
        "chunk_thresholds": th.model_dump(),
    }


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
) -> IngestStats:
    t0 = time.monotonic()
    public = tenant_id == PUBLIC_CORPUS_TENANT_ID
    th = thresholds or ChunkThresholds(counter=counter.name)
    src = normalize_text(raw_text)
    checksum = hashlib.sha256(src.encode("utf-8")).hexdigest()

    with tenant_session(engine, tenant_id, allow_public=public) as s:
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

    gen = _generation(embedder, th)
    with tenant_session(engine, tenant_id, allow_public=public) as s:
        live = s.execute(
            text(
                "SELECT DISTINCT embedding_model, embedding_revision, chunker_version, "
                "chunk_thresholds::text FROM chunks WHERE tenant_id = :t"
            ),
            {"t": tenant_id},
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
        if official_id is not None:
            old = (
                s.execute(
                    text(
                        "UPDATE documents SET valid_to = COALESCE(valid_to, current_date) "
                        "WHERE tenant_id = :t AND official_id = :o AND valid_to IS NULL "
                        "RETURNING id"
                    ),
                    {"t": tenant_id, "o": official_id},
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
        )
        s.add(doc)
        s.flush()
        ids: dict[str, uuid.UUID] = {}
        parents = {n.path: n.parent for n in nodes}
        for n in nodes:  # document order: parents precede children
            ids[n.path] = uuid.uuid4()
            pp = parents[n.path]
            parent_id = ids[pp] if pp is not None else None
            s.add(
                DocNode(
                    id=ids[n.path],
                    document_id=doc.id,
                    tenant_id=tenant_id,
                    parent_id=parent_id,
                    node_type=n.node_type,
                    label=n.label,
                    ordinal=n.ordinal,
                    path=n.path,
                    full_text=src[n.start : n.end],
                )
            )
            s.flush()
        for c, v in zip(chunks, vectors, strict=True):
            s.add(
                ChunkRow(
                    tenant_id=tenant_id,
                    document_id=doc.id,
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
                    **gen,
                )
            )
        s.flush()

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
    )
