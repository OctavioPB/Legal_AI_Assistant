"""Seed the public corpus into Postgres with the local stack (WS2.T07).

    uv run python -m app.ingestion.seed [--only LFT,CPEUM] [--database legalai]

Runs as `corpus_ingestor` (the only role that may write PUBLIC_CORPUS). Unchanged documents cost no
embedding call; the generation guard refuses to mix embedders or thresholds (ADR-006). The embedder
and tokenizer must be the local ones (ADR-014): fake vectors never enter the live set.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from sqlalchemy import create_engine, text

from app.db.session import PUBLIC_CORPUS_TENANT_ID
from app.ingestion.corpus import BY_KEY, CORPUS, CorpusDoc
from app.ingestion.embedders import OllamaEmbedder
from app.ingestion.model import ChunkThresholds, DocMeta
from app.ingestion.store import ingest_document
from app.ingestion.tokens import HFTokenCounter, TokenCounter
from app.settings import Settings, get_settings

DATA = Path(__file__).resolve().parents[2] / "data" / "corpus"


def seed(
    settings: Settings,
    docs: tuple[CorpusDoc, ...],
    *,
    database: str | None = None,
    counter: TokenCounter | None = None,
    embedder: OllamaEmbedder | None = None,
    out: object = sys.stdout,
) -> int:
    counter = counter or HFTokenCounter()
    thresholds = ChunkThresholds.from_settings(settings, counter.name)
    embedder = embedder or OllamaEmbedder(settings.embedder_model, base_url=settings.ollama_url)
    ingestor = create_engine(settings.ingestor_url(database))
    reader = create_engine(settings.superuser_url(database))
    failed = 0
    write = out.write  # type: ignore[attr-defined]
    try:
        for d in docs:
            path = DATA / d.folder / d.filename
            if not path.exists():
                write(f"{d.key:26} SKIPPED: {path} not found\n")
                failed += 1
                continue
            c, lv, st, mu = d.jurisdiction
            with reader.connect() as conn:
                jid = conn.execute(
                    text(
                        "SELECT id FROM jurisdictions WHERE country = :c AND level = :l "
                        "AND state_code IS NOT DISTINCT FROM :s "
                        "AND municipality IS NOT DISTINCT FROM :m"
                    ),
                    {"c": c, "l": lv, "s": st, "m": mu},
                ).scalar_one()
            meta = DocMeta(country=d.country, scope=d.scope, code=d.code, title=d.title,
                           version_label=d.version_label, language=d.language)  # fmt: skip
            t0 = time.monotonic()
            try:
                r = ingest_document(
                    ingestor, PUBLIC_CORPUS_TENANT_ID, meta=meta, doc_type=d.doc_type,
                    jurisdiction_id=jid, source_uri=f"file://data/corpus/{d.folder}/{d.filename}",
                    raw_text=path.read_text(encoding="utf-8"), embedder=embedder, counter=counter,
                    thresholds=thresholds, official_id=d.key, legal_domain=list(d.legal_domain),
                    grammar=d.grammar,
                )  # fmt: skip
                write(
                    f"{d.key:26} {r.status:9} chunks={r.chunks_written:6} "
                    f"retired={r.previous_versions_retired}"
                    f" over_window={len(r.exceeds_window)} {time.monotonic() - t0:6.1f}s "
                    f"(embed {r.embed_seconds:.1f}s)\n"
                )
            except Exception as exc:  # one document failing must not abort the run
                failed += 1
                write(f"{d.key:26} FAILED    {type(exc).__name__}: {str(exc)[:200]}\n")
    finally:
        ingestor.dispose()
        reader.dispose()
    return failed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="app.ingestion.seed")
    ap.add_argument("--only", help="comma-separated keys: " + ",".join(BY_KEY))
    ap.add_argument("--database", help="database name (default: legalai)")
    a = ap.parse_args(argv)
    keys = a.only.split(",") if a.only else [d.key for d in CORPUS]
    unknown = [k for k in keys if k not in BY_KEY]
    if unknown:
        sys.stderr.write(f"unknown corpus keys: {unknown}\n")
        return 2
    failed = seed(get_settings(), tuple(BY_KEY[k] for k in keys), database=a.database)
    sys.stdout.write(f"\n{len(keys) - failed} ingested/unchanged, {failed} failed\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
