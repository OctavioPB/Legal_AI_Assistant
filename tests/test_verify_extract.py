"""The deterministic reference grammar of the brief verifier (ADR-019): no database, no model."""

from __future__ import annotations

import pytest

from app.corpus.verify import extract, fold_keep_length, human_ref, stem


def refs(text_: str) -> list[tuple[str, str | None]]:
    return [(r.resolution, stem(r)) for r in extract(text_)]


def test_lists_fracciones_and_the_law_named_after_them() -> None:
    assert refs(
        "Con fundamento en los artículos 47, fracción II, y 48 de la Ley Federal del Trabajo."
    ) == [
        ("named", "MX-FED-LFT-ART47-FRACII"),
        ("named", "MX-FED-LFT-ART48"),
    ]
    assert refs("arts. 47 al 49 LFT") == [
        ("named", "MX-FED-LFT-ART47"), ("named", "MX-FED-LFT-ART48"), ("named", "MX-FED-LFT-ART49"),
    ]  # fmt: skip
    assert refs("artículo 47, fracciones I y II, de la LFT") == [
        ("named", "MX-FED-LFT-ART47-FRACI"), ("named", "MX-FED-LFT-ART47-FRACII"),
    ]  # fmt: skip


def test_apartados_bis_and_lettered_articles() -> None:
    assert refs("art. 123, apartado A, fracción XXII, de la Constitución Política de los Estados "
                "Unidos Mexicanos") == [("named", "MX-FED-CPEUM-ART123-APARTADOA-FRACXXII")]  # fmt: skip
    assert refs("artículo 3 Bis de la LFT") == [("named", "MX-FED-LFT-ART3BIS")]
    assert refs("artículo 15-C de la LFT") == [("named", "MX-FED-LFT-ART15C")]
    assert refs("artículo 47 a la fecha, según la LFT") == [("named", "MX-FED-LFT-ART47")]


def test_the_law_comes_from_context_only_when_it_is_not_named() -> None:
    got = refs("El artículo 47 de la LFT es claro. El artículo 48 prevé la reinstalación.")
    assert got == [("named", "MX-FED-LFT-ART47"), ("contextual", "MX-FED-LFT-ART48")]
    assert refs("La citada ley, en su artículo 48.") == [("contextual", None)]  # no law yet


def test_laws_outside_the_corpus_and_ambiguous_names_are_never_guessed() -> None:
    assert refs("artículo 21 de la Ley del Seguro Social") == [("outside", None)]
    assert refs("artículo 5 de la Ley General de Salud") == [("outside", None)]
    assert refs("artículo 1910 del Código Civil") == [("ambiguous", None)]
    assert refs("artículo 1910 del Código Civil Federal") == [("named", "MX-FED-CCF-ART1910")]
    assert refs("artículo 2 del Código Civil para el Estado de Nuevo León") == [
        ("named", "MX-NL-CCNL-ART2")
    ]


def test_us_sections() -> None:
    assert refs("See Cal. Lab. Code § 201(c)(1).") == [("named", "US-CA-LAB-SEC201-SUBC-SUB1")]
    assert refs("Under section 139.2 of the Labor Code") == [("named", "US-CA-LAB-SEC139P2")]


def test_quotations_belong_to_their_own_sentence() -> None:
    text_ = ('El artículo 47 de la LFT dispone: "Son causas de rescisión de la relación". '
             'El artículo 48 de la LFT no se cita textualmente.')  # fmt: skip
    a, b = extract(text_)
    assert a.quotes == ("Son causas de rescisión de la relación",) and b.quotes == ()
    # a colon announces the quotation in the next sentence
    c = extract(
        'Lo prevé el artículo 48 de la LFT:\n"El trabajador podrá solicitar la reinstalación".'
    )
    assert c[0].quotes == ("El trabajador podrá solicitar la reinstalación",)


def test_spans_point_at_the_reference_in_the_original_text() -> None:
    text_ = "Según el ARTÍCULO 47 de la Ley Federal del Trabajo"
    [r] = extract(text_)
    assert text_[r.span[0] : r.span[1]] == "ARTÍCULO 47"
    assert len(fold_keep_length(text_)) == len(text_)
    assert human_ref(r) == "art. 47, Ley Federal del Trabajo"


@pytest.mark.parametrize("noise", ["", "Sin referencias aquí.", "El art. de fe.", "§ sin número"])
def test_text_without_references(noise: str) -> None:
    assert extract(noise) == []
