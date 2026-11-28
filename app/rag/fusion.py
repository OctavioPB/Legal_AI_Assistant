"""Reciprocal Rank Fusion. Depends only on RANKS, never on raw scores; ties break by cite_id so a
run is reproducible (rag.md determinism)."""

from __future__ import annotations

from collections.abc import Sequence


def rrf(rankings: Sequence[Sequence[str]], *, k: int, top: int) -> list[tuple[str, float]]:
    """`rankings`: each arm's cite_ids, best first. Returns [(cite_id, fused_score)], best first."""
    if k < 1 or top < 1:
        raise ValueError("k and top must be >= 1")
    score: dict[str, float] = {}
    for ranking in rankings:
        seen: set[str] = set()
        for pos, cite in enumerate(ranking, 1):
            if cite in seen:  # a repeat inside one arm never double counts
                continue
            seen.add(cite)
            score[cite] = score.get(cite, 0.0) + 1.0 / (k + pos)
    ordered = sorted(score.items(), key=lambda kv: (-kv[1], kv[0]))
    return ordered[:top]


def convex(
    arms: Sequence[Sequence[tuple[str, float]]], *, weights: Sequence[float], top: int
) -> list[tuple[str, float]]:
    """ADR-048 (Bruch, Gai and Ingber 2023): a weighted sum of each arm's min-max normalised score.
    A candidate an arm did not return scores 0 there. Unlike RRF this reads raw scores; ties still
    break by cite_id, so a run stays reproducible."""
    if top < 1 or len(arms) != len(weights) or any(w < 0 for w in weights):
        raise ValueError("top must be >= 1 and there must be one non-negative weight per arm")
    score: dict[str, float] = {}
    for arm, w in zip(arms, weights, strict=True):
        best: dict[str, float] = {}
        for cite, s in arm:  # a repeat inside one arm keeps its best score
            best[cite] = max(s, best.get(cite, s))
        if not best:
            continue
        lo, hi = min(best.values()), max(best.values())
        for cite, s in best.items():
            norm = (s - lo) / (hi - lo) if hi > lo else 1.0
            score[cite] = score.get(cite, 0.0) + w * norm
    ordered = sorted(score.items(), key=lambda kv: (-kv[1], kv[0]))
    return ordered[:top]
