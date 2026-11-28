"""ADR-048: convex fusion of min-max normalised arm scores (pure function)."""

from __future__ import annotations

import pytest

from app.rag.fusion import convex

SEM = [("A", 0.90), ("B", 0.80), ("C", 0.50)]
LEX = [("C", 4.0), ("D", 2.0), ("A", 0.0)]


def test_scores_are_normalised_per_arm_and_weighted() -> None:
    out = dict(convex([SEM, LEX], weights=(0.5, 0.5), top=10))
    # A: sem 1.0, lex 0.0 -> 0.5 ; C: sem 0.0, lex 1.0 -> 0.5 ; B: sem 0.75 -> 0.375 ; D: lex 0.5 -> 0.25
    assert out == pytest.approx({"A": 0.5, "C": 0.5, "B": 0.375, "D": 0.25})


def test_alpha_one_is_the_semantic_order_and_zero_the_lexical_one() -> None:
    assert [c for c, _ in convex([SEM, LEX], weights=(1.0, 0.0), top=3)] == ["A", "B", "C"]
    assert [c for c, _ in convex([SEM, LEX], weights=(0.0, 1.0), top=2)] == ["C", "D"]


def test_ties_break_by_cite_id_and_the_result_is_reproducible() -> None:
    first = convex([SEM, LEX], weights=(0.5, 0.5), top=4)
    assert first == convex([list(reversed(SEM)), list(reversed(LEX))], weights=(0.5, 0.5), top=4)
    assert [c for c, _ in first][:2] == ["A", "C"]  # tied at 0.5: A before C


def test_an_arm_with_one_score_or_none_does_not_divide_by_zero() -> None:
    assert dict(convex([[("A", 0.3)], []], weights=(0.5, 0.5), top=5)) == {"A": 0.5}


def test_bad_arguments_are_refused() -> None:
    with pytest.raises(ValueError):
        convex([SEM], weights=(0.5, 0.5), top=5)
    with pytest.raises(ValueError):
        convex([SEM, LEX], weights=(-0.1, 1.1), top=5)
