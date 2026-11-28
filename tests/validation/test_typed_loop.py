"""ADR-033 (B): the regeneration loop with typed records, against the scripted fake model."""

from __future__ import annotations

import json

from app.validation.loop import generate_validated
from app.validation.records import SCHEMA
from app.validation.types import RetrievedSet
from tests.fakes import FakeLLM

ART = "MX-FED-LFT-ART47-FRACII-v1"
RS = RetrievedSet.build([(ART, ["II. Incurrir el trabajador en faltas de probidad u honradez."])])
CITAS = {ART: "Ley Federal del Trabajo, art. 47, fracc. II"}


def records(*recs: tuple[str, str, str, list[str]]) -> str:
    return json.dumps(
        {"records": [{"section": s, "kind": k, "text": t, "cites": c} for s, k, t, c in recs]},
        ensure_ascii=False,
    )


GOOD = records(
    ("I", "analysis", "Se consulta si la falta de probidad permite rescindir.", []),
    ("II", "framing", "A continuación se resumen los puntos principales.", []),
    ("II", "law", "El patrón puede rescindir por faltas de probidad u honradez.", [ART]),
)
BAD_FRAMING = records(
    ("I", "analysis", "Se consulta si la falta de probidad permite rescindir.", []),
    ("II", "framing", "El patrón puede rescindir la relación sin responsabilidad alguna.", []),
    ("II", "law", "El patrón puede rescindir por faltas de probidad u honradez.", [ART]),
)


def run(llm: FakeLLM):  # type: ignore[no-untyped-def]
    return generate_validated(
        llm, "drafting-primary", system="s", user="u", retrieved=RS, workflow="dossier_builder",
        tenant_id="t", max_tokens=100, regen_max_tokens=100, typed_citations=CITAS,
    )  # fmt: skip


def test_a_valid_record_list_is_rendered_validated_and_answered_with_the_schema_sent() -> None:
    llm = FakeLLM([GOOD])
    res = run(llm)
    assert res.answer is not None and res.regenerations == 0
    assert f"u honradez. [cite: {ART}]" in res.answer
    assert res.answer.rstrip().endswith(f"* {ART}: {CITAS[ART]}")  # section (V) written by code
    assert llm.calls[0]["schema"] == SCHEMA
    assert res.attempts[0].raw_output == GOOD and res.attempts[0].output == res.answer


def test_a_legal_claim_labelled_framing_is_regenerated_with_feedback_then_answered() -> None:
    llm = FakeLLM([BAD_FRAMING, GOOD])
    res = run(llm)
    assert res.answer is not None and res.regenerations == 1
    assert res.verdicts[0].record_problems and not res.verdicts[0].grounded
    assert "no cumplen las reglas de su tipo" in str(llm.calls[1]["user"])


def test_broken_json_twice_ends_in_the_system_refusal() -> None:
    res = run(FakeLLM(["no es json", "tampoco"]))
    assert res.answer is None and res.refusal is not None
    assert res.refusal.reason_key == "validation.unsupported"
    assert "formato de registros" in res.verdicts[0].record_problems[0]


def test_an_empty_record_list_is_the_model_declining() -> None:
    res = run(FakeLLM([json.dumps({"records": []})]))
    assert res.answer is None and res.refusal is not None
    assert res.refusal.reason_key == "validation.model_declined"


def test_free_text_is_untouched_when_typed_is_off() -> None:
    llm = FakeLLM([f"El patrón puede rescindir por faltas de probidad [cite: {ART}]."])
    res = generate_validated(
        llm, "drafting-primary", system="s", user="u", retrieved=RS, workflow="dossier_builder",
        tenant_id="t", max_tokens=100, regen_max_tokens=100,
    )  # fmt: skip
    assert res.answer is not None and llm.calls[0]["schema"] is None
    assert res.attempts[0].raw_output is None
