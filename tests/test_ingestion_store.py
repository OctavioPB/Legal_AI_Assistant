"""Idempotent ingestion, reform versioning and version guards against real Postgres/RLS."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import Engine, text

from app.db.session import PUBLIC_CORPUS_TENANT_ID, tenant_transaction
from app.ingestion.model import DocMeta
from app.ingestion.store import VersionMismatchError, ingest_document
from app.ingestion.tokens import ApproxCounter
from tests.fakes import fake_embedding
from tests.helpers import jurisdiction_id
from tests.ingestion.fixtures import FIXTURES


class CountingEmbedder:
    def __init__(self, model: str = "fake-embed", revision: str = "r1") -> None:
        self.model, self.revision, self.calls = model, revision, 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        return [fake_embedding(t) for t in texts]


def _meta(version: str = "2025.06") -> DocMeta:
    return DocMeta(country="MX", scope="FED", code="LFT", title="Ley X", version_label=version)


def _ingest(engine: Engine, jid: int, emb: CountingEmbedder, raw: str, version: str = "2025.06"):  # type: ignore[no-untyped-def]
    return ingest_document(
        engine,
        PUBLIC_CORPUS_TENANT_ID,
        meta=_meta(version),
        doc_type="ley",
        jurisdiction_id=jid,
        source_uri="file://lft.txt",
        raw_text=raw,
        embedder=emb,
        counter=ApproxCounter(),
        official_id="LFT",
    )


@pytest.fixture
def fed_id(admin_engine: Engine) -> int:
    with admin_engine.connect() as c:
        return jurisdiction_id(c, "MX", "federal")


def test_ingest_then_unchanged_rerun_makes_no_provider_call(
    ingestor_engine: Engine, app_engine: Engine, fed_id: int
) -> None:
    emb = CountingEmbedder()
    first = _ingest(ingestor_engine, fed_id, emb, FIXTURES["transitorios"])
    assert first.status == "added" and first.chunks_written >= 3
    calls = emb.calls
    again = _ingest(ingestor_engine, fed_id, emb, FIXTURES["transitorios"])
    assert again.status == "unchanged" and emb.calls == calls
    with tenant_transaction(ingestor_engine, PUBLIC_CORPUS_TENANT_ID, allow_public=True) as c:
        n = c.execute(text("SELECT count(*) FROM chunks")).scalar_one()
        docs = c.execute(text("SELECT count(*) FROM documents")).scalar_one()
    assert n == first.chunks_written and docs == 1


def test_reform_creates_new_version_and_retires_previous(
    ingestor_engine: Engine, fed_id: int
) -> None:
    emb = CountingEmbedder()
    _ingest(ingestor_engine, fed_id, emb, FIXTURES["derogated"], "2025.06")
    reformed = FIXTURES["derogated"].replace("Artículo 9.-", "Artículo 9.- (Reformado) ")
    stats = _ingest(ingestor_engine, fed_id, emb, reformed, "2026.01")
    assert stats.previous_versions_retired == 1  # art. 8 was already derogated
    with tenant_transaction(ingestor_engine, PUBLIC_CORPUS_TENANT_ID, allow_public=True) as c:
        rows = c.execute(
            text("SELECT cite_id, is_current FROM chunks WHERE cite_id LIKE '%ART9-%' ORDER BY 1")
        ).all()
        assert rows == [("MX-FED-LFT-ART9-v2025.06", False), ("MX-FED-LFT-ART9-v2026.01", True)]
        assert (
            c.execute(text("SELECT count(*) FROM documents")).scalar_one() == 2
        )  # nothing deleted


def test_mixing_embedding_generations_is_refused(ingestor_engine: Engine, fed_id: int) -> None:
    _ingest(ingestor_engine, fed_id, CountingEmbedder(), FIXTURES["derogated"])
    with pytest.raises(VersionMismatchError):
        _ingest(
            ingestor_engine,
            fed_id,
            CountingEmbedder(revision="r2"),
            FIXTURES["article_without_fracciones"],
        )
    with tenant_transaction(ingestor_engine, PUBLIC_CORPUS_TENANT_ID, allow_public=True) as c:
        assert c.execute(text("SELECT count(*) FROM documents")).scalar_one() == 1


def test_threshold_override_without_version_bump_is_refused(
    ingestor_engine: Engine, fed_id: int
) -> None:
    from app.ingestion.model import ChunkThresholds

    _ingest(ingestor_engine, fed_id, CountingEmbedder(), FIXTURES["derogated"])
    with pytest.raises(VersionMismatchError):
        ingest_document(
            ingestor_engine,
            PUBLIC_CORPUS_TENANT_ID,
            meta=_meta(),
            doc_type="ley",
            jurisdiction_id=fed_id,
            source_uri="file://other.txt",
            raw_text=FIXTURES["article_without_fracciones"],
            embedder=CountingEmbedder(),
            counter=ApproxCounter(),
            thresholds=ChunkThresholds(min_tokens=50, counter=ApproxCounter.name),
        )


def test_failed_embed_leaves_nothing_behind(ingestor_engine: Engine, fed_id: int) -> None:
    class Boom(CountingEmbedder):
        def embed(self, texts: list[str]) -> list[list[float]]:
            raise RuntimeError("provider down")

    with pytest.raises(RuntimeError):
        _ingest(ingestor_engine, fed_id, Boom(), FIXTURES["derogated"])
    with tenant_transaction(ingestor_engine, PUBLIC_CORPUS_TENANT_ID, allow_public=True) as c:
        assert c.execute(text("SELECT count(*) FROM documents")).scalar_one() == 0


def test_client_file_ingest_uses_cf_namespace_under_tenant(
    app_engine: Engine, tenant_a: uuid.UUID, admin_engine: Engine
) -> None:
    with admin_engine.connect() as c:
        jid = jurisdiction_id(c, "MX", "federal")
    meta = DocMeta(
        country="MX",
        scope="FED",
        code="X",
        title="Expediente 1",
        version_label="1",
        client_file="EXP0001",
    )
    stats = ingest_document(
        app_engine,
        tenant_a,
        meta=meta,
        doc_type="client_file",
        jurisdiction_id=jid,
        source_uri="s3://b/a/exp1.pdf",
        raw_text=FIXTURES["derogated"],
        embedder=CountingEmbedder(),
        counter=ApproxCounter(),
    )
    assert stats.chunks_written == 2
    with tenant_transaction(app_engine, tenant_a) as c:
        ids = (
            c.execute(
                text("SELECT cite_id FROM chunks WHERE tenant_id = :t ORDER BY 1"), {"t": tenant_a}
            )
            .scalars()
            .all()
        )
    assert ids == ["CF-EXP0001-ART8", "CF-EXP0001-ART9"]


@pytest.mark.parametrize(
    ("name", "vector", "message"),
    [
        ("dimension", [0.1] * 768, "768 dimensions"),
        ("nan", [float("nan")] * 1024, "NaN or infinite"),
        ("inf", [float("inf")] * 1024, "NaN or infinite"),
        ("zero", [0.0] * 1024, "zero vector"),
    ],
)
def test_bad_embeddings_are_rejected_before_anything_is_written(
    ingestor_engine: Engine, fed_id: int, name: str, vector: list[float], message: str
) -> None:
    from app.ingestion.store import InvalidEmbeddingError

    class Bad(CountingEmbedder):
        def embed(self, texts: list[str]) -> list[list[float]]:
            return [list(vector) for _ in texts]

    with pytest.raises(InvalidEmbeddingError, match=message):
        _ingest(ingestor_engine, fed_id, Bad(), FIXTURES["derogated"])
    with tenant_transaction(ingestor_engine, PUBLIC_CORPUS_TENANT_ID, allow_public=True) as c:
        assert c.execute(text("SELECT count(*) FROM documents")).scalar_one() == 0, name


def test_the_us_grammar_goes_through_the_same_store(ingestor_engine: Engine, fed_id: int) -> None:
    us = (
        "Division - Title - Part 3 Chapter 5 Article -\n\n"
        "§ 1102.5. (a)An employer shall not retaliate against an employee for disclosing information "
        "to a government agency. (b)An employer shall not adopt any policy preventing such disclosure."
    )
    meta = DocMeta(
        country="US",
        scope="CA",
        code="LAB",
        title="Cal. Lab. Code",
        version_label="2025",
        language="en",
    )
    stats = ingest_document(
        ingestor_engine, PUBLIC_CORPUS_TENANT_ID, meta=meta, doc_type="statute", jurisdiction_id=fed_id,
        source_uri="file://us.txt", raw_text=us, embedder=CountingEmbedder(), counter=ApproxCounter(),
        grammar="us",
    )  # fmt: skip
    assert stats.status == "added" and stats.chunks_written >= 1
    with tenant_transaction(ingestor_engine, PUBLIC_CORPUS_TENANT_ID, allow_public=True) as c:
        ids = c.execute(text("SELECT cite_id FROM chunks ORDER BY cite_id")).scalars().all()
    assert all(i.startswith("US-CA-LAB-SEC1102P5") for i in ids), ids


def test_a_reform_retires_the_old_version_when_the_new_one_enters_into_force(
    ingestor_engine: Engine, fed_id: int
) -> None:
    """ADR-050: seen on the CPF, the old version kept `valid_to` = the day the watcher detected the
    reform, after the new version's `valid_from`: two versions "in force" for twelve days."""
    from datetime import date

    emb = CountingEmbedder()
    _ingest(ingestor_engine, fed_id, emb, FIXTURES["derogated"], "2025.06")
    reformed = FIXTURES["derogated"].replace("Artículo 9.-", "Artículo 9.- (Reformado) ")
    ingest_document(
        ingestor_engine, PUBLIC_CORPUS_TENANT_ID, meta=_meta("2026.01"), doc_type="ley",
        jurisdiction_id=fed_id, source_uri="file://lft.txt", raw_text=reformed, embedder=emb,
        counter=ApproxCounter(), official_id="LFT", valid_from=date(2026, 1, 15),
    )  # fmt: skip
    with tenant_transaction(ingestor_engine, PUBLIC_CORPUS_TENANT_ID, allow_public=True) as c:
        rows = c.execute(
            text("SELECT version_label, valid_from, valid_to FROM documents ORDER BY version_label")
        ).all()
    assert rows[0].version_label == "2025.06" and rows[0].valid_to == date(2026, 1, 15)
    assert rows[1].valid_from == date(2026, 1, 15) and rows[1].valid_to is None
