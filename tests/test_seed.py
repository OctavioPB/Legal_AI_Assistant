"""The corpus seeder end to end against the test database, with fake embeddings (no Ollama)."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from sqlalchemy import Engine, text

from app.db.session import PUBLIC_CORPUS_TENANT_ID, tenant_transaction
from app.ingestion.corpus import BY_KEY, CORPUS
from app.ingestion.seed import seed
from app.ingestion.tokens import ApproxCounter
from app.settings import Settings
from tests.fakes import FakeEmbedder

DATA = Path(__file__).resolve().parents[1] / "data" / "corpus"


def test_the_registry_covers_every_downloaded_file_and_only_catalog_jurisdictions() -> None:
    assert len({d.key for d in CORPUS}) == len(CORPUS) == 8
    for d in CORPUS:
        c, level, state, muni = d.jurisdiction
        assert c == d.country and level in ("federal", "state", "municipal")
        assert (state is None) == (level == "federal") and (muni is not None) == (
            level == "municipal"
        )
        assert d.doc_type in {"codigo", "ley", "reglamento", "bando", "statute"}
        assert d.grammar == ("us" if d.country == "US" else "mx")


@pytest.mark.skipif(
    not (DATA / "text" / "REG_JUSTICIA_CIVICA_MTY.txt").exists(), reason="corpus not downloaded"
)
def test_seeding_a_real_document_is_idempotent_and_correctly_scoped(
    ingestor_engine: Engine, admin_engine: Engine, settings: Settings
) -> None:
    doc = BY_KEY["REG_JUSTICIA_CIVICA_MTY"]
    out = io.StringIO()
    kw = {
        "database": "legalai_test",
        "counter": ApproxCounter(),
        "embedder": FakeEmbedder(),
        "out": out,
    }
    s = settings.model_copy(update={"chunk_max_tokens": 400, "embedder_window": 512})
    assert seed(s, (doc,), **kw) == 0  # type: ignore[arg-type]
    assert "added" in out.getvalue()
    embedder = FakeEmbedder()
    kw["embedder"] = embedder
    out2 = io.StringIO()
    kw["out"] = out2
    assert seed(s, (doc,), **kw) == 0  # type: ignore[arg-type]
    assert "unchanged" in out2.getvalue() and embedder.calls == 0  # the second run costs nothing
    with tenant_transaction(ingestor_engine, PUBLIC_CORPUS_TENANT_ID, allow_public=True) as c:
        row = c.execute(
            text(
                "SELECT count(*) AS n, min(country) AS country, min(level) AS level, "
                "min(state_code) AS st, min(municipality) AS muni, min(doc_type) AS dt, "
                "bool_and(tenant_id = public_corpus_id()) AS public FROM chunks"
            )
        ).one()
    assert row.n > 100 and (row.country, row.level, row.st, row.muni) == (
        "MX",
        "municipal",
        "NL",
        "Monterrey",
    )
    assert row.dt == "reglamento" and row.public is True


def test_a_missing_source_file_is_reported_and_does_not_abort_the_run(
    settings: Settings, admin_engine: Engine
) -> None:
    from dataclasses import replace

    ghost = replace(BY_KEY["LFT"], key="DOES_NOT_EXIST")
    out = io.StringIO()
    failed = seed(
        settings,
        (ghost,),
        database="legalai_test",
        counter=ApproxCounter(),
        embedder=FakeEmbedder(),
        out=out,
    )  # type: ignore[arg-type]
    assert failed == 1 and "SKIPPED" in out.getvalue()
