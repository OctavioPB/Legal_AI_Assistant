"""ADR-050: retrieval of the law as of a date, inside the one predicate builder (jurisdictions.md)."""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import Engine, text

from app.db.session import PUBLIC_CORPUS_TENANT_ID, tenant_transaction
from app.ingestion.model import DocMeta
from app.ingestion.store import ingest_document
from app.ingestion.tokens import ApproxCounter
from app.rag.context import SessionContext, load_session_context
from app.rag.jurisdiction import build_jurisdiction_predicate
from app.rag.pipeline import retrieve
from app.settings import Settings
from tests.fakes import FakeReranker
from tests.helpers import jurisdiction_id
from tests.test_retrieval import EMB

F = " ".join(["considerando"] * 70)


def _ctx(**kw: object) -> SessionContext:
    return SessionContext(tenant_id=uuid.uuid4(), jurisdiction_id=1, country="MX", level="federal",
                          **kw)  # type: ignore[arg-type]  # fmt: skip


def test_without_a_date_the_predicate_is_exactly_the_current_text_one() -> None:
    p = build_jurisdiction_predicate(_ctx())
    assert "chunks.is_current" in p.sql and "j_asof" not in p.params and "documents" not in p.sql


def test_with_a_date_the_version_in_force_replaces_current_and_values_stay_bound() -> None:
    p = build_jurisdiction_predicate(_ctx(as_of=date(2025, 6, 1)))
    assert "chunks.is_current" not in p.sql and "2025-06-01" not in p.sql  # bound, never embedded
    assert p.params["j_asof"] == "2025-06-01"
    assert "d.valid_from IS NOT NULL" in p.sql and "client_file" in p.sql


def _law(engine: Engine, fed: int, version: str, vf: date | None, body: str) -> None:
    meta = DocMeta(country="MX", scope="FED", code="LFT", title="Ley Federal del Trabajo",
                   version_label=version, language="es")  # fmt: skip
    ingest_document(engine, PUBLIC_CORPUS_TENANT_ID, meta=meta, doc_type="ley", jurisdiction_id=fed,
                    source_uri=f"file://lft-{version}.txt", raw_text=body, embedder=EMB,
                    counter=ApproxCounter(), official_id="LFT", valid_from=vf)  # fmt: skip


@pytest.fixture
def versions(ingestor_engine: Engine, admin_engine: Engine) -> int:
    with admin_engine.connect() as c:
        fed = jurisdiction_id(c, "MX", "federal")
    _law(
        ingestor_engine,
        fed,
        "2020",
        date(2020, 1, 1),
        f"Artículo 15-C.- Texto de subcontratación. {F}",
    )
    _law(ingestor_engine, fed, "2021", date(2021, 4, 24), f"Artículo 15-C.- (Se deroga) {F}")
    return fed


def _search(app_engine: Engine, fed: int, as_of: date | None):  # type: ignore[no-untyped-def]
    tenant = uuid.uuid4()
    with tenant_transaction(app_engine, tenant) as c:
        ctx = load_session_context(c, tenant, fed, language="es", as_of=as_of)
        return retrieve(c, ctx, "artículo 15-C subcontratación", embedder=EMB,
                        reranker=FakeReranker(), counter=ApproxCounter(),
                        settings=Settings(refusal_tau=0.0))  # fmt: skip


def test_a_dated_session_sees_the_version_in_force_on_that_day(
    app_engine: Engine, versions: int
) -> None:
    before = _search(app_engine, versions, date(2020, 6, 1))
    assert {h.cite_id.rsplit("-v", 1)[1] for h in before.hits} == {"2020"}
    after = _search(app_engine, versions, date(2022, 1, 1))
    assert {h.cite_id.rsplit("-v", 1)[1] for h in after.hits} == {"2021"}
    today = _search(app_engine, versions, None)  # no date: today's current text, as before
    assert {h.cite_id.rsplit("-v", 1)[1] for h in today.hits} == {"2021"}


def test_a_day_no_dated_version_covers_is_refused_with_its_own_reason(
    app_engine: Engine, versions: int
) -> None:
    r = _search(app_engine, versions, date(2019, 1, 1))
    assert r.refusal is not None and r.refusal.reason_key == "retrieval.no_version_as_of"
    assert "retrieval.no_version_as_of" in r.refusal.block


def test_a_version_with_unknown_dates_is_never_assumed_to_cover_a_date(
    app_engine: Engine, ingestor_engine: Engine, admin_engine: Engine
) -> None:
    with admin_engine.connect() as c:
        fed = jurisdiction_id(c, "MX", "federal")
    _law(ingestor_engine, fed, "2024.01", None, f"Artículo 15-C.- Texto sin fecha conocida. {F}")
    with admin_engine.connect() as c:
        assert c.execute(text("SELECT valid_from FROM documents")).scalar() is None
    r = _search(app_engine, fed, date(2024, 1, 1))
    assert r.refusal is not None and r.refusal.reason_key == "retrieval.no_version_as_of"
