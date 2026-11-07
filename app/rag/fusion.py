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
