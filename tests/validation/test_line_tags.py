"""ADR-021: a tag in the first sentence of a line labels that whole line, and nothing else.

Seen with the real local model in Argumentar: "* **[razonamiento estratégico] Falacia de conclusión
anticipada:** Usted da por sentado ... No obstante, la ausencia del aviso es ..." was refused because
only the first sentence counted as tagged."""

from __future__ import annotations

from app.validation.types import RetrievedSet
from app.validation.validator import STRATEGIC_TAG, validate

ART = "MX-FED-LFT-ART47-CAPUT-v2026.11"
RS = RetrievedSet.build(
    [(ART, ["Artículo 47.- El patrón que despida a un trabajador deberá darle aviso escrito."])]
)
CITED = f"El patrón que despida a un trabajador deberá darle aviso escrito de la rescisión [cite: {ART}]."


def check(text: str, workflow: str = "adversarial"):  # type: ignore[no-untyped-def]
    return validate(f"{CITED}\n\n{text}", RS, workflow=workflow)


def test_a_tag_at_the_start_of_a_line_covers_the_whole_line() -> None:
    v = check(
        f"* **{STRATEGIC_TAG} Falacia de conclusión anticipada:** Usted da por sentado que el despido "
        "es injustificado por la sola omisión del aviso. No obstante, la ausencia del aviso es una "
        "presunción que admite prueba en contrario. Su estrategia ignora que el patrón puede acreditar "
        "la causa justa con otros medios."
    )
    assert v.grounded and not v.uncovered


def test_own_analysis_at_the_start_of_a_line_covers_it_too() -> None:
    v = check(
        "**Respuesta:** [análisis propio] Si el patrón no entregó el aviso, la presunción opera a favor "
        "de la trabajadora. Por ello conviene ofrecer prueba testimonial del despido verbal.",
        workflow="dossier_builder",
    )
    assert v.grounded and not v.uncovered


def test_the_line_label_ends_with_the_line() -> None:
    v = check(
        f"{STRATEGIC_TAG} La contraparte negará el despido y alegará abandono del empleo.\n"
        "La reinstalación procede siempre que el trabajador tenga más de un año de antigüedad."
    )
    assert v.uncovered == (
        "La reinstalación procede siempre que el trabajador tenga más de un año de antigüedad.",
    )


def test_a_tag_later_in_the_line_covers_only_its_own_sentence() -> None:
    v = check(
        "La reinstalación procede siempre que el trabajador tenga más de un año de antigüedad. "
        f"{STRATEGIC_TAG} Conviene ofrecer la prueba testimonial del despido verbal."
    )
    assert any(u.startswith("La reinstalación procede") for u in v.uncovered)


def test_a_strategic_tag_outside_the_strategic_workflows_covers_nothing() -> None:
    v = check(
        f"{STRATEGIC_TAG} La contraparte negará el despido. Además el patrón debe pagar tres meses de "
        "salario al trabajador despedido.",
        workflow="dossier_builder",
    )
    assert not v.grounded and v.uncovered


def test_a_cite_inside_a_tagged_line_is_still_validated() -> None:
    v = check(
        f"{STRATEGIC_TAG} La contraparte invocará otra norma. Esto dice la ley [cite: MX-FED-INVENTADO-v1]."
    )
    assert not v.grounded and v.invalid == ("MX-FED-INVENTADO-v1",)
