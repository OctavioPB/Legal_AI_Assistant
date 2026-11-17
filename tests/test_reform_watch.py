"""Reform watcher (ADR-017): detect a new version of a law, ingest it aside of nothing (a NEW version,
the old one retired, never deleted), diff it per citable unit, and let the rest of the system follow the
lineage. The network is a fake: sockets are disabled in the suite."""

from __future__ import annotations

import dataclasses
import io
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import Engine, text

from app.corpus.lineage import carry_forward, successions
from app.db.session import PUBLIC_CORPUS_TENANT_ID, tenant_transaction
from app.ingestion.corpus import BY_KEY
from app.ingestion.reform import Unit, diff
from app.ingestion.tokens import ApproxCounter
from app.ingestion.watch import FetchResult, Watcher, published_on
from tests.lawtext import (
    EMB,
    LFT_URL,
    ROBOTS,
    WATCH_SETTINGS,
    FakeFetcher,
    FakeParser,
    ingest_law,
    ok,
    pdf,
    v1,
    v2,
    v3,
)

LFT = (BY_KEY["LFT"],)


def watcher(engine: Engine, fetcher: FakeFetcher, tmp: Path, **kw: object) -> Watcher:
    return Watcher(
        settings=WATCH_SETTINGS, engine=engine, fetcher=fetcher, parser=FakeParser(),
        embedder=EMB, counter=ApproxCounter(), inbox=tmp / "inbox", history=tmp / "history",
        out=io.StringIO(), **kw,  # type: ignore[arg-type]
    )  # fmt: skip


def q(engine: Engine, sql: str, **p: object) -> list:  # type: ignore[type-arg]
    with tenant_transaction(engine, PUBLIC_CORPUS_TENANT_ID, allow_public=True) as c:
        return list(c.execute(text(sql), p).all())


def stem_changes(engine: Engine) -> dict[str, str]:
    rows = q(engine, "SELECT coalesce(new_cite_id, old_cite_id) AS id, change FROM cite_lineage")
    return {r.id.rsplit("-v", 1)[0]: r.change for r in rows}


def test_a_reform_becomes_a_new_version_with_its_lineage(
    ingestor_engine: Engine, tmp_path: Path
) -> None:
    ingest_law(ingestor_engine, v1())
    f = FakeFetcher({ROBOTS: FetchResult(404), LFT_URL: ok(pdf(v2()))})
    [o] = watcher(ingestor_engine, f, tmp_path).run(LFT)
    assert o.outcome == "ingested", o.detail
    docs = q(ingestor_engine, "SELECT version_label, valid_from, valid_to FROM documents "
             "WHERE official_id = 'LFT' ORDER BY ingested_at")  # fmt: skip
    assert [d.version_label for d in docs] == ["2026.05.14", "2026.10.01"]
    assert docs[0].valid_to is not None and docs[1].valid_to is None  # retired, never deleted
    assert docs[1].valid_from == date(2026, 10, 1)  # from "Última reforma publicada DOF ..."
    old_current = q(ingestor_engine, "SELECT count(*) AS n FROM chunks c JOIN documents d "
                    "ON d.id = c.document_id WHERE d.version_label = '2026.05.14' AND c.is_current")  # fmt: skip
    assert old_current[0].n == 0
    ch = stem_changes(ingestor_engine)
    assert ch["MX-FED-LFT-ART47-FRACII"] == "amended"
    assert ch["MX-FED-LFT-ART47-FRACIII"] == "added"
    assert ch["MX-FED-LFT-ART48"] == "unchanged"
    assert ch["MX-FED-LFT-ART49"] == "removed"
    assert ch["MX-FED-LFT-ART50"] == "derogated"
    assert ch["MX-FED-LFT-ART50BIS"] == "added"
    [chg] = q(ingestor_engine, "SELECT counts, published_on, old_version, new_version "
              "FROM corpus_changes")  # fmt: skip
    assert chg.published_on == date(2026, 10, 1)
    assert (chg.old_version, chg.new_version) == ("2026.05.14", "2026.10.01")
    # 47-II and the header paragraph that carries the reform date
    assert chg.counts["amended"] == 2 and chg.counts["removed"] == 1
    [check] = q(ingestor_engine, "SELECT outcome, sha256, etag FROM source_checks")
    assert (check.outcome, check.etag) == ("ingested", '"e1"') and check.sha256
    assert list((tmp_path / "history").iterdir()), "the exact bytes ingested are kept"


def test_nothing_new_costs_nothing(ingestor_engine: Engine, tmp_path: Path) -> None:
    ingest_law(ingestor_engine, v1())
    body = pdf(v2())
    f = FakeFetcher({ROBOTS: FetchResult(404), LFT_URL: [ok(body), ok(body), FetchResult(304)]})
    w = watcher(ingestor_engine, f, tmp_path)
    assert w.run(LFT)[0].outcome == "ingested"
    second = w.run(LFT)[0]  # same bytes
    assert (second.outcome, second.detail) == ("unchanged", "same file as the version in use")
    third = w.run(LFT)[0]  # 304 Not Modified, after a conditional request
    assert third.outcome == "unchanged"
    assert f.calls[-1][2] == '"e1"', "the ETag of the last good check is sent back"
    # different bytes, same legal text (a re-exported PDF): no new version
    f.routes[LFT_URL] = ok(pdf(v2()) + b"\n", etag='"e2"')
    assert w.run(LFT)[0].detail == "new file, same legal text"
    assert len(q(ingestor_engine, "SELECT id FROM documents WHERE official_id = 'LFT'")) == 2


@pytest.mark.parametrize(
    ("robots", "answer", "outcome"),
    [
        (FetchResult(404), FetchResult(403, error="HTTP 403"), "blocked"),
        (FetchResult(404), FetchResult(429, error="HTTP 429"), "blocked"),
        (FetchResult(404), ok(b"<html>Checking your browser...</html>"), "blocked"),
        (FetchResult(200, b"User-agent: *\nDisallow: /LeyesBiblio/"), ok(pdf(v2())), "blocked"),
        (FetchResult(503), ok(pdf(v2())), "skipped"),
        (FetchResult(404), FetchResult(0, error="TimeoutError: timed out"), "failed"),
    ],
)
def test_blocks_are_recorded_never_evaded(
    ingestor_engine: Engine, tmp_path: Path, robots: FetchResult, answer: FetchResult, outcome: str
) -> None:
    ingest_law(ingestor_engine, v1())
    f = FakeFetcher({ROBOTS: robots, LFT_URL: answer})
    [o] = watcher(ingestor_engine, f, tmp_path).run(LFT)
    assert o.outcome == outcome, o.detail
    assert sum(1 for c in f.calls if c[1] == LFT_URL) <= 1, "one request per source, no retries"
    if robots.status in (200, 503):
        assert not any(c[1] == LFT_URL for c in f.calls), "robots said no: the law is not fetched"
    assert len(q(ingestor_engine, "SELECT id FROM documents WHERE official_id = 'LFT'")) == 1
    assert q(ingestor_engine, "SELECT outcome FROM source_checks")[0].outcome == outcome


def test_dry_run_writes_nothing(ingestor_engine: Engine, tmp_path: Path) -> None:
    ingest_law(ingestor_engine, v1())
    f = FakeFetcher({ROBOTS: FetchResult(404), LFT_URL: ok(pdf(v2()))})
    [o] = watcher(ingestor_engine, f, tmp_path, dry_run=True).run(LFT)
    assert "DRY RUN" in o.detail
    assert q(ingestor_engine, "SELECT count(*) AS n FROM source_checks")[0].n == 0
    assert len(q(ingestor_engine, "SELECT id FROM documents")) == 1
    assert not (tmp_path / "history").exists() or not list((tmp_path / "history").iterdir())


def test_an_inbox_file_is_used_first_and_moved_aside(
    ingestor_engine: Engine, tmp_path: Path
) -> None:
    ingest_law(ingestor_engine, v1())
    (tmp_path / "inbox").mkdir()
    (tmp_path / "inbox" / "LFT.txt").write_text(v2(), encoding="utf-8")
    f = FakeFetcher({})
    [o] = watcher(ingestor_engine, f, tmp_path).run(LFT)
    assert o.outcome == "ingested" and f.calls == [], "the inbox needs no network"
    assert not (tmp_path / "inbox" / "LFT.txt").exists()
    assert list((tmp_path / "inbox" / "processed").iterdir())


def test_a_source_without_official_url_is_skipped_with_instructions(
    ingestor_engine: Engine, tmp_path: Path
) -> None:
    no_url = dataclasses.replace(BY_KEY["CC_NL"], source_url=None)  # every source has one now
    f = FakeFetcher({})
    [o] = watcher(ingestor_engine, f, tmp_path).run((no_url,))
    assert o.outcome == "skipped" and "inbox/CC_NL.pdf" in o.detail and f.calls == []


def test_a_run_that_died_before_its_diff_is_backfilled(
    ingestor_engine: Engine, tmp_path: Path
) -> None:
    ingest_law(ingestor_engine, v1())
    ingest_law(ingestor_engine, v2(), "2026.10.01")  # ingested, but no lineage recorded
    w = watcher(ingestor_engine, FakeFetcher({}), tmp_path)
    assert w.backfill() == 1
    assert stem_changes(ingestor_engine)["MX-FED-LFT-ART49"] == "removed"
    assert w.backfill() == 0


def test_lineage_is_followed_across_reforms_and_only_unchanged_units_are_carried(
    ingestor_engine: Engine, tmp_path: Path
) -> None:
    ingest_law(ingestor_engine, v1())
    f = FakeFetcher({ROBOTS: FetchResult(404), LFT_URL: [ok(pdf(v2())), ok(pdf(v3()), '"e3"')]})
    w = watcher(ingestor_engine, f, tmp_path)
    w.run(LFT)
    w.run(LFT)
    with tenant_transaction(ingestor_engine, PUBLIC_CORPUS_TENANT_ID, allow_public=True) as c:
        s = successions(c, ["MX-FED-LFT-ART48-v2026.05.14", "MX-FED-LFT-ART47-FRACI-v2026.05.14",
                            "MX-FED-LFT-ART49-v2026.05.14"])  # fmt: skip
        assert s["MX-FED-LFT-ART48-v2026.05.14"].current == "MX-FED-LFT-ART48-v2026.10.20"
        assert s["MX-FED-LFT-ART48-v2026.05.14"].change == "unchanged"
        assert s["MX-FED-LFT-ART47-FRACI-v2026.05.14"].change == "amended"  # in the 2nd reform
        assert s["MX-FED-LFT-ART49-v2026.05.14"].current is None
        carried = carry_forward(c, ("MX-FED-LFT-ART48-v2026.05.14",
                                    "MX-FED-LFT-ART47-FRACI-v2026.05.14", "OTHER-ID"))  # fmt: skip
    assert carried == ("MX-FED-LFT-ART48-v2026.10.20", "OTHER-ID")


def test_the_diff_is_per_unit_and_ignores_the_version_suffix() -> None:
    old = [
        Unit("X-ART1-v1", "a  b", "1"),
        Unit("X-ART2-v1", "texto", "2"),
        Unit("X-ART3-v1", "c", "3"),
    ]
    new = [Unit("X-ART1-v2", "a b", "1"), Unit("X-ART2-v2", "Artículo 2.- Se deroga.", "2"),
           Unit("X-ART4-v2", "d", "4")]  # fmt: skip
    assert [(ln.old, ln.new, ln.change) for ln in diff(old, new)] == [
        ("X-ART1-v1", "X-ART1-v2", "unchanged"),  # whitespace is not a reform
        ("X-ART2-v1", "X-ART2-v2", "derogated"),
        ("X-ART3-v1", None, "removed"),
        (None, "X-ART4-v2", "added"),
    ]


def test_the_reform_date_comes_from_the_official_header() -> None:
    assert published_on("TEXTO VIGENTE\nÚltima reforma publicada DOF 14-05-2026\n") == date(
        2026, 5, 14
    )
    assert published_on("Ultima Reforma Publicada en el P.O. 3/2/2025") == date(2025, 2, 3)
    assert published_on("sin fecha") is None
