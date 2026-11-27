"""ADR-041: the retrieval profiler is eval tooling and is tested like code."""

from __future__ import annotations

from evals.profile_retrieval import TimedEmbedder, summarize


class _Inner:
    model, revision = "m", "r"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]


def test_the_timed_embedder_counts_every_call_and_passes_vectors_through() -> None:
    emb = TimedEmbedder(_Inner())
    assert emb.embed(["a", "b"]) == [[1.0, 0.0], [1.0, 0.0]]
    emb.embed(["c"])
    assert [n for n, _ in emb.calls] == [2, 1] and (emb.model, emb.revision) == ("m", "r")


def test_the_summary_reports_median_p95_and_max_per_stage() -> None:
    rows = [{"rerank_s": float(i), "embed_calls": 2} for i in range(1, 21)]
    out = summarize(rows)  # type: ignore[arg-type]
    assert out["rerank_s"] == {"p50": 10.5, "p95": 19.0, "max": 20.0}
    assert out["embed_calls"]["max"] == 2.0
