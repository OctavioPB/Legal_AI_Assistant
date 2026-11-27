"""ADR-006: build the next index generation aside, switch it in atomically, roll back, prune.

    uv run python -m app.ingestion.rebuild status
    uv run python -m app.ingestion.rebuild build [--note TEXT] [--only LFT,CPF]
    uv run python -m app.ingestion.rebuild fill N          # documents added meanwhile
    uv run python -m app.ingestion.rebuild activate N
    uv run python -m app.ingestion.rebuild rollback        # back to the previous generation
    uv run python -m app.ingestion.rebuild prune N         # drop a retired generation's rows

What a generation is: the derived rows of every document (its tree in `doc_nodes`, its `chunks` and
their vectors) under one embedder, revision, chunker version and thresholds (invariant 5). The
`documents` themselves are not versioned: a rebuild re-derives the same documents.

Where each document's text comes from, and how it is checked before anything is written:
* a public law: its source file (`documents.source_uri`, a .txt or the official PDF through the same
  Docling path as the watcher); the normalized text must hash to the stored checksum, so the
  rebuild re-reads exactly the bytes that were loaded, or it stops;
* a firm document (its original is never stored, ADR-025): the text kept in its tree, in document
  order. The new units must be the same ids with the same text as the live ones, or it stops.

Runs as the superuser: an explicit, reviewed step (multi-tenancy.md), because it writes every
firm's derived rows and RLS only lets each role see the active generation. Nothing is visible to
the application until `activate`, which is one transaction; `rollback` is the same switch back.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.orm import Session

from app.db.session import PUBLIC_CORPUS_TENANT_ID
from app.ingestion.chunker import CHUNKER_VERSION, Chunker
from app.ingestion.corpus import BY_KEY, CorpusDoc
from app.ingestion.model import ChunkThresholds, DocMeta
from app.ingestion.normalizer_generic import normalize_generic_text
from app.ingestion.normalizer_generic import parse as parse_generic
from app.ingestion.normalizer_mx import normalize_text as normalize_mx
from app.ingestion.normalizer_mx import parse as parse_mx
from app.ingestion.normalizer_us import normalize_us_text
from app.ingestion.normalizer_us import parse as parse_us
from app.ingestion.store import EMBED_BATCH, Embedder, _generation, validate_vectors, write_tree
from app.ingestion.tokens import TokenCounter

ROOT = Path(__file__).resolve().parents[2]
Grammar = Literal["mx", "us", "generic"]
_GRAMMARS: dict[str, tuple[Callable[[str], str], Callable[[str], list[Any]]]] = {
    "mx": (normalize_mx, parse_mx),
    "us": (normalize_us_text, parse_us),
    "generic": (normalize_generic_text, parse_generic),
}


class RebuildError(RuntimeError):
    """The rebuild cannot be faithful: it stops instead of writing a different corpus."""


@dataclass
class DocResult:
    document_id: uuid.UUID
    name: str
    chunks: int
    removed: list[str] = field(default_factory=list)  # ids of the live generation not rebuilt
    added: list[str] = field(default_factory=list)
    text_changed: list[str] = field(default_factory=list)


@dataclass
class BuildReport:
    generation: int
    docs: list[DocResult] = field(default_factory=list)
    seconds: float = 0.0

    def summary(self) -> str:
        lines = [
            f"generation {self.generation}: {len(self.docs)} documents in {self.seconds:.0f} s"
        ]
        for d in self.docs:
            lines.append(
                f"  {d.name:34} chunks {d.chunks:6}  removed {len(d.removed):4}  added "
                f"{len(d.added):4}  text changed {len(d.text_changed):4}"
            )
        return "\n".join(lines)


# ── reading the generations ───────────────────────────────────────────────────────────────────────
def active_generation(conn: Connection) -> int:
    gid = conn.execute(text("SELECT id FROM index_generations WHERE status = 'active'")).scalar()
    if gid is None:
        raise RebuildError("no active generation (migration 0015 not applied?)")
    return int(gid)


def status(conn: Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        text(
            "SELECT g.id, g.status, g.embedding_model, g.embedding_revision, g.chunker_version, "
            "g.note, g.created_at, g.activated_at, "
            "(SELECT count(*) FROM chunks c WHERE c.generation = g.id) AS chunks, "
            "(SELECT count(DISTINCT n.document_id) FROM doc_nodes n WHERE n.generation = g.id) "
            "AS documents FROM index_generations g ORDER BY g.id"
        )
    ).mappings()
    return [dict(r) for r in rows]


# ── the text of each document ────────────────────────────────────────────────────────────────────
def _public_source(row: Any) -> tuple[CorpusDoc, str]:
    d = BY_KEY.get(row.official_id or "")
    if d is None:  # an approved source (ADR-026) is read with the same rules
        raise RebuildError(
            f"{row.official_id}: not in the static registry; rebuild it with the "
            "discovery registry loaded (not supported yet)"
        )
    uri = str(row.source_uri)
    if not uri.startswith("file://"):
        raise RebuildError(f"{row.official_id}: source {uri} is not a local file")
    path = ROOT / uri.removeprefix("file://")
    if not path.exists():
        raise RebuildError(f"{row.official_id}: source file {path} is missing")
    if path.suffix.lower() == ".pdf":
        from app.ingestion.parser import DoclingParser  # the watcher's own path (ADR-017)

        return d, DoclingParser().parse(path).text
    return d, path.read_text(encoding="utf-8")


def _firm_text(conn: Connection, document_id: uuid.UUID, generation: int) -> str:
    """A firm document's text from its live tree: its top-level nodes, in document order."""
    parts = conn.execute(
        text(
            "SELECT full_text FROM doc_nodes WHERE document_id = :d AND generation = :g "
            "AND nlevel(path) = (SELECT min(nlevel(path)) FROM doc_nodes WHERE document_id = :d "
            "AND generation = :g) ORDER BY ordinal, path"
        ),
        {"d": document_id, "g": generation},
    ).scalars()
    return "\n\n".join(parts)


def _live_units(conn: Connection, document_id: uuid.UUID, generation: int) -> dict[str, str]:
    rows = conn.execute(
        text("SELECT cite_id, text FROM chunks WHERE document_id = :d AND generation = :g"),
        {"d": document_id, "g": generation},
    ).all()
    return {r.cite_id: r.text for r in rows}


# ── building ──────────────────────────────────────────────────────────────────────────────────────
_DOCS_SQL = (
    "SELECT d.id, d.tenant_id, d.official_id, d.doc_type, d.title, d.language, d.version_label, "
    "d.source_uri, d.checksum_sha256, d.valid_to, j.country, f.grammar AS file_grammar, "
    "f.file_token, d.id IN (SELECT document_id FROM doc_nodes WHERE generation = :target) AS done "
    "FROM documents d JOIN jurisdictions j ON j.id = d.jurisdiction_id "
    "LEFT JOIN client_files f ON f.document_id = d.id AND f.tenant_id = d.tenant_id "
    "WHERE EXISTS (SELECT 1 FROM doc_nodes n WHERE n.document_id = d.id AND n.generation = :live) "
    "ORDER BY d.tenant_id <> CAST(:pub AS uuid), d.ingested_at, d.id"
)


def _rebuild_one(
    engine: Engine,
    row: Any,
    live: int,
    target: int,
    embedder: Embedder,
    counter: TokenCounter,
    th: ChunkThresholds,
) -> DocResult:
    public = row.tenant_id == PUBLIC_CORPUS_TENANT_ID
    with engine.connect() as conn:
        old = _live_units(conn, row.id, live)
        if public:
            d, raw = _public_source(row)
            grammar: str = d.grammar
            meta = DocMeta(
                country=d.country,
                scope=d.scope,
                code=d.code,
                title=row.title,
                version_label=row.version_label,
                language=row.language,
            )
        else:
            if not row.file_token or not row.file_grammar:
                raise RebuildError(f"document {row.id}: a firm document without its upload row")
            raw = _firm_text(conn, row.id, live)
            grammar = row.file_grammar
            meta = DocMeta(
                country=row.country,
                scope="CF",
                code=row.file_token,
                title=row.title,
                version_label=row.version_label,
                language=row.language,
                client_file=row.file_token,
                citation_style="plain" if grammar == "generic" else "legal",
            )
    normalize, parse = _GRAMMARS[grammar]
    src = normalize(raw)
    name = row.official_id or f"firm document {row.file_token}"
    if public and hashlib.sha256(src.encode("utf-8")).hexdigest() != row.checksum_sha256:
        raise RebuildError(
            f"{name}: the source no longer hashes to the stored checksum; it is "
            "not the text that was loaded, so it is not rebuilt"
        )
    nodes = parse(src)
    chunks = Chunker(counter, th).chunk(src, nodes, meta)
    new = {c.cite_id: c.text for c in chunks}
    result = DocResult(
        document_id=row.id,
        name=name,
        chunks=len(chunks),
        removed=sorted(set(old) - set(new)),
        added=sorted(set(new) - set(old)),
        text_changed=sorted(k for k in set(old) & set(new) if old[k] != new[k]),
    )
    if not public and (result.removed or result.added or result.text_changed):
        raise RebuildError(
            f"{name}: the rebuild would change {len(result.removed)} removed, "
            f"{len(result.added)} added and {len(result.text_changed)} changed "
            "units of a firm document; stopping (its original is not stored)"
        )
    vectors: list[list[float]] = []
    texts = [c.embedded_text for c in chunks]
    for i in range(0, len(texts), EMBED_BATCH):
        vectors.extend(embedder.embed(texts[i : i + EMBED_BATCH]))
    validate_vectors([c.cite_id for c in chunks], vectors)
    with Session(engine) as s, s.begin():
        write_tree(
            s,
            tenant_id=row.tenant_id,
            doc_id=row.id,
            src=src,
            nodes=nodes,
            chunks=chunks,
            vectors=vectors,
            gen=_generation(embedder, th),
            generation=target,
        )
        if row.valid_to is not None:  # a retired version stays retired (invariant 3)
            s.execute(
                text(
                    "UPDATE chunks SET is_current = false WHERE document_id = :d "
                    "AND generation = :g"
                ),
                {"d": row.id, "g": target},
            )
    return result


def build(
    engine: Engine,
    embedder: Embedder,
    counter: TokenCounter,
    th: ChunkThresholds,
    *,
    note: str = "",
    only: set[str] | None = None,
    out: Any = sys.stdout,
) -> BuildReport:
    t0 = time.monotonic()
    with engine.begin() as conn:
        live = active_generation(conn)
        if conn.execute(text("SELECT 1 FROM index_generations WHERE status = 'building'")).first():
            raise RebuildError(
                "a generation is already being built: fill it, activate it or prune it first"
            )
        conn.execute(  # what the migration could not see under RLS (0015)
            text(
                "UPDATE index_generations g SET embedding_model = c.m, embedding_revision = c.r, "
                "chunker_version = c.v FROM (SELECT min(embedding_model) AS m, "
                "min(embedding_revision) AS r, min(chunker_version) AS v FROM chunks "
                "WHERE generation = :g) c WHERE g.id = :g AND g.chunker_version IS NULL"
            ),
            {"g": live},
        )
        target = int(conn.execute(text("SELECT max(id) + 1 FROM index_generations")).scalar_one())
        conn.execute(
            text(
                "INSERT INTO index_generations (id, status, embedding_model, embedding_revision, "
                "chunker_version, note) VALUES (:i, 'building', :m, :r, :c, :n)"
            ),
            {
                "i": target,
                "m": embedder.model,
                "r": embedder.revision,
                "c": CHUNKER_VERSION,
                "n": note[:400] or None,
            },
        )
    report = fill(engine, target, embedder, counter, th, only=only, out=out, live=live)
    report.seconds = time.monotonic() - t0
    return report


def fill(
    engine: Engine,
    target: int,
    embedder: Embedder,
    counter: TokenCounter,
    th: ChunkThresholds,
    *,
    only: set[str] | None = None,
    out: Any = sys.stdout,
    live: int | None = None,
) -> BuildReport:
    """Rebuild every live document that has no tree in `target` yet (all of them on a new build;
    the ones uploaded or reformed meanwhile afterwards)."""
    with engine.connect() as conn:
        live = live if live is not None else active_generation(conn)
        st = conn.execute(
            text("SELECT status FROM index_generations WHERE id = :i"), {"i": target}
        ).scalar()
        if st != "building":
            raise RebuildError(f"generation {target} is {st!r}, not 'building'")
        rows = conn.execute(
            text(_DOCS_SQL), {"live": live, "target": target, "pub": PUBLIC_CORPUS_TENANT_ID}
        ).all()
    report = BuildReport(generation=target)
    for row in rows:
        if row.done or (only and (row.official_id or "") not in only):
            continue
        t = time.monotonic()
        r = _rebuild_one(engine, row, live, target, embedder, counter, th)
        report.docs.append(r)
        out.write(f"{r.name:34} {r.chunks:6} chunks  {time.monotonic() - t:6.1f} s\n")
        out.flush()
    return report


# ── switching ─────────────────────────────────────────────────────────────────────────────────────
def missing(conn: Connection, target: int) -> list[str]:
    """Live documents with no tree in `target`: activating now would make them vanish."""
    live = active_generation(conn)
    rows = conn.execute(
        text(_DOCS_SQL), {"live": live, "target": target, "pub": PUBLIC_CORPUS_TENANT_ID}
    ).all()
    return [str(r.official_id or r.file_token or r.id) for r in rows if not r.done]


def activate(engine: Engine, target: int) -> None:
    with engine.begin() as conn:
        conn.execute(text("LOCK TABLE index_generations IN EXCLUSIVE MODE"))
        st = conn.execute(
            text("SELECT status FROM index_generations WHERE id = :i"), {"i": target}
        ).scalar()
        if st not in ("building", "retired"):
            raise RebuildError(
                f"generation {target} is {st!r}: only a built or retired one can be activated"
            )
        if st == "building":
            gone = missing(conn, target)
            if gone:
                raise RebuildError(
                    f"generation {target} lacks {len(gone)} live document(s) "
                    f"({', '.join(gone[:5])}): run `fill {target}` first"
                )
        conn.execute(
            text("UPDATE index_generations SET status = 'retired' WHERE status = 'active'")
        )
        conn.execute(
            text(
                "UPDATE index_generations SET status = 'active', activated_at = now() WHERE id = :i"
            ),
            {"i": target},
        )


def rollback(engine: Engine) -> int:
    """Re-activate the most recently active retired generation."""
    with engine.connect() as conn:
        prev = conn.execute(
            text(
                "SELECT id FROM index_generations WHERE status = 'retired' "
                "AND activated_at IS NOT NULL ORDER BY activated_at DESC LIMIT 1"
            )
        ).scalar()
    if prev is None:
        raise RebuildError("no previous generation to go back to")
    activate(engine, int(prev))
    return int(prev)


def prune(engine: Engine, target: int) -> int:
    """Delete a non-active generation's rows (a retired one after the new one proved itself, or
    an abandoned build). The generation row stays, as history."""
    with engine.begin() as conn:
        st = conn.execute(
            text("SELECT status FROM index_generations WHERE id = :i"), {"i": target}
        ).scalar()
        if st is None or st == "active":
            raise RebuildError(
                f"generation {target} is {st!r}: the active generation is never pruned"
            )
        n = conn.execute(text("DELETE FROM chunks WHERE generation = :g"), {"g": target}).rowcount
        conn.execute(text("DELETE FROM doc_nodes WHERE generation = :g"), {"g": target})
        conn.execute(
            text(
                "UPDATE index_generations SET status = 'retired', note = left(coalesce(note, '') "
                "|| ' (pruned)', 400) WHERE id = :i"
            ),
            {"i": target},
        )
    return int(n or 0)


# ── command line ──────────────────────────────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    from app.ingestion.embedders import OllamaEmbedder
    from app.ingestion.tokens import HFTokenCounter
    from app.settings import get_settings

    ap = argparse.ArgumentParser(prog="app.ingestion.rebuild")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    b = sub.add_parser("build")
    b.add_argument("--note", default="")
    b.add_argument("--only", default="", help="comma-separated official ids (a trial build)")
    for name in ("fill", "activate", "prune"):
        sub.add_parser(name).add_argument("generation", type=int)
    sub.add_parser("rollback")
    a = ap.parse_args(argv)
    settings = get_settings()
    engine = create_engine(settings.superuser_url())
    try:
        if a.cmd == "status":
            with engine.connect() as conn:
                for r in status(conn):
                    print(r)
            return 0
        if a.cmd in ("build", "fill"):
            counter = HFTokenCounter()
            th = ChunkThresholds.from_settings(settings, counter.name)
            embedder = OllamaEmbedder(settings.embedder_model, base_url=settings.ollama_url)
            only = {x.strip() for x in getattr(a, "only", "").split(",") if x.strip()} or None
            if a.cmd == "build":
                rep = build(engine, embedder, counter, th, note=a.note, only=only)
            else:
                rep = fill(engine, a.generation, embedder, counter, th)
            print(rep.summary())
            return 0
        if a.cmd == "activate":
            activate(engine, a.generation)
            print(f"generation {a.generation} is active")
            return 0
        if a.cmd == "rollback":
            print(f"generation {rollback(engine)} is active again")
            return 0
        print(f"{prune(engine, a.generation)} chunks of generation {a.generation} deleted")
        return 0
    except RebuildError as exc:
        print(f"STOPPED: {exc}", file=sys.stderr)
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
