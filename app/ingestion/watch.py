"""Reform watcher (feature 6, ADR-017): detect a new version of a loaded law and adapt the system.

    uv run python -m app.ingestion.watch                 # every source: fetch, compare, ingest,
    diff
    uv run python -m app.ingestion.watch --only LFT      # one source
    uv run python -m app.ingestion.watch --dry-run       # fetch and compare only, write nothing
    uv run python -m app.ingestion.watch --check-urls    # HEAD each official URL (registry health)

The ONLY component allowed to reach the network, and only the official URLs of
`app/ingestion/corpus.py` (closed list, https). Conditional GET (ETag / Last-Modified, then sha256);
robots.txt is honoured; 401/403/429 or a non-PDF answer (a bot challenge) is recorded as `blocked`
and
the source is skipped, never evaded. A new file can also be dropped in `data/corpus/inbox/{KEY}.pdf`
(or `.txt`) for sources without an official URL.

A changed source is parsed with the same Docling path as the seed, ingested as a NEW version with
the
same embedding generation (invariant 5; the old version is retired, never deleted, invariant 3), and
diffed per citable unit into `cite_lineage` (`app/ingestion/reform.py`). The lineage is what makes
the
rest adapt: carried sources, alerts, the navigator's history, the verifier and `doctor`.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, Engine, create_engine, text

from app.db.session import PUBLIC_CORPUS_TENANT_ID, tenant_transaction
from app.ingestion.corpus import BY_KEY, CORPUS, CorpusDoc
from app.ingestion.discover import record_index, watched_sources
from app.ingestion.indexes import INDEXES, IndexLayoutError, IndexSpec, read_index
from app.ingestion.leginfo import (
    RangeError,
    RangeFile,
    extract_code,
    latest_archive,
    load_cache,
    save_cache,
)
from app.ingestion.model import ChunkThresholds, DocMeta
from app.ingestion.net import Fetcher, FetchResult, UrllibFetcher, robots_allows
from app.ingestion.normalizer_mx import normalize_text as normalize_mx
from app.ingestion.normalizer_us import normalize_us_text
from app.ingestion.parser import PdfParser
from app.ingestion.reform import missing_lineage, record
from app.ingestion.store import Embedder, ingest_document
from app.ingestion.tokens import TokenCounter
from app.settings import Settings, get_settings

DATA = Path(__file__).resolve().parents[2] / "data" / "corpus"
INBOX = DATA / "inbox"
HISTORY = DATA / "raw" / "history"
_REFORM_DATE = re.compile(
    r"[ÚU]ltima\s+reforma\s+publicada(?:\s+en\s+el)?(?:\s+DOF|\s+P\.\s?O\.)?\s*[:.]?\s*"
    r"(\d{1,2})[-/](\d{1,2})[-/](\d{4})",
    re.IGNORECASE,
)


# ---- one run
# ---------------------------------------------------------------------------------------
@dataclass
class Outcome:
    key: str
    outcome: str  # unchanged | ingested | failed | blocked | skipped
    detail: str = ""
    http_status: int | None = None
    sha256: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    counts: dict[str, int] = field(default_factory=dict)


def published_on(text_: str) -> date | None:
    """The 'Última reforma publicada DD-MM-YYYY' line of the official PDFs (first pages only)."""
    m = _REFORM_DATE.search(text_[:6000])
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def _baseline_sha(d: CorpusDoc) -> str | None:
    """The sha256 of the file the seed was built from (SHA256SUMS), the first run's baseline."""
    sums = DATA / "SHA256SUMS"
    if not sums.exists():
        return None
    for line in sums.read_text("utf-8").splitlines():
        parts = line.split()
        if len(parts) == 2 and Path(parts[1].lstrip("*")).stem == d.key:
            return parts[0].lower()
    return None


def _last_check(conn: Connection, key: str) -> Any:
    return conn.execute(
        text(
            "SELECT sha256, etag, last_modified FROM source_checks WHERE official_id = :o "
            "AND outcome IN ('unchanged','ingested','indexed') AND sha256 IS NOT NULL "
            "ORDER BY checked_at DESC LIMIT 1"
        ),
        {"o": key},
    ).one_or_none()


def _current_doc(conn: Connection, key: str) -> Any:
    return conn.execute(
        text(
            "SELECT id, checksum_sha256, version_label FROM documents WHERE official_id = :o "
            "AND tenant_id = public_corpus_id() AND valid_to IS NULL "
            "ORDER BY ingested_at DESC LIMIT 1"
        ),
        {"o": key},
    ).one_or_none()


def _jurisdiction_id(conn: Connection, d: CorpusDoc) -> int:
    c, lv, st, mu = d.jurisdiction
    jid: int = conn.execute(
        text(
            "SELECT id FROM jurisdictions WHERE country = :c AND level = :l "
            "AND state_code IS NOT DISTINCT FROM :s AND municipality IS NOT DISTINCT FROM :m"
        ),
        {"c": c, "l": lv, "s": st, "m": mu},
    ).scalar_one()
    return jid


def _save_check(engine: Engine, o: Outcome, source: str) -> None:
    with tenant_transaction(engine, PUBLIC_CORPUS_TENANT_ID, allow_public=True) as conn:
        conn.execute(
            text(
                "INSERT INTO source_checks (tenant_id, official_id, source, outcome, http_status, "
                "etag, last_modified, sha256, detail) VALUES (public_corpus_id(), :o, :s, :out, "
                ":h, "
                ":e, :lm, :sha, :d)"
            ),
            {
                "o": o.key,
                "s": source,
                "out": o.outcome,
                "h": o.http_status,
                "e": o.etag,
                "lm": o.last_modified,
                "sha": o.sha256,
                "d": o.detail[:2000] or None,
            },
        )


@dataclass
class Watcher:
    settings: Settings
    engine: Engine  # corpus_ingestor: the only role that writes PUBLIC_CORPUS
    fetcher: Fetcher
    parser: PdfParser
    embedder: Embedder
    counter: TokenCounter
    inbox: Path = INBOX
    history: Path = HISTORY
    dry_run: bool = False
    out: Any = sys.stdout
    _robots: dict[str, bool | None] = field(default_factory=dict)

    def backfill(self) -> int:
        """Record the lineage of versions ingested by a run that died before its diff."""
        n = 0
        with tenant_transaction(self.engine, PUBLIC_CORPUS_TENANT_ID, allow_public=True) as conn:
            for key, old_id, new_id, uri in missing_lineage(conn):
                record(
                    conn,
                    PUBLIC_CORPUS_TENANT_ID,
                    official_id=key,
                    old_document_id=old_id,
                    new_document_id=new_id,
                    source_uri=uri,
                    published_on=None,
                )
                n += 1
        return n

    def _obtain(self, d: CorpusDoc, last: Any) -> tuple[Outcome, bytes | None, str, str]:
        """(outcome-so-far, body, source, extension). Inbox first, then the official URL."""
        for ext in ("pdf", "txt"):
            f = self.inbox / f"{d.key}.{ext}"
            if f.exists():
                return Outcome(d.key, "pending"), f.read_bytes(), f"inbox:{f.name}", ext
        if d.source_url is None:
            return (
                Outcome(
                    d.key,
                    "skipped",
                    f"no official URL: drop a new version in data/corpus/inbox/{d.key}.pdf "
                    "(or .txt)",
                ),
                None,
                "none",
                "",
            )
        gate = self._robots_gate(d.key, d.source_url)
        if gate is not None:
            return gate, None, d.source_url, ""
        if d.fetch == "leginfo":
            return self._obtain_leginfo(d, last)
        res = self.fetcher.get(
            d.source_url,
            etag=last.etag if last else None,
            last_modified=last.last_modified if last else None,
        )
        base = Outcome(
            d.key,
            "pending",
            http_status=res.status or None,
            etag=res.etag,
            last_modified=res.last_modified,
        )
        if res.status == 304:
            base.outcome, base.sha256 = "unchanged", last.sha256 if last else None
            base.etag, base.last_modified = (
                (last.etag, last.last_modified) if last else (None, None)
            )
            return base, None, d.source_url, ""
        if res.status in (401, 403, 429):
            base.outcome, base.detail = (
                "blocked",
                f"the server refused automated access ({res.error})",
            )
            return base, None, d.source_url, ""
        if res.body is None or not (200 <= res.status < 300):
            base.outcome, base.detail = "failed", res.error or f"HTTP {res.status}"
            return base, None, d.source_url, ""
        ext = "pdf" if d.source_url.lower().endswith(".pdf") else "txt"
        if ext == "pdf" and not res.body.startswith(b"%PDF-"):
            base.outcome = "blocked"
            base.detail = "the answer is not a PDF (an HTML page or a bot challenge): not evaded"
            return base, None, d.source_url, ""
        return base, res.body, d.source_url, ext

    def _robots_gate(self, key: str, url: str) -> Outcome | None:
        host = urllib.parse.urlsplit(url).netloc
        if host not in self._robots:
            self._robots[host] = robots_allows(
                self.fetcher, url, self.settings.reform_watch_user_agent
            )
        allowed = self._robots[host]
        if allowed is False:
            return Outcome(key, "blocked", "robots.txt disallows this URL")
        if allowed is None:
            return Outcome(key, "skipped", "robots.txt unavailable (5xx/no answer): retry later")
        return None

    def _refused(self, o: Outcome, res: FetchResult) -> bool:
        """Fill `o` for an answer that is not a usable 2xx. True when the source stops here."""
        if res.status in (401, 403, 429):
            o.outcome, o.detail = "blocked", f"the server refused automated access ({res.error})"
            return True
        if not (200 <= res.status < 300):
            o.outcome, o.detail = "failed", res.error or f"HTTP {res.status}"
            return True
        return False

    def _obtain_leginfo(self, d: CorpusDoc, last: Any) -> tuple[Outcome, bytes | None, str, str]:
        """California (ADR-026): the newest yearly archive of the official directory, checked with
        a HEAD (ETag); only when it changed is the code extracted, by byte ranges, re-reading only
        the sections whose row changed since the last extraction."""
        assert d.source_url is not None
        o = Outcome(d.key, "pending")
        listing = self.fetcher.get(d.source_url)
        o.http_status = listing.status or None
        if self._refused(o, listing):
            return o, None, d.source_url, ""
        url = latest_archive(listing.body or b"", d.source_url)
        if url is None:
            o.outcome, o.detail = "failed", "no pubinfo_YYYY.zip in the official listing"
            return o, None, d.source_url, ""
        head = self.fetcher.get(url, method="HEAD")
        o.http_status, o.etag, o.last_modified = head.status or None, head.etag, head.last_modified
        if self._refused(o, head):
            return o, None, url, ""
        if last is not None and head.etag and head.etag == last.etag:
            o.outcome, o.sha256, o.detail = "unchanged", last.sha256, "archive not modified (ETag)"
            return o, None, url, ""
        if not head.size:
            o.outcome, o.detail = "failed", "the archive has no Content-Length"
            return o, None, url, ""
        cache_path = self.history / f"{d.key}.leginfo-cache.json"
        rf = RangeFile(self.fetcher, url, head.size, self.settings.leginfo_max_requests)
        try:
            with zipfile.ZipFile(rf) as z:
                ex = extract_code(z, d.code, date.today(), load_cache(cache_path))
        except (RangeError, zipfile.BadZipFile, KeyError) as exc:
            o.outcome, o.detail = "failed", f"archive: {type(exc).__name__}: {str(exc)[:300]}"
            return o, None, url, ""
        # A download cache of public law, not corpus state: kept even in a dry run, so the next
        # run does not read every section again.
        save_cache(cache_path, ex.cache)
        o.detail = (
            f"{ex.sections} sections, {ex.lob_reads} read from the archive, {rf.requests} requests"
        )
        return o, ex.text.encode("utf-8"), url, "txt"

    def check(self, d: CorpusDoc) -> Outcome:
        with tenant_transaction(self.engine, PUBLIC_CORPUS_TENANT_ID, allow_public=True) as conn:
            last = _last_check(conn, d.key)
            cur = _current_doc(conn, d.key)
        o, body, source, ext = self._obtain(d, last)
        if body is None:
            return self._finish(o, source)
        o.sha256 = hashlib.sha256(body).hexdigest()
        baseline = last.sha256 if last else _baseline_sha(d)
        if o.sha256 == baseline:
            o.outcome, o.detail = "unchanged", "same file as the version in use"
            return self._finish(o, source, consumed=ext if source.startswith("inbox:") else None)
        if cur is None and not d.approved:  # an approved source has no seed: this IS its first
            o.outcome, o.detail = "skipped", "not seeded yet: run app.ingestion.seed first"
            return self._finish(o, source)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.history.mkdir(parents=True, exist_ok=True)
        kept = self.history / f"{d.key}-{stamp}-{o.sha256[:8]}.{ext}"
        if self.dry_run:
            o.outcome, o.detail = (
                "unchanged",
                "DRY RUN: a different file is available (not ingested)",
            )
            return o
        kept.write_bytes(body)  # public law: the exact bytes ingested are kept, like raw/
        try:
            raw_text = self.parser.parse(kept).text if ext == "pdf" else body.decode("utf-8")
        except Exception as exc:  # one source failing never aborts the run
            o.outcome, o.detail = "failed", f"parse: {type(exc).__name__}: {str(exc)[:300]}"
            return self._finish(o, source)
        normalize = normalize_us_text if d.grammar == "us" else normalize_mx
        checksum = hashlib.sha256(normalize(raw_text).encode("utf-8")).hexdigest()
        if cur is not None and checksum == cur.checksum_sha256:
            o.outcome, o.detail = "unchanged", "new file, same legal text"
            return self._finish(o, source, consumed=ext if source.startswith("inbox:") else None)
        pub = published_on(raw_text)
        label = pub.strftime("%Y.%m.%d") if pub else date.today().strftime("%Y.%m.%d")
        if cur is not None and label <= str(cur.version_label or ""):
            label = f"{label}r{stamp[-6:]}"  # never reuse a version label: cite_ids are immutable
        how = o.detail  # what obtaining it cost (California: sections read from the archive)
        o = self._ingest(d, o, kept, raw_text, label, pub)
        if how:
            o.detail = f"{o.detail} ({how})"
        return self._finish(o, source, consumed=ext if source.startswith("inbox:") else None)

    def _ingest(
        self, d: CorpusDoc, o: Outcome, kept: Path, raw_text: str, label: str, pub: date | None
    ) -> Outcome:
        try:
            with tenant_transaction(
                self.engine, PUBLIC_CORPUS_TENANT_ID, allow_public=True
            ) as conn:
                jid = _jurisdiction_id(conn, d)
            meta = DocMeta(
                country=d.country,
                scope=d.scope,
                code=d.code,
                title=d.title,
                version_label=label,
                language=d.language,
            )
            th = ChunkThresholds.from_settings(self.settings, self.counter.name)
            rel = (
                kept.relative_to(DATA.parent.parent)
                if kept.is_relative_to(DATA.parent.parent)
                else kept
            )
            uri = f"file://{rel.as_posix()}"
            stats = ingest_document(
                self.engine,
                PUBLIC_CORPUS_TENANT_ID,
                meta=meta,
                doc_type=d.doc_type,
                jurisdiction_id=jid,
                source_uri=uri,
                raw_text=raw_text,
                embedder=self.embedder,
                counter=self.counter,
                thresholds=th,
                official_id=d.key,
                legal_domain=list(d.legal_domain),
                valid_from=pub,
                grammar=d.grammar,
            )
        except Exception as exc:
            o.outcome, o.detail = "failed", f"ingest: {type(exc).__name__}: {str(exc)[:300]}"
            return o
        if stats.status == "unchanged" or stats.document_id is None:
            o.outcome, o.detail = "unchanged", "already ingested"
            return o
        totals: dict[str, int] = {}
        with tenant_transaction(self.engine, PUBLIC_CORPUS_TENANT_ID, allow_public=True) as conn:
            for old_id in stats.retired_document_ids:
                _, c = record(
                    conn,
                    PUBLIC_CORPUS_TENANT_ID,
                    official_id=d.key,
                    old_document_id=old_id,
                    new_document_id=stats.document_id,
                    source_uri=uri,
                    published_on=pub,
                )
                for k, v in c.items():
                    totals[k] = totals.get(k, 0) + v
        o.outcome, o.counts = "ingested", totals
        o.detail = f"version {label}: {stats.chunks_written} chunks; " + (
            ", ".join(f"{k}={v}" for k, v in totals.items())
            if stats.retired_document_ids
            else "first version (approved source, ADR-026)"
        )
        return o

    def _finish(self, o: Outcome, source: str, consumed: str | None = None) -> Outcome:
        if not self.dry_run:
            _save_check(self.engine, o, source)
            if consumed:  # an inbox file is moved aside once handled, so it is not re-read
                f = self.inbox / f"{o.key}.{consumed}"
                done = self.inbox / "processed"
                done.mkdir(parents=True, exist_ok=True)
                shutil.move(str(f), done / f"{o.key}-{datetime.now():%Y%m%d-%H%M%S}.{consumed}")
        return o

    def read_index(self, spec: IndexSpec) -> Outcome:
        """ADR-026: read one official index and record its entries. Downloads no law."""
        key = f"index:{spec.index_id}"
        with tenant_transaction(self.engine, PUBLIC_CORPUS_TENANT_ID, allow_public=True) as conn:
            last = _last_check(conn, key)
        gate = self._robots_gate(key, spec.url)
        if gate is not None:
            return self._finish(gate, spec.url)
        res = self.fetcher.get(
            spec.url,
            etag=last.etag if last else None,
            last_modified=last.last_modified if last else None,
        )
        o = Outcome(key, "pending", http_status=res.status or None, etag=res.etag,
                    last_modified=res.last_modified)  # fmt: skip
        if res.status == 304 and last is not None:
            o.outcome, o.sha256, o.detail = "unchanged", last.sha256, "index not modified"
            o.etag, o.last_modified = last.etag, last.last_modified
            return self._finish(o, spec.url)
        if self._refused(o, res) or res.body is None:
            o.outcome = "failed" if o.outcome == "pending" else o.outcome
            return self._finish(o, spec.url)
        o.sha256 = hashlib.sha256(res.body).hexdigest()
        if last is not None and o.sha256 == last.sha256:
            o.outcome, o.detail = "unchanged", "same index page"
            return self._finish(o, spec.url)
        try:
            entries = read_index(spec, res.body)
        except IndexLayoutError as exc:
            o.outcome, o.sha256, o.detail = "failed", None, str(exc)
            return self._finish(o, spec.url)
        if self.dry_run:
            o.outcome, o.detail = (
                "indexed",
                f"DRY RUN: {len(entries)} entries read, nothing recorded",
            )
            return o
        with tenant_transaction(self.engine, PUBLIC_CORPUS_TENANT_ID, allow_public=True) as conn:
            counts = record_index(conn, spec, entries)
        o.outcome, o.counts = "indexed", counts
        o.detail = (
            f"{counts['entries']} entries: {counts['new']} new, {counts['missing']} no longer "
            f"listed, {counts['returned']} listed again"
        )
        return self._finish(o, spec.url)

    def run_indexes(self, specs: tuple[IndexSpec, ...]) -> list[Outcome]:
        results = []
        for spec in specs:
            try:
                o = self.read_index(spec)
            except Exception as exc:  # one index failing never stops the laws' checks
                o = Outcome(
                    f"index:{spec.index_id}", "failed", f"{type(exc).__name__}: {exc}"[:300]
                )
                if not self.dry_run:
                    with contextlib.suppress(Exception):
                        _save_check(self.engine, o, spec.url)
            self.out.write(f"{o.key:26} {o.outcome:9}           {o.detail}\n")
            results.append(o)
        return results

    def run(self, docs: tuple[CorpusDoc, ...]) -> list[Outcome]:
        if not self.dry_run:
            n = self.backfill()
            if n:
                self.out.write(f"backfilled the lineage of {n} version(s)\n")
        results = []
        for d in docs:
            t0 = time.monotonic()
            try:
                o = self.check(d)
            except (
                Exception
            ) as exc:  # isolate failures per source (lesson: failed[] without aborting)
                o = Outcome(d.key, "failed", f"{type(exc).__name__}: {str(exc)[:300]}")
                if not self.dry_run:
                    with contextlib.suppress(Exception):  # the database may be the failure
                        _save_check(self.engine, o, d.source_url or "none")
            self.out.write(f"{d.key:26} {o.outcome:9} {time.monotonic() - t0:7.1f}s  {o.detail}\n")
            results.append(o)
        return results


def check_urls(fetcher: Fetcher, out: Any, docs: tuple[CorpusDoc, ...] = CORPUS) -> int:
    bad = 0
    targets = [(d.key, d.source_url) for d in docs]
    targets += [(f"index:{s.index_id}", s.url) for s in INDEXES.values()]
    for key, url in targets:
        if url is None:
            out.write(f"{key:26} (no official URL: inbox only)\n")
            continue
        r = fetcher.get(url, method="HEAD")
        ok = 200 <= r.status < 400
        bad += not ok
        out.write(f"{key:26} {r.status or '-':>4} {r.error or ''} {url}\n")
    return bad


def _offline_counter(cls: Any) -> TokenCounter:
    """The embedder's tokenizer from the LOCAL cache: the watcher contacts only the official URLs
    (ADR-017), not the model hub. Falls back to the normal load only if it was never cached."""
    try:
        from huggingface_hub import try_to_load_from_cache

        cached = try_to_load_from_cache("BAAI/bge-m3", "tokenizer.json")
        if isinstance(cached, str):
            return cls(path=cached)  # type: ignore[no-any-return]
    except ImportError:
        pass
    return cls()  # type: ignore[no-any-return]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="app.ingestion.watch")
    ap.add_argument("--only", help="comma-separated keys: " + ",".join(BY_KEY) + " (or approved)")
    ap.add_argument("--dry-run", action="store_true", help="fetch and compare only; write nothing")
    ap.add_argument("--check-urls", action="store_true", help="HEAD every official URL and exit")
    ap.add_argument("--no-indexes", action="store_true", help="skip the official indexes (ADR-026)")
    ap.add_argument("--database", help="database name (default: legalai)")
    ap.add_argument(
        "--if-stale",
        type=float,
        metavar="HOURS",
        help="do nothing if the last check is newer than HOURS (scheduled/launch runs)",
    )
    a = ap.parse_args(argv)
    s = get_settings()
    fetcher = UrllibFetcher(
        s.reform_watch_user_agent, s.reform_watch_timeout_s, s.reform_watch_max_bytes
    )
    engine = create_engine(s.ingestor_url(a.database))
    with tenant_transaction(engine, PUBLIC_CORPUS_TENANT_ID, allow_public=True) as conn:
        sources = {d.key: d for d in watched_sources(conn)}
    if a.check_urls:
        engine.dispose()
        return 1 if check_urls(fetcher, sys.stdout, tuple(sources.values())) else 0
    keys = a.only.split(",") if a.only else list(sources)
    unknown = [k for k in keys if k not in sources]
    if unknown:
        sys.stderr.write(f"unknown corpus keys: {unknown}\n")
        engine.dispose()
        return 2
    # ADR-017: the parser's layout models and the tokenizer come from the local cache; the only
    # network destinations of this job are the official URLs of the registry and the indexes.
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    from app.ingestion.embedders import OllamaEmbedder
    from app.ingestion.parser import DoclingParser
    from app.ingestion.tokens import HFTokenCounter

    if a.if_stale is not None:
        with tenant_transaction(engine, PUBLIC_CORPUS_TENANT_ID, allow_public=True) as conn:
            age = conn.execute(
                text("SELECT extract(epoch FROM now() - max(checked_at)) / 3600 FROM source_checks")
            ).scalar()
        if age is not None and float(age) < a.if_stale:
            sys.stdout.write(
                f"last check {float(age):.1f} h ago (< {a.if_stale} h): nothing to do\n"
            )
            engine.dispose()
            return 0
    try:
        w = Watcher(
            settings=s,
            engine=engine,
            fetcher=fetcher,
            parser=DoclingParser(),
            embedder=OllamaEmbedder(s.embedder_model, base_url=s.ollama_url),
            counter=_offline_counter(HFTokenCounter),
            dry_run=a.dry_run,
        )
        indexed = [] if (a.only or a.no_indexes) else w.run_indexes(tuple(INDEXES.values()))
        results = w.run(tuple(sources[k] for k in keys))
    finally:
        engine.dispose()
    failed = [r for r in [*indexed, *results] if r.outcome == "failed"]
    sys.stdout.write(
        f"\n{len(results)} source(s): "
        + ", ".join(
            f"{k}={sum(1 for r in results if r.outcome == k)}"
            for k in ("unchanged", "ingested", "blocked", "skipped", "failed")
        )
        + "\n"
    )
    new = sum(r.counts.get("new", 0) for r in indexed)
    if new:
        sys.stdout.write(
            f"{new} law(s) newly listed by an official index: "
            "python -m app.ingestion.discover list --status new\n"
        )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "FetchResult",
    "Fetcher",
    "Outcome",
    "UrllibFetcher",
    "Watcher",
    "published_on",
    "robots_allows",
]
