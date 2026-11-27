"""ADR-049: two MX heading defects found while building ADR-031, each a fixture first.

(a) Títulos numbered with cardinal words ("TITULO ONCE" ... "TITULO DIECISEIS" in the LFT) were not
    headings: every article after art. 522 sat under "TITULO DECIMO Prescripción", and the heading
    text was glued onto the last article before it.
(b) A bare heading ("CAPITULO IV") whose name is on the next non-blank line in mixed case lost the
    name, which became a junk "Párrafo 1" unit of its own (432 such units in the loaded codes).
"""

from __future__ import annotations

import pytest

from app.ingestion.chunker import Chunker
from app.ingestion.model import DocMeta
from app.ingestion.normalizer_mx import normalize_text, parse
from app.ingestion.tokens import ApproxCounter

pytestmark = pytest.mark.xfail(strict=True, reason="ADR-049 measured and not adopted yet")

META = DocMeta(country="MX", scope="FED", code="LFT", title="Ley Federal del Trabajo",
               version_label="1")  # fmt: skip


def chunks(text: str):  # type: ignore[no-untyped-def]
    src = normalize_text(text)
    return Chunker(ApproxCounter()).chunk(src, parse(src), META)


LFT_LIKE = """TITULO DECIMO Prescripción

Artículo 522.- Para los efectos de la prescripción, los meses se regularán por el número de días.

TITULO ONCE

Autoridades del Trabajo y Servicios Sociales

CAPITULO I

Disposiciones generales

Artículo 523.- La aplicación de las normas de trabajo compete, en sus respectivas jurisdicciones, a las autoridades.

TITULO CATORCE

Derecho Procesal del Trabajo

CAPITULO IV

De la Capacidad, Personalidad y Legitimación

Artículo 689.- Son partes en el proceso del trabajo, las personas físicas o morales que acrediten su interés jurídico.
"""


def test_cardinal_titulos_open_their_own_titulo_and_keep_their_names() -> None:
    by_id = {c.cite_id: c for c in chunks(LFT_LIKE)}
    art523 = by_id["MX-FED-LFT-ART523-v1"]
    assert "TITULO ONCE Autoridades del Trabajo y Servicios Sociales" in art523.breadcrumb
    assert "CAPITULO I Disposiciones generales" in art523.breadcrumb
    assert "Prescripción" not in art523.breadcrumb
    art689 = by_id["MX-FED-LFT-ART689-v1"]
    assert "TITULO CATORCE Derecho Procesal del Trabajo" in art689.breadcrumb
    assert "CAPITULO IV De la Capacidad, Personalidad y Legitimación" in art689.breadcrumb


def test_the_article_before_a_cardinal_titulo_no_longer_swallows_it() -> None:
    art522 = next(c for c in chunks(LFT_LIKE) if c.cite_id == "MX-FED-LFT-ART522-v1")
    assert art522.text.endswith("número de días.")
    assert "TITULO ONCE" not in art522.text


def test_a_heading_name_is_not_a_unit_of_its_own() -> None:
    texts = [c.text for c in chunks(LFT_LIKE)]
    assert not any(
        t.strip() in ("Disposiciones generales", "Derecho Procesal del Trabajo") for t in texts
    )


CAPS_TWO_LINES = """CAPITULO II

DE LAS OBLIGACIONES DEL MANDATARIO CON

RESPECTO AL MANDANTE

Artículo 2562.- El mandatario, en el desempeño de su encargo, se sujetará a las instrucciones recibidas.
"""


def test_an_all_caps_name_may_run_over_two_lines() -> None:
    [art] = [c for c in chunks(CAPS_TWO_LINES) if c.cite_id.endswith("ART2562-v1")]
    assert (
        "CAPITULO II DE LAS OBLIGACIONES DEL MANDATARIO CON RESPECTO AL MANDANTE" in art.breadcrumb
    )


NOT_NAMES = """CAPITULO UNICO

Artículo 723.- Son objeto del patrimonio de la familia: la casa habitación y la parcela.

CAPITULO I

Capítulo derogado DOF 14-01-1948

CAPITULO III

El patrón podrá rescindir la relación de trabajo sin responsabilidad, cuando el trabajador incurra en faltas de probidad u honradez durante sus labores, y en los demás casos que esta ley prevé de manera expresa.

TITULO SEGUNDO

CAPITULO I

Artículo 20.- Se entiende por relación de trabajo la prestación de un trabajo personal subordinado.
"""


@pytest.mark.xfail(strict=False, reason="guards the fix; passes trivially without it")
def test_articles_notes_sentences_and_headings_are_never_taken_as_a_name() -> None:
    by_id = {c.cite_id: c for c in chunks(NOT_NAMES)}
    assert any(k.endswith("ART723-v1") for k in by_id)  # the article stays an article
    crumbs = " ".join(c.breadcrumb for c in by_id.values())
    assert "CAPITULO I Capítulo derogado" not in crumbs  # a "... DOF ..." note
    assert "CAPITULO III El patrón" not in crumbs  # a sentence
    assert "TITULO SEGUNDO CAPITULO I" not in crumbs  # the next heading
