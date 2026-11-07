"""Deterministic test doubles shared across suites (style.md): no provider is ever called."""

from __future__ import annotations

import hashlib
import math

from app.db.models import EMBEDDING_DIM
from app.orchestration.aliases import AliasName
from app.orchestration.client import LLMResult


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


class FakeEmbedder:
    """Deterministic `Embedder`/`QueryEmbedder` (hashed bag-of-words); counts calls."""

    def __init__(self, model: str = "fake-embed", revision: str = "r1") -> None:
        self.model, self.revision, self.calls = model, revision, 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        return [fake_embedding(t) for t in texts]


class FakeReranker:
    """Score = fraction of query words (accent/case folded) present in the text. Deterministic."""

    name = "fake-reranker"

    def __init__(self) -> None:
        self.calls = 0

    @staticmethod
    def _words(s: str) -> set[str]:
        import unicodedata

        folded = unicodedata.normalize("NFKD", s.lower())
        return {w for w in "".join(c for c in folded if not unicodedata.combining(c)).split() if w}

    def score(self, query: str, texts: list[str]) -> list[float]:
        self.calls += 1
        q = self._words(query)
        return [len(q & self._words(t)) / len(q) if q else 0.0 for t in texts]


class FakeLLM:
    """Scripted `LLMClient`: queued replies, records every call, never touches a network."""

    def __init__(self, replies: list[str] | None = None, model_id: str = "fake-model-1") -> None:
        self.replies = list(replies or ["<insuficiente></insuficiente>"])
        self.model_id = model_id
        self.calls: list[dict[str, object]] = []

    def complete(
        self, alias: AliasName, *, system: str, user: str, max_tokens: int, tenant_id: str
    ) -> LLMResult:
        self.calls.append(
            {
                "alias": alias,
                "system": system,
                "user": user,
                "max_tokens": max_tokens,
                "tenant": tenant_id,
            }
        )
        text = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return LLMResult(text=text, alias=alias, resolved_model_id=self.model_id)
