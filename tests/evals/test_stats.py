"""ADR-032: exact statistics for the eval reports, checked against published values."""

from __future__ import annotations

import pytest

from evals.stats import clopper_pearson, interval, mcnemar_exact, zero_failure_n


@pytest.mark.parametrize(
    ("k", "n", "lower", "upper"),
    [
        # the project's own headline numbers (computed with scipy.stats.beta on 2026-11-26)
        (5, 5, 0.4782, 1.0),
        (1, 20, 0.0013, 0.2487),
        (0, 5, 0.0, 0.5218),
        (0, 25, 0.0, 0.1372),
        (5, 8, 0.2449, 0.9148),
        # textbook value: 7 of 10 -> (0.3475, 0.9333)
        (7, 10, 0.3475, 0.9333),
    ],
)
def test_clopper_pearson_matches_the_exact_beta_quantiles(
    k: int, n: int, lower: float, upper: float
) -> None:
    assert clopper_pearson(k, n) == pytest.approx((lower, upper), abs=1e-4)


def test_mcnemar_uses_only_the_flipped_cases() -> None:
    assert mcnemar_exact(0, 0) == 1.0
    assert mcnemar_exact(0, 3) == 0.25  # 2 * 0.5**3
    assert mcnemar_exact(0, 5) == 0.0625
    assert mcnemar_exact(0, 6) == 0.0312
    assert mcnemar_exact(2, 2) == 1.0
    assert mcnemar_exact(1, 9) == mcnemar_exact(9, 1) == 0.0215


def test_zero_failure_sample_sizes_match_the_rule_of_three() -> None:
    assert [zero_failure_n(r) for r in (0.05, 0.02, 0.01)] == [59, 149, 299]
    assert pytest.approx(3.0) == 300 * 0.01  # rule of three: 3/n, n = 300 for 1 %


def test_bad_inputs_are_refused_not_guessed() -> None:
    with pytest.raises(ValueError):
        clopper_pearson(3, 2)
    with pytest.raises(ValueError):
        clopper_pearson(0, 0)
    assert interval(0, 0) is None
