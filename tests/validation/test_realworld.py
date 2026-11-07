"""Two behaviours found by running the REAL local model against the real corpus:

* "Art. 291-M" must not split a sentence (the citation ended up in another unit);
* a quotation that flattens a list drops the "I.", "II." enumerators; the WORDS are still verbatim.
"""

from __future__ import annotations

from app.validation.types import RetrievedSet
from app.validation.validator import fold_words, validate

ART = "MX-FED-LFT-ART353G-v2026.11"
FRAGMENT = (
    "Artículo 353-G.- Son causas especiales de rescisión de la relación de trabajo, sin "
    "responsabilidad para el patrón, además de la que establece el artículo 47, las siguientes:\n\n"
    "I. El incumplimiento de las obligaciones a que aluden las fracciones I, II, III y VI del "
    "artículo 353-D;\nII. La violación de las normas técnicas o administrativas necesarias para el "
    "funcionamiento de la unidad médica."
)
RS = RetrievedSet.build([(ART, [FRAGMENT])])


def ok(text: str) -> bool:
    return validate(text, RS, workflow="dossier_builder").grounded


def test_an_abbreviation_followed_by_a_number_does_not_split_the_sentence() -> None:
    text = (
        "Además existen causales para residentes médicos y plataformas digitales previstas en "
        f"el Art. 353-G de la ley [cite: {ART}]."
    )
    v = validate(text, RS, workflow="dossier_builder")
    assert v.grounded and not v.uncovered


def test_a_real_sentence_boundary_after_an_abbreviation_like_word_still_splits() -> None:
    text = f"Procede la rescisión según el art. Además el patrón debe indemnizar siempre al trabajador [cite: {ART}]."
    v = validate(text, RS, workflow="dossier_builder")
    # "Además ..." is a new sentence (it does not start with a number): the first half has no cite
    assert any("según el art" in u for u in v.uncovered)


def test_a_quotation_that_flattens_a_list_without_its_numerals_is_verbatim() -> None:
    quote = (
        "Son causas especiales de rescisión de la relación de trabajo, sin responsabilidad para el "
        "patrón, además de la que establece el artículo 47, las siguientes: El incumplimiento de "
        "las obligaciones a que aluden las fracciones I, II, III y VI del artículo 353-D"
    )
    assert ok(f'La norma dispone: "{quote}" [cite: {ART}].')
    assert fold_words("I. El incumplimiento; II) La violación") == "el incumplimiento; la violacion"


def test_a_changed_word_inside_the_quotation_is_still_rejected() -> None:
    altered = (
        "Son causas especiales de rescisión de la relación de trabajo, sin responsabilidad para el "
        "patrón, además de la que establece el artículo 47, las siguientes: La inobservancia de las "
        "obligaciones a que aluden las fracciones"
    )
    v = validate(f'La norma dispone: "{altered}" [cite: {ART}].', RS, workflow="dossier_builder")
    assert not v.grounded and v.bad_quotes


def test_numerals_inside_the_text_itself_are_not_stripped_from_real_words() -> None:
    assert "fracciones i, ii, iii y vi" in fold_words(
        "fracciones I, II, III y VI"
    )  # not at an enumerator slot


ANNOTATED = (
    "Artículo 291-M.- Además de las causas establecidas en el artículo 47, se podrá rescindir en los "
    "siguientes supuestos:\n\nI. Presentar documentación falsa al realizar el registro de datos;\n\n"
    "Fracción reformada DOF 30-11-2012\n\nII. Comprometer por imprudencia la seguridad de la persona "
    "usuaria.\n\n(ADICIONADO, P.O. 13 DE OCTUBRE DE 2000) Último párrafo."
)
ART2 = "MX-FED-LFT-ART291M-v2026.11"
RS2 = RetrievedSet.build([(ART2, [ANNOTATED])])


def test_a_quotation_that_skips_reform_annotations_is_still_verbatim() -> None:
    quote = (
        "se podrá rescindir en los siguientes supuestos: Presentar documentación falsa al realizar el "
        "registro de datos; Comprometer por imprudencia la seguridad de la persona usuaria"
    )
    v = validate(f'La norma dispone: "{quote}" [cite: {ART2}].', RS2, workflow="dossier_builder")
    assert v.grounded, v


def test_annotations_are_not_a_loophole_for_changed_words() -> None:
    quote = "se podrá rescindir en los siguientes supuestos: Presentar documentos apócrifos al realizar el registro"
    v = validate(f'La norma dispone: "{quote}" [cite: {ART2}].', RS2, workflow="dossier_builder")
    assert not v.grounded and v.bad_quotes


def test_only_annotation_shaped_text_is_dropped() -> None:
    from app.validation.validator import fold_words

    assert "reformado" in fold_words(
        "El texto reformado por el legislador es claro"
    )  # a normal sentence
    assert fold_words("Fracción reformada DOF 30-11-2012") == ""
