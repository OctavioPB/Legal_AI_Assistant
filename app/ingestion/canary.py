"""ETL canary (ADR-017/026/027): are the official sources and news feeds still there, and do our
readers still understand them? Reads, never writes: no database, no model, no secrets.

    python -m app.ingestion.canary [--report canary.md] [--json canary.json]

Standard library only (`app.ingestion.net`, `indexes`, `leginfo`, `corpus`, `app.news.sources`), so
a scheduled GitHub workflow runs it without installing the ingestion stack. Same rules as the
watcher: https, robots.txt (RFC 9309) honoured, one request per source, a refusal recorded, never
evaded. What it checks:

* every law of the static registry: a HEAD of its official URL (California: the official listing
  and a HEAD of the newest yearly archive, never the archive itself);
* every official index: one GET, parsed by its real reader; fewer than `min_entries` rows is a
  layout change;
* every network news source: one GET, parsed by its real reader; zero items is a layout change.

Exit code 1 when any source is blocked, unreachable or no longer understood. The loading itself
stays on the machine that holds the database (`dev-up.ps1 -WatchReforms`).
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from functools import partial
from typing import Literal

from app.ingestion.corpus import CORPUS, CorpusDoc
from app.ingestion.indexes import INDEXES, IndexLayoutError, IndexSpec, read_index
from app.ingestion.leginfo import latest_archive
from app.ingestion.net import Fetcher, FetchResult, UrllibFetcher, robots_allows
from app.news.sources import SOURCES, FeedError, NewsSource, parse_rss, parse_scjn

USER_AGENT = (
    "LegalAI-ReformWatcher/1.0"  # = Settings.reform_watch_user_agent (a test keeps them equal)
)
TIMEOUT_S = 60.0
MAX_BYTES = 20 * 1024 * 1024  # an index page or a feed; laws are only HEAD-requested
MIN_ARCHIVE_BYTES = 100 * 1024 * 1024  # California's yearly archive is ~1.2 GB

Outcome = Literal["ok", "blocked", "robots", "unreachable", "layout"]
Kind = Literal["law", "index", "news"]
NETWORK_NEWS: tuple[NewsSource, ...] = tuple(s for s in SOURCES if s.reader != "internal")


@dataclass(frozen=True)
class Check:
    key: str
    kind: Kind
    url: str
    outcome: Outcome
    http_status: int | None = None
    items: int | None = None
    detail: str = ""


class Canary:
    def __init__(self, fetcher: Fetcher) -> None:
        self.fetcher = fetcher
        self._robots: dict[str, bool | None] = {}

    def _robots_gate(self, key: str, kind: Kind, url: str) -> Check | None:
        host = urllib.parse.urlsplit(url).netloc
        if host not in self._robots:
            self._robots[host] = robots_allows(self.fetcher, url, USER_AGENT)
        allowed = self._robots[host]
        if allowed is False:
            return Check(key, kind, url, "robots", detail="robots.txt disallows this URL")
        if allowed is None:
            return Check(
                key, kind, url, "unreachable", detail="robots.txt did not answer (5xx/none)"
            )
        return None

    @staticmethod
    def _refused(key: str, kind: Kind, url: str, r: FetchResult) -> Check | None:
        if r.status in (401, 403, 429):
            return Check(key, kind, url, "blocked", r.status, detail=r.error or f"HTTP {r.status}")
        if not (200 <= r.status < 400):
            return Check(key, kind, url, "unreachable", r.status or None, detail=r.error or "")
        return None

    def law(self, d: CorpusDoc) -> Check:
        url = d.source_url or ""
        if not url:
            return Check(d.key, "law", url, "unreachable", detail="no official URL in the registry")
        gate = self._robots_gate(d.key, "law", url)
        if gate:
            return gate
        if d.fetch == "leginfo":
            listing = self.fetcher.get(url)
            bad = self._refused(d.key, "law", url, listing)
            if bad:
                return bad
            archive = latest_archive(listing.body or b"", url)
            if archive is None:
                return Check(
                    d.key, "law", url, "layout", listing.status, detail="no pubinfo_YYYY.zip listed"
                )
            head = self.fetcher.get(archive, method="HEAD")
            bad = self._refused(d.key, "law", archive, head)
            if bad:
                return bad
            if (head.size or 0) < MIN_ARCHIVE_BYTES:
                small = f"archive smaller than expected ({head.size} bytes)"
                return Check(d.key, "law", archive, "layout", head.status, detail=small)
            name = archive.rsplit("/", 1)[-1]
            info = f"{name}, {head.size} bytes, ETag {head.etag}"
            return Check(d.key, "law", archive, "ok", head.status, detail=info)
        head = self.fetcher.get(url, method="HEAD")
        return self._refused(d.key, "law", url, head) or Check(d.key, "law", url, "ok", head.status)

    def index(self, spec: IndexSpec) -> Check:
        key = f"index:{spec.index_id}"
        gate = self._robots_gate(key, "index", spec.url)
        if gate:
            return gate
        r = self.fetcher.get(spec.url)
        bad = self._refused(key, "index", spec.url, r)
        if bad:
            return bad
        try:
            n = len(read_index(spec, r.body or b""))
        except IndexLayoutError as exc:
            return Check(key, "index", spec.url, "layout", r.status, detail=str(exc))
        return Check(key, "index", spec.url, "ok", r.status, n)

    def news(self, src: NewsSource) -> Check:
        key, url = f"news:{src.source_id}", src.url or ""
        gate = self._robots_gate(key, "news", url)
        if gate:
            return gate
        r = self.fetcher.get(url)
        bad = self._refused(key, "news", url, r)
        if bad:
            return bad
        try:
            items = (parse_scjn(r.body or b"", src.outlet) if src.reader == "scjn"
                     else parse_rss(r.body or b"", src.outlet, 280))  # fmt: skip
        except FeedError as exc:
            return Check(key, "news", url, "layout", r.status, detail=str(exc))
        if not items:
            return Check(
                key, "news", url, "layout", r.status, 0, detail="no items: the page changed shape"
            )
        return Check(key, "news", url, "ok", r.status, len(items))

    def run(
        self,
        laws: Iterable[CorpusDoc] = CORPUS,
        indexes: Iterable[IndexSpec] = INDEXES.values(),
        news: Iterable[NewsSource] = NETWORK_NEWS,
    ) -> list[Check]:
        out: list[Check] = []
        for d in laws:
            out.append(self._safe(d.key, "law", partial(self.law, d)))
        for spec in indexes:
            out.append(self._safe(f"index:{spec.index_id}", "index", partial(self.index, spec)))
        for src in news:
            out.append(self._safe(f"news:{src.source_id}", "news", partial(self.news, src)))
        return out

    @staticmethod
    def _safe(key: str, kind: Kind, fn: Callable[[], Check]) -> Check:
        try:
            return fn()
        except Exception as exc:  # one source failing never hides the others
            return Check(key, kind, "", "unreachable", detail=f"{type(exc).__name__}: {exc}")


_LABEL = {
    "ok": "✅ ok",
    "blocked": "⛔ bloqueada",
    "robots": "🚫 robots.txt",
    "unreachable": "⚠️ sin respuesta",
    "layout": "🧩 cambió de formato",
}


def report(checks: list[Check], when: datetime) -> str:
    bad = [c for c in checks if c.outcome != "ok"]
    head = (f"**{len(bad)} de {len(checks)} fuentes con problemas.**" if bad
            else f"**Las {len(checks)} fuentes responden y se leen bien.**")  # fmt: skip
    lines = [
        f"## Canario ETL · {when:%Y-%m-%d %H:%M} UTC",
        "",
        head,
        "",
        "Lectura sin escritura desde los servidores de GitHub (EE. UU.): una fuente que bloquea "
        "direcciones de centros de datos puede responder bien desde el equipo de la demo. La carga "
        "real corre en ese equipo (`dev-up.ps1 -WatchReforms`).",
        "",
        "| Fuente | Tipo | Resultado | HTTP | Elementos | Detalle |",
        "|---|---|---|---|---|---|",
    ]
    for c in sorted(checks, key=lambda c: (c.outcome == "ok", c.kind, c.key)):
        detail = c.detail.replace("|", "/")[:160]
        lines.append(f"| `{c.key}` | {c.kind} | {_LABEL[c.outcome]} | {c.http_status or '—'} | "
                     f"{c.items if c.items is not None else '—'} | {detail} |")  # fmt: skip
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="app.ingestion.canary")
    ap.add_argument("--report", help="write the Markdown report here")
    ap.add_argument("--json", help="write the checks as JSON here")
    a = ap.parse_args(argv)
    checks = Canary(UrllibFetcher(USER_AGENT, TIMEOUT_S, MAX_BYTES)).run()
    text = report(checks, datetime.now(UTC))
    sys.stdout.write(text)
    if a.report:
        with open(a.report, "w", encoding="utf-8") as f:
            f.write(text)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump([asdict(c) for c in checks], f, ensure_ascii=False, indent=1)
    return 1 if any(c.outcome != "ok" for c in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())
