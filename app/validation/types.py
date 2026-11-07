"""Frozen types of the citation validator (rag.md "Citation validator")."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.settings import Settings


class ValidationConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    quote_min_words: int = 5
    coverage_min_words: int = 6
    max_retries: int = 1
    max_answer_chars: int = 200_000  # a runaway output is refused before the heavier checks

    @classmethod
    def from_settings(cls, s: Settings) -> ValidationConfig:
        return cls(
            quote_min_words=s.validator_quote_min_words,
            coverage_min_words=s.validator_coverage_min_words,
            max_retries=s.validator_max_retries,
        )


class RetrievedSet(BaseModel):
    """THIS query's retrieved cite_ids and every text a cite may legitimately quote from.

    A model can only cite what this query retrieved: a valid chunk from another query is still a
    hallucinated source. `texts[cite]` holds the chunk text and, when parent expansion offered
    the whole article, that text too.
    """

    model_config = ConfigDict(frozen=True)

    texts: Mapping[str, tuple[str, ...]]

    @classmethod
    def build(cls, entries: Iterable[tuple[str, Iterable[str]]]) -> RetrievedSet:
        merged: dict[str, list[str]] = {}
        for cite, texts in entries:
            bucket = merged.setdefault(cite, [])
            bucket.extend(t for t in texts if t not in bucket)
        return cls(texts={c: tuple(t) for c, t in merged.items()})

    @property
    def ids(self) -> frozenset[str]:
        return frozenset(self.texts)


class Verdict(BaseModel):
    model_config = ConfigDict(frozen=True)

    cited: tuple[str, ...]  # valid, deduped in order of first appearance
    invalid: tuple[str, ...]  # not in this query's retrieved set
    placeholders: tuple[str, ...]  # copied example/template markers
    refused: bool  # the model declined: a block and nothing grounded around it
    gaps: int = 0  # blocks used as section gaps inside an otherwise grounded answer
    prose_refusal: bool  # reads as a refusal but without the block: a format violation
    uncovered: tuple[str, ...]  # legal-looking sentences with neither a cite nor an allowed tag
    bad_quotes: tuple[str, ...]  # quoted statute text not found in the cited fragment
    analysis_share: float  # share of sentences carried only by [análisis propio] (monitoring)
    grounded: bool

    @property
    def problems(self) -> bool:
        return bool(self.invalid or self.placeholders or self.uncovered or self.bad_quotes)


RefusalReason = Literal[
    "validation.model_declined",  # the model emitted the block: its words are discarded
    "validation.format_violation",  # refusal in prose, without the block
    "validation.unsupported",  # still failing after the allowed regeneration(s)
]


class ValidationRefusal(BaseModel):
    """System-written refusal after generation (ADR-007); the model never writes its text."""

    model_config = ConfigDict(frozen=True)

    reason_key: RefusalReason
    block: str
    verdicts: tuple[Verdict, ...]
