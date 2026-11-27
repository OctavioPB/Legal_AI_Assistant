"""Regeneration flow with a mocked LLM; the refusal text is always written by the system."""

from __future__ import annotations

from app.rag.gate import refusal_block
from app.validation.loop import build_feedback, generate_validated
from app.validation.types import RetrievedSet, ValidationConfig
from app.validation.validator import validate
from tests.fakes import FakeLLM

ART47 = "MX-FED-LFT-ART47-FRACX-v2025.06"
TEXT47 = "X. Tener el trabajador más de tres faltas de asistencia en un período de treinta días."
RS = RetrievedSet.build([(ART47, [TEXT47])])
GOOD = f"La rescisión procede por más de tres faltas en treinta días [cite: {ART47}]."
INVENTED = (
    "La rescisión procede por más de tres faltas en treinta días [cite: MX-FED-LFT-ART999-v1]."
)


def run(replies: list[str], retries: int = 1):  # type: ignore[no-untyped-def]
    llm = FakeLLM(replies)
    out = generate_validated(
        llm, "drafting-primary", system="SYS", user="¿Causales?", retrieved=RS,
        workflow="dossier_builder", tenant_id="t1", max_tokens=111, regen_max_tokens=222,
        config=ValidationConfig(max_retries=retries),
    )  # fmt: skip
    return out, llm


def test_grounded_first_try_makes_one_call_and_no_regeneration() -> None:
    out, llm = run([GOOD])
    assert out.answer == GOOD and out.refusal is None and out.regenerations == 0
    assert len(llm.calls) == 1 and llm.calls[0]["max_tokens"] == 111


def test_invented_cite_triggers_exactly_one_regeneration_with_feedback_and_its_own_cap() -> None:
    out, llm = run([INVENTED, GOOD])
    assert out.answer == GOOD and out.regenerations == 1 and len(out.verdicts) == 2
    first, second = llm.calls
    assert "MX-FED-LFT-ART999-v1" in str(second["user"]) and "¿Causales?" in str(second["user"])
    assert second["max_tokens"] == 222 and first["system"] == second["system"] == "SYS"
    assert out.verdicts[0].invalid and out.verdicts[1].grounded


def test_persistent_failure_ends_in_a_system_written_refusal() -> None:
    out, llm = run([INVENTED, INVENTED])
    assert out.answer is None and len(llm.calls) == 2  # bounded: 1 + max_retries
    r = out.refusal
    assert r is not None and r.reason_key == "validation.unsupported"
    assert (
        r.block
        == refusal_block("validation.unsupported")
        == "<insuficiente>validation.unsupported</insuficiente>"
    )
    assert len(r.verdicts) == 2


def test_the_models_own_refusal_text_is_discarded() -> None:
    chatty = "<insuficiente>\nNo encontré nada, pero lo que sé es que el art. 47 dice otra cosa.\n</insuficiente>"
    out, llm = run([chatty])
    assert out.answer is None and len(llm.calls) == 1
    assert out.refusal is not None and out.refusal.reason_key == "validation.model_declined"
    assert "art. 47" not in out.refusal.block and "No encontré" not in out.refusal.block


def test_refusal_in_prose_is_a_format_violation_not_an_answer() -> None:
    out, llm = run(["No encontré información suficiente en las fuentes para contestar."])
    assert out.answer is None and len(llm.calls) == 1
    assert out.refusal is not None and out.refusal.reason_key == "validation.format_violation"
    assert out.refusal.block == "<insuficiente>validation.format_violation</insuficiente>"


def test_zero_retries_refuses_at_the_first_failure() -> None:
    out, llm = run([INVENTED, GOOD], retries=0)
    assert out.refusal is not None and len(llm.calls) == 1


def test_resolved_model_id_travels_with_the_result() -> None:
    out, _ = run([GOOD])
    assert out.resolved_model_id == "fake-model-1"


def test_feedback_is_built_by_code_from_the_verdict() -> None:
    fb = build_feedback(
        validate(
            INVENTED + " Y además todo esto es siempre cierto para cualquiera.",
            RS,
            workflow="dossier_builder",
        )
    )
    assert "MX-FED-LFT-ART999-v1" in fb and "sin cita ni marca" in fb


def test_the_retry_keeps_the_first_prompt_as_an_exact_prefix_so_its_cache_is_reused() -> None:
    """ADR-043 (measured 2026-11-26): with the system prompt unchanged and the user turn only
    extended, Ollama reused the cached prefill: 0.8 s instead of 14.1 s for a 6 400-token prompt.
    Anything placed BEFORE the original user text (or a changed system prompt) would lose that."""
    _, llm = run([INVENTED, GOOD])
    first, second = llm.calls
    assert second["system"] == first["system"]
    assert str(second["user"]).startswith(str(first["user"]))
    assert len(str(second["user"])) > len(str(first["user"]))
