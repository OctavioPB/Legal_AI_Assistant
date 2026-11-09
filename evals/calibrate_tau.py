"""Calibrate the refusal threshold tau from the hand-made probe sets (ADR-007, WS3.T04).

    uv run python -m evals.calibrate_tau [--write]

Runs the REAL retrieval (both arms, RRF, dense rerank) on every wording, with no LLM, and reports
how the best rerank score separates covered topics from absent ones. `--write` stores the chosen
tau in config/retrieval.local.yaml together with the run id, embedder revision and the numbers.
tau is never guessed in code: the gate refuses to run without one.

Honest limits: ~80 covered and ~65 absent wordings (one topic = 3 wordings, not 3 independent
samples), authored by an AI; a lawyer must review the probe sets before this gates a release.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import create_engine, text

from app.db.session import tenant_transaction
from app.ingestion.embedders import OllamaEmbedder
from app.ingestion.tokens import HFTokenCounter
from app.main import rerank_label
from app.rag.context import load_session_context
from app.rag.pipeline import retrieve
from app.rag.rerank import DenseReranker
from app.settings import get_settings

ROOT = Path(__file__).resolve().parents[1]
PROBES = Path(__file__).with_name("probes") / "tau_probes_v1.json"
OUT = ROOT / "config" / "retrieval.local.yaml"
SESSIONS = {
    "fed": ("MX", "federal", None, None, "es"),
    "nl": ("MX", "state", "NL", None, "es"),
    "mty": ("MX", "municipal", "NL", "Monterrey", "es"),
    "ca": ("US", "state", "CA", None, "en"),
}


def best_and_rank(hits: list[Any], gold: list[str]) -> tuple[float | None, int | None, int]:
    best = max((h.score for h in hits), default=None)
    rank = next((i + 1 for i, h in enumerate(hits) if any(g in h.cite_id for g in gold)), None)
    return best, rank, len(hits)


def auc(positive: list[float], negative: list[float]) -> float:
    """P(a covered wording scores above an absent one): 0.5 = no signal, 1.0 = perfect."""
    wins = sum((p > n) + 0.5 * (p == n) for p in positive for n in negative)
    return wins / (len(positive) * len(negative))


def choose_tau(cov: list[float], absn: list[float], min_refused: float = 0.95) -> dict[str, Any]:
    """Smallest tau that refuses >= min_refused of the absent wordings, and what it costs."""
    grid = [round(0.30 + 0.005 * i, 3) for i in range(0, 121)]
    rows = []
    for t in grid:
        rows.append(
            {
                "tau": t,
                "absent_refused": sum(s < t for s in absn) / len(absn),
                "covered_answered": sum(s >= t for s in cov) / len(cov),
            }
        )
    ok = [r for r in rows if r["absent_refused"] >= min_refused]
    pick = min(ok, key=lambda r: r["tau"]) if ok else max(rows, key=lambda r: r["absent_refused"])
    return {"pick": pick, "grid": rows}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="evals.calibrate_tau")
    ap.add_argument("--write", action="store_true", help="store the chosen tau in config/")
    ap.add_argument("--min-refused", type=float, default=0.95)
    ap.add_argument(
        "--tau", type=float, help="operating point chosen by a person (recorded as such)"
    )
    ap.add_argument("--reranker", choices=["dense", "cross"], default="dense")
    a = ap.parse_args(argv)

    s = get_settings().model_copy(update={"refusal_tau": 0.0})  # report scores, refuse nothing
    probes = json.loads(PROBES.read_text(encoding="utf-8"))
    emb = OllamaEmbedder(s.embedder_model, base_url=s.ollama_url)
    counter = HFTokenCounter()
    if a.reranker == "cross":
        from app.rag.rerank import CrossEncoderReranker

        reranker: Any = CrossEncoderReranker()
    else:
        reranker = DenseReranker(emb)
    app = create_engine(s.app_url())
    admin = create_engine(s.superuser_url())
    tenant = uuid.uuid4()  # read-only context: only PUBLIC_CORPUS is visible
    jids: dict[str, int] = {}
    with admin.connect() as c:
        for key, (country, level, st, mu, _) in SESSIONS.items():
            jids[key] = c.execute(
                text(
                    "SELECT id FROM jurisdictions WHERE country=:c AND level=:l "
                    "AND state_code IS NOT DISTINCT FROM :s AND municipality IS NOT DISTINCT FROM :m"
                ),
                {"c": country, "l": level, "s": st, "m": mu},
            ).scalar_one()

    results: list[dict[str, Any]] = []
    t0 = time.monotonic()
    for kind in ("covered", "absent"):
        for topic in probes[kind]:
            lang = SESSIONS[topic["session"]][4]
            for wording in topic["wordings"]:
                with tenant_transaction(app, tenant) as conn:
                    ctx = load_session_context(conn, tenant, jids[topic["session"]], language=lang)  # type: ignore[arg-type]
                    r = retrieve(conn, ctx, wording, embedder=emb, reranker=reranker,
                                 counter=counter, settings=s)  # fmt: skip
                best, rank, n = best_and_rank(list(r.hits), topic.get("gold", []))
                results.append(
                    {
                        "kind": kind,
                        "topic": topic["topic"],
                        "session": topic["session"],
                        "wording": wording,
                        "best": best,
                        "gold_rank": rank,
                        "n_hits": n,
                        "top": [(h.cite_id, round(h.score, 3)) for h in r.hits[:3]],
                    }  # fmt: skip
                )
    cov = [x for x in results if x["kind"] == "covered"]
    absn = [x for x in results if x["kind"] == "absent"]
    cs = [x["best"] for x in cov if x["best"] is not None]
    as_ = [x["best"] for x in absn if x["best"] is not None]

    def stats(xs: list[float]) -> str:
        q = statistics.quantiles(xs, n=20)
        return (f"n={len(xs)} min={min(xs):.3f} p10={q[1]:.3f} median={statistics.median(xs):.3f} "
                f"p90={q[17]:.3f} max={max(xs):.3f}")  # fmt: skip

    sys.stdout.write(f"retrieval ran {len(results)} wordings in {time.monotonic() - t0:.0f}s\n")
    sys.stdout.write(f"covered best-score : {stats(cs)}\nabsent  best-score : {stats(as_)}\n")
    found = [x for x in cov if x["gold_rank"] is not None]
    sys.stdout.write(f"gold in top 8 for {len(found)}/{len(cov)} covered wordings; "
                     f"rank 1: {sum(x['gold_rank'] == 1 for x in found)}\n")  # fmt: skip
    for kind, rows in (("covered, gold MISSING", [x for x in cov if x["gold_rank"] is None]),
                       ("absent, highest scores", sorted(absn, key=lambda x: -(x["best"] or 0))[:8]),
                       ("covered, lowest scores", sorted(cov, key=lambda x: x["best"] or 0)[:8])):  # fmt: skip
        sys.stdout.write(f"\n-- {kind}\n")
        for x in rows:
            sys.stdout.write(
                f"  {x['best'] or 0:.3f} [{x['session']}] {x['wording'][:62]:62} {x['top'][0][0].split('-v')[0] if x['top'] else '-'}\n"
            )

    sel = choose_tau(cs, as_, a.min_refused)
    pick = sel["pick"]
    if (
        a.tau is not None
    ):  # a human decision between false refusals and weak evidence reaching the model
        pick = min(sel["grid"], key=lambda r: abs(r["tau"] - a.tau))
    sys.stdout.write(f"\ncandidate tau={pick['tau']}: refuses {pick['absent_refused']:.0%} of absent, "
                     f"answers {pick['covered_answered']:.0%} of covered\n")  # fmt: skip
    for t in (0.45, 0.5, 0.55, 0.6, 0.65, 0.7):
        r = next(x for x in sel["grid"] if abs(x["tau"] - t) < 1e-9)
        sys.stdout.write(
            f"  tau={t:.2f}  absent refused {r['absent_refused']:.0%}  covered answered {r['covered_answered']:.0%}\n"
        )

    run_id = datetime.now(UTC).strftime("tau-%Y%m%dT%H%M%SZ")
    record = {"run_id": run_id, "embedder": {"model": emb.model, "revision": emb.revision},
              "reranker": rerank_label(reranker.name, s), "auc": round(auc(cs, as_), 4), "probes": str(PROBES.name), "probes_authored_by": probes["authored_by"],
              "n_covered": len(cov), "n_absent": len(absn), "pick": pick, "results": results}  # fmt: skip
    runs = ROOT / "evals" / "runs"
    runs.mkdir(exist_ok=True)
    (runs / f"{run_id}.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    sys.stdout.write(f"\nrun record: evals/runs/{run_id}.json\n")
    if a.write:
        OUT.parent.mkdir(exist_ok=True)
        OUT.write_text(yaml.safe_dump({"tau": pick["tau"], "calibration_run": run_id, "calibrated_on": run_id[4:12],
                                       "embedder": record["embedder"], "reranker": record["reranker"],
                                       "absent_refused": round(pick["absent_refused"], 3),
                                       "covered_answered": round(pick["covered_answered"], 3),
                                       "n_covered": len(cov), "n_absent": len(absn),
                                       "caveat": "AI-drafted probe sets; lawyer review pending"},
                                      sort_keys=False), encoding="utf-8")  # fmt: skip
        sys.stdout.write(f"wrote {OUT.relative_to(ROOT)}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
