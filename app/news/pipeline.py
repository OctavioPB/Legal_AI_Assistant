"""The news pipeline (ADR-027): collect, score with fixed rules, keep the best `news_feed_size`.

    uv run python -m app.news.pipeline            # collect and update the feed
    uv run python -m app.news.pipeline --dry-run  # collect and print the feed it would keep

Internal sources need no network: the reforms the watcher recorded (`corpus_changes`) and what the
official index says (`corpus_index_entries`: new laws, recent reforms). Network sources are the
closed list of `app/news/sources.py`, read with the watcher's rules (robots.txt, one request,
conditional GET, a block recorded in `source_checks` as `news:<id>` and never evaded).

The feed is ONE shared list (public corpus tenant, written only by `corpus_ingestor`): every run
merges what it found with the current feed, re-scores everything (recency moves) and keeps the top
N in one transaction. An item keeps the day it was first seen. Nothing is generated: titles and
excerpts are the sources' own, clipped.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.parse
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import Connection, Engine, create_engine, text

from app.db.session import PUBLIC_CORPUS_TENANT_ID, tenant_transaction
from app.ingestion.discover import watched_sources
from app.ingestion.watch import (
    Fetcher,
    Outcome,
    UrllibFetcher,
    _last_check,
    _save_check,
    robots_allows,
)
from app.news.score import Scored, dedupe, legal_terms, recency
from app.news.sources import BY_ID, SOURCES, FeedError, NewsSource, RawItem, parse_rss, parse_scjn
from app.settings import Settings, get_settings


@dataclass
class Candidate:
    source_id: str
    kind: str
    title: str
    url: str
    outlet: str
    published_at: datetime | None
    excerpt: str
    official: bool
    facts: dict[str, Any] = field(default_factory=dict)
    first_seen_at: datetime | None = None
    score: float = 0.0
    terms: tuple[str, ...] = ()


@dataclass
class Report:
    sources: list[Outcome]
    feed: list[Candidate]
    added: int
    dropped: int


def _at(d: date | datetime | None) -> datetime | None:
    if d is None:
        return None
    if isinstance(d, datetime):
        return d if d.tzinfo else d.replace(tzinfo=UTC)
    return datetime(d.year, d.month, d.day, 12, tzinfo=UTC)


# ---- internal sources (no network) -------------------------------------------------------------
def collect_internal(conn: Connection, s: Settings, now: datetime) -> list[Candidate]:
    since = now - timedelta(days=s.news_recent_days)
    official_url = {d.key: d.source_url for d in watched_sources(conn)}
    out: list[Candidate] = []
    src = BY_ID["reforms"]
    for r in conn.execute(
        text(
            "SELECT c.id, c.official_id, d.title, c.published_on, c.detected_at, c.counts, "
            "c.new_version FROM corpus_changes c JOIN documents d ON d.id = c.new_document_id "
            "WHERE c.detected_at >= :since ORDER BY c.detected_at DESC"
        ),
        {"since": since},
    ):
        counts = {k: int(v) for k, v in dict(r.counts).items() if k != "unchanged" and int(v)}
        out.append(
            Candidate(
                src.source_id,
                "reform",
                r.title,
                f"/reformas?c={r.id}",
                src.outlet,
                _at(r.published_on) or _at(r.detected_at),
                "",
                True,
                facts={
                    "counts": counts,
                    "version": r.new_version,
                    "official_url": official_url.get(r.official_id),
                },
            )
        )
    src = BY_ID["dip_index"]
    for r in conn.execute(
        text(
            "SELECT entry_key, title, url, status, published_on, last_reform_on, last_reform_text, "
            "note FROM corpus_index_entries WHERE missing_since IS NULL AND "
            "(status = 'new' OR last_reform_on >= :since_d)"
        ),
        {"since_d": since.date()},
    ):
        is_new = r.status == "new"
        out.append(
            Candidate(
                src.source_id,
                "new_law" if is_new else "recent_reform",
                r.title,
                r.url,
                src.outlet,
                _at(r.published_on if is_new else r.last_reform_on),
                "",
                True,
                facts={"last_reform": r.last_reform_text, "note": r.note},
            )
        )
    return out


# ---- network sources ---------------------------------------------------------------------------
def collect_source(
    engine: Engine,
    fetcher: Fetcher,
    s: Settings,
    src: NewsSource,
    robots: dict[str, bool | None],
    dry_run: bool,
) -> tuple[Outcome, list[RawItem]]:
    assert src.url is not None
    key = f"news:{src.source_id}"
    o = Outcome(key, "pending")
    host = urllib.parse.urlsplit(src.url).netloc
    if host not in robots:
        robots[host] = robots_allows(fetcher, src.url, s.reform_watch_user_agent)
    items: list[RawItem] = []
    if robots[host] is False:
        o.outcome, o.detail = "blocked", "robots.txt disallows this URL"
    elif robots[host] is None:
        o.outcome, o.detail = "skipped", "robots.txt unavailable (5xx/no answer): retry later"
    else:
        with tenant_transaction(engine, PUBLIC_CORPUS_TENANT_ID, allow_public=True) as conn:
            last = _last_check(conn, key)
        res = fetcher.get(
            src.url,
            etag=last.etag if last else None,
            last_modified=last.last_modified if last else None,
        )
        o.http_status, o.etag, o.last_modified = res.status or None, res.etag, res.last_modified
        if res.status == 304 and last is not None:
            o.outcome, o.sha256, o.detail = "unchanged", last.sha256, "not modified"
            o.etag, o.last_modified = last.etag, last.last_modified
        elif res.status in (401, 403, 429):
            o.outcome, o.detail = "blocked", f"the server refused automated access ({res.error})"
        elif not (200 <= res.status < 300) or res.body is None:
            o.outcome, o.detail = "failed", res.error or f"HTTP {res.status}"
        else:
            o.sha256 = hashlib.sha256(res.body).hexdigest()
            try:
                items = (
                    parse_scjn(res.body, src.outlet)
                    if src.reader == "scjn"
                    else parse_rss(res.body, src.outlet, s.news_excerpt_chars)
                )
            except FeedError as exc:
                o.outcome, o.sha256, o.detail = "failed", None, str(exc)
            else:
                o.outcome, o.detail = "collected", f"{len(items)} items"
                o.counts = {"items": len(items)}
    if not dry_run:
        _save_check(engine, o, src.url)
    return o, items


# ---- scoring and the feed ----------------------------------------------------------------------
def score_all(cands: list[Candidate], s: Settings, now: datetime) -> list[Candidate]:
    kept: list[Candidate] = []
    for c in cands:
        src = BY_ID[c.source_id]
        terms, weight = legal_terms(f"{c.title} {c.excerpt}")
        if src.needs_legal_terms and weight < s.news_min_press_terms:
            continue  # a general outlet's item that names no legal matter
        when = c.published_at or c.first_seen_at or now
        c.score = round(
            src.weight + weight + recency(min(when, now), now, s.news_half_life_days), 4
        )
        c.terms = tuple(terms)
        kept.append(c)
    by_url = {c.url: c for c in kept}
    best = dedupe(Scored(c.url, c.title, c.score, c.terms, c.source_id) for c in kept)
    feed: list[Candidate] = []
    per_source: dict[str, int] = {}
    for b in best:  # highest first; a source past its cap gives way to the next source
        c = by_url[b.key]
        if per_source.get(c.source_id, 0) >= s.news_max_per_source:
            continue
        per_source[c.source_id] = per_source.get(c.source_id, 0) + 1
        feed.append(c)
        if len(feed) == s.news_feed_size:
            break
    return feed


def _current(conn: Connection) -> list[Candidate]:
    rows = conn.execute(
        text(
            "SELECT source_id, kind, title, url, outlet, published_at, excerpt, official, facts, "
            "first_seen_at FROM news_items WHERE in_feed"
        )
    ).all()
    return [
        Candidate(
            r.source_id,
            r.kind,
            r.title,
            r.url,
            r.outlet,
            r.published_at,
            r.excerpt or "",
            r.official,
            dict(r.facts),
            r.first_seen_at,
        )
        for r in rows
        if r.source_id in BY_ID  # a source removed from the registry leaves the feed
    ]


def merge(current: list[Candidate], found: list[Candidate], now: datetime) -> list[Candidate]:
    """Fresh data wins, but an item keeps the day it was first seen."""
    by_url = {c.url: c for c in current}
    for f in found:
        old = by_url.get(f.url)
        first = old.first_seen_at if old is not None and old.first_seen_at else now
        by_url[f.url] = replace(f, first_seen_at=first)
    return list(by_url.values())


def write_feed(conn: Connection, feed: list[Candidate], now: datetime) -> int:
    urls = [c.url for c in feed]
    dropped = conn.execute(  # retired, never deleted (public rows)
        text(
            "UPDATE news_items SET in_feed = false WHERE in_feed AND NOT (url = ANY(:u)) "
            "RETURNING id"
        ),
        {"u": urls},
    ).all()
    for c in feed:
        conn.execute(
            text(
                "INSERT INTO news_items (tenant_id, source_id, kind, title, url, outlet, "
                "published_at, first_seen_at, excerpt, facts, terms, score, official, "
                "refreshed_at, in_feed) "
                "VALUES (public_corpus_id(), :src, :kind, :title, :url, :outlet, :pub, :first, "
                ":ex, "
                "CAST(:facts AS jsonb), :terms, :score, :official, :now, true) "
                "ON CONFLICT (tenant_id, url) DO UPDATE SET source_id = EXCLUDED.source_id, "
                "kind = EXCLUDED.kind, title = EXCLUDED.title, outlet = EXCLUDED.outlet, "
                "published_at = EXCLUDED.published_at, excerpt = EXCLUDED.excerpt, "
                "facts = EXCLUDED.facts, terms = EXCLUDED.terms, score = EXCLUDED.score, "
                "official = EXCLUDED.official, refreshed_at = EXCLUDED.refreshed_at, in_feed = true"
            ),
            {
                "src": c.source_id,
                "kind": c.kind,
                "title": c.title[:600],
                "url": c.url,
                "outlet": c.outlet[:200],
                "pub": c.published_at,
                "first": c.first_seen_at or now,
                "ex": c.excerpt or None,
                "facts": json.dumps(c.facts, default=str),
                "terms": list(c.terms),
                "score": c.score,
                "official": c.official,
                "now": now,
            },
        )
    return len(dropped)


def run(
    engine: Engine,
    fetcher: Fetcher,
    s: Settings,
    *,
    dry_run: bool = False,
    sources: tuple[NewsSource, ...] = SOURCES,
    now: datetime | None = None,
    out: Any = sys.stdout,
) -> Report:
    now = now or datetime.now(UTC)
    robots: dict[str, bool | None] = {}
    outcomes: list[Outcome] = []
    found: list[Candidate] = []
    for src in sources:
        if src.reader == "internal":
            continue
        try:
            o, items = collect_source(engine, fetcher, s, src, robots, dry_run)
        except Exception as exc:  # one source failing never stops the feed
            o, items = (
                Outcome(f"news:{src.source_id}", "failed", f"{type(exc).__name__}: {exc}"[:300]),
                [],
            )
        outcomes.append(o)
        out.write(f"{o.key:24} {o.outcome:9} {o.detail}\n")
        found += [
            Candidate(
                src.source_id,
                src.kind,
                i.title,
                i.url,
                i.outlet,
                i.published_at,
                i.excerpt,
                src.official,
            )
            for i in items
        ]
    with tenant_transaction(engine, PUBLIC_CORPUS_TENANT_ID, allow_public=True) as conn:
        if any(src.reader == "internal" for src in sources):
            found += [
                c
                for c in collect_internal(conn, s, now)
                if c.source_id in {x.source_id for x in sources}
            ]
        current = _current(conn)
        current_urls = {c.url for c in current}
        feed = score_all(merge(current, found, now), s, now)
        added = sum(1 for c in feed if c.url not in current_urls)
        dropped = 0 if dry_run else write_feed(conn, feed, now)
    out.write(f"feed: {len(feed)} items ({added} new, {dropped} dropped)\n")
    return Report(outcomes, feed, added, dropped)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="app.news.pipeline")
    ap.add_argument("--dry-run", action="store_true", help="collect and print; write nothing")
    ap.add_argument("--database", help="database name (default: legalai)")
    a = ap.parse_args(argv)
    s = get_settings()
    fetcher = UrllibFetcher(
        s.reform_watch_user_agent, s.reform_watch_timeout_s, s.reform_watch_max_bytes
    )
    engine = create_engine(s.ingestor_url(a.database))
    try:
        rep = run(engine, fetcher, s, dry_run=a.dry_run)
    finally:
        engine.dispose()
    if a.dry_run:
        for c in rep.feed:
            sys.stdout.write(
                f"{c.score:6.2f}  [{c.kind}] {c.title[:100]}  ({', '.join(c.terms)})\n"
            )
    return 1 if all(o.outcome in ("failed", "blocked") for o in rep.sources) and rep.sources else 0


if __name__ == "__main__":
    raise SystemExit(main())
