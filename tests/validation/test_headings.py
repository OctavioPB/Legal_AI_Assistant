"""Titles written with explicit markup assert nothing (seen in the real model's output); a claim
dressed as a title, or a bold lead-in followed by a claim, is still checked."""

from __future__ import annotations

from app.validation.types import RetrievedSet
from app.validation.validator import validate

ART47 = "MX-FED-LFT-ART47-FRACX-v2025.06"
RS = RetrievedSet.build([(ART47, ["X. Tener el trabajador más de tres faltas de asistencia."])])
GOOD = f"La rescisión procede por más de tres faltas en treinta días [cite: {ART47}]."


def grounded(text: str) -> bool:
    return validate(text, RS, workflow="dossier_builder").grounded


def test_bold_only_and_markdown_heading_lines_are_titles() -> None:
    assert grounded(f"**Causas generales de rescisión sin responsabilidad para el patrón**\n{GOOD}")
    assert grounded(f"## (II) Marco normativo aplicable a la relación de trabajo\n{GOOD}")
    assert grounded(f"- **Causas especiales de rescisión para residentes médicos**:\n{GOOD}")


def test_a_bold_lead_in_followed_by_a_claim_is_still_a_claim() -> None:
    v = validate(
        f"{GOOD}\n**Importante:** el patrón siempre debe pagar una indemnización adicional al trabajador.",
        RS,
        workflow="dossier_builder",
    )
    assert not v.grounded and any("indemnización adicional" in u for u in v.uncovered)


def test_an_unmarked_short_sentence_is_not_a_title() -> None:
    v = validate(
        f"{GOOD}\nEl patrón debe pagar siempre una indemnización completa",
        RS,
        workflow="dossier_builder",
    )
    assert not v.grounded
