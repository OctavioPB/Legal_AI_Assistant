"""ADR-026: official indexes are read, never acted on alone; an operator's approval makes a law a
watched source; California's code is read from its official archive by byte ranges. The network is
a fake (sockets are disabled in the suite); the index page is the real one, saved and trimmed."""

from __future__ import annotations

import dataclasses
import io
import zipfile
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import Engine, text

from app.db.session import PUBLIC_CORPUS_TENANT_ID, tenant_transaction
from app.ingestion.corpus import BY_KEY, LEGINFO
from app.ingestion.discover import (
    DecisionError,
    approve,
    code_for,
    dismiss,
    display_title,
    doc_type_for,
    record_index,
    watched_sources,
)
from app.ingestion.indexes import INDEXES, IndexLayoutError, parse_diputados, read_index
from app.ingestion.leginfo import RangeFile, extract_code, latest_archive
from app.ingestion.tokens import ApproxCounter
from app.ingestion.watch import FetchResult, Watcher
from tests.lawtext import EMB, FILL, WATCH_SETTINGS, FakeFetcher, FakeParser, ok, pdf, v1

PAGE = (Path(__file__).parent / "fixtures" / "indexes" / "diputados_index.htm").read_bytes()
DIP = INDEXES["DIP_FED"]
SMALL = dataclasses.replace(DIP, min_entries=1)  # the fixture keeps 9 of the 318 rows
DIP_ROBOTS = "https://www.diputados.gob.mx/robots.txt"


def watcher(engine: Engine, fetcher: FakeFetcher, tmp: Path, **kw: object) -> Watcher:
    return Watcher(
        settings=WATCH_SETTINGS, engine=engine, fetcher=fetcher, parser=FakeParser(),
        embedder=EMB, counter=ApproxCounter(), inbox=tmp / "inbox", history=tmp / "history",
        out=io.StringIO(), **kw,  # type: ignore[arg-type]
    )  # fmt: skip


def q(engine: Engine, sql: str, **p: object) -> list:  # type: ignore[type-arg]
    with tenant_transaction(engine, PUBLIC_CORPUS_TENANT_ID, allow_public=True) as c:
        return list(c.execute(text(sql), p).all())


def statuses(engine: Engine) -> dict[str, str]:
    rows = q(engine, "SELECT entry_key, status, missing_since FROM corpus_index_entries")
    return {r.entry_key: r.status + ("/missing" if r.missing_since else "") for r in rows}


# ---- the index page ------------------------------------------------------------------------------
def test_the_diputados_index_is_parsed_from_the_page_as_served() -> None:
    entries = {e.key: e for e in parse_diputados(PAGE, DIP.url)}
    assert set(entries) == {"CPEUM", "CCom", "CFPC", "79", "LEDPDE", "LSInt_300519", "LFT",
                            "LGAg", "218"}, "header rows and non-law links are not laws"  # fmt: skip
    lft = entries["LFT"]
    assert lft.title == "LEY Federal del Trabajo"  # ISO-8859-1 decoded, not mojibake
    assert lft.url == "https://www.diputados.gob.mx/LeyesBiblio/pdf/LFT.pdf"
    assert (lft.published_on, lft.last_reform_on) == (date(1970, 4, 1), date(2026, 5, 14))
    assert entries["CPEUM"].title.startswith("CONSTITUCIÓN Política")
    assert entries["CPEUM"].note == "Nueva reforma"
    cfpc = entries["CFPC"]  # abrogated in a term; last "reform" is a court ruling, not a DOF date
    assert cfpc.last_reform_on is None and "Sentencia SCJN" in cfpc.last_reform_text
    assert cfpc.note.startswith("(Abrogado")
    assert entries["LEDPDE"].last_reform_text == "Sin reforma"
    assert entries["218"].published_on is None  # "DOF 01 al 08/01/1912" is not one date


def test_a_page_that_changed_shape_fails_instead_of_emptying_the_catalog(
    ingestor_engine: Engine, tmp_path: Path
) -> None:
    with pytest.raises(IndexLayoutError):
        read_index(DIP, PAGE)  # 9 rows < the 200 the real index always has
    with tenant_transaction(ingestor_engine, PUBLIC_CORPUS_TENANT_ID, allow_public=True) as c:
        record_index(c, SMALL, parse_diputados(PAGE, DIP.url))
    f = FakeFetcher({DIP_ROBOTS: FetchResult(404), DIP.url: ok(b"<html>en mantenimiento</html>")})
    o = watcher(ingestor_engine, f, tmp_path).read_index(DIP)
    assert o.outcome == "failed" and "changed shape" in o.detail
    assert "missing" not in "".join(statuses(ingestor_engine).values()), "nothing marked gone"


# ---- discovery state -----------------------------------------------------------------------------
def test_the_first_read_is_the_catalog_and_later_entries_are_new(
    ingestor_engine: Engine, tmp_path: Path
) -> None:
    entries = parse_diputados(PAGE, DIP.url)
    first, rest = entries[:-1], entries[-1]
    with tenant_transaction(ingestor_engine, PUBLIC_CORPUS_TENANT_ID, allow_public=True) as c:
        assert record_index(c, SMALL, first)["new"] == 0
    assert set(statuses(ingestor_engine).values()) == {"available"}
    with tenant_transaction(ingestor_engine, PUBLIC_CORPUS_TENANT_ID, allow_public=True) as c:
        counts = record_index(c, SMALL, [*first[1:], rest])  # one appears, one leaves
    assert (counts["new"], counts["missing"]) == (1, 1)
    s = statuses(ingestor_engine)
    assert s[rest.key] == "new" and s[first[0].key] == "available/missing"
    with tenant_transaction(ingestor_engine, PUBLIC_CORPUS_TENANT_ID, allow_public=True) as c:
        assert record_index(c, SMALL, entries)["returned"] == 1  # listed again: not missing
    assert statuses(ingestor_engine)[first[0].key] == "available"


def test_reading_an_index_downloads_no_law_and_costs_nothing_when_unchanged(
    ingestor_engine: Engine, tmp_path: Path
) -> None:
    f = FakeFetcher({DIP_ROBOTS: FetchResult(404), DIP.url: [ok(PAGE), ok(PAGE), FetchResult(304)]})
    w = watcher(ingestor_engine, f, tmp_path)
    o = w.read_index(SMALL)
    assert o.outcome == "indexed" and o.counts["entries"] == 9, o.detail
    assert {c[1] for c in f.calls} == {DIP_ROBOTS, DIP.url}, "no law was fetched"
    assert w.read_index(SMALL).detail == "same index page"
    third = w.read_index(SMALL)
    assert third.outcome == "unchanged" and f.calls[-1][2] == '"e1"', "conditional GET"
    checks = q(
        ingestor_engine, "SELECT official_id, outcome FROM source_checks ORDER BY checked_at"
    )
    assert (checks[0].official_id, checks[0].outcome) == ("index:DIP_FED", "indexed")


# ---- the operator's decision ---------------------------------------------------------------------
def _catalog(engine: Engine) -> None:
    with tenant_transaction(engine, PUBLIC_CORPUS_TENANT_ID, allow_public=True) as c:
        record_index(c, SMALL, parse_diputados(PAGE, DIP.url))


def test_an_approval_files_the_law_from_closed_lists_only(ingestor_engine: Engine) -> None:
    _catalog(ingestor_engine)
    with tenant_transaction(ingestor_engine, PUBLIC_CORPUS_TENANT_ID, allow_public=True) as c:
        d = approve(c, "DIP_FED", "LGAg", domains=["environmental"], by="Lic. Ruiz")
        assert d in watched_sources(c)
    assert (d.key, d.code, d.scope, d.doc_type, d.grammar) == ("LGAG", "LGAG", "FED", "ley", "mx")
    assert d.jurisdiction == ("MX", "federal", None, None) and d.approved
    assert d.title == "Ley General de Aguas"
    assert d.source_url == "https://www.diputados.gob.mx/LeyesBiblio/pdf/LGAg.pdf"
    assert statuses(ingestor_engine)["LGAg"] == "approved"
    [row] = q(ingestor_engine, "SELECT decided_by, source_key FROM corpus_index_entries "
              "WHERE entry_key = 'LGAg'")  # fmt: skip
    assert (row.decided_by, row.source_key) == ("Lic. Ruiz", "LGAG")


@pytest.mark.parametrize(
    ("key", "kw", "message"),
    [
        ("LGAg", {"domains": ["aguas"]}, "legal domains"),  # not in the closed catalog
        ("LGAg", {"domains": []}, "legal domains"),
        ("LGAg", {"domains": ["civil"], "by": "  "}, "--by"),
        ("LFT", {"domains": ["labor"]}, "already watched"),  # the static registry has it
        ("79", {"domains": ["fiscal"]}, "--doc-type"),  # "IMPUESTO sobre ..." is not a ley/código
        ("NOPE", {"domains": ["civil"]}, "not in the index"),
    ],
)
def test_an_approval_refuses_what_it_cannot_file(
    ingestor_engine: Engine, key: str, kw: dict[str, object], message: str
) -> None:
    _catalog(ingestor_engine)
    args = {"by": "Lic. Ruiz", **kw}
    with (
        tenant_transaction(ingestor_engine, PUBLIC_CORPUS_TENANT_ID, allow_public=True) as c,
        pytest.raises(DecisionError, match=message),
    ):
        approve(c, "DIP_FED", key, **args)  # type: ignore[arg-type]
    assert q(ingestor_engine, "SELECT count(*) AS n FROM corpus_sources")[0].n == 0


def test_a_decision_is_made_once(ingestor_engine: Engine) -> None:
    _catalog(ingestor_engine)
    with tenant_transaction(ingestor_engine, PUBLIC_CORPUS_TENANT_ID, allow_public=True) as c:
        approve(c, "DIP_FED", "79", domains=["fiscal"], by="R", doc_type="ley")  # explicit type
        dismiss(c, "DIP_FED", "LEDPDE", by="R")
        with pytest.raises(DecisionError, match="already approved"):
            approve(c, "DIP_FED", "79", domains=["fiscal"], by="R", doc_type="ley")
        with pytest.raises(DecisionError, match="already dismissed"):
            approve(c, "DIP_FED", "LEDPDE", domains=["finance"], by="R")
    assert statuses(ingestor_engine)["LEDPDE"] == "dismissed"
    assert [d.key for d in watched_sources_now(ingestor_engine) if d.approved] == ["L79"]


def watched_sources_now(engine: Engine) -> tuple:  # type: ignore[type-arg]
    with tenant_transaction(engine, PUBLIC_CORPUS_TENANT_ID, allow_public=True) as c:
        return watched_sources(c)


def test_the_app_role_cannot_approve_for_every_firm(app_engine: Engine, tenant_a: object) -> None:
    """The public corpus is shared: the API's role reads the catalog but never writes it."""
    with pytest.raises(Exception, match=r"row-level security|permission"):
        with tenant_transaction(app_engine, tenant_a) as c:  # type: ignore[arg-type]
            c.execute(text(
                "INSERT INTO corpus_sources (tenant_id, key, index_id, entry_key, title, "
                "source_url, country, level, scope, code, language, doc_type, legal_domain, "
                "grammar, approved_by) VALUES (public_corpus_id(), 'X', 'DIP_FED', 'X', 'x', "
                "'https://x', 'MX', 'federal', 'FED', 'XX', 'es', 'ley', ARRAY['civil'], 'mx', 'x')"
            ))  # fmt: skip


def test_an_approved_law_is_ingested_as_its_first_version(
    ingestor_engine: Engine, tmp_path: Path
) -> None:
    _catalog(ingestor_engine)
    with tenant_transaction(ingestor_engine, PUBLIC_CORPUS_TENANT_ID, allow_public=True) as c:
        d = approve(c, "DIP_FED", "LGAg", domains=["environmental"], by="R")
    law = v1().replace("LEY FEDERAL DEL TRABAJO", "LEY GENERAL DE AGUAS")
    f = FakeFetcher({DIP_ROBOTS: FetchResult(404), d.source_url or "": ok(pdf(law))})
    [o] = watcher(ingestor_engine, f, tmp_path).run((d,))
    assert o.outcome == "ingested" and "first version" in o.detail, o.detail
    [doc] = q(ingestor_engine, "SELECT title, doc_type, legal_domain, version_label, valid_to "
              "FROM documents WHERE official_id = 'LGAG'")  # fmt: skip
    assert (doc.title, doc.doc_type, doc.legal_domain) == ("Ley General de Aguas", "ley",
                                                           ["environmental"])  # fmt: skip
    assert doc.version_label == "2026.05.14" and doc.valid_to is None
    ids = [r.cite_id for r in q(ingestor_engine, "SELECT cite_id FROM chunks")]
    assert ids and all(i.startswith("MX-FED-LGAG-") for i in ids)
    assert q(ingestor_engine, "SELECT count(*) AS n FROM corpus_changes")[0].n == 0  # no lineage
    # a static source that was never seeded is still not ingested by the watcher
    f2 = FakeFetcher({DIP_ROBOTS: FetchResult(404), BY_KEY["CPF"].source_url or "": ok(pdf(law))})
    [o2] = watcher(ingestor_engine, f2, tmp_path).run((BY_KEY["CPF"],))
    assert o2.outcome == "skipped" and "seed" in o2.detail


def test_names_and_codes_come_from_the_official_file_name() -> None:
    assert [code_for(k) for k in ("LGAg", "LSInt_300519", "79", "LRArt76_fracVI")] == [
        "LGAG", "LSINT300519", "L79", "LRART76FRACVI",
    ]  # fmt: skip
    assert display_title("CÓDIGO de Comercio") == "Código de Comercio"
    assert doc_type_for("CÓDIGO de Comercio") == "codigo"
    assert doc_type_for("Ley General para Prevenir") == "ley"
    assert doc_type_for("ORDENANZA General de la Armada") is None


def test_every_static_source_has_an_official_url_now() -> None:
    """ADR-026 C: the four inbox-only sources got their official route."""
    assert all(d.source_url and d.source_url.startswith("https://") for d in BY_KEY.values())
    assert BY_KEY["CA_LABOR_CODE"].fetch == "leginfo"


# ---- California (leginfo) ------------------------------------------------------------------------
def _row(sec: str, lob: str, eff: str = "2020-01-01", code: str = "LAB", active: str = "Y",
         head: tuple[str, ...] = ("1", "NULL", "NULL", "1", "NULL")) -> str:  # fmt: skip
    c = ["NULL"] * 16
    c[0], c[1], c[2], c[6], c[14], c[15] = "id", f"`{code}`", sec, eff, lob, active
    c[8:13] = head
    return "\t".join(c)


def _archive(sections: dict[str, tuple[str, str]], extra_rows: list[str] = ()) -> bytes:  # type: ignore[assignment]
    """sections: num -> (lob name, text). Plus distractor rows of other codes and padding so the
    archive is larger than one read block."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        rows = [_row(n, lob) for n, (lob, _) in sections.items()]
        rows += [_row("1", "OTHER_1.lob", code="CIV"), *extra_rows]
        z.writestr("LAW_SECTION_TBL.dat", "\n".join(rows) + "\n")
        for _, (lob, body) in sections.items():
            z.writestr(lob, f"<caml:Content><p>{body}</p></caml:Content>")
        z.writestr("OTHER_1.lob", "<p>civil</p>")
        z.writestr("BILL_PADDING.lob", bytes(range(256)) * 400)  # incompressible-ish bulk
    return buf.getvalue()


ZIP_URL = LEGINFO + "pubinfo_2025.zip"
LISTING = (b'<a href="pubinfo_2023.zip">2023</a> <a href="pubinfo_2025.zip">2025</a> '
           b'<a href="pubinfo_daily_Mon.zip">Mon</a>')  # fmt: skip
CA_ROBOTS = LEGINFO + "robots.txt"
SECTIONS = {
    "200": ("LAW_SECTION_TBL_1.lob", f"As used in this part: wages includes all amounts {FILL}"),
    "201": ("LAW_SECTION_TBL_2.lob", f"If an employer discharges an employee, wages {FILL}"),
    "1182.12": ("LAW_SECTION_TBL_3.lob", f"The minimum wage for all industries shall be {FILL}"),
}


def test_the_newest_yearly_archive_is_chosen() -> None:
    assert latest_archive(LISTING, LEGINFO) == ZIP_URL
    assert latest_archive(b"<html>nothing</html>", LEGINFO) is None


def test_the_code_in_force_today_is_extracted_by_ranges_never_downloaded_whole() -> None:
    future = _row("202", "LAW_SECTION_TBL_9.lob", eff="2099-01-01")
    old = _row("201", "LAW_SECTION_TBL_0.lob", eff="2010-01-01")  # superseded by the 2020 row
    data = _archive(SECTIONS, [future, old])
    f = FakeFetcher({}, files={ZIP_URL: data})
    rf = RangeFile(f, ZIP_URL, len(data), max_requests=100)
    with zipfile.ZipFile(rf) as z:  # type: ignore[arg-type]
        ex = extract_code(z, "LAB", date(2026, 11, 17))
    assert ex.sections == 3 and ex.lob_reads == 3
    assert [ln.split(" ", 2)[1] for ln in ex.text.split("\n\n") if ln.startswith("§")] == [
        "200", "201", "1182.12",
    ], "natural order"  # fmt: skip
    assert "wages includes" in ex.text and "civil" not in ex.text  # only the LAB code
    assert any("202: only future" in s for s in ex.skipped)
    assert any("201: superseded" in s for s in ex.skipped)
    # central directory + section table + 3 section texts; the padding member is never read
    assert rf.requests <= 8


def test_california_is_watched_by_etag_and_re_reads_only_changed_sections(
    ingestor_engine: Engine, tmp_path: Path
) -> None:
    ca = dataclasses.replace(BY_KEY["CA_LABOR_CODE"], approved=True)  # no seed in the test DB
    data = _archive(SECTIONS)
    f = FakeFetcher({CA_ROBOTS: FetchResult(404), LEGINFO: ok(LISTING)},
                    files={ZIP_URL: data}, etags={ZIP_URL: '"w1"'})  # fmt: skip
    w = watcher(ingestor_engine, f, tmp_path)
    [o] = w.run((ca,))
    assert o.outcome == "ingested", o.detail
    ids = {r.cite_id.rsplit("-v", 1)[0] for r in q(ingestor_engine, "SELECT cite_id FROM chunks")}
    assert {"US-CA-LAB-SEC200", "US-CA-LAB-SEC201", "US-CA-LAB-SEC1182P12"} <= ids
    first_ranges = f.range_calls
    assert first_ranges > 0

    [o] = w.run((ca,))  # same ETag: a HEAD, no range request
    assert (o.outcome, o.detail) == ("unchanged", "archive not modified (ETag)")
    assert f.range_calls == first_ranges

    changed = dict(SECTIONS)
    changed["201"] = ("LAW_SECTION_TBL_7.lob", f"If an employer discharges an employee, all {FILL}")
    f.files[ZIP_URL], f.etags[ZIP_URL] = _archive(changed), '"w2"'
    before = f.range_calls
    [o] = w.run((ca,))
    assert o.outcome == "ingested", o.detail
    assert "1 read from the archive" in w.out.getvalue()  # type: ignore[attr-defined]
    assert "3 read from the archive" in w.out.getvalue().splitlines()[0]  # type: ignore[attr-defined]
    assert f.range_calls > before  # the changed archive was read again (by ranges)
    changes = {r.id: r.change for r in q(ingestor_engine,
               "SELECT coalesce(new_cite_id, old_cite_id) AS id, change FROM cite_lineage")}  # fmt: skip
    assert {k.rsplit("-v", 1)[0]: v for k, v in changes.items()}["US-CA-LAB-SEC201"] == "amended"


def test_a_server_that_ignores_byte_ranges_fails_the_source_instead_of_downloading_it(
    ingestor_engine: Engine, tmp_path: Path
) -> None:
    ca = dataclasses.replace(BY_KEY["CA_LABOR_CODE"], approved=True)

    class NoRanges(FakeFetcher):
        def get(self, url: str, **kw: object) -> FetchResult:  # type: ignore[override]
            if kw.get("byte_range"):
                return FetchResult(200, error="the server ignored the byte range")
            return super().get(url, **kw)  # type: ignore[arg-type]

    f = NoRanges({CA_ROBOTS: FetchResult(404), LEGINFO: ok(LISTING)},
                 files={ZIP_URL: _archive(SECTIONS)}, etags={ZIP_URL: '"w1"'})  # fmt: skip
    [o] = watcher(ingestor_engine, f, tmp_path).run((ca,))
    assert o.outcome == "failed" and "RangeError" in o.detail
    assert q(ingestor_engine, "SELECT count(*) AS n FROM documents")[0].n == 0
