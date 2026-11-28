"""ADR-052: a reform decree's heading travels with its own decree, and its units carry its date.

Found on golden gd2-024 (2026-11-27): each "DECRETO ... / Publicado en el Diario Oficial de la
Federación el ..." heading was glued to the PREVIOUS decree's last unit, so no unit knew its own
decree's date; the model saw an undated decree that ADDED art. 15-C next to an undated one that
REPEALED it, and declined.
"""

from __future__ import annotations

from app.ingestion.chunker import Chunker
from app.ingestion.model import DocMeta
from app.ingestion.normalizer_mx import normalize_text, parse
from app.ingestion.tokens import ApproxCounter

META = DocMeta(country="MX", scope="FED", code="LFT", title="Ley Federal del Trabajo",
               version_label="1")  # fmt: skip
WORDS = " ".join(["texto"] * 70)  # above the merge-up minimum: every unit stays its own unit


def chunks(text: str):  # type: ignore[no-untyped-def]
    src = normalize_text(text)
    return Chunker(ApproxCounter()).chunk(src, parse(src), META), src


TWO_DECREES = f"""Artículo 15-C.- (Se deroga)

TRANSITORIOS

Primero. El presente decreto entrará en vigor al día siguiente de su publicación. {WORDS}

DECRETO por el que se reforman, adicionan y derogan diversas disposiciones de la Ley Federal del Trabajo.

Publicado en el Diario Oficial de la Federación el 30 de noviembre de 2012

Artículo Único.- Se ADICIONAN los artículos 15-A; 15-B; 15-C; 15-D de la Ley Federal del Trabajo. {WORDS}

TRANSITORIOS

Primero. El presente Decreto entrará en vigor al día siguiente. {WORDS}

DECRETO por el que se reforman, adicionan y derogan diversas disposiciones de la Ley Federal del Trabajo;
de la Ley del Seguro Social.

Publicado en el Diario Oficial de la Federación el 23 de abril de 2021

Artículo Primero. Se reforman los artículos 12; 13 y se derogan los artículos 15A; 15-B; 15-C, y 15-D. {WORDS}
"""


def test_each_decree_unit_carries_its_own_publication_date() -> None:
    by_label = {c.citation_string: c for c in chunks(TWO_DECREES)[0]}
    adds = next(c for k, c in by_label.items() if "Único transitorio" in k)
    repeals = next(c for k, c in by_label.items() if "Primero transitorio, decreto DOF 23-04" in k)
    assert "decreto DOF 30-11-2012" in adds.citation_string
    assert "decreto DOF 30-11-2012" in adds.breadcrumb
    assert "decreto DOF 23-04-2021" in repeals.breadcrumb
    assert "se derogan los artículos 15A" in repeals.text


def test_a_decree_heading_is_its_own_hidden_unit_not_part_of_a_neighbour() -> None:
    """Measured on gd2-024 (2026-11-27): a fragment that opens with a long multi-law decree title
    made the drafting model decline 5/5; the same context without it answered 5/5."""
    out, _ = chunks(TWO_DECREES)
    previous = next(c for c in out if c.text.startswith("Primero. El presente decreto entrará"))
    assert "DECRETO" not in previous.text and "Publicado" not in previous.text
    heads = [c for c in out if c.text.startswith("DECRETO por el que")]
    assert len(heads) == 2 and all(c.is_navigation for c in heads)
    assert "30 de noviembre de 2012" in heads[0].text and "Ley del Seguro Social" in heads[1].text
    first = next(c for c in out if "Se ADICIONAN" in c.text)
    assert first.text.startswith("Artículo Único.-") and not first.is_navigation
    second = next(c for c in out if "se derogan los artículos 15A" in c.text)
    assert second.text.startswith("Artículo Primero.") and not second.is_navigation


def test_no_text_is_lost_or_duplicated() -> None:
    out, src = chunks(TWO_DECREES)
    lines = [ln.strip() for c in out for ln in c.text.split("\n") if ln.strip()]
    expected = [ln.strip() for ln in src.split("\n") if ln.strip() and ln.strip() != "TRANSITORIOS"]
    assert sorted(lines) == sorted(expected)


NOT_A_UNIT = f"""Artículo 1.- Texto de la ley. {WORDS}

TRANSITORIOS

Primero. Entrará en vigor al día siguiente. {WORDS}

LEY del Notariado para el Distrito Federal y Territorios.

Publicada en el Diario Oficial de la Federación el 23 de febrero de 1946

TRANSITORIOS:

ARTICULO 1o.- La presente Ley entrará en vigor treinta días después. {WORDS}

DECRETO por el que se reforma el artículo 74.

Publicado en el Diario Oficial de la Federación el 17 de enero de 2006

ARTÍCULO CUADRAGÉSIMO OCTAVO. Se reforman los artículos 126 y 407. {WORDS}
"""


def test_a_heading_not_followed_by_a_unit_stays_where_it_was() -> None:
    """A TRANSITORIOS heading has no text of its own, and an article the grammar does not read as
    a transitorio unit ("ARTICULO 1o.-", "ARTÍCULO CUADRAGÉSIMO OCTAVO.") cannot take the block:
    the lines stay where they were, so the structure and every cite_id are unchanged."""
    out, src = chunks(NOT_A_UNIT)
    lines = sorted(ln.strip() for c in out for ln in c.text.split("\n") if ln.strip())
    expected = sorted(
        ln.strip() for ln in src.split("\n")
        if ln.strip() and not ln.strip().startswith("TRANSITORIOS")
    )  # fmt: skip
    assert lines == expected
    # known limit: the date labels transitorio UNITS only; a numbered article inside transitorios
    # is read as a paragraph and carries none
    art1 = next(c for c in out if "treinta días" in c.text)
    assert "decreto DOF" not in art1.breadcrumb


def test_the_original_transitorios_take_the_laws_own_date() -> None:
    text = f"""Publicado en el Diario Oficial de la Federación el 1o de abril de 1970

Artículo 1.- La presente Ley es de observancia general. {WORDS}

TRANSITORIOS

Primero. Esta Ley entrará en vigor el día primero de mayo de 1970. {WORDS}
"""
    [tr] = [c for c in chunks(text)[0] if "transitorio" in c.citation_string]
    assert "decreto DOF 01-04-1970" in tr.citation_string
