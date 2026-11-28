"""ADR-041: where the retrieval time goes, stage by stage, over the golden queries.

    uv run python -m evals.profile_retrieval [--with-llm] [--repeat 2] [--file PATH]

Stages are timed separately, in the order `retrieve()` runs them: query embedding (the semantic
arm's own call), the semantic arm's SQL, the lexical arm, the explicit-reference resolver, the
rerank (the dense reranker embeds the query and every candidate again) and context assembly.
Every embedder call is counted (texts and seconds).

`--with-llm` asks the drafting model for one token before each query, at the app's context size,
as a real conversation does between turns. Measured on 2026-11-26: retrieval alone is ~0.56 s at
the median; the 3-9 s seen in full golden runs came from Ollama keeping one model at a time
(see ADR-041). Nothing is written anywhere; the output is a JSON summary on stdout.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

from evals.golden import load
from evals.run import GOLDEN, _eval_tenant, _jurisdiction_id, build_deps

_LANG = {"es-MX": "es", "en-US": "en"}


class TimedEmbedder:
    """Wraps the query embedder: counts calls and texts, and the seconds spent in each."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.model = getattr(inner, "model", "")
        self.revision = getattr(inner, "revision", "")
        self.calls: list[tuple[int, float]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        t = time.perf_counter()
        out: list[list[float]] = self.inner.embed(texts)
        self.calls.append((len(texts), time.perf_counter() - t))
        return out


def _warm_llm(deps: Any) -> None:
    """One token from the drafting model, so it is resident before the next retrieval."""
    import httpx

    model = deps.aliases.spec("drafting-primary").model_id
    httpx.post(
        "http://127.0.0.1:11434/api/generate",
        # the same context size the app requests (llm_num_ctx): its KV cache is what fills the VRAM
        json={
            "model": model,
            "prompt": "ok",
            "stream": False,
            "options": {"num_predict": 1, "num_ctx": deps.settings.llm_num_ctx},
        },
        timeout=600,
    )


def profile_case(engine: Any, tenant: Any, case: Any, deps: Any) -> dict[str, float | int]:
    from app.db.session import tenant_transaction
    from app.rag.context import load_session_context
    from app.rag.expansion import build_context
    from app.rag.fusion import rrf
    from app.rag.pipeline import explicit_units
    from app.rag.search import lexical_arm, semantic_arm

    s, st = case.session, deps.settings
    emb = TimedEmbedder(deps.embedder)
    reranker = (
        type(deps.reranker)(emb)
        if type(deps.reranker).__name__ == "DenseReranker"
        else deps.reranker
    )
    row: dict[str, float | int] = {}
    with tenant_transaction(engine, tenant) as conn:
        jid = _jurisdiction_id(conn, s.jurisdiction)
        ctx = load_session_context(conn, tenant, jid, language=_LANG[s.language],  # type: ignore[arg-type]
                                   include_historical=s.include_historical)  # fmt: skip
        t = time.perf_counter()
        sem = semantic_arm(conn, ctx, case.query, emb, top=st.retrieval_arm_top)
        row["semantic_total_s"] = time.perf_counter() - t
        row["query_embed_s"] = emb.calls[0][1]
        row["semantic_sql_s"] = row["semantic_total_s"] - row["query_embed_s"]
        t = time.perf_counter()
        lex = lexical_arm(conn, ctx, case.query, top=st.retrieval_arm_top, match=st.lexical_match)
        row["lexical_s"] = time.perf_counter() - t
        by_cite = {h.cite_id: h for h in (*lex, *sem)}
        fused = rrf([[h.cite_id for h in sem], [h.cite_id for h in lex]], k=st.retrieval_rrf_k,
                    top=st.retrieval_rrf_top)  # fmt: skip
        cands = [by_cite[c] for c, _ in fused]
        t = time.perf_counter()
        pinned = (
            explicit_units(conn, ctx, case.query, emb, st)
            if st.retrieval_explicit_reference
            else []
        )
        row["explicit_s"] = time.perf_counter() - t
        cands = [*pinned, *[h for h in cands if h.cite_id not in {p.cite_id for p in pinned}]]
        texts = [f"{h.breadcrumb}\n{h.text}" if st.rerank_breadcrumb else h.text for h in cands]
        t = time.perf_counter()
        scores = reranker.score(case.query, texts) if cands else []
        row["rerank_s"] = time.perf_counter() - t
        row["rerank_texts"] = len(texts) + (1 if texts else 0)
        ranked = sorted(zip(cands, scores, strict=True), key=lambda p: (-p[1], p[0].cite_id))
        top = [
            h.model_copy(update={"score": float(sc), "rank": i})
            for i, (h, sc) in enumerate(ranked[: st.retrieval_rerank_top], 1)
        ]
        t = time.perf_counter()
        build_context(
            conn, top, budget_tokens=st.retrieval_context_budget_tokens, counter=deps.counter,
            citable_units=st.context_citable_units, include_historical=ctx.include_historical,
            apartado=st.context_unit_apartado,
        )  # fmt: skip
        row["context_s"] = time.perf_counter() - t
    row["embed_calls"] = len(emb.calls)
    row["embed_texts"] = sum(n for n, _ in emb.calls)
    row["embed_s"] = sum(sec for _, sec in emb.calls)
    row["total_s"] = sum(v for k, v in row.items() if k.endswith("_s") and k not in (
        "query_embed_s", "semantic_sql_s", "embed_s"))  # fmt: skip
    return row


def summarize(rows: list[dict[str, float | int]]) -> dict[str, dict[str, float]]:
    keys = [k for k in rows[0] if k != "embed_calls"] + ["embed_calls"]
    out: dict[str, dict[str, float]] = {}
    for k in keys:
        xs = sorted(float(r[k]) for r in rows)
        p95 = xs[min(len(xs) - 1, round(0.95 * (len(xs) - 1)))]
        out[k] = {"p50": round(statistics.median(xs), 3), "p95": round(p95, 3),
                  "max": round(xs[-1], 3)}  # fmt: skip
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="evals.profile_retrieval")
    ap.add_argument("--file", type=Path, default=GOLDEN)
    ap.add_argument(
        "--with-llm", action="store_true", help="keep the drafting model resident between queries"
    )
    ap.add_argument("--repeat", type=int, default=1)
    a = ap.parse_args(argv)
    cases = load(a.file)
    state, engine = build_deps()
    tenant = _eval_tenant(state.settings)
    rows: list[dict[str, float | int]] = []
    for _ in range(a.repeat):
        for c in cases:
            if a.with_llm:
                _warm_llm(state.chat)
            rows.append(profile_case(engine, tenant, c, state.chat))
    report = {"cases": len(cases), "repeat": a.repeat, "with_llm": a.with_llm,
              "reranker": getattr(state.chat.reranker, "name", type(state.chat.reranker).__name__),
              "stages": summarize(rows)}  # fmt: skip
    sys.stdout.write(json.dumps(report, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
