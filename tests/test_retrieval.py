"""End-to-end retrieval (WS3.T02-T05) over a corpus ingested through the real ingestion path."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import Engine, text

from app.db.session import PUBLIC_CORPUS_TENANT_ID, tenant_transaction
from app.ingestion.model import DocMeta
from app.ingestion.store import ingest_document
from app.ingestion.tokens import ApproxCounter
from app.rag.context import load_session_context
from app.rag.expansion import render_context
from app.rag.pipeline import Retrieval, retrieve
from app.rag.search import semantic_arm
from app.settings import Settings
from tests.fakes import FakeEmbedder, FakeReranker
from tests.helpers import jurisdiction_id

FILL = " ".join(["considerando"] * 70)  # long enough that each fracción stays its own chunk

LFT = f"""Artículo 47.- Son causas de rescisión de la relación de trabajo, sin responsabilidad para el patrón:
I. Engañarlo el trabajador con certificados falsos respecto de su capacidad {FILL}
II. Incurrir el trabajador en faltas de probidad u honradez durante sus labores {FILL}
Artículo 48.- El trabajador podrá solicitar ante la Junta la reinstalación en el trabajo que desempeñaba.
"""
NL = f"Artículo 1148.- La posesión de buena fe se pierde por la prescripción adquisitiva {FILL}\n"
JAL = f"Artículo 1148.- La posesión jalisciense se rige por reglas propias de Jalisco {FILL}\n"
CA = f"Section 1102.5.- An employer shall not retaliate against an employee for disclosing {FILL}\n"
INDEX = "ÍNDICE\nArtículo 47 ........ 3\nArtículo 48 ........ 4\n\n" + LFT
CLIENT = f"Artículo 1.- Cláusula confidencial del despacho sobre rescisión secreta {FILL}\n"

SETTINGS = Settings(refusal_tau=0.3)
EMB = FakeEmbedder()


def _put(
    engine: Engine,
    tenant: uuid.UUID,
    jid: int,
    code: str,
    scope: str,
    raw: str,
    *,
    country: str = "MX",
    lang: str = "es",
    doc_type: str = "ley",
) -> None:
    public = tenant == PUBLIC_CORPUS_TENANT_ID
    meta = DocMeta(
        country=country,
        scope=scope,
        code=code,
        title=f"Ley {code}",
        version_label="1",
        language=lang,  # type: ignore[arg-type]
        client_file=None if public else "EXP1",
    )
    ingest_document(
        engine,
        tenant,
        meta=meta,
        doc_type=doc_type if public else "client_file",
        jurisdiction_id=jid,
        source_uri=f"file://{code}.txt",
        raw_text=raw,
        embedder=EMB,
        counter=ApproxCounter(),
    )


@pytest.fixture
def corpus(
    ingestor_engine: Engine, app_engine: Engine, admin_engine: Engine, tenant_a: uuid.UUID
) -> dict[str, int]:
    with admin_engine.connect() as c:
        j = {
            "fed": jurisdiction_id(c, "MX", "federal"),
            "nl": jurisdiction_id(c, "MX", "state", "NL"),
            "jal": jurisdiction_id(c, "MX", "state", "JAL"),
            "ca": jurisdiction_id(c, "US", "state", "CA"),
        }
    pub = PUBLIC_CORPUS_TENANT_ID
    _put(ingestor_engine, pub, j["fed"], "LFT", "FED", LFT)
    _put(ingestor_engine, pub, j["nl"], "CCNL", "NL", NL, doc_type="codigo")
    _put(ingestor_engine, pub, j["jal"], "CCJAL", "JAL", JAL, doc_type="codigo")
    _put(
        ingestor_engine, pub, j["ca"], "LAB", "CA", CA, country="US", lang="en", doc_type="statute"
    )
    _put(app_engine, tenant_a, j["nl"], "EXP1", "NL", CLIENT)
    return j


def _run(
    engine: Engine,
    tenant: uuid.UUID,
    jid: int,
    query: str,
    *,
    lang: str = "es",
    settings: Settings = SETTINGS,
) -> Retrieval:
    with tenant_transaction(engine, tenant) as c:
        ctx = load_session_context(c, tenant, jid, language=lang)  # type: ignore[arg-type]
        return retrieve(
            c,
            ctx,
            query,
            embedder=EMB,
            reranker=FakeReranker(),
            counter=ApproxCounter(),
            settings=settings,
        )


def test_exact_article_lookup_and_stacking(
    app_engine: Engine, corpus: dict[str, int], tenant_a: uuid.UUID
) -> None:
    r = _run(app_engine, tenant_a, corpus["nl"], "artículo 47 rescisión")
    assert r.refusal is None
    assert any("ART47" in c for c in r.lexical_ids)  # accent-folded lexical arm finds it
    ids = {h.cite_id for h in r.hits}
    assert any("-LFT-ART47" in i for i in ids)
    assert not any("CCJAL" in i for i in ids)  # sibling state never appears


def test_siblings_and_other_countries_never_leak(
    app_engine: Engine, corpus: dict[str, int], tenant_a: uuid.UUID
) -> None:
    for q in ("posesión", "Jalisco", "employer retaliate disclosing"):
        r = _run(app_engine, tenant_a, corpus["nl"], q)
        every = {*r.semantic_ids, *r.lexical_ids}
        assert not any("CCJAL" in i or "-LAB-" in i for i in every), (q, every)
    ca = _run(app_engine, tenant_a, corpus["ca"], "employer retaliate disclosing", lang="en")
    assert ca.refusal is None and ca.hits[0].cite_id.startswith("US-CA-LAB")
    assert not any(i.startswith("MX-") for i in {*ca.semantic_ids, *ca.lexical_ids})


def test_client_file_is_private_to_its_tenant(
    app_engine: Engine, corpus: dict[str, int], tenant_a: uuid.UUID, tenant_b: uuid.UUID
) -> None:
    mine = _run(app_engine, tenant_a, corpus["nl"], "cláusula confidencial secreta")
    theirs = _run(app_engine, tenant_b, corpus["nl"], "cláusula confidencial secreta")
    assert any(i.startswith("CF-") for i in mine.lexical_ids)
    assert not any(i.startswith("CF-") for i in (*theirs.lexical_ids, *theirs.semantic_ids))


def test_navigation_chunks_are_excluded(
    ingestor_engine: Engine, app_engine: Engine, corpus: dict[str, int], tenant_a: uuid.UUID
) -> None:
    _put(ingestor_engine, PUBLIC_CORPUS_TENANT_ID, corpus["fed"], "IDX", "FED", INDEX)
    r = _run(app_engine, tenant_a, corpus["fed"], "artículo 47 48")
    with tenant_transaction(app_engine, tenant_a) as c:
        nav = set(c.execute(text("SELECT cite_id FROM chunks WHERE is_navigation")).scalars().all())
    assert nav and not nav & {*r.lexical_ids, *r.semantic_ids}


def test_out_of_corpus_query_is_refused_by_code_without_context(
    app_engine: Engine, corpus: dict[str, int], tenant_a: uuid.UUID
) -> None:
    s = Settings(refusal_tau=0.9)
    r = _run(app_engine, tenant_a, corpus["nl"], "mecánica cuántica de agujeros negros", settings=s)
    assert r.refusal is not None and r.fragments == ()
    assert r.refusal.block == f"<insuficiente>{r.refusal.reason_key}</insuficiente>"


def test_expansion_returns_whole_article_deduped_within_budget(
    app_engine: Engine, corpus: dict[str, int], tenant_a: uuid.UUID
) -> None:
    r = _run(app_engine, tenant_a, corpus["fed"], "rescisión certificados falsos probidad honradez")
    art47 = [
        f for f in r.fragments if "ART47" in f.cite_id or any("ART47" in a for a in f.also_cite_ids)
    ]
    assert len(art47) == 1  # fracciones I and II dedupe into one article fragment
    f = art47[0]
    assert f.expanded and "Artículo 47" in f.text and "faltas de probidad" in f.text
    assert "Artículo 48" not in f.text  # the parent article, not its sibling
    assert f.also_cite_ids  # both fracción chunks stay citable
    offered = {f.cite_id, *f.also_cite_ids}
    assert offered <= {h.cite_id for h in r.hits}


@pytest.mark.parametrize("budget", [60, 200, 500, 3000])
def test_context_never_exceeds_the_token_budget(
    app_engine: Engine, corpus: dict[str, int], tenant_a: uuid.UUID, budget: int
) -> None:
    s = Settings(refusal_tau=0.0, retrieval_context_budget_tokens=budget)
    r = _run(app_engine, tenant_a, corpus["nl"], "rescisión posesión artículo", settings=s)
    assert ApproxCounter().count(render_context(r.fragments)) <= budget


def test_other_embedding_generation_is_invisible_to_the_semantic_arm(
    app_engine: Engine, corpus: dict[str, int], tenant_a: uuid.UUID
) -> None:
    with tenant_transaction(app_engine, tenant_a) as c:
        ctx = load_session_context(c, tenant_a, corpus["nl"])
        assert semantic_arm(c, ctx, "posesión", FakeEmbedder(revision="r-other"), top=5) == []
        assert semantic_arm(c, ctx, "posesión", EMB, top=5)


def test_retrieval_is_deterministic(
    app_engine: Engine, corpus: dict[str, int], tenant_a: uuid.UUID
) -> None:
    a = _run(app_engine, tenant_a, corpus["nl"], "rescisión de la relación de trabajo")
    b = _run(app_engine, tenant_a, corpus["nl"], "rescisión de la relación de trabajo")
    assert [h.cite_id for h in a.hits] == [h.cite_id for h in b.hits]


@pytest.mark.parametrize("bad", [[0.0] * 1024, [float("nan")] * 1024, [0.1] * 12])
def test_an_unusable_query_vector_fails_loudly_instead_of_returning_garbage(
    app_engine: Engine, corpus: dict[str, int], tenant_a: uuid.UUID, bad: list[float]
) -> None:
    from app.rag.search import InvalidQueryVectorError

    class BadEmbedder(FakeEmbedder):
        def embed(self, texts: list[str]) -> list[list[float]]:
            return [list(bad) for _ in texts]

    with tenant_transaction(app_engine, tenant_a) as c:
        ctx = load_session_context(c, tenant_a, corpus["nl"])
        with pytest.raises(InvalidQueryVectorError):
            semantic_arm(c, ctx, "posesión", BadEmbedder(), top=5)
