"""Golden suite core (WS5.T04-T06): case schema, matching, per-case and per-category metrics, release
gates and the before/after comparator. Pure functions only: everything here is unit-tested; the I/O
(database, models, files) lives in `evals/run.py`.

Honesty rules built in (eval-runner skill):
* `ai-draft` cases (no lawyer review) are reported, never gate a release;
* faithfulness is NOT computed here: it needs a lawyer on the review sheet (`evals/review_sheet.py`);
* citation precision is 1.00 by construction when the validator works (the model only sees retrieved
  fragments and invalid ids are rejected): it is a regression alarm, not evidence of quality;
* one case moves a small category by many points: the comparator says when a change is within noise.
"""

from __future__ import annotations

import json
import statistics
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from evals.stats import ALPHA, interval, mcnemar_exact

Category = Literal[
    "statutory_lookup", "exact_identifier", "jurisdiction_isolation", "refusal_required",
    "cross_lingual", "temporal_validity", "injection",
]  # fmt: skip
CATEGORIES: tuple[str, ...] = Category.__args__  # type: ignore[attr-defined]

# Release gates (eval-runner skill). Hard gates bind even with few cases; soft gates are read with
# the noise rule (1-2 cases of difference is noise).
GATES: dict[str, tuple[str, float]] = {
    "citation_precision": (">=", 1.0),
    "jurisdiction_leakage": ("<=", 0.0),
    "refusal_accuracy": (">=", 0.95),
    "false_refusal": ("<=", 0.05),
    "recall_at_k": (">=", 0.90),
}
HARD_GATES = ("citation_precision", "jurisdiction_leakage")


class Jurisdiction(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    country: Literal["MX", "US"]
    level: Literal["federal", "state", "municipal"]
    state_code: str | None = None
    municipality: str | None = None


class Session(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    language: Literal["es-MX", "en-US"]
    jurisdiction: Jurisdiction
    persona: str
    workflow: Literal["dossier_builder", "conversar", "adversarial", "argumentar"] = (
        "dossier_builder"
    )
    include_historical: bool = False


class GoldenCase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    category: Category
    authored_by: str  # "lawyer:<id>" | "ai-draft"
    reviewed_by: str | None = None
    session: Session
    query: str = Field(min_length=1)
    expected_behavior: Literal["answer", "refuse"]
    gold_chunks: tuple[str, ...] = ()
    gold_alternatives: tuple[str, ...] = ()  # added by a lawyer; "PREFIX*" matches a stem prefix
    distractor_chunks: tuple[str, ...] = ()
    gold_answer_criteria: tuple[str, ...] = ()
    # a narrow ALARM, not faithfulness: words that must not appear in the answer because they belong
    # to a known confusion (e.g. apartado A's text under apartado B's id). Accent/case-folded.
    must_not_contain: tuple[str, ...] = ()
    notes: str = ""

    @property
    def gating(self) -> bool:
        """Only lawyer-authored or lawyer-reviewed cases count toward release gates."""
        return self.authored_by != "ai-draft" or self.reviewed_by is not None


def load(path: Path) -> list[GoldenCase]:
    cases = [
        GoldenCase.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    ids = [c.id for c in cases]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        raise ValueError(f"duplicate case ids: {dup}")
    return cases


def matches(cite_id: str, pattern: str) -> bool:
    """Exact id, or a stem prefix written as 'PREFIX*' (e.g. every fracción of an article)."""
    return cite_id.startswith(pattern[:-1]) if pattern.endswith("*") else cite_id == pattern


def any_match(ids: Iterable[str], patterns: Iterable[str]) -> bool:
    pats = list(patterns)
    return any(matches(i, p) for i in ids for p in pats)


def first_rank(ids: list[str], patterns: Iterable[str]) -> int | None:
    pats = list(patterns)
    return next((n + 1 for n, i in enumerate(ids) if any(matches(i, p) for p in pats)), None)


Span = tuple[str, int, int]  # (document id, char_start, char_end) of a chunk in its source


def _merged(spans: Iterable[Span]) -> dict[str, list[tuple[int, int]]]:
    """Per document, the union of the spans as sorted disjoint intervals (a character counts once)."""
    by_doc: dict[str, list[tuple[int, int]]] = {}
    for doc, s, e in spans:
        if e > s:
            by_doc.setdefault(doc, []).append((s, e))
    out: dict[str, list[tuple[int, int]]] = {}
    for doc, ivs in by_doc.items():
        merged: list[tuple[int, int]] = []
        for s, e in sorted(ivs):
            if merged and s <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], e))
            else:
                merged.append((s, e))
        out[doc] = merged
    return out


def _length(ivs: dict[str, list[tuple[int, int]]]) -> int:
    return sum(e - s for spans in ivs.values() for s, e in spans)


def char_overlap(hits: Iterable[Span], gold: Iterable[Span]) -> tuple[float | None, float | None]:
    """ADR-037 (D), after LegalBench-RAG: (precision, recall) in characters of source text.
    precision = retrieved characters that are gold / retrieved characters; recall = gold characters
    retrieved / gold characters. None when there is nothing to divide by."""
    h, g = _merged(hits), _merged(gold)
    inter = 0
    for doc, hs in h.items():
        for a, b in hs:
            for c, d in g.get(doc, ()):
                inter += max(0, min(b, d) - max(a, c))
    hl, gl = _length(h), _length(g)
    return (inter / hl if hl else None), (inter / gl if gl else None)


def in_scope(sel: Jurisdiction, row: Jurisdiction) -> bool:
    """Stacking rule (jurisdictions.md), restated independently of the predicate builder so the
    suite can catch a bug in it: a selection sees its own level and the levels ABOVE it, in its own
    country, never a sibling."""
    if row.country != sel.country:
        return False
    if row.level == "federal":
        return True
    if row.level == "state":
        return sel.level in ("state", "municipal") and row.state_code == sel.state_code
    return (
        sel.level == "municipal"
        and row.state_code == sel.state_code
        and row.municipality == sel.municipality
    )


class CaseResult(BaseModel):
    """One case in one run. `answer`/`cited` are None in retrieval-only runs."""

    model_config = ConfigDict(frozen=True)

    id: str
    category: str
    gating: bool
    expected: Literal["answer", "refuse"]
    has_gold: bool
    hits: tuple[str, ...]
    gold_rank: int | None
    leaked: tuple[str, ...]  # hit ids outside the session's scope
    distractors_in_top: tuple[str, ...]
    gate_refused: bool
    outcome: Literal["answered", "refused"] | None = None
    refusal_reason: str | None = None
    cited: tuple[str, ...] = ()
    invalid_cited: tuple[str, ...] = ()  # cited but not in the retrieved set (must stay empty)
    cited_gold: bool | None = None
    regenerations: int = 0
    retrieval_s: float = 0.0
    total_s: float = 0.0
    request_id: str | None = None
    error: str | None = None
    query: str = ""
    answer: str | None = None  # the validated answer, for the lawyer's review sheet
    # ADR-037 (D): characters of the top-k that are gold / characters of gold that are in the top-k
    char_precision: float | None = None
    char_recall: float | None = None
    cited_texts: dict[str, list[str]] = {}  # cite_id -> FULL text(s) it may quote (never truncated)
    forbidden_found: tuple[str, ...] = ()  # `must_not_contain` entries present in the answer


def score_case(
    case: GoldenCase,
    hits: list[str],
    scope: dict[str, Jurisdiction],
    *,
    gate_refused: bool,
    outcome: Literal["answered", "refused"] | None = None,
    refusal_reason: str | None = None,
    cited: Iterable[str] = (),
    retrieved_set: Iterable[str] = (),
    regenerations: int = 0,
    retrieval_s: float = 0.0,
    total_s: float = 0.0,
    request_id: str | None = None,
    answer: str | None = None,
    texts: dict[str, list[str]] | None = None,
    spans: tuple[list[Span], list[Span]] | None = None,
) -> CaseResult:
    gold = (*case.gold_chunks, *case.gold_alternatives)
    cp, cr = char_overlap(*spans) if spans is not None and gold else (None, None)
    cited_t = tuple(dict.fromkeys(cited))
    rset = set(retrieved_set)
    return CaseResult(
        id=case.id, category=case.category, gating=case.gating, expected=case.expected_behavior,
        has_gold=bool(gold), hits=tuple(hits), gold_rank=first_rank(hits, gold) if gold else None,
        leaked=tuple(h for h in hits if h in scope and not in_scope(case.session.jurisdiction, scope[h])),
        distractors_in_top=tuple(h for h in hits if any(matches(h, d) for d in case.distractor_chunks)),
        gate_refused=gate_refused, outcome=outcome, refusal_reason=refusal_reason, cited=cited_t,
        invalid_cited=tuple(c for c in cited_t if c not in rset) if outcome == "answered" else (),
        cited_gold=any_match(cited_t, gold) if outcome == "answered" and gold else None,
        regenerations=regenerations, retrieval_s=retrieval_s, total_s=total_s, request_id=request_id,
        query=case.query, answer=answer, char_precision=cp, char_recall=cr,
        forbidden_found=tuple(w for w in case.must_not_contain if answer and _fold(w) in _fold(answer)),
        cited_texts={c: list((texts or {}).get(c, [])) for c in cited_t} if outcome == "answered" else {},
    )  # fmt: skip


def _fold(t: str) -> str:
    import unicodedata

    t = unicodedata.normalize("NFKD", t)
    return "".join(c for c in t if not unicodedata.combining(c)).lower()


def _ratio(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def _counts(results: list[CaseResult]) -> dict[str, tuple[int, int]]:
    """(numerator, denominator) of every rate: the one definition both the rates and their intervals use."""
    answer = [r for r in results if r.expected == "answer"]
    refuse = [r for r in results if r.expected == "refuse"]
    with_gold = [r for r in answer if r.has_gold]
    full = [r for r in results if r.outcome is not None]
    answered = [r for r in full if r.outcome == "answered"]
    cited = [c for r in answered for c in r.cited]
    invalid = [c for r in answered for c in r.invalid_cited]
    return {
        "recall_at_k": (sum(r.gold_rank is not None for r in with_gold), len(with_gold)),
        "jurisdiction_leakage": (sum(bool(r.leaked) for r in results), len(results)),
        "distractor_rate": (sum(bool(r.distractors_in_top) for r in results), len(results)),
        "gate_refusal_on_refuse": (sum(r.gate_refused for r in refuse), len(refuse)),
        "refusal_accuracy": (
            sum(r.outcome == "refused" for r in refuse if r.outcome), sum(1 for r in refuse if r.outcome)
        ),
        "false_refusal": (
            sum(r.outcome == "refused" for r in answer if r.outcome), sum(1 for r in answer if r.outcome)
        ),
        "citation_precision": (len(cited) - len(invalid), len(cited)),
        "gold_citation": (
            sum(bool(r.cited_gold) for r in answered if r.expected == "answer"),
            sum(1 for r in answered if r.expected == "answer" and r.cited_gold is not None),
        ),
        "regeneration_rate": (sum(r.regenerations > 0 for r in full), len(full)),
        "forbidden_content": (sum(bool(r.forbidden_found) for r in answered), len(answered)),
    }  # fmt: skip


def metrics(results: list[CaseResult]) -> dict[str, float | int | None]:
    """Metrics over a set of results (a category, the gating subset, or everything)."""
    out: dict[str, float | int | None] = {"n": len(results)}
    out.update({name: _ratio(k, n) for name, (k, n) in _counts(results).items()})
    # ADR-037 (D): means over the cases that have gold (no interval: they are not k-of-n rates)
    for name in ("char_precision", "char_recall"):
        vals = [getattr(r, name) for r in results if getattr(r, name) is not None]
        out[f"{name}_at_k"] = round(sum(vals) / len(vals), 4) if vals else None
    out["errors"] = sum(r.error is not None for r in results)
    return out


def intervals(results: list[CaseResult]) -> dict[str, dict[str, float | int] | None]:
    """ADR-032: the exact 95 % (Clopper-Pearson) interval of every rate, with its k and n."""
    return {name: interval(k, n) for name, (k, n) in _counts(results).items()}


def percentile(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    return round(s[min(len(s) - 1, round(q * (len(s) - 1)))], 2)


def summarize(results: list[CaseResult]) -> dict[str, object]:
    by_cat = {c: [r for r in results if r.category == c] for c in CATEGORIES}
    gating = [r for r in results if r.gating]
    return {
        "overall": metrics(results),
        "by_category": {c: metrics(rs) for c, rs in by_cat.items() if rs},
        # ADR-032: exact 95 % intervals; with dozens of cases a rate without its interval overstates itself
        "intervals": intervals(results),
        "intervals_by_category": {c: intervals(rs) for c, rs in by_cat.items() if rs},
        "gating": metrics(gating) if gating else None,
        "n_gating": len(gating),
        "n_ai_draft": len(results) - len(gating),
        "latency": {
            "retrieval_p50": percentile([r.retrieval_s for r in results], 0.5),
            "retrieval_p95": percentile([r.retrieval_s for r in results], 0.95),
            "total_p50": percentile([r.total_s for r in results if r.outcome], 0.5),
            "total_p95": percentile([r.total_s for r in results if r.outcome], 0.95),
        },
        "faithfulness": None,  # lawyer grading on the review sheet; never computed here
    }


def gate_report(m: dict[str, float | int | None] | None, n_gating: int) -> dict[str, object]:
    """Gates are evaluated on the lawyer-reviewed subset only. With none, nothing is gated."""
    if not m or not n_gating:
        return {"status": "not_gating", "reason": "0 lawyer-reviewed cases (all ai-draft)"}
    out: dict[str, object] = {}
    for name, (op, thr) in GATES.items():
        v = m.get(name)
        if v is None:
            out[name] = {"value": None, "pass": None}
            continue
        ok = v >= thr if op == ">=" else v <= thr
        out[name] = {"value": v, "threshold": f"{op} {thr}", "pass": ok, "hard": name in HARD_GATES}
    failed = [k for k, v in out.items() if isinstance(v, dict) and v["pass"] is False]
    if any(k in HARD_GATES for k in failed):
        out["status"] = "fail"
    else:
        out["status"] = "soft_fail" if failed else "pass"
    return out


def case_ok(r: CaseResult) -> bool:
    """Did the case behave as expected? (outcome if present, else the retrieval expectation)."""
    if r.leaked or r.error or r.invalid_cited or r.forbidden_found:
        return False
    if r.outcome is not None:
        if r.expected == "refuse":
            return r.outcome == "refused"
        return r.outcome == "answered" and (r.cited_gold is not False)
    return r.gate_refused if r.expected == "refuse" else r.gold_rank is not None


def compare(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Per-category deltas, flipped cases, and the noise rule (eval-runner skill, WS5.T06)."""
    b_cases = {c["id"]: CaseResult.model_validate(c) for c in before["cases"]}
    a_cases = {c["id"]: CaseResult.model_validate(c) for c in after["cases"]}
    common = sorted(b_cases.keys() & a_cases.keys())
    flips = [
        {"id": i, "category": a_cases[i].category, "before": case_ok(b_cases[i]), "after": case_ok(a_cases[i])}
        for i in common if case_ok(b_cases[i]) != case_ok(a_cases[i])
    ]  # fmt: skip
    deltas: dict[str, dict[str, object]] = {}
    for cat in CATEGORIES:
        bs = [b_cases[i] for i in common if b_cases[i].category == cat]
        if not bs:
            continue
        mb, ma = metrics(bs), metrics([a_cases[i] for i in common if a_cases[i].category == cat])
        row: dict[str, object] = {"n": len(bs)}
        for k in (
            "recall_at_k",
            "refusal_accuracy",
            "false_refusal",
            "gold_citation",
            "jurisdiction_leakage",
        ):
            vb, va = mb.get(k), ma.get(k)
            if vb is None or va is None:
                continue
            d = round(float(va) - float(vb), 4)
            row[k] = {"before": vb, "after": va, "delta": d}
        deltas[cat] = row
    worse = [f for f in flips if f["before"] and not f["after"]]
    better = [f for f in flips if not f["before"] and f["after"]]
    one_case = {cat: round(1 / v["n"], 4) for cat, v in deltas.items()}  # type: ignore[operator]
    # ADR-032: an exact McNemar test on the flipped cases decides "improvement", not a count of flips.
    # Any worse flip is still a regression to explain (eval-runner skill), whatever the p-value.
    p_value = mcnemar_exact(len(worse), len(better))
    if worse:
        verdict = "regression"
    elif better and p_value < ALPHA:
        verdict = "improvement"
    else:
        verdict = "within_noise"
    return {
        "flips_worse": worse, "flips_better": better, "by_category": deltas,
        "one_case_moves": one_case,
        "mcnemar": {"worse": len(worse), "better": len(better), "p_value": p_value, "alpha": ALPHA},
        "verdict": verdict,
        "note": "improvement needs an exact McNemar p < 0.05 on the flipped cases; any worse flip must be explained.",
    }  # fmt: skip


def read_run(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def median(xs: list[float]) -> float | None:
    return statistics.median(xs) if xs else None
