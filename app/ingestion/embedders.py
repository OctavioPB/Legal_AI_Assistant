"""Local embedder over Ollama (ADR-014): bge-m3, 1024 dimensions, 8192-token window.

`revision` is the model's content digest, so a re-pulled or replaced model is a VERSION CHANGE that
the generation guard (invariant 5) refuses to mix with existing vectors. `truncate` is always off:
an input over the window fails loudly instead of being cut silently (ADR-012).
"""

from __future__ import annotations

import math
import time

import httpx

from app.orchestration.ollama import DEFAULT_URL, model_digest
from app.orchestration.resilient import ProviderError

BATCH = 32


class OllamaEmbedder:
    def __init__(
        self,
        model: str = "bge-m3",
        *,
        base_url: str = DEFAULT_URL,
        timeout_s: float = 300.0,
        retries: int = 3,
        sleep: object = time.sleep,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.model = model
        self._http = httpx.Client(base_url=base_url, timeout=timeout_s, transport=transport)
        self.revision = model_digest(model, base_url=base_url, transport=transport)[:12]
        self._retries = retries
        self._sleep = sleep

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        last: Exception | None = None
        for attempt in range(self._retries):
            try:
                res = self._http.post(
                    "/api/embed", json={"model": self.model, "input": texts, "truncate": False}
                )
                if res.status_code >= 500:
                    raise ProviderError(f"ollama {res.status_code}: {res.text[:160]}")
                if res.status_code >= 400:
                    raise ValueError(
                        f"embedding request rejected ({res.status_code}): {res.text[:300]}"
                    )
                vectors: list[list[float]] = res.json()["embeddings"]
                return vectors
            except (httpx.TransportError, ProviderError) as exc:
                last = exc
                if attempt + 1 < self._retries:
                    self._sleep(min(2.0 * 2**attempt, 15.0))  # type: ignore[operator]
        raise ProviderError(f"embedding failed after {self._retries} attempts: {last}")

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), BATCH):
            out.extend(self._embed_batch(texts[i : i + BATCH]))
        if len(out) != len(texts):
            raise ProviderError("embedder returned a different number of vectors than texts")
        return out

    def close(self) -> None:
        self._http.close()


def cosine(a: list[float], b: list[float]) -> float:
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return sum(x * y for x, y in zip(a, b, strict=True)) / (na * nb) if na and nb else 0.0
