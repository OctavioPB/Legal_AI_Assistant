"""ADR-050: overlapping validity between versions of one law is corrected, never deleted."""

from __future__ import annotations

from datetime import date

from sqlalchemy import Engine, text

from app.db.session import PUBLIC_CORPUS_TENANT_ID
from app.ingestion.validity import apply, overlaps
from tests.helpers import jurisdiction_id


def _doc(c, fed: int, label: str, vf: date | None, vt: date | None, at: str) -> None:  # type: ignore[no-untyped-def]
    c.execute(
        text(
            "INSERT INTO documents (tenant_id, jurisdiction_id, doc_type, title, language, "
            "version_label, source_uri, checksum_sha256, official_id, valid_from, valid_to, "
            "ingested_at) VALUES (:t, :j, 'ley', 'CPF', 'es', :l, 'file://x', encode(sha256(convert_to(:l, 'UTF8')), 'hex'), 'CPF', "
            ":vf, :vt, CAST(:at AS timestamptz))"
        ),
        {"t": PUBLIC_CORPUS_TENANT_ID, "j": fed, "l": label, "vf": vf, "vt": vt, "at": at},
    )


def test_the_older_version_ends_where_the_newer_begins_and_nothing_is_deleted(
    admin_engine: Engine,
) -> None:
    with admin_engine.begin() as c:
        fed = jurisdiction_id(c, "MX", "federal")
        _doc(c, fed, "2026.11", None, date(2026, 11, 25), "2026-11-07")
        _doc(c, fed, "2026.11.13", date(2026, 11, 13), None, "2026-11-25")
        [fix] = overlaps(c)
        assert (fix.version_label, fix.old_valid_to, fix.new_valid_to) == (
            "2026.11",
            date(2026, 11, 25),
            date(2026, 11, 13),
        )
        assert apply(c, [fix]) == 1
        assert overlaps(c) == []  # idempotent
        n = c.execute(text("SELECT count(*) FROM documents WHERE official_id = 'CPF'")).scalar()
    assert n == 2


def test_versions_that_do_not_overlap_are_left_alone(admin_engine: Engine) -> None:
    with admin_engine.begin() as c:
        fed = jurisdiction_id(c, "MX", "federal")
        _doc(c, fed, "a", date(2020, 1, 1), date(2021, 1, 1), "2026-11-01")
        _doc(c, fed, "b", date(2021, 1, 1), None, "2026-11-02")
        assert overlaps(c) == []
