"""Frozen retrieval objects (layers never exchange loose dicts)."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict


class Hit(BaseModel):
    model_config = ConfigDict(frozen=True)

    chunk_id: uuid.UUID
    cite_id: str
    citation_string: str
    document_id: uuid.UUID
    node_id: uuid.UUID
    text: str
    breadcrumb: str
    score: float  # arm score / fused score / rerank score depending on the stage
    rank: int  # 1-based position within the stage that produced it


class Refusal(BaseModel):
    """System-emitted refusal (ADR-005, ADR-007): produced by code, never by model prose."""

    model_config = ConfigDict(frozen=True)

    reason_key: Literal["retrieval.no_hits", "retrieval.below_threshold"]
    best_score: float | None
    tau: float
    fragments_above_tau: int
    block: str  # the exact machine-parseable <insuficiente>...</insuficiente>


class Fragment(BaseModel):
    model_config = ConfigDict(frozen=True)

    cite_id: str  # first hit of the deduped parent
    also_cite_ids: tuple[str, ...]
    citation_string: str
    text: str
    expanded: bool  # True: the whole parent article; False: the chunk text alone
    tokens: int
