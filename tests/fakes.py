"""Deterministic test doubles shared across suites (style.md): no provider is ever called."""

from __future__ import annotations

import hashlib
import math

from app.db.models import EMBEDDING_DIM


def fake_embedding(text: str, dim: int = EMBEDDING_DIM) -> list[float]:
    """Hashed bag-of-words, L2-normalized: identical text -> identical vector, shared words ->
    nearby vectors. Stands in for the real embedder (Protocol `Embedder`, WS0.T06)."""
    vec = [0.0] * dim
    for word in text.lower().split():
        h = int(hashlib.md5(word.encode("utf-8")).hexdigest(), 16)
        vec[h % dim] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def vector_literal(vec: list[float]) -> str:
    """pgvector text form, for raw-SQL tests."""
    return "[" + ",".join(f"{v:.6f}" for v in vec) + "]"
