"""WS1.T02 — corpus schema: indexes, derived columns, immutability, accent-folding search, ltree."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.db.models import Chunk, DocNode
from app.db.session import PUBLIC_CORPUS_TENANT_ID, tenant_session, tenant_transaction
from tests import helpers
from tests.fakes import fake_embedding, vector_literal
from tests.helpers import Article


def test_required_extensions_and_text_search_configs(admin_engine: Engine) -> None:
    with admin_engine.connect() as conn:
        ext = set(conn.execute(text("SELECT extname FROM pg_extension")).scalars())
        cfgs = set(conn.execute(text("SELECT cfgname FROM pg_ts_config")).scalars())
    assert {"vector", "ltree", "unaccent"} <= ext
    assert {"legal_es", "legal_en"} <= cfgs


def test_index_inventory(admin_engine: Engine) -> None:
    with admin_engine.connect() as conn:
        defs = {
            name: d
            for name, d in conn.execute(
                text("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'chunks'")
            )
        }
    assert "USING hnsw (embedding vector_cosine_ops)" in defs["chunks_hnsw_idx"]
    assert "m='16'" in defs["chunks_hnsw_idx"] or "m=16" in defs["chunks_hnsw_idx"]
    assert "USING gin (fts)" in defs["chunks_fts_idx"]
    assert "USING gin (legal_domain)" in defs["chunks_domain_idx"]
    cols = "(tenant_id, country, level, state_code, municipality, is_current)"
    assert cols in defs["chunks_prefilter_idx"]
    assert "(embedding_model, embedding_revision, chunker_version)" in defs["chunks_version_idx"]


def _plan(conn: Connection, sql: str, **params: object) -> str:
    return "\n".join(conn.execute(text("EXPLAIN " + sql), params).scalars())


def test_prefilter_query_uses_the_composite_index(
    app_engine: Engine, admin_engine: Engine, tenant_a: uuid.UUID
) -> None:
    helpers.seed_bulk(admin_engine, tenant_a, n=3000, states=8)
    with tenant_transaction(app_engine, tenant_a) as conn:
        conn.execute(
            text("SET LOCAL enable_seqscan = off")
        )  # we test usability, not the cost model
        plan = _plan(
            conn,
            "SELECT id FROM chunks WHERE tenant_id = :t AND country = 'MX' AND level = 'state' "
            "AND state_code = 'BC' AND municipality IS NULL AND is_current",
            t=tenant_a,
        )
    assert "chunks_prefilter_idx" in plan, plan


def test_semantic_arm_plans_prefilter_first_and_hnsw_when_unselective(
    app_engine: Engine, admin_engine: Engine, tenant_a: uuid.UUID
) -> None:
    """Measured behaviour the retrieval code (WS3) relies on:
    - a SELECTIVE jurisdiction filter is resolved by the prefilter index and the few survivors are
      ranked exactly (no ANN approximation: the hard pre-filter in its purest form);
    - an unselective query is served by the HNSW index, with RLS applied as a filter on its scan,
      which is why `hnsw.iterative_scan` must be set (else a filtered top-k can come back short).

    The selective case keeps one state in 32 (~3 % of the rows). With one in 8 (12.5 %) the two
    plans cost nearly the same (prefilter + sort 241.7 vs HNSW down to 225) and the choice flipped
    with the HNSW index's size statistics, which depend on how much earlier tests filled, emptied
    and autovacuumed `chunks` (failed 2 of ~5 full runs on 2026-11-27, never alone). At 3 % the
    prefilter plan costs 149 against >= 264 for HNSW.
    """
    helpers.seed_bulk(admin_engine, tenant_a, n=3000, states=32)
    q = vector_literal(fake_embedding("faltas de asistencia"))
    order = "ORDER BY embedding <=> CAST(:q AS vector) LIMIT 8"
    selective = (
        "SELECT id FROM chunks WHERE country = 'MX' AND level = 'state' AND state_code = 'BC' "
    )
    with tenant_transaction(app_engine, tenant_a) as conn:
        conn.execute(text("SET LOCAL hnsw.iterative_scan = 'relaxed_order'"))
        plan_selective = _plan(conn, selective + order, q=q)
        plan_broad = _plan(conn, "SELECT id FROM chunks " + order, q=q)
        selective_rows = conn.execute(text(selective + order), {"q": q}).all()
        broad_rows = conn.execute(text("SELECT id FROM chunks " + order), {"q": q}).all()
    assert "chunks_prefilter_idx" in plan_selective, plan_selective
    assert "chunks_hnsw_idx" not in plan_selective  # exact ranking over the prefiltered set
    assert "chunks_hnsw_idx" in plan_broad, plan_broad
    assert len(selective_rows) == 8 and len(broad_rows) == 8


def test_lexical_arm_folds_accents_and_case(
    app_engine: Engine, ingestor_engine: Engine, tenant_a: uuid.UUID, tenant_b: uuid.UUID
) -> None:
    helpers.seed_overlapping_tenants(app_engine, ingestor_engine, tenant_a, tenant_b)
    with tenant_transaction(app_engine, tenant_a) as conn:
        for query in ("articulo 47 rescision", "ARTÍCULO 47 RESCISIÓN", "artículo 47"):
            hits = (
                conn.execute(
                    text(
                        "SELECT cite_id FROM chunks WHERE fts @@ websearch_to_tsquery('legal_es', :q)"
                    ),
                    {"q": query},
                )
                .scalars()
                .all()
            )
            assert "MX-FED-LFT-ART47-v2025.06" in hits, query


def test_denormalized_columns_are_derived_never_trusted(
    app_engine: Engine, ingestor_engine: Engine, tenant_a: uuid.UUID, tenant_b: uuid.UUID
) -> None:
    helpers.seed_overlapping_tenants(app_engine, ingestor_engine, tenant_a, tenant_b)
    with tenant_transaction(app_engine, tenant_a) as conn:
        row = conn.execute(
            text(
                "SELECT country, level, state_code, municipality, doc_type, language, jurisdiction_id "
                "FROM chunks WHERE cite_id = 'MX-NL-CCNL-ART1148-v2023'"
            )
        ).one()
        doc_j = conn.execute(
            text(
                "SELECT d.jurisdiction_id FROM documents d JOIN chunks c ON c.document_id = d.id "
                "WHERE c.cite_id = 'MX-NL-CCNL-ART1148-v2023'"
            )
        ).scalar_one()
    assert tuple(row)[:6] == ("MX", "state", "NL", None, "codigo", "es")
    assert row.jurisdiction_id == doc_j
    # a caller that tries to lie about the jurisdiction is overridden by the trigger
    with tenant_session(app_engine, tenant_a) as s:
        chunk = s.query(Chunk).filter_by(tenant_id=tenant_a, cite_id="CF-EXP-0001-ART47").one()
        chunk.country, chunk.state_code = "US", "CA"
        s.flush()
        s.refresh(chunk)
        assert (chunk.country, chunk.state_code) == ("MX", None)


def test_cite_id_is_immutable_but_is_current_flips(
    app_engine: Engine, ingestor_engine: Engine, tenant_a: uuid.UUID, tenant_b: uuid.UUID
) -> None:
    helpers.seed_overlapping_tenants(app_engine, ingestor_engine, tenant_a, tenant_b)
    with (
        pytest.raises(DBAPIError, match="cite_id is immutable"),
        tenant_transaction(ingestor_engine, PUBLIC_CORPUS_TENANT_ID, allow_public=True) as conn,
    ):
        conn.execute(text("UPDATE chunks SET cite_id = 'MX-FED-LFT-ART47-v9999'"))
    with tenant_transaction(ingestor_engine, PUBLIC_CORPUS_TENANT_ID, allow_public=True) as conn:
        n = conn.execute(
            text("UPDATE chunks SET is_current = false WHERE cite_id = 'MX-FED-LFT-ART47-v2025.06'")
        ).rowcount
    assert n == 1  # a reform flips is_current on the old version; nothing is deleted


def test_cite_id_namespaces_and_uniqueness(
    app_engine: Engine, ingestor_engine: Engine, tenant_a: uuid.UUID
) -> None:
    # a private chunk cannot squat a public-looking id ...
    with pytest.raises(IntegrityError, match="chunks_cite_namespace_ck"):
        helpers.seed_document(
            app_engine,
            tenant_a,
            doc_type="client_file",
            jurisdiction=("MX", "federal", None, None),
            title="Exp",
            articles=[Article("MX-FED-LFT-ART47-v2025.06", "Artículo 47", "texto")],
        )
    # ... and the public corpus cannot use the private prefix
    with pytest.raises(IntegrityError, match="chunks_cite_namespace_ck"):
        helpers.seed_document(
            ingestor_engine,
            PUBLIC_CORPUS_TENANT_ID,
            doc_type="ley",
            jurisdiction=("MX", "federal", None, None),
            title="LFT",
            articles=[Article("CF-FAKE-1", "Artículo 1", "texto")],
        )
    # duplicate cite_id inside one tenant
    with pytest.raises(IntegrityError, match="chunks_tenant_cite_uq"):
        helpers.seed_document(
            app_engine,
            tenant_a,
            doc_type="client_file",
            jurisdiction=("MX", "federal", None, None),
            title="Exp",
            articles=[Article("CF-DUP", "Art 1", "a"), Article("CF-DUP", "Art 2", "b")],
        )


def test_ingestion_is_idempotent_per_source(app_engine: Engine, tenant_a: uuid.UUID) -> None:
    kwargs = dict(
        doc_type="client_file",
        jurisdiction=("MX", "federal", None, None),
        title="Exp",
        source="s3://bucket/same.pdf",
        articles=[Article("CF-ONE", "Art 1", "a")],
    )
    helpers.seed_document(app_engine, tenant_a, **kwargs)  # type: ignore[arg-type]
    with pytest.raises(IntegrityError, match="documents_source_uq"):
        helpers.seed_document(app_engine, tenant_a, **kwargs)  # type: ignore[arg-type]


def test_ltree_ancestors_support_parent_expansion(app_engine: Engine, tenant_a: uuid.UUID) -> None:
    helpers.seed_document(
        app_engine,
        tenant_a,
        doc_type="client_file",
        jurisdiction=("MX", "federal", None, None),
        title="Exp",
        articles=[Article("CF-ART1", "Artículo 1", "Son causas:")],
    )
    with tenant_session(app_engine, tenant_a) as s:
        art = s.query(DocNode).filter_by(label="Artículo 1").one()
        s.add(
            DocNode(
                document_id=art.document_id,
                tenant_id=tenant_a,
                parent_id=art.id,
                node_type="fraccion",
                label="Fracción II",
                ordinal=0,
                path="doc.art1.fracII",
                full_text="Faltar más de tres días.",
            )
        )
    with tenant_transaction(app_engine, tenant_a) as conn:
        ancestors = (
            conn.execute(
                text(
                    "SELECT label FROM doc_nodes WHERE path @> 'doc.art1.fracII'::ltree "
                    "AND path <> 'doc.art1.fracII'::ltree ORDER BY nlevel(path)"
                )
            )
            .scalars()
            .all()
        )
    assert ancestors == ["Artículo 1"]  # the fracción's article, one indexed ancestor query


def test_parent_must_live_in_the_same_document(app_engine: Engine, tenant_a: uuid.UUID) -> None:
    for slug in ("one", "two"):
        helpers.seed_document(
            app_engine,
            tenant_a,
            doc_type="client_file",
            jurisdiction=("MX", "federal", None, None),
            title=f"Exp {slug}",
            articles=[Article(f"CF-{slug.upper()}", "Artículo 1", "x")],
        )
    with tenant_session(app_engine, tenant_a) as s:
        one = (
            s.query(DocNode)
            .join(Chunk, Chunk.node_id == DocNode.id)
            .filter(Chunk.cite_id == "CF-ONE")
            .one()
        )
        two = (
            s.query(DocNode)
            .join(Chunk, Chunk.node_id == DocNode.id)
            .filter(Chunk.cite_id == "CF-TWO")
            .one()
        )
        one_id, two_doc = one.id, two.document_id
    with pytest.raises(IntegrityError, match="doc_nodes_parent_fk"):
        with tenant_session(app_engine, tenant_a) as s:
            s.add(
                DocNode(
                    document_id=two_doc,
                    tenant_id=tenant_a,
                    parent_id=one_id,
                    node_type="fraccion",
                    label="X",
                    ordinal=0,
                    path="doc.art1.x",
                    full_text="x",
                )
            )


def test_version_columns_are_mandatory(admin_engine: Engine) -> None:
    with admin_engine.connect() as conn:
        nullable = (
            conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns WHERE table_name = 'chunks' "
                    "AND column_name IN ('embedding','embedding_model','embedding_revision',"
                    "'chunker_version','chunk_thresholds') AND is_nullable = 'YES'"
                )
            )
            .scalars()
            .all()
        )
    assert nullable == [], "invariant 5: every chunk records what it was built with"
