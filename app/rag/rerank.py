"""Rerankers (WS3.T03). Baseline: dense re-scoring with the same local embedder.

`DenseReranker` embeds the query and each candidate and scores by cosine similarity. It is the
no-extra-download baseline; a cross-encoder replaces it ONLY if the golden suite shows it earns its
place (CLAUDE.md: measure each piece; Library-RAG's CPU reranker was 20x slower for no recall gain).
"""

from __future__ import annotations

from app.ingestion.embedders import cosine
from app.rag.search import QueryEmbedder


class DenseReranker:
    name = "bge-m3-dense-cosine"

    def __init__(self, embedder: QueryEmbedder) -> None:
        self._embedder = embedder

    def score(self, query: str, texts: list[str]) -> list[float]:
        if not texts:
            return []
        vectors = self._embedder.embed([query, *texts])
        q, rest = vectors[0], vectors[1:]
        return [cosine(q, v) for v in rest]


class CrossEncoderReranker:
    """Local cross-encoder (bge-reranker-v2-m3 by default): reads query and passage TOGETHER, so
    its score separates "answers the question" from "shares vocabulary" far better than a cosine.
    Runs on CPU in this setup (the installed torch has no CUDA); scores are sigmoid(logit) in 0..1.
    """

    def __init__(
        self,
        repo: str = "BAAI/bge-reranker-v2-m3",
        *,
        max_length: int = 512,
        batch_size: int = 8,
        threads: int | None = None,
    ) -> None:
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        if threads:
            torch.set_num_threads(threads)
        self._torch = torch
        self._tok = AutoTokenizer.from_pretrained(repo)
        self._model = AutoModelForSequenceClassification.from_pretrained(repo).eval()
        self._max_length, self._batch = max_length, batch_size
        self.name = f"{repo.split('/')[-1]}"

    def score(self, query: str, texts: list[str]) -> list[float]:
        out: list[float] = []
        with self._torch.inference_mode():
            for i in range(0, len(texts), self._batch):
                batch = texts[i : i + self._batch]
                enc = self._tok(
                    [query] * len(batch), batch, padding=True, truncation="only_second",
                    max_length=self._max_length, return_tensors="pt",
                )  # fmt: skip
                logits = self._model(**enc).logits.view(-1)
                out.extend(float(x) for x in self._torch.sigmoid(logits))
        return out
