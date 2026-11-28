---
name: eval-runner
description: Run, extend, and interpret the golden dataset evals for retrieval accuracy, citation precision, jurisdiction isolation, and refusal behavior. Use whenever prompts, chunker, embeddings, retrieval params, or model aliases change, or when adding new golden cases.
---

# Eval Runner

## Run
```bash
uv run python -m evals.run --suite golden                 # full suite
uv run python -m evals.run --suite golden --category jurisdiction_isolation
uv run python -m evals.run --suite golden --model-alias drafting-primary
uv run python -m evals.run --suite golden --tag before-<change>
uv run python -m evals.run --compare <run_id_a> <run_id_b>   # regression diff
```
Results land in `evals/runs/{timestamp}/report.json` + Langfuse traces. Every run records tag,
commit, the full effective settings (τ, k, RRF k, budgets, aliases, prompt manifest, embedding and
chunker versions) and per-case outcomes, so a number is never separated from its configuration.

### Run record (one append-only line/object per run)
`ts`, `tag`, `commit`, `dirty` (uncommitted changes → the run is marked, not trusted for gates),
`session languages`, effective settings, resolved model ids per alias, prompt manifest hash,
embedding/chunker versions + thresholds, `n_cases` per category (and `ai-draft` count),
per-category metrics, `failures`, **per-case ranks/outcomes** (so `--compare` lists flips),
latency p50/p95 for retrieval and end-to-end, regeneration and refusal rates.

### Preflight (blocks the run)
Before spending any LLM call the runner validates the golden set against the live index (what
`validate_golden` does): every gold/alternative/distractor `cite_id` exists in this generation,
vigencia matches, no duplicate ids. A set that points to nothing yields "problems" and aborts —
fix the eval first. Identical-text gold across versions is accepted only within vigencia scope.

### Variance
LLM-dependent categories run at fixed low temperature and are repeated (≥ 3 runs) before a
soft-gate change is called real; report min/median/max. Library-RAG never estimated this and lists
it as a validity threat. Retrieval-only runs are deterministic (stable tie-breaks) and run once.

## Release gates (CI-blocking)
citation_precision = 1.00 · jurisdiction_leakage = 0.00 · faithfulness ≥ 0.98 ·
refusal_accuracy ≥ 0.95 · false_refusal ≤ 0.05 · recall@8 ≥ 0.90

Gates are only meaningful with enough cases per category. With ~25 cases (v1), one case moves a
category 4+ points: treat a 1.00/0.00 hard gate as binding, but treat soft-gate movements of 1–2
cases as NOISE and say so in the PR. Never claim an improvement from noise.

ADR-032 makes this a calculation: every run report carries the exact 95 % (Clopper-Pearson) interval
of each rate (`summary.intervals`, `--intervals <run>` re-scores an old run), and `--compare` calls a
change an "improvement" only when an exact McNemar test on the flipped cases gives p < 0.05 (3 better
and 0 worse is p = 0.25: noise). Any worse flip is still a regression to explain. Quote rates with
their interval ("0 of 25 [0–14 %]"), never alone.

## Protocol for a change (prompts, chunker, embeddings, retrieval params, router)
1. Clean tree (or note the diff). Run `--tag before-<change>`.
2. Apply the change. Chunker/embedding/threshold changes → build a new generation aside (ADR-006)
   and re-embed before evaluating.
3. Run `--tag after-<change>`; run both `es-MX` and `en-US` sessions.
4. `--compare` the two: metrics overall AND per category, plus the list of cases that flipped
   hit→miss and miss→hit.
5. Accept only if the target metric improves without a regression > 2 points in any category and
   both hard gates hold. A gain that sinks one category (e.g. exact-citation lookups) is a
   regression in disguise.
6. Record both numbers in the PLAN log; architectural changes get an ADR citing the run ids.

## Baselines first
Before tuning, record separate runs for semantic-only, lexical-only, RRF, RRF+rerank (retrieval
metrics, no LLM). A component that does not improve the metrics is removed. Retrieval is
evaluated without the LLM first: if the right fragment is not retrieved, no model fixes it.

## Adding a golden case
1. Copy schema from `evals/golden/README.md`; append JSONL to `evals/golden/golden_v*.jsonl`.
2. Every case needs `distractor_chunks` — a case without plausible distractors measures nothing.
3. Cover the six categories; jurisdiction_isolation cases must pair same-numbered articles
   from different states (the highest-value failure probe).
4. Gold chunks must exist in the seeded eval corpus (`evals/corpus/`); run
   `uv run python -m evals.validate_golden` before committing.
5. **Provenance fields are mandatory:** `authored_by` (`lawyer:<id>` | `ai-draft`) and
   `reviewed_by` (lawyer id or `null`). A set drafted by the model from the corpus inherits the
   corpus' vocabulary and overstates performance. `ai-draft` cases are reported separately and do
   not count toward release gates until a lawyer reviews them.
6. Alternative gold: another chunk that answers equally well is added to `gold_alternatives` ONLY
   by a lawyer, never by the model, and satisfies the item once (recall cannot be inflated by
   listing many). Vigencia/version duplicates (same article text in v2024 and v2025) are accepted
   only inside the case's vigencia scope.
7. Include a few exact-identifier cases ("art. 123 apartado B", "§ 1983", “1a./J. 15/2024”) and
   cross-lingual cases whose answer is in the other language's corpus.

## Review sheets (human grading)
`uv run python -m evals.review_sheet --run <id>` writes one sheet per run listing, for each
answer, every claim next to the FULL text of the fragment it cites (never truncated). Faithfulness
and coverage are graded by a lawyer on these sheets. ADRs that depend on that grading stay
Proposed until it exists. If an NLI/LLM judge is used for the faithfulness gate, log its
agreement rate with the lawyer grades and alarm below the agreed threshold; a judge nobody has
calibrated does not gate.

## Interpreting failures
Classify each failed case BEFORE proposing a fix:
- **chunking** — answer split, merged, or lacking its caput/breadcrumb;
- **vocabulary** — query terms absent (synonym, cross-language, "amparo"-type concepts);
- **ambiguity** — several chunks legitimately answer (lawyer adds alternatives, rerun);
- **jurisdiction/vigencia** — predicate or `is_current` handling;
- **wrong eval** — gold badly written or stale. Fix evaluation errors first or you optimize noise.

Then:
- citation_precision < 1 → check validator logs first (was it caught+regenerated or emitted?).
- leakage > 0 → suspect predicate builder or a missing prefilter in a new query path — this is
  a sev-1, stop and fix.
- false_refusal up after prompt change → core layer likely over-tightened; diff layer 1 last.
- false_refusal up after retrieval change → τ now mis-calibrated for the new score distribution;
  rerun the probe sets (ADR-007) before touching prompts.
- A metric that is 1.00 "by construction" (e.g. “cited chunk was retrieved” when the model only
  sees retrieved chunks) proves nothing about faithfulness; judge those by hand.

## Eval harness hygiene
The runner, scorers and sheet generator have unit tests; every defect found in eval tooling
(mis-parsed grade, truncated sheet, biased set) becomes a test or a rule. Keep results append-only.
