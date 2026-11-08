"""What the REAL local model needed from the regeneration turn (found in the traces):
the previous answer in front of it, a minimal-edit instruction, and `[...]` as an omission mark."""

from __future__ import annotations

from app.validation.loop import build_feedback, generate_validated
from app.validation.types import RetrievedSet, ValidationConfig
from app.validation.validator import validate
from tests.fakes import FakeLLM

ART = "MX-FED-LFT-ART123-v1"
TEXT = (
    "El patrono que despida a un obrero sin causa justificada, por haber ingresado a una asociación "
    "o sindicato, estará obligado, a elección del trabajador, a cumplir el contrato o a indemnizarlo "
    "con el importe de tres meses de salario."
)
RS = RetrievedSet.build([(ART, [TEXT])])
BAD = f"El patrono debe indemnizar siempre en todos los casos sin excepción alguna [cite: {ART}]. Y además otra cosa sin cita."
GOOD = f"El patrono debe indemnizar o cumplir el contrato [cite: {ART}]."


def run(replies: list[str]):  # type: ignore[no-untyped-def]
    llm = FakeLLM(replies)
    out = generate_validated(
        llm, "drafting-primary", system="S", user="¿Indemnización?", retrieved=RS,
        workflow="dossier_builder", tenant_id="t", max_tokens=10, regen_max_tokens=10,
        config=ValidationConfig(),
    )  # fmt: skip
    return out, llm


def test_the_correction_turn_contains_the_previous_answer_and_asks_for_a_minimal_edit() -> None:
    out, llm = run([BAD, GOOD])
    assert out.answer == GOOD and len(llm.calls) == 2
    turn = str(llm.calls[1]["user"])
    assert "¿Indemnización?" in turn  # the original question is still there
    assert BAD in turn and "Respuesta anterior" in turn  # ...and so is what must be fixed
    assert "SOLO lo que se indica" in turn and "conservando igual" in turn
    assert turn.index("Respuesta anterior") < turn.index("Corrección requerida")


def test_a_huge_previous_answer_is_clipped_in_the_correction_turn() -> None:
    huge = BAD + " relleno" * 10_000
    _, llm = run([huge, GOOD])
    assert len(str(llm.calls[1]["user"])) < 20_000


def test_the_feedback_names_each_problem_in_plain_instructions() -> None:
    fb = build_feedback(validate(BAD, RS, workflow="dossier_builder"))
    assert "sin cita ni marca" in fb and "Y además otra cosa sin cita" in fb


def test_a_bracketed_omission_mark_is_not_part_of_the_quoted_words() -> None:
    quote = "El patrono que despida a un obrero sin causa justificada [...] estará obligado, a elección del trabajador"
    assert validate(
        f'La Constitución dice: "{quote}" [cite: {ART}].', RS, workflow="dossier_builder"
    ).grounded
    for mark in ("[…]", "...", "…", "[ ... ]"):
        q = f"El patrono que despida a un obrero sin causa justificada {mark} a cumplir el contrato o a indemnizarlo"
        assert validate(f'Dice: "{q}" [cite: {ART}].', RS, workflow="dossier_builder").grounded, (
            mark
        )


def test_words_still_have_to_match_around_an_omission() -> None:
    q = "El patrono que despida a un obrero sin causa justificada [...] estará obligado a pagar siempre el doble"
    v = validate(f'Dice: "{q}" [cite: {ART}].', RS, workflow="dossier_builder")
    assert not v.grounded and v.bad_quotes
