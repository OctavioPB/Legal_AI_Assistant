"""ADR-037 (C): a unit the hits refer to joins the CONTEXT (and becomes citable), never the hits."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import Engine

from app.db.session import PUBLIC_CORPUS_TENANT_ID, tenant_transaction
from app.ingestion.model import DocMeta
from app.ingestion.store import ingest_document
from app.ingestion.tokens import ApproxCounter
from app.rag.context import load_session_context
from app.rag.pipeline import retrieve
from app.settings import Settings
from tests.fakes import FakeReranker
from tests.helpers import jurisdiction_id
from tests.test_retrieval import EMB

F = " ".join(["considerando"] * 70)
LAW = f"""Artículo 48.- El trabajador podrá solicitar la reinstalación o la indemnización en los términos del artículo 50 de esta Ley. {F}

Artículo 49.- El patrón quedará eximido de reinstalar al trabajador en los casos siguientes. {F}

Artículo 50.- Las indemnizaciones consistirán en el importe de tres meses de salario y veinte días por cada año. {F}
"""


@pytest.fixture
def lft(ingestor_engine: Engine, admin_engine: Engine) -> int:
    with admin_engine.connect() as c:
        fed = jurisdiction_id(c, "MX", "federal")
    meta = DocMeta(country="MX", scope="FED", code="LFT", title="Ley Federal del Trabajo",
                   version_label="1", language="es")  # fmt: skip
    ingest_document(ingestor_engine, PUBLIC_CORPUS_TENANT_ID, meta=meta, doc_type="ley",
                    jurisdiction_id=fed, source_uri="file://lft.txt", raw_text=LAW,
                    embedder=EMB, counter=ApproxCounter(), official_id="LFT")  # fmt: skip
    return fed


@pytest.mark.parametrize("crossref", [False, True])
def test_a_referenced_article_joins_the_context_but_not_the_hits(
    app_engine: Engine, lft: int, crossref: bool
) -> None:
    tenant = uuid.uuid4()
    s = Settings(refusal_tau=0.0, retrieval_rerank_top=1, retrieval_crossref=crossref)
    q = "¿Puede el trabajador solicitar la reinstalación o la indemnización?"
    with tenant_transaction(app_engine, tenant) as c:
        ctx = load_session_context(c, tenant, lft, language="es")
        r = retrieve(
            c, ctx, q, embedder=EMB, reranker=FakeReranker(), counter=ApproxCounter(), settings=s
        )
    hits = [h.cite_id for h in r.hits]
    shown = {cid for f in r.fragments for cid in (f.cite_id, *f.also_cite_ids)}
    art50 = "MX-FED-LFT-ART50-v1"
    if hits[0] != "MX-FED-LFT-ART48-v1":
        pytest.skip(
            "the fake ranking did not put art. 48 first; the property is about its reference"
        )
    assert art50 not in hits  # never a hit: the gate and recall@8 do not see it
    assert (art50 in shown) is crossref
