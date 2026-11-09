# Golden set

`golden_v2.jsonl` is the live set (one JSON object per line). `golden_v1.jsonl` is the original design
sketch: it points at a hypothetical corpus (Jalisco, CCPA, `v2025.06` ids) that is not loaded, so the
preflight rejects it; it is kept only as history.

## Schema (`evals/golden.py::GoldenCase`, strict: unknown fields are an error)

| Field | Meaning |
|---|---|
| `id` | unique, `gd2-NNN` |
| `category` | `statutory_lookup`, `exact_identifier`, `jurisdiction_isolation`, `refusal_required`, `cross_lingual`, `temporal_validity` (`injection` reserved for WS7.T04) |
| `authored_by` | `lawyer:<id>` or `ai-draft` |
| `reviewed_by` | lawyer id, or `null`. **Only lawyer-authored or lawyer-reviewed cases gate a release.** |
| `session` | `language` (`es-MX`/`en-US`), `jurisdiction` (`country`, `level`, `state_code`, `municipality`: a catalog row), `persona`, `workflow`, `include_historical` |
| `query` | exactly what the user types |
| `expected_behavior` | `answer` (needs `gold_chunks`) or `refuse` (no gold) |
| `gold_chunks` | `cite_id`s that answer the question |
| `gold_alternatives` | other ids that answer equally well; added by a lawyer. `PREFIX*` matches every id starting with PREFIX (e.g. any fracción of an article) |
| `distractor_chunks` | plausible wrong answers (same number in another law or state, the repealed version, a neighbouring fracción). Every answer case should have one |
| `gold_answer_criteria` | what the lawyer checks on the review sheet |
| `notes` | why the case exists |

## Status (2026-11-08)

All 25 v2 cases are `ai-draft`, written by the assistant against the seeded corpus. They are useful to
catch regressions and to calibrate the harness, **not** to claim quality: a set drafted from the corpus
inherits its vocabulary and overstates performance. A lawyer must review (and rewrite) them, add
alternatives, and add cases of their own before any number gates a release.

## Commands

```bash
uv run python -m evals.run --preflight-only                 # the set points at real ids? (no model)
uv run python -m evals.run --retrieval-only --tag x         # deterministic, ~20 s
uv run python -m evals.run --tag before-x                   # full: local model, ~30-40 min
uv run python -m evals.run --compare <run_a> <run_b>        # per category + flipped cases + noise rule
uv run python -m evals.review_sheet --run <run_id>          # lawyer sheet: every sentence + FULL fragment
```
