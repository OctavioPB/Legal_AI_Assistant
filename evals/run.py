"""Golden suite runner (WS5.T04-T06).

    uv run python -m evals.run --suite golden [--tag before-x] [--category ...] [--retrieval-only]
    uv run python -m evals.run --compare <run_a> <run_b>

Runs the REAL pipeline (catalog session -> jurisdiction predicate -> hybrid search -> rerank -> tau ->
composer -> local model -> validator) as a dedicated `golden-eval` firm, so nothing touches the demo
account. Before any model call the preflight checks the golden set against the live index and aborts
on problems. Every run writes `evals/runs/golden-<ts>[-tag]/report.json` with its full configuration
(commit, dirty flag, settings, resolved model ids, prompt manifest hash, embedding/chunker versions),
per-case outcomes and per-category metrics. Gates are evaluated on lawyer-reviewed cases only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, Engine, create_engine, text

from evals.golden import (
    CaseResult,
    GoldenCase,
    Jurisdiction,
    case_ok,
    compare,
    gate_report,
    load,
    matches,
    read_run,
    score_case,
    summarize,
)

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "evals" / "golden" / "golden_v2.jsonl"
RUNS = ROOT / "evals" / "runs"
EVAL_TENANT_SLUG = "golden-eval"
_LANG = {"es-MX": "es", "en-US": "en"}


# ---- preflight ------------------------------------------------------------------------------------
def preflight(conn: Connection, cases: list[GoldenCase]) -> list[str]:
    """Problems that make a run meaningless: ids that point at nothing in this generation, a session
    the catalog does not know, a 'refuse' case with gold, an 'answer' case without gold."""
    problems: list[str] = []
    exact = {
        p for c in cases for p in (*c.gold_chunks, *c.gold_alternatives, *c.distractor_chunks)
        if not p.endswith("*")
    }  # fmt: skip
    found = set(
        conn.execute(
            text("SELECT cite_id FROM chunks WHERE cite_id = ANY(:ids)"), {"ids": sorted(exact)}
        ).scalars()
    )
    for c in cases:
        for field, ids in (("gold", c.gold_chunks), ("alternative", c.gold_alternatives),
                           ("distractor", c.distractor_chunks)):  # fmt: skip
            for p in ids:
                if p.endswith("*"):
                    n = conn.execute(
                        text("SELECT count(*) FROM chunks WHERE cite_id LIKE :p"),
                        {"p": p[:-1].replace("%", r"\%").replace("_", r"\_") + "%"},
                    ).scalar_one()
                    if not n:
                        problems.append(f"{c.id}: {field} prefix {p} matches nothing")
                elif p not in found:
                    problems.append(f"{c.id}: {field} {p} does not exist in the live index")
        if c.expected_behavior == "refuse" and c.gold_chunks:
            problems.append(f"{c.id}: a 'refuse' case cannot have gold chunks")
        if c.expected_behavior == "answer" and not c.gold_chunks:
            problems.append(f"{c.id}: an 'answer' case needs gold chunks")
        if _jurisdiction_id(conn, c.session.jurisdiction) is None:
            problems.append(f"{c.id}: session jurisdiction not in the catalog")
    return problems


def _jurisdiction_id(conn: Connection, j: Jurisdiction) -> int | None:
    jid: int | None = conn.execute(
        text(
            "SELECT id FROM jurisdictions WHERE country = :c AND level = :l "
            "AND state_code IS NOT DISTINCT FROM :s AND municipality IS NOT DISTINCT FROM :m"
        ),
        {"c": j.country, "l": j.level, "s": j.state_code, "m": j.municipality},
    ).scalar()
    return jid


def _scope(conn: Connection, ids: list[str]) -> dict[str, Jurisdiction]:
    """Jurisdiction of each hit, read from the chunk row (denormalized by trigger from the catalog)."""
    if not ids:
        return {}
    rows = conn.execute(
        text(
            "SELECT cite_id, country, level, state_code, municipality FROM chunks "
            "WHERE cite_id = ANY(:ids)"
        ),
        {"ids": ids},
    ).all()
    return {
        r.cite_id: Jurisdiction(
            country=r.country, level=r.level, state_code=r.state_code, municipality=r.municipality
        )  # fmt: skip
        for r in rows
    }


# ---- run record -------------------------------------------------------------------------------------
def _git() -> tuple[str | None, bool]:
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                                capture_output=True, text=True, check=True).stdout.strip()  # fmt: skip
        dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                                    capture_output=True, text=True, check=True).stdout.strip())  # fmt: skip
        return commit, dirty
    except (OSError, subprocess.CalledProcessError):
        return None, True


def _config(
    settings: Any, conn: Connection, registry: Any, embedder: Any, reranker: Any
) -> dict[str, Any]:
    manifest = ROOT / "app" / "prompts" / "manifest.yaml"
    gen = conn.execute(
        text(
            "SELECT DISTINCT embedding_model, embedding_revision, chunker_version, "
            "chunk_thresholds::text AS th FROM chunks"
        )
    ).all()
    return {
        "settings": {k: getattr(settings, k) for k in (
            "refusal_tau", "refusal_min_fragments", "retrieval_arm_top", "retrieval_rrf_k",
            "retrieval_rrf_top", "retrieval_rerank_top", "retrieval_context_budget_tokens",
            "context_citable_units", "validator_quote_min_words", "validator_coverage_min_words",
        ) if hasattr(settings, k)},
        "resolved_models": registry.pinned_ids(),
        "embedder": {"model": embedder.model, "revision": embedder.revision},
        "reranker": getattr(reranker, "name", type(reranker).__name__),
        "prompt_manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        "generations": [
            {"embedding_model": g[0], "embedding_revision": g[1], "chunker_version": g[2], "thresholds": g[3]}
            for g in gen
        ],
    }  # fmt: skip


# ---- execution --------------------------------------------------------------------------------------
def _eval_tenant(settings: Any) -> uuid.UUID:
    """The `golden-eval` firm: created once by the provisioner role, so eval traffic and its audit
    rows never mix with the demo account."""
    admin = create_engine(settings.superuser_url())
    try:
        with admin.connect() as c:
            tid = c.execute(
                text("SELECT id FROM tenants WHERE slug = :s"), {"s": EVAL_TENANT_SLUG}
            ).scalar()
    finally:
        admin.dispose()
    if tid is not None:
        return uuid.UUID(str(tid))
    prov = create_engine(settings.provisioner_url())
    try:
        with prov.begin() as c:
            tid = c.execute(
                text(
                    "INSERT INTO tenants (slug, name) VALUES (:s, 'Golden evaluation') RETURNING id"
                ),
                {"s": EVAL_TENANT_SLUG},
            ).scalar_one()
    finally:
        prov.dispose()
    return uuid.UUID(str(tid))


def run_case(
    engine: Engine, tenant: uuid.UUID, case: GoldenCase, deps: Any, *, full: bool
) -> CaseResult:
    from app.chat.service import ChatRequest, answer_query
    from app.db.session import tenant_transaction
    from app.rag.context import load_session_context
    from app.rag.pipeline import retrieve

    s = case.session
    t0 = time.monotonic()
    with tenant_transaction(engine, tenant) as conn:
        jid = _jurisdiction_id(conn, s.jurisdiction)
        ctx = load_session_context(conn, tenant, jid, language=_LANG[s.language],  # type: ignore[arg-type]
                                   include_historical=s.include_historical)  # fmt: skip
        if not full:
            r = retrieve(conn, ctx, case.query, embedder=deps.embedder, reranker=deps.reranker,
                         counter=deps.counter, settings=deps.settings)  # fmt: skip
            hits = [h.cite_id for h in r.hits]
            return score_case(case, hits, _scope(conn, hits), gate_refused=r.refusal is not None,
                              retrieval_s=time.monotonic() - t0)  # fmt: skip
        req = ChatRequest(query=case.query, persona=s.persona, workflow=s.workflow)  # type: ignore[arg-type]
        stamps: dict[str, float] = {}
        res = answer_query(conn, ctx, req, deps, lambda st: stamps.setdefault(st, time.monotonic()))
        tr = deps.tracer.get(tenant, res.request_id)
        hits = [h.cite_id for h in tr.hits]
        scope = _scope(conn, hits)
    gen_at = stamps.get("generating") or stamps.get("validating")
    return score_case(
        case, hits, scope, gate_refused=res.status == "refused" and not tr.attempts,
        outcome=res.status, refusal_reason=res.refusal_reason,
        cited=[c.cite_id for c in res.citations], retrieved_set=tr.retrieved_texts.keys(),
        regenerations=max(0, len(tr.attempts) - 1),
        retrieval_s=(gen_at - t0) if gen_at else time.monotonic() - t0,
        total_s=time.monotonic() - t0, request_id=str(res.request_id), answer=res.answer,
        texts={k: list(v) for k, v in tr.retrieved_texts.items()},
    )  # fmt: skip


def build_deps() -> tuple[Any, Engine]:
    from app.main import build_state
    from app.settings import get_settings

    st = build_state(get_settings())
    return st, st.engine


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="evals.run")
    ap.add_argument("--suite", choices=["golden"], default="golden")
    ap.add_argument("--file", type=Path, default=GOLDEN)
    ap.add_argument("--tag", default="")
    ap.add_argument("--category", action="append", help="repeatable; default: every category")
    ap.add_argument("--case", action="append", help="repeatable case id")
    ap.add_argument("--retrieval-only", action="store_true", help="no model calls (deterministic)")
    ap.add_argument("--preflight-only", action="store_true")
    ap.add_argument("--compare", nargs=2, metavar=("RUN_A", "RUN_B"))
    a = ap.parse_args(argv)

    if a.compare:
        paths = [Path(p) if Path(p).exists() else RUNS / p / "report.json" for p in a.compare]
        out = compare(read_run(paths[0]), read_run(paths[1]))
        sys.stdout.write(json.dumps(out, ensure_ascii=False, indent=1) + "\n")
        return 1 if out["verdict"] == "regression" else 0

    cases = load(a.file)
    if a.category:
        cases = [c for c in cases if c.category in a.category]
    if a.case:
        cases = [c for c in cases if c.id in a.case]
    state, engine = build_deps()
    deps = state.chat
    admin = create_engine(state.settings.superuser_url())
    try:
        with admin.connect() as conn:
            problems = preflight(conn, cases)
            config = _config(deps.settings, conn, deps.aliases, deps.embedder, deps.reranker)
    finally:
        admin.dispose()
    if problems:
        sys.stdout.write("PREFLIGHT FAILED: fix the golden set before running it\n")
        for p in problems:
            sys.stdout.write(f"  - {p}\n")
        return 2
    sys.stdout.write(f"preflight ok: {len(cases)} cases\n")
    if a.preflight_only:
        return 0

    tenant = _eval_tenant(state.settings)
    results: list[CaseResult] = []
    t0 = time.monotonic()
    for c in cases:
        try:
            r = run_case(engine, tenant, c, deps, full=not a.retrieval_only)
        except Exception as exc:  # one case failing never hides the others
            r = CaseResult(id=c.id, category=c.category, gating=c.gating, expected=c.expected_behavior,
                           has_gold=bool(c.gold_chunks), hits=(), gold_rank=None, leaked=(),
                           distractors_in_top=(), gate_refused=False,
                           error=f"{type(exc).__name__}: {str(exc)[:300]}")  # fmt: skip
        results.append(r)
        mark = "ok " if case_ok(r) else "BAD"
        sys.stdout.write(
            f"{mark} {c.id} {c.category:22} {r.outcome or ('gate-refused' if r.gate_refused else 'retrieved'):12} "
            f"gold@{r.gold_rank or '-'} leak={len(r.leaked)} {r.total_s or r.retrieval_s:6.1f}s {r.error or ''}\n"
        )
        sys.stdout.flush()

    commit, dirty = _git()
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"golden-{ts}" + (f"-{a.tag}" if a.tag else "")
    summary = summarize(results)
    report = {
        "run_id": run_id, "ts": ts, "tag": a.tag, "commit": commit, "dirty": dirty,
        "suite": a.file.name, "mode": "retrieval-only" if a.retrieval_only else "full",
        "languages": sorted({c.session.language for c in cases}), "config": config,
        "n_cases": len(results),
        "n_by_category": {cat: sum(r.category == cat for r in results) for cat in {r.category for r in results}},
        "summary": summary,
        "gates": gate_report(summary["gating"], summary["n_gating"]),  # type: ignore[arg-type]
        "cases": [r.model_dump() for r in results],
        "seconds": round(time.monotonic() - t0, 1),
    }  # fmt: skip
    out = RUNS / run_id
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str),
                                     encoding="utf-8")  # fmt: skip
    sys.stdout.write(json.dumps({"overall": summary["overall"], "gates": report["gates"]},
                                ensure_ascii=False, indent=1) + "\n")  # fmt: skip
    sys.stdout.write(f"\nrun record: evals/runs/{run_id}/report.json"
                     f"{'  (DIRTY tree: not trusted for gates)' if dirty else ''}\n")  # fmt: skip
    return 0


__all__ = ["main", "matches", "preflight", "run_case"]

if __name__ == "__main__":
    raise SystemExit(main())
