"""ADR-022: a unit the query names explicitly is resolved by the citation grammar, under the SAME
jurisdiction predicate, placed first, and passes the tau gate. Golden gd2-009 (art. 123-B-XIII) and
gd2-025 (art. 525, derogated, historical session) were lost by similarity ranking."""

from __future__ import annotations

import uuid

from sqlalchemy import Engine

from app.corpus.verify import extract, stem
from app.db.session import tenant_transaction
from app.ingestion.tokens import ApproxCounter
from app.rag.context import load_session_context
from app.rag.pipeline import retrieve
from app.settings import Settings
from tests.fakes import FakeReranker
from tests.test_retrieval import EMB, corpus  # noqa: F401  (fixture)

STRICT = Settings(refusal_tau=0.99)  # nothing passes on similarity alone


def run(engine: Engine, tenant: uuid.UUID, jid: int, q: str, *, explicit: bool, lang: str = "es"):  # type: ignore[no-untyped-def]
    s = STRICT.model_copy(update={"retrieval_explicit_reference": explicit})
    with tenant_transaction(engine, tenant) as c:
        ctx = load_session_context(c, tenant, jid, language=lang)  # type: ignore[arg-type]
        return retrieve(
            c, ctx, q, embedder=EMB, reranker=FakeReranker(), counter=ApproxCounter(), settings=s
        )


def test_a_named_unit_comes_first_and_passes_the_gate(app_engine, corpus, tenant_a) -> None:  # type: ignore[no-untyped-def]
    q = "¿Qué dice el artículo 48 de la Ley Federal del Trabajo?"
    off = run(app_engine, tenant_a, corpus["fed"], q, explicit=False)
    assert off.refusal is not None, "with tau 0.99 similarity alone never passes"
    on = run(app_engine, tenant_a, corpus["fed"], q, explicit=True)
    assert on.refusal is None and on.hits[0].cite_id == "MX-FED-LFT-ART48-v1"
    assert on.fragments, "the named unit reaches the context (and the validator, unchanged)"


def test_an_article_reference_brings_its_units_in_document_order(
    app_engine, corpus, tenant_a
) -> None:  # type: ignore[no-untyped-def]
    on = run(
        app_engine, tenant_a, corpus["fed"], "Explique el artículo 47 de la LFT", explicit=True
    )
    ids = [h.cite_id for h in on.hits[:3]]  # the caput (lead-in) is the article's first unit
    assert ids == [
        "MX-FED-LFT-ART47-CAPUT-v1", "MX-FED-LFT-ART47-FRACI-v1", "MX-FED-LFT-ART47-FRACII-v1",
    ]  # fmt: skip


def test_a_named_law_outside_the_session_is_never_pinned(app_engine, corpus, tenant_a) -> None:  # type: ignore[no-untyped-def]
    q = "¿Qué dice el artículo 1148 del Código Civil para el Estado de Nuevo León?"
    assert stem(extract(q)[0]) == "MX-NL-CCNL-ART1148"  # the grammar resolves it...
    on = run(app_engine, tenant_a, corpus["fed"], q, explicit=True)  # ...but the session is federal
    assert on.refusal is not None
    assert not any("CCNL" in h.cite_id for h in on.hits), "the predicate still decides"


def test_only_named_laws_are_resolved() -> None:
    assert extract("El artículo 48 prevé la reinstalación.")[0].resolution == "none"
    q = "¿Qué establece el artículo 123, apartado B, fracción XIII de la Constitución?"
    [r] = extract(q)
    assert (r.resolution, stem(r)) == ("named", "MX-FED-CPEUM-ART123-APARTADOB-FRACXIII")
    state = extract("¿Qué dice la Constitución del Estado de Nuevo León en su artículo 5?")
    assert all(x.official_id != "CPEUM" for x in state)
