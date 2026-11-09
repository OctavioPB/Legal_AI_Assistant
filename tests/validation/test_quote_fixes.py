"""A quotation that fails the verbatim check gets, in the regeneration feedback, the closest EXACT
passage of the cited fragment (found by code, from the source). Seen with the real model (golden
gd2-021): "facultades de LAS que carezca" vs the law's "facultades de que carezca"; the feedback showed
the model its own quote cut at 140 characters, before the wrong word, and it repeated the error."""

from __future__ import annotations

import time

from app.validation.loop import build_feedback
from app.validation.types import RetrievedSet
from app.validation.validator import closest_passage, validate

FRAC_I = (
    "I. Engañarlo el trabajador o en su caso, el sindicato que lo hubiese propuesto o recomendado con "
    "certificados falsos o referencias en los que se atribuyan al trabajador capacidad, aptitudes o "
    "facultades de que carezca. Esta causa de rescisión dejará de tener efecto después de treinta días."
)
ID = "MX-FED-LFT-ART47-FRACI-v1"
RS = RetrievedSet.build([(ID, [FRAC_I])])
WRONG = (
    "Engañarlo el trabajador o en su caso, el sindicato que lo hubiese propuesto o recomendado con "
    "certificados falsos o referencias en los que se atribuyan al trabajador capacidad, aptitudes o "
    "facultades de las que carezca"
)


def test_the_verbatim_rule_is_unchanged_and_the_fix_is_the_source_text() -> None:
    v = validate(f'La fracción I dice: "{WRONG}" [cite: {ID}].', RS, workflow="dossier_builder")
    assert not v.grounded and len(v.bad_quotes) == 1, "a changed word still fails"
    [fix] = v.quote_fixes
    assert fix.endswith("facultades de que carezca") and fix in FRAC_I, fix
    fb = build_feedback(v)
    assert 'el fragmento dice exactamente: "' in fb and "facultades de que carezca" in fb


def test_no_fix_is_offered_for_a_quote_that_is_not_in_the_source_at_all() -> None:
    v = validate(
        f'La ley dice: "el patrón deberá pagar una compensación de veinte salarios mínimos" [cite: {ID}].',
        RS, workflow="dossier_builder",
    )  # fmt: skip
    assert v.bad_quotes and v.quote_fixes == ("",)
    assert "dice exactamente" not in build_feedback(v)


def test_a_correct_quote_needs_no_fix() -> None:
    ok = "facultades de que carezca"
    v = validate(f'Dice "certificados falsos o referencias en los que se atribuyan al trabajador capacidad, aptitudes o {ok}" [cite: {ID}].', RS, workflow="dossier_builder")  # fmt: skip
    assert v.grounded and v.quote_fixes == ()


def test_the_search_stays_fast_on_a_long_article() -> None:
    long_src = " ".join(["palabra"] * 6000) + " " + FRAC_I
    t0 = time.monotonic()
    fix = closest_passage(WRONG, [long_src])
    assert fix.endswith("facultades de que carezca")
    assert time.monotonic() - t0 < 10
