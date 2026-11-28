"""ADR-038 (B): real model outputs, replayed through today's validator, keep their verdicts.

The fixture holds attempts of the end-to-end case E2E-LAB-01 (fictional; gemma4:26b, 2026-11-17)
that can be replayed faithfully from the stored evidence. A validator change that flips one of
these verdicts must be deliberate: re-extract the fixture and say why in the PR.
"""

from __future__ import annotations

from pathlib import Path

from evals.replay import _workflow, extract_e2e, read, record, replay

FIXTURE = Path(__file__).parent / "replay" / "e2e_lab01.jsonl"


def test_real_outputs_keep_their_recorded_verdicts() -> None:
    records = read(FIXTURE)
    assert len(records) >= 8
    assert {r["recorded"]["grounded"] for r in records} == {True, False}  # both kinds are covered
    assert replay(records) == []


def test_a_changed_verdict_is_reported_not_hidden() -> None:
    cite = "MX-FED-LFT-ART47-v1"
    texts = {cite: ["II. Incurrir el trabajador en faltas de probidad u honradez."]}
    ok = record("x#1", "t", "conversar", f"El patrón puede rescindir por faltas de probidad [cite: {cite}].",
                texts, {"grounded": True, "invalid": []})  # fmt: skip
    assert replay([ok]) == []
    flipped = {**ok, "recorded": {**ok["recorded"], "grounded": False}}
    [diff] = replay([flipped])
    assert diff["recorded"]["grounded"] is False and diff["now"]["grounded"] is True


def test_e2e_extraction_skips_what_it_cannot_replay_faithfully() -> None:
    run = {"steps": {"chat": [{
        "id": "C1", "evidence": [{"cite_id": "A", "unit_text": "t", "article_text": "t"}],
        "trace": {"prompt_versions": ["core/anti_hallucination@v1", "workflow/conversar@v1"],
                  "attempts": [
                      {"output": "x", "verdict": {"grounded": True, "cited": ["A"], "invalid": []}},
                      {"output": "y", "verdict": {"grounded": True, "cited": ["B"], "invalid": []}},
                      {"output": "z", "verdict": {"grounded": False, "cited": [], "invalid": []}},
                  ]}}]}}  # fmt: skip
    records, skipped = extract_e2e(run, "run.json")
    assert [r["id"] for r in records] == ["C1#1"] and skipped == 2
    assert _workflow(["workflow/argumentar@v2"]) == "argumentar" and _workflow(["x/y@v1"]) is None
