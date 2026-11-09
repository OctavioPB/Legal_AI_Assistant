"""The golden harness is code and is tested like code (eval-runner skill: eval tooling has bugs too)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import Engine

from evals.golden import (
    GoldenCase,
    Jurisdiction,
    case_ok,
    compare,
    gate_report,
    in_scope,
    load,
    matches,
    metrics,
    score_case,
    summarize,
)
from evals.review_sheet import build, cited_in

FED = Jurisdiction(country="MX", level="federal")
NL = Jurisdiction(country="MX", level="state", state_code="NL")
JAL = Jurisdiction(country="MX", level="state", state_code="JAL")
MTY = Jurisdiction(country="MX", level="municipal", state_code="NL", municipality="Monterrey")
GDL = Jurisdiction(country="MX", level="municipal", state_code="JAL", municipality="Guadalajara")
CA = Jurisdiction(country="US", level="state", state_code="CA")


def mk(
    expected: str = "answer", gold: tuple[str, ...] = ("G-1",), j: Jurisdiction = FED, **kw: object
) -> GoldenCase:
    return GoldenCase.model_validate({
        "id": kw.pop("id", "gd-x"), "category": kw.pop("category", "statutory_lookup"),
        "authored_by": kw.pop("authored_by", "ai-draft"), "reviewed_by": kw.pop("reviewed_by", None),
        "session": {"language": "es-MX", "jurisdiction": j.model_dump(), "persona": "labor"},
        "query": "q", "expected_behavior": expected,
        "gold_chunks": list(gold) if expected == "answer" else [], **kw,
    })  # fmt: skip


def test_prefix_patterns_and_exact_ids() -> None:
    assert matches("MX-FED-LFT-ART47-FRACII-v1", "MX-FED-LFT-ART47-FRAC*")
    assert not matches("MX-FED-LFT-ART470-v1", "MX-FED-LFT-ART47-FRAC*")
    assert matches("A-1", "A-1") and not matches("A-10", "A-1")


@pytest.mark.parametrize(
    ("sel", "row", "ok"),
    [
        (FED, FED, True), (FED, NL, False), (NL, FED, True), (NL, NL, True), (NL, JAL, False),
        (MTY, NL, True), (MTY, FED, True), (MTY, MTY, True), (MTY, GDL, False), (NL, MTY, False),
        (CA, FED, False), (FED, Jurisdiction(country="US", level="federal"), False),
    ],
)  # fmt: skip
def test_the_stacking_rule_is_restated_independently(
    sel: Jurisdiction, row: Jurisdiction, ok: bool
) -> None:
    assert in_scope(sel, row) is ok


def test_a_case_scores_leaks_distractors_and_invalid_citations() -> None:
    c = mk(gold=("G-1",), j=NL, distractor_chunks=["D-1"])
    r = score_case(
        c, ["X-1", "G-1", "D-1", "JAL-1"], {"X-1": FED, "G-1": NL, "D-1": FED, "JAL-1": JAL},
        gate_refused=False, outcome="answered", cited=["G-1", "Z-9", "G-1"], retrieved_set=["G-1", "X-1"],
    )  # fmt: skip
    assert r.gold_rank == 2 and r.leaked == ("JAL-1",) and r.distractors_in_top == ("D-1",)
    assert r.cited == ("G-1", "Z-9") and r.invalid_cited == ("Z-9",) and r.cited_gold is True
    assert not case_ok(r)  # a leak or an invalid citation is never "ok"


def test_metrics_count_each_rate_over_its_own_denominator() -> None:
    ans_ok = score_case(mk(id="a"), ["G-1"], {"G-1": FED}, gate_refused=False, outcome="answered",
                        cited=["G-1"], retrieved_set=["G-1"])  # fmt: skip
    ans_refused = score_case(mk(id="b"), [], {}, gate_refused=True, outcome="refused")
    ref_ok = score_case(mk("refuse", id="c"), [], {}, gate_refused=True, outcome="refused")
    ref_bad = score_case(mk("refuse", id="d"), ["G-1"], {"G-1": FED}, gate_refused=False,
                         outcome="answered", cited=["G-1"], retrieved_set=["G-1"])  # fmt: skip
    m = metrics([ans_ok, ans_refused, ref_ok, ref_bad])
    assert m["recall_at_k"] == 0.5  # over the 2 answer cases with gold only
    assert m["refusal_accuracy"] == 0.5 and m["false_refusal"] == 0.5
    assert m["citation_precision"] == 1.0 and m["jurisdiction_leakage"] == 0.0
    assert m["gold_citation"] == 1.0  # the refuse case that answered is not an "answer" case


def test_ai_draft_cases_never_gate_a_release() -> None:
    r = score_case(mk(), ["G-1"], {"G-1": FED}, gate_refused=False)
    s = summarize([r])
    assert s["n_gating"] == 0 and s["n_ai_draft"] == 1 and s["faithfulness"] is None
    assert gate_report(s["gating"], s["n_gating"])["status"] == "not_gating"  # type: ignore[arg-type]
    reviewed = score_case(mk(reviewed_by="lawyer:ana"), ["G-1", "JAL-1"], {"G-1": FED, "JAL-1": JAL},
                          gate_refused=False)  # fmt: skip
    s2 = summarize([reviewed])
    g = gate_report(s2["gating"], s2["n_gating"])  # type: ignore[arg-type]
    assert g["status"] == "fail" and g["jurisdiction_leakage"]["pass"] is False  # type: ignore[index]


def test_the_comparator_lists_flips_and_applies_the_noise_rule() -> None:
    def run(ok_ids: set[str]) -> dict[str, object]:
        cases = [score_case(mk(id=i), ["G-1"] if i in ok_ids else [], {"G-1": FED}, gate_refused=False)
                 for i in ("a", "b", "c", "d")]  # fmt: skip
        return {"cases": [c.model_dump() for c in cases]}

    worse = compare(run({"a", "b", "c"}), run({"a", "b"}))
    assert worse["verdict"] == "regression" and [f["id"] for f in worse["flips_worse"]] == ["c"]  # type: ignore[index]
    small = compare(run({"a"}), run({"a", "b"}))
    assert small["verdict"] == "within_noise"
    assert small["one_case_moves"]["statutory_lookup"] == 0.25  # type: ignore[index]


def test_the_case_file_is_strict(tmp_path: Path) -> None:
    good = mk().model_dump()
    p = tmp_path / "g.jsonl"
    p.write_text("\n".join(json.dumps(x) for x in (good, good)), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        load(p)
    with pytest.raises(ValidationError):
        GoldenCase.model_validate({**good, "surprise": 1})


def test_the_shipped_golden_set_is_honest_about_its_authorship() -> None:
    cases = load(Path(__file__).resolve().parents[2] / "evals" / "golden" / "golden_v2.jsonl")
    assert len(cases) >= 25 and len({c.category for c in cases}) >= 6
    assert all(c.authored_by == "ai-draft" and c.reviewed_by is None for c in cases)
    assert not any(c.gating for c in cases), (
        "ai-draft cases must not gate until a lawyer reviews them"
    )
    for c in cases:
        assert (c.expected_behavior == "answer") == bool(c.gold_chunks), c.id
    assert {c.session.language for c in cases} == {"es-MX", "en-US"}


def test_the_review_sheet_never_truncates_a_fragment() -> None:
    long_text = (
        "Artículo 999.- " + " ".join(f"palabra{i}" for i in range(1500)) + " FIN-DEL-FRAGMENTO"
    )
    r = score_case(mk(), ["G-1"], {"G-1": FED}, gate_refused=False, outcome="answered",
                   cited=["G-1"], retrieved_set=["G-1"], answer="La norma dice algo [cite: G-1]. Otra frase.",
                   texts={"G-1": [long_text]})  # fmt: skip
    refused = score_case(mk("refuse", id="r"), [], {}, gate_refused=True, outcome="refused",
                         refusal_reason="retrieval.below_tau")  # fmt: skip
    sheet = build({"run_id": "t", "mode": "full", "cases": [r.model_dump(), refused.model_dump()]})
    assert long_text in sheet and "FIN-DEL-FRAGMENTO" in sheet
    assert "retrieval.below_tau" in sheet and "¿El rechazo es correcto?" in sheet
    assert cited_in("x [cite: A; B, A] y [cite: C]") == ["A", "B", "C"]


def test_preflight_rejects_a_set_that_points_at_nothing(
    admin_engine: Engine, ingestor_engine: Engine
) -> None:
    from app.db.session import PUBLIC_CORPUS_TENANT_ID
    from evals.run import preflight
    from tests.helpers import Article, seed_document

    seed_document(ingestor_engine, PUBLIC_CORPUS_TENANT_ID, doc_type="ley",
                  jurisdiction=("MX", "federal", None, None), title="Ley",
                  articles=[Article("MX-FED-LFT-ART47-FRACII-v1", "Artículo 47", "texto")])  # fmt: skip
    cases = [
        mk(gold=("MX-FED-LFT-ART47-FRACII-v1",), id="ok", gold_alternatives=["MX-FED-LFT-ART47-*"]),
        mk(gold=("MX-FED-LFT-ART99-v1",), id="missing", distractor_chunks=["NOPE-*"]),
        mk(gold=("MX-FED-LFT-ART47-FRACII-v1",), j=Jurisdiction(country="MX", level="state", state_code="ZZ"),
           id="nojur"),
    ]  # fmt: skip
    with admin_engine.connect() as conn:
        problems = preflight(conn, cases)
    assert not any(p.startswith("ok:") for p in problems)
    assert "missing: gold MX-FED-LFT-ART99-v1 does not exist in the live index" in problems
    assert "missing: distractor prefix NOPE-* matches nothing" in problems
    assert "nojur: session jurisdiction not in the catalog" in problems


def test_must_not_contain_is_an_alarm_on_the_answer_text() -> None:
    c = mk(must_not_contain=["capacitación"])
    bad = score_case(c, ["G-1"], {"G-1": FED}, gate_refused=False, outcome="answered", cited=["G-1"],
                     retrieved_set=["G-1"], answer="Las empresas deben dar CAPACITACION [cite: G-1].")  # fmt: skip
    good = score_case(c, ["G-1"], {"G-1": FED}, gate_refused=False, outcome="answered", cited=["G-1"],
                      retrieved_set=["G-1"], answer="Los militares se rigen por sus leyes [cite: G-1].")  # fmt: skip
    assert bad.forbidden_found == ("capacitación",) and not case_ok(bad)
    assert good.forbidden_found == () and case_ok(good)
    assert metrics([bad, good])["forbidden_content"] == 0.5
