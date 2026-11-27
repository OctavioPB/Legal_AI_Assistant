"""ADR-006: a generation is built aside, invisible until it is switched in, and switched back.

Against real Postgres with the production roles: RLS shows every role only the active generation,
the rebuild writes the next one as the superuser, `activate`/`rollback` are one switch each, and the
rebuild stops instead of writing a corpus that differs from what was loaded.
"""

from __future__ import annotations

import io
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import Engine, text

from app.db.session import PUBLIC_CORPUS_TENANT_ID, tenant_transaction
from app.ingestion import rebuild
from app.ingestion.model import ChunkThresholds, DocMeta
from app.ingestion.store import ingest_document
from app.ingestion.tokens import ApproxCounter
from tests.fakes import fake_embedding
from tests.helpers import jurisdiction_id
from tests.ingestion.fixtures import FIXTURES


class Emb:
    model, revision = "fake-embed", "r1"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [fake_embedding(t) for t in texts]


COUNTER = ApproxCounter()
TH = ChunkThresholds(counter=COUNTER.name)
LAW = FIXTURES["transitorios"]


@pytest.fixture(autouse=True)
def reset_generations(admin_engine: Engine):  # type: ignore[no-untyped-def]
    yield
    with admin_engine.begin() as c:  # chunks/doc_nodes are truncated by clean_data afterwards
        c.execute(text("DELETE FROM chunks WHERE generation > 1"))
        c.execute(text("DELETE FROM doc_nodes WHERE generation > 1"))
        c.execute(text("UPDATE index_generations SET status = 'retired' WHERE id > 1"))
        c.execute(text("DELETE FROM index_generations WHERE id > 1"))
        c.execute(text("UPDATE index_generations SET status = 'active' WHERE id = 1"))


@pytest.fixture
def fed(admin_engine: Engine) -> int:
    with admin_engine.connect() as c:
        return jurisdiction_id(c, "MX", "federal")


@pytest.fixture
def law_source(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """The source file of the public law: what the rebuild re-reads (editable by a test)."""
    src = {"raw": LAW}
    d = SimpleNamespace(grammar="mx", country="MX", scope="FED", code="LFT")
    monkeypatch.setattr(rebuild, "_public_source", lambda row: (d, src["raw"]))
    return src


def _seed_law(ingestor_engine: Engine, fed: int) -> None:
    ingest_document(
        ingestor_engine, PUBLIC_CORPUS_TENANT_ID,
        meta=DocMeta(country="MX", scope="FED", code="LFT", title="Ley X", version_label="1"),
        doc_type="ley", jurisdiction_id=fed, source_uri="file://lft.txt", raw_text=LAW,
        embedder=Emb(), counter=COUNTER, official_id="LFT",
    )  # fmt: skip


def _seed_firm_file(app_engine: Engine, tenant: uuid.UUID, fed: int) -> uuid.UUID:
    with tenant_transaction(app_engine, tenant) as c:
        user = c.execute(
            text("INSERT INTO users (tenant_id, email, display_name) VALUES (:t, 'a@f.mx', 'A') "
                 "RETURNING id"), {"t": tenant},
        ).scalar_one()  # fmt: skip
    meta = DocMeta(country="MX", scope="CF", code="TOKEN1", title="Contrato", version_label="1",
                   client_file="TOKEN1", citation_style="plain")  # fmt: skip
    # 11 clauses: ltree sorts "s10" before "s2", so section order must come from `ordinal`
    raw = "\n\n".join(
        f"CLAUSULA {n}.- TITULO {n}\n\nEl arrendatario cumple la obligacion numero {n} del contrato."
        for n in [
            "PRIMERA",
            "SEGUNDA",
            "TERCERA",
            "CUARTA",
            "QUINTA",
            "SEXTA",
            "SEPTIMA",
            "OCTAVA",
            "NOVENA",
            "DECIMA",
            "UNDECIMA",
        ]
    )
    stats = ingest_document(
        app_engine, tenant, meta=meta, doc_type="client_file", jurisdiction_id=fed,
        source_uri="client-file://x", raw_text=raw, embedder=Emb(), counter=COUNTER,
        grammar="generic",
    )  # fmt: skip
    with tenant_transaction(app_engine, tenant, user_id=user) as c:
        c.execute(
            text(
                "INSERT INTO client_files (tenant_id, uploaded_by, title, kind, language, "
                "jurisdiction_id, file_token, source_format, status, grammar, document_id) "
                "VALUES (:t, :u, 'Contrato', 'contrato', 'es', :j, 'TOKEN1', 'text', 'ready', "
                "'generic', :d)"
            ),
            {"t": tenant, "u": user, "j": fed, "d": stats.document_id},
        )
    assert stats.document_id is not None
    return stats.document_id


def _visible(engine: Engine, tenant: uuid.UUID) -> list[tuple[str, int]]:
    with tenant_transaction(engine, tenant, allow_public=True) as c:
        return [tuple(r) for r in c.execute(text(  # type: ignore[misc]
            "SELECT cite_id, generation FROM chunks ORDER BY cite_id")).all()]  # fmt: skip


def _build(admin_engine: Engine) -> rebuild.BuildReport:
    return rebuild.build(admin_engine, Emb(), COUNTER, TH, note="test", out=io.StringIO())


def test_a_built_generation_is_invisible_until_activated_and_rollback_restores(
    admin_engine: Engine, ingestor_engine: Engine, app_engine: Engine, fed: int,
    tenant_a: uuid.UUID, law_source: dict[str, str],
) -> None:  # fmt: skip
    _seed_law(ingestor_engine, fed)
    before = _visible(app_engine, tenant_a)
    assert before and {g for _, g in before} == {1}

    rep = _build(admin_engine)
    assert rep.generation == 2 and rep.docs[0].removed == rep.docs[0].added == []
    assert _visible(app_engine, tenant_a) == before  # built aside: nobody sees it

    with admin_engine.connect() as c:
        n2 = c.execute(text("SELECT count(*) FROM chunks WHERE generation = 2")).scalar_one()
    assert n2 == len(before)

    rebuild.activate(admin_engine, 2)
    after = _visible(app_engine, tenant_a)
    assert [cid for cid, _ in after] == [cid for cid, _ in before]  # same ids (invariant 3)
    assert {g for _, g in after} == {2}

    assert rebuild.rollback(admin_engine) == 1
    assert _visible(app_engine, tenant_a) == before


def test_every_role_under_rls_sees_and_writes_only_the_active_generation(
    admin_engine: Engine, ingestor_engine: Engine, fed: int, law_source: dict[str, str],
) -> None:  # fmt: skip
    _seed_law(ingestor_engine, fed)
    _build(admin_engine)
    with tenant_transaction(ingestor_engine, PUBLIC_CORPUS_TENANT_ID, allow_public=True) as c:
        gens = c.execute(text("SELECT DISTINCT generation FROM doc_nodes")).scalars().all()
        assert gens == [1]
        node = c.execute(text("SELECT id FROM doc_nodes LIMIT 1")).scalar_one()
    with (
        pytest.raises(Exception, match=r"row-level security|violates"),
        tenant_transaction(ingestor_engine, PUBLIC_CORPUS_TENANT_ID, allow_public=True) as c,
    ):
        c.execute(text("UPDATE doc_nodes SET generation = 2 WHERE id = :n"), {"n": node})


def test_a_retired_document_stays_retired_in_the_new_generation(
    admin_engine: Engine, ingestor_engine: Engine, fed: int, law_source: dict[str, str],
) -> None:  # fmt: skip
    _seed_law(ingestor_engine, fed)
    with admin_engine.begin() as c:
        c.execute(text("UPDATE documents SET valid_to = current_date"))
        c.execute(text("UPDATE chunks SET is_current = false"))
    _build(admin_engine)
    with admin_engine.connect() as c:
        cur = c.execute(text("SELECT bool_or(is_current) FROM chunks WHERE generation = 2"))
        assert cur.scalar_one() is False


def test_a_firm_document_is_rebuilt_from_its_stored_text_with_the_same_units(
    admin_engine: Engine, app_engine: Engine, ingestor_engine: Engine, fed: int,
    tenant_a: uuid.UUID, law_source: dict[str, str],
) -> None:  # fmt: skip
    _seed_law(ingestor_engine, fed)
    doc = _seed_firm_file(app_engine, tenant_a, fed)
    rep = _build(admin_engine)
    firm = next(d for d in rep.docs if d.document_id == doc)
    assert firm.chunks >= 2 and not (firm.removed or firm.added or firm.text_changed)
    rebuild.activate(admin_engine, 2)
    with tenant_transaction(app_engine, tenant_a) as c:
        rows = c.execute(
            text("SELECT cite_id, generation FROM chunks WHERE document_id = :d"), {"d": doc}
        ).all()
    assert rows and {g for _, g in rows} == {2} and all(r[0].startswith("CF-") for r in rows)


def test_a_firm_document_whose_units_would_change_stops_the_build(
    admin_engine: Engine, app_engine: Engine, fed: int, tenant_a: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    _seed_firm_file(app_engine, tenant_a, fed)
    real = rebuild._firm_text
    monkeypatch.setattr(
        rebuild, "_firm_text", lambda c, d, g: real(c, d, g) + "\n\nTERCERA.- X\n\nNueva."
    )
    with pytest.raises(rebuild.RebuildError, match="firm document"):
        _build(admin_engine)
    with admin_engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM chunks WHERE generation = 2")).scalar_one() == 0


def test_a_source_that_no_longer_hashes_to_the_loaded_text_stops_the_build(
    admin_engine: Engine, ingestor_engine: Engine, fed: int, law_source: dict[str, str],
) -> None:  # fmt: skip
    _seed_law(ingestor_engine, fed)
    law_source["raw"] = LAW.replace("Vigor", "Vigencia")
    with pytest.raises(rebuild.RebuildError, match="checksum"):
        _build(admin_engine)


def test_activation_refuses_a_generation_missing_a_live_document(
    admin_engine: Engine, ingestor_engine: Engine, app_engine: Engine, fed: int,
    tenant_a: uuid.UUID, law_source: dict[str, str],
) -> None:  # fmt: skip
    _seed_law(ingestor_engine, fed)
    _build(admin_engine)
    _seed_firm_file(app_engine, tenant_a, fed)  # uploaded meanwhile: lands in the live generation
    with pytest.raises(rebuild.RebuildError, match="fill 2"):
        rebuild.activate(admin_engine, 2)
    rep = rebuild.fill(admin_engine, 2, Emb(), COUNTER, TH, out=io.StringIO())
    assert len(rep.docs) == 1
    rebuild.activate(admin_engine, 2)


def test_only_one_build_at_a_time_and_the_active_generation_is_never_pruned(
    admin_engine: Engine, ingestor_engine: Engine, fed: int, law_source: dict[str, str],
) -> None:  # fmt: skip
    _seed_law(ingestor_engine, fed)
    _build(admin_engine)
    with pytest.raises(rebuild.RebuildError, match="already being built"):
        _build(admin_engine)
    rebuild.activate(admin_engine, 2)
    with pytest.raises(rebuild.RebuildError, match="never pruned"):
        rebuild.prune(admin_engine, 2)
    assert rebuild.prune(admin_engine, 1) > 0
    with admin_engine.connect() as c:
        assert (
            c.execute(text("SELECT count(*) FROM doc_nodes WHERE generation = 1")).scalar_one() == 0
        )
