"""Refusal gate (ADR-005, ADR-007): runs AFTER rerank and BEFORE any LLM call. The refusal block
is produced here, by code; the model is never asked to decline in its own words."""

from __future__ import annotations

from collections.abc import Sequence

from app.rag.types import Hit, Refusal

REFUSAL_OPEN, REFUSAL_CLOSE = "<insuficiente>", "</insuficiente>"


def refusal_block(reason_key: str) -> str:
    """Server messages are catalog KEYS (style.md i18n); the UI renders the text."""
    return f"{REFUSAL_OPEN}{reason_key}{REFUSAL_CLOSE}"


def apply_gate(
    reranked: Sequence[Hit], *, tau: float | None, min_fragments: int = 1
) -> Refusal | None:
    """None = proceed. Proceeding needs >= `min_fragments` hits with score >= tau."""
    if tau is None:
        raise ValueError("tau is not calibrated (ADR-007): refusing to run the gate on a guess")
    if not reranked:
        return Refusal(
            reason_key="retrieval.no_hits",
            best_score=None,
            tau=tau,
            fragments_above_tau=0,
            block=refusal_block("retrieval.no_hits"),
        )
    above = sum(1 for h in reranked if h.score >= tau)
    if above >= min_fragments:
        return None
    return Refusal(
        reason_key="retrieval.below_threshold",
        best_score=max(h.score for h in reranked),
        tau=tau,
        fragments_above_tau=above,
        block=refusal_block("retrieval.below_threshold"),
    )
