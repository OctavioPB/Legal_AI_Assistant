"""ADR-007 (c): the m-fragment gate's arithmetic in the tau calibration (pure functions)."""

from __future__ import annotations

from evals.calibrate_tau import auc, min_fragments_report, mth_score


def test_the_mth_score_is_the_mth_best_and_too_few_hits_cannot_pass() -> None:
    assert mth_score([0.5, 0.9, 0.7], 1) == 0.9
    assert mth_score([0.5, 0.9, 0.7], 2) == 0.7
    assert mth_score([0.9], 2) == 0.0
    assert mth_score([], 1) == 0.0


def test_a_second_fragment_can_separate_what_the_best_one_cannot() -> None:
    # covered topics have several good fragments; absent ones have one lucky near-match
    covered = [[0.80, 0.75, 0.70], [0.70, 0.68, 0.40], [0.66, 0.65, 0.60]]
    absent = [[0.72, 0.30, 0.20], [0.69, 0.35, 0.30], [0.50, 0.40, 0.30]]
    rows = {r["m"]: r for r in min_fragments_report(covered, absent, tau=0.6, ms=(1, 2))}
    assert rows[1]["auc"] < rows[2]["auc"] == 1.0
    assert rows[2]["at_current_tau"] == {"tau": 0.6, "covered_answered": 1.0, "absent_refused": 1.0}
    assert rows[1]["at_current_tau"]["absent_refused"] < 1.0


def test_no_tau_in_force_reports_only_the_separation() -> None:
    [row] = min_fragments_report([[0.9]], [[0.1]], tau=None, ms=(1,))
    assert "at_current_tau" not in row and row["auc"] == auc([0.9], [0.1]) == 1.0


def _rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for t, scores in (("a1", [0.50, 0.70]), ("a2", [0.40, 0.45]), ("a3", [0.30, 0.35])):
        rows += [{"kind": "absent", "topic": t, "best": s} for s in scores]
    for t, scores in (("c1", [0.80, 0.90]), ("c2", [0.55, 0.85])):
        rows += [{"kind": "covered", "topic": t, "best": s} for s in scores]
    return rows


def test_a_topic_is_violated_if_any_of_its_wordings_crosses_the_gate() -> None:
    from evals.calibrate_tau import topic_violations

    assert topic_violations(_rows(), 0.6, "absent") == (1, 3)  # a1's second wording passes
    assert topic_violations(_rows(), 0.6, "covered") == (1, 2)  # c2's first wording is refused


def test_the_certified_bound_is_an_exact_upper_bound_over_topics() -> None:
    from evals.calibrate_tau import certified_bounds, conformal_tau
    from evals.stats import clopper_pearson

    b = certified_bounds(_rows(), 0.6, delta=0.1)
    assert b["absent_let_through"] == {"topics": 3, "violated": 1,
                                       "upper_bound": clopper_pearson(1, 3, alpha=0.2)[1]}  # fmt: skip
    # with 3 topics even zero violations cannot certify 10 %: the guarantee is honest about n
    assert conformal_tau(_rows(), alpha=0.10, delta=0.1) is None
    assert conformal_tau(_rows(), alpha=0.60, delta=0.1)["tau"] == 0.705  # type: ignore[index]
