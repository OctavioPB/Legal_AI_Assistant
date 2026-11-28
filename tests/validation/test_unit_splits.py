"""Sentence units the real local model's research answer was refused for (2026-11-08, after ADR-020):
a cited sentence cut in two by a numeral's period, its first half then reported as uncited."""

from __future__ import annotations

from app.validation.types import RetrievedSet
from app.validation.validator import validate

ART = "MX-FED-LFT-ART341-v2026.11"
RS = RetrievedSet.build([(ART, ["Artículo 341.- Texto de la norma."])])


def check(text: str):  # type: ignore[no-untyped-def]
    return validate(text, RS, workflow="dossier_builder")


def test_an_ordinal_with_a_period_does_not_cut_the_cited_sentence() -> None:
    """Seen: '... conforme lo establecido en el artículo 1°. de la Constitución ... [cite: X]'."""
    v = check(
        "Se considerará despido injustificado de las personas trabajadoras del hogar todo el que "
        "se dé por discriminación conforme lo establecido en el artículo 1°. de la Constitución "
        f"Política y la legislación aplicable [cite: {ART}]."
    )
    assert v.grounded and not v.uncovered


def test_an_inline_list_after_a_semicolon_stays_one_sentence() -> None:
    """Seen: '... antigüedad menor de un año; II. Si comprueba ante el Tribunal ... [cite: X]'."""
    v = check(
        "El patrón queda eximido de reinstalar en los casos siguientes: I. Cuando se trate de "
        "trabajadores con antigüedad menor de un año; II. Si comprueba ante el Tribunal que no es "
        f"posible el desarrollo normal de la relación de trabajo [cite: {ART}]."
    )
    assert v.grounded and not v.uncovered


def test_the_last_item_of_an_inline_list_after_y_stays_in_the_sentence() -> None:
    """Seen: '... V. Cuando se trate de trabajadores eventuales, y VI. Cuando se trate de ... [cite: X]'."""
    v = check(
        "La persona empleadora queda eximida en los casos siguientes: I. En los casos de "
        "trabajadores de confianza; II. Cuando se trate de trabajadores eventuales, y III. Cuando se "
        f"trate de personas trabajadoras en plataformas digitales [cite: {ART}]."
    )
    assert v.grounded and not v.uncovered


def test_a_real_sentence_after_a_numeral_still_splits() -> None:
    """The merge is narrow: a sentence that ENDS at 'fracción II.' or 'artículo 1°.' followed by a
    new capitalised sentence stays two units, so an uncited legal sentence cannot ride on its
    neighbour's citation."""
    v = check(
        "La causa de rescisión aplicable está prevista en la fracción II. El patrón debe además "
        f"pagar una indemnización de tres meses de salario al trabajador [cite: {ART}]."
    )
    assert any("fracción II" in u for u in v.uncovered)
    v2 = check(
        "La igualdad entre las personas la consagra el artículo 1°. El patrón debe pagar tres "
        f"meses de salario al trabajador despedido [cite: {ART}]."
    )
    assert any("artículo 1°" in u for u in v2.uncovered)


def test_an_abbreviation_before_a_roman_numeral_does_not_cut_the_sentence() -> None:
    """`_NUMBER_START` held a backspace byte instead of a word boundary since 2026-11-07, so
    "fracc. IV, el patrón ..." was cut at "fracc." and its first half reported as uncited."""
    v = check(
        "Conforme a lo dispuesto en el art. 47, fracc. IV, el patrón puede rescindir la relación "
        f"de trabajo sin responsabilidad por actos graves de violencia [cite: {ART}]."
    )
    assert v.grounded and not v.uncovered


def test_an_abbreviation_before_a_word_that_starts_like_a_numeral_still_splits() -> None:
    """The boundary is a word boundary: "Inciso", "Las" or "Ciudad" are not numerals."""
    from app.validation.validator import _units

    assert len(_units("Ver núm. Las partes acordaron el plazo.")) == 2
    assert _units("Ver fracc. VI del artículo citado.") == ["Ver fracc. VI del artículo citado."]
