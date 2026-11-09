"""ADR-023: a unit inside an apartado expands to that apartado, not to the whole article.

Golden gd2-009: art. 123 CPEUM rendered whole (~34k characters, both apartados) let the model cite
123-B-XIII while paraphrasing 123-A-XIII; the validator proves provenance, not faithfulness."""

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
ART123 = f"""Artículo 123.- Toda persona tiene derecho al trabajo digno y socialmente útil.

A. Entre los obreros, jornaleros, empleados domésticos y artesanos:

I. La duración de la jornada máxima será de ocho horas {F}

XIII. Las empresas estarán obligadas a proporcionar a sus trabajadores capacitación o adiestramiento {F}

B. Entre los Poderes de la Unión y sus trabajadores:

I. La jornada diaria máxima de trabajo diurna será de ocho horas {F}

XIII. Los militares, marinos y los miembros de las instituciones policiales se regirán por sus propias leyes {F}
"""
B13 = "MX-FED-CPEUM-ART123-APARTADOB-FRACXIII-v1"


@pytest.fixture
def cpeum(ingestor_engine: Engine, admin_engine: Engine) -> int:
    with admin_engine.connect() as c:
        fed = jurisdiction_id(c, "MX", "federal")
    meta = DocMeta(country="MX", scope="FED", code="CPEUM", title="Constitución", version_label="1",
                   language="es")  # fmt: skip
    ingest_document(ingestor_engine, PUBLIC_CORPUS_TENANT_ID, meta=meta, doc_type="ley",
                    jurisdiction_id=fed, source_uri="file://cpeum.txt", raw_text=ART123,
                    embedder=EMB, counter=ApproxCounter(), official_id="CPEUM")  # fmt: skip
    return fed


@pytest.mark.parametrize("apartado", [False, True])
def test_the_context_of_a_unit_in_an_apartado(
    app_engine: Engine, cpeum: int, apartado: bool
) -> None:
    tenant = uuid.uuid4()
    s = Settings(refusal_tau=0.0, context_unit_apartado=apartado)
    q = "¿Qué establece el artículo 123, apartado B, fracción XIII de la Constitución?"
    with tenant_transaction(app_engine, tenant) as c:
        ctx = load_session_context(c, tenant, cpeum, language="es")
        r = retrieve(
            c, ctx, q, embedder=EMB, reranker=FakeReranker(), counter=ApproxCounter(), settings=s
        )
    [frag] = [f for f in r.fragments if f.cite_id == B13 or B13 in f.also_cite_ids]
    if apartado:
        assert frag.text.startswith("B.") and "militares" in frag.text
        assert "capacitación" not in frag.text, "apartado A's fracción XIII is not in this fragment"
        offered = {frag.cite_id, *frag.also_cite_ids}
        assert not any("APARTADOA" in i for i in offered), (
            "only apartado B's units are citable here"
        )
    else:  # the previous behaviour: the whole article, both apartados
        assert "capacitación" in frag.text and "militares" in frag.text
