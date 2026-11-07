"""Section gaps. Found with the REAL local model: gemma wrote `<insuficiente></insuficiente>` as the
content of the section for which no source was retrieved ("(III) Criterios jurisprudenciales"),
inside an otherwise well-cited answer. That is honest behaviour; treating it as a refusal of the
whole answer discarded a good answer. A block with nothing grounded around it is still a refusal.
"""

from __future__ import annotations

from app.validation.loop import generate_validated
from app.validation.types import RetrievedSet, ValidationConfig
from app.validation.validator import GAP, normalize_gaps, validate
from tests.fakes import FakeLLM

ART47 = "MX-FED-LFT-ART47-FRACX-v2025.06"
TEXT47 = "X. Tener el trabajador más de tres faltas de asistencia en un período de treinta días."
RS = RetrievedSet.build([(ART47, [TEXT47])])
GOOD = f"La rescisión procede por más de tres faltas en treinta días [cite: {ART47}]."


def test_a_block_inside_a_grounded_answer_is_a_section_gap_not_a_refusal() -> None:
    answer = (
        f"(II) Marco normativo\n{GOOD}\n\n"
        "(III) Criterios jurisprudenciales\n<insuficiente></insuficiente>\n\n"
        "(IV) Análisis\n[análisis propio]\n"
        "Valoración mía sobre la carga de la prueba en este supuesto concreto."
    )
    v = validate(answer, RS, workflow="dossier_builder")
    assert v.grounded and not v.refused and v.gaps == 1 and v.cited == (ART47,)


def test_text_the_model_puts_inside_a_gap_is_discarded_and_never_reaches_coverage() -> None:
    out, n = normalize_gaps(
        "A <insuficiente>El modelo explica con sus palabras que no hay nada</insuficiente> B "
        "<INSUFICIENTE></INSUFICIENTE> C <insuficiente>sin cerrar"
    )
    assert n == 2 and out == f"A {GAP} B {GAP} C <insuficiente>sin cerrar"
    long_gap = (
        f"{GOOD}\n<insuficiente>No se recuperó ninguna tesis ni jurisprudencia aplicable a este "
        "caso concreto</insuficiente>"
    )
    assert validate(long_gap, RS, workflow="dossier_builder").grounded


def test_a_block_with_nothing_grounded_is_still_a_refusal() -> None:
    assert validate("<insuficiente></insuficiente>", RS, workflow="dossier_builder").refused
    mixed = "Texto <insuficiente>x</insuficiente> más texto sin ninguna cita."
    assert validate(mixed, RS, workflow="dossier_builder").refused


def test_a_gap_does_not_launder_a_forged_citation() -> None:
    forged = f"{GOOD} <insuficiente></insuficiente> Y además [cite: MX-FED-FAKE-1]."
    v = validate(forged, RS, workflow="dossier_builder")
    assert not v.grounded and v.invalid == ("MX-FED-FAKE-1",) and not v.refused


def test_the_loop_returns_the_answer_with_the_gap_text_removed_and_keeps_the_raw_output() -> None:
    reply = f"{GOOD}\n<insuficiente>Aquí el modelo escribe lo que quiere</insuficiente>"
    llm = FakeLLM([reply])
    out = generate_validated(
        llm, "drafting-primary", system="S", user="u", retrieved=RS, workflow="dossier_builder",
        tenant_id="t", max_tokens=10, regen_max_tokens=10, config=ValidationConfig(),
    )  # fmt: skip
    assert out.refusal is None and len(llm.calls) == 1 and out.verdicts[0].gaps == 1
    assert out.answer is not None and "lo que quiere" not in out.answer
    assert out.answer.endswith(GAP)
    assert out.attempts[0].output == reply  # the trace keeps what the model really said


def test_a_tag_alone_on_its_line_labels_the_following_paragraph_as_the_real_model_writes_it() -> (
    None
):
    analysis = "Es fundamental observar que la legislación distingue entre causales generales y especiales."
    ok = f"{GOOD}\n\n(IV) Análisis\n[análisis propio]\n{analysis}"
    assert validate(ok, RS, workflow="dossier_builder").grounded
    # the label ends at a blank line: a later untagged legal claim is NOT covered by it
    leak = f"{ok}\n\nAdemás el patrón siempre debe pagar una indemnización adicional al trabajador despedido."
    v = validate(leak, RS, workflow="dossier_builder")
    assert not v.grounded and any("indemnización adicional" in u for u in v.uncovered)
    # and a new section heading ends it too
    heading = f"{ok}\n(V) Fuentes\nAdemás el patrón siempre debe pagar una indemnización adicional al trabajador."
    assert not validate(heading, RS, workflow="dossier_builder").grounded


def test_the_strategic_label_line_only_counts_in_the_adversarial_workflow() -> None:
    text = f"{GOOD}\n[razonamiento estratégico]\nTu teoría presume hechos que nunca se probaron en el expediente."
    assert validate(text, RS, workflow="adversarial").grounded
    assert not validate(text, RS, workflow="dossier_builder").grounded
