"""ETL canary (`app.ingestion.canary`, `.github/workflows/etl-canary.yml`): reads every source with the
real readers, never writes, honours robots.txt and reports each failure kind. The network is a fake;
the pages are the real ones, saved and trimmed."""

from __future__ import annotations

import dataclasses
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml

from app.ingestion import canary
from app.ingestion.canary import Canary, report
from app.ingestion.corpus import BY_KEY, CORPUS, LEGINFO
from app.ingestion.indexes import INDEXES
from app.ingestion.net import FetchResult
from app.news.sources import BY_ID
from app.settings import Settings
from tests.lawtext import FakeFetcher, ok
from tests.test_corpus_discovery import LISTING, PAGE, ZIP_URL
from tests.test_news import JORNADA, NEXOS, SCJN

ROOT = Path(__file__).resolve().parents[1]
DIP = dataclasses.replace(INDEXES["DIP_FED"], min_entries=1)  # the fixture keeps 9 rows
NEWS = tuple(BY_ID[k] for k in ("scjn", "juego_corte", "jornada_politica"))
ROBOTS = {
    "https://www.diputados.gob.mx/robots.txt", "https://www.hcnl.gob.mx/robots.txt",
    "https://www.monterrey.gob.mx/robots.txt", "https://downloads.leginfo.legislature.ca.gov/robots.txt",
    "https://www.scjn.gob.mx/robots.txt", "https://eljuegodelacorte.nexos.com.mx/robots.txt",
    "https://www.jornada.com.mx/robots.txt",
}  # fmt: skip


def healthy_routes() -> dict[str, FetchResult | list[FetchResult]]:
    routes: dict[str, FetchResult | list[FetchResult]] = {r: FetchResult(404) for r in ROBOTS}
    for d in CORPUS:
        if d.fetch == "file":
            routes[d.source_url or ""] = FetchResult(200, b"", size=1000)
    routes[LEGINFO] = ok(LISTING)
    routes[DIP.url] = ok(PAGE)
    routes[BY_ID["scjn"].url or ""] = ok(SCJN)
    routes[BY_ID["juego_corte"].url or ""] = ok(NEXOS)
    routes[BY_ID["jornada_politica"].url or ""] = ok(JORNADA)
    return routes


class Sized(FakeFetcher):
    """HEAD of the archive reports a size without holding 1.2 GB in memory."""

    size = 1_292_263_530

    def get(self, url: str, **kw: object) -> FetchResult:  # type: ignore[override]
        if kw.get("method") == "HEAD" and url == ZIP_URL:
            self.calls.append(("HEAD", url, None, None))
            return FetchResult(200, b"", '"w1"', None, size=self.size)
        return super().get(url, **kw)  # type: ignore[arg-type]


def fetcher(size: int = 1_292_263_530, **over: FetchResult) -> Sized:
    f = Sized({**healthy_routes(), **over})
    f.size = size
    return f


def run(f: FakeFetcher) -> dict[str, canary.Check]:
    return {c.key: c for c in Canary(f).run(CORPUS, (DIP,), NEWS)}


def test_every_source_read_with_the_real_readers_and_nothing_else() -> None:
    f = fetcher()
    checks = run(f)
    assert {c.outcome for c in checks.values()} == {"ok"}, {k: c.detail for k, c in checks.items()}
    assert checks["index:DIP_FED"].items == 9
    assert (checks["news:scjn"].items, checks["news:juego_corte"].items) == (4, 2)
    assert "pubinfo_2025.zip" in checks["CA_LABOR_CODE"].detail
    laws = [c for c in f.calls if c[1] in {d.source_url for d in CORPUS if d.fetch == "file"}]
    assert all(c[0] == "HEAD" for c in laws), "a law is never downloaded by the canary"
    assert not any(c[0] == "GET" and c[1] == ZIP_URL for c in f.calls), "never the 1.2 GB archive"
    per_url = [c[1] for c in f.calls if not c[1].endswith("/robots.txt")]
    assert len(per_url) == len(set(per_url)), "one request per source"
    hosts = [c[1] for c in f.calls if c[1].endswith("/robots.txt")]
    assert len(hosts) == len(set(hosts)), "robots.txt once per host"


def test_each_failure_kind_is_named_and_the_others_still_run() -> None:
    lft, scjn = BY_KEY["LFT"].source_url or "", BY_ID["scjn"].url or ""
    f = fetcher(
        size=5_000,  # California's archive suddenly tiny: something changed
        **{
            lft: FetchResult(403, error="HTTP 403"),
            scjn: ok(b"<html><body>en mantenimiento</body></html>"),
            DIP.url: FetchResult(0, error="TimeoutError: timed out"),
            "https://www.jornada.com.mx/robots.txt": FetchResult(
                200, b"User-agent: *\nDisallow: /rss/"
            ),
        },
    )
    c = run(f)
    assert c["LFT"].outcome == "blocked" and c["LFT"].http_status == 403
    assert c["news:scjn"].outcome == "layout"
    assert c["index:DIP_FED"].outcome == "unreachable"
    assert c["news:jornada_politica"].outcome == "robots"
    assert not any(x[1] == BY_ID["jornada_politica"].url for x in f.calls), "robots said no"
    assert c["CA_LABOR_CODE"].outcome == "layout"
    assert c["CPF"].outcome == "ok" and c["news:juego_corte"].outcome == "ok"


def test_a_real_index_with_too_few_rows_is_a_layout_change() -> None:
    real = INDEXES["DIP_FED"]  # min_entries 200; the fixture has 9 rows
    c = {x.key: x for x in Canary(fetcher()).run((), (real,), ())}
    assert c["index:DIP_FED"].outcome == "layout" and "changed shape" in c["index:DIP_FED"].detail


def test_a_crashing_reader_is_reported_not_raised() -> None:
    class Boom(FakeFetcher):
        def get(self, url: str, **kw: object) -> FetchResult:  # type: ignore[override]
            if "robots" in url:
                return FetchResult(404)
            raise RuntimeError("socket closed")

    [c] = Canary(Boom({})).run((BY_KEY["LFT"],), (), ())
    assert c.outcome == "unreachable" and "socket closed" in c.detail


def test_the_report_puts_problems_first_and_counts_them() -> None:
    checks = list(
        run(
            fetcher(**{BY_KEY["CPF"].source_url or "": FetchResult(429, error="HTTP 429")})
        ).values()
    )
    text = report(checks, datetime(2026, 11, 18, 10, 23, tzinfo=UTC))
    assert "**1 de 12 fuentes con problemas.**" in text
    rows = [ln for ln in text.splitlines() if ln.startswith("| `")]
    assert rows[0].startswith("| `CPF`") and "bloqueada" in rows[0]
    clean = report(list(run(fetcher()).values()), datetime(2026, 11, 18, tzinfo=UTC))
    assert "Las 12 fuentes responden y se leen bien." in clean


def test_the_canary_speaks_with_the_watchers_user_agent() -> None:
    assert Settings.model_fields["reform_watch_user_agent"].default == canary.USER_AGENT


def test_the_canary_needs_only_the_standard_library() -> None:
    """The workflow runs it with `uv run --no-project --isolated`: no dependency may sneak in."""
    code = (
        "import sys, app.ingestion.canary; "
        "heavy = {'pydantic', 'pydantic_settings', 'sqlalchemy', 'docling', 'torch', 'yaml'}; "
        "print(sorted(m for m in sys.modules if m.split('.')[0] in heavy))"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True,
                         check=True)  # fmt: skip
    assert out.stdout.strip() == "[]", out.stdout


def test_the_workflow_reads_only_and_holds_no_secret() -> None:
    wf = yaml.safe_load((ROOT / ".github/workflows/etl-canary.yml").read_text("utf-8"))
    on = wf.get("on") or wf.get(True)  # YAML 1.1 reads the key `on` as true
    assert "schedule" in on and "workflow_dispatch" in on
    assert wf["permissions"] == {"contents": "read", "issues": "write"}
    text = (ROOT / ".github/workflows/etl-canary.yml").read_text("utf-8")
    assert not re.search(r"\$\{\{\s*secrets\.", text), "the canary needs no secret"
    steps = wf["jobs"]["canary"]["steps"]
    run_step = next(s for s in steps if s.get("id") == "canary")
    assert (
        "--no-project --isolated" in run_step["run"] and "app.ingestion.canary" in run_step["run"]
    )
    assert not any("uv sync" in str(s.get("run", "")) for s in steps), "nothing is installed"
