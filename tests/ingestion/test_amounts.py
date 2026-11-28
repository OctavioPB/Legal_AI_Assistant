"""ADR-038 (D): an amount written as a figure and in words must agree; code compares them."""

from __future__ import annotations

import pytest

from app.ingestion.amounts import mismatches, words_to_int


@pytest.mark.parametrize(
    ("words", "n"),
    [
        ("QUINCE MIL", 15_000),
        ("UN MILLÓN DOSCIENTOS MIL", 1_200_000),
        ("DOS MILLONES QUINIENTOS MIL", 2_500_000),
        ("DOSCIENTOS CINCUENTA Y TRES MIL CUATROCIENTOS DOCE", 253_412),
        ("MIL QUINIENTOS", 1_500),
        ("CIEN", 100),
        ("CIENTO UNO", 101),
        ("veintiún mil", 21_000),
        ("SETECIENTOS NOVENTA Y NUEVE", 799),
    ],
)
def test_spanish_amounts_in_words(words: str, n: int) -> None:
    assert words_to_int(words) == n


def test_words_it_cannot_read_are_not_a_number() -> None:
    assert words_to_int("QUINCE MILLARDOS") is None
    assert words_to_int("") is None


def test_a_figure_that_disagrees_with_its_words_is_reported() -> None:
    text = (
        "La renta mensual será de $15,000.00 (QUINCE MIL PESOS 00/100 M.N.) y la pena de "
        "$16,000.00 (QUINCE MIL PESOS 00/100 M.N.); depósito de $1,200,000.00 (UN MILLÓN "
        "DOSCIENTOS MIL PESOS 00/100 M.N.)."
    )
    [bad] = mismatches(text)
    assert (bad.figure, bad.in_words) == (16_000, 15_000)
    assert bad.text.startswith("$16,000.00 (QUINCE MIL PESOS")


def test_amounts_without_the_twice_written_form_or_unreadable_words_are_left_alone() -> None:
    assert mismatches("Pagará $15,000.00 mensuales.") == []
    assert mismatches("Pagará $15,000.00 (lo pactado en la cláusula 00/100).") == []
    assert mismatches("$15,000 (QUINCE MIL PESOS 00/100 M.N.)") == []  # no cents in the figure
