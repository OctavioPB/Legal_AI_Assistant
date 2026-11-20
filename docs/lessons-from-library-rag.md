# Lessons from Library-RAG applied to Legal AI Assistant

Source: `C:\Users\USER\Documents\Github\Library-RAG` (README, CLAUDE, DECISIONS — 64 ADRs —, PLAN,
`docs/reporte-academico.md`, skills). Reviewed on 2026-11-02.

Library-RAG is a 100 % local RAG over a personal Markdown library, with the same underlying contract
as this project: everything is cited, and if there is no evidence it says so. Its figures come from one
corpus, one user and small local models (Qwen3-4B, Gemma 4 26B-A4B); **they are not carried over as
numbers, they are carried over as hypotheses to measure here** with the golden suite.

## How to read the table
*Evidence* = what was measured there (ADR/section of the report). *Application* = where it is reflected here.
*Status*: **Rule** (already in rules/skills), **ADR proposed** (decision still to be measured),
**Task** (in PLAN).

| # | Lesson | Evidence in Library-RAG | Application here | Status |
|---|---|---|---|---|
| 1 | Measure before adopting; keep only what improves | §8.1: four plausible ideas rejected once measured | CLAUDE.md "Lessons", `eval-runner` (baselines per arm), WS3.T03 | Rule / Task |
| 2 | Map-reduce as the default synthesis worsens fidelity and abstention | ADR-017: fidelity 0.87 → 0.58, abstention 2/3 → 0/3, 4× latency | ADR-008, anti-roadmap | ADR proposed |
| 3 | Equal-weight fusion of rewritten/translated queries lowers recall | ADR-038, 052: −6 pts recall@8; synthesis 0.54 → 0.43–0.48 | ADR-009, WS3.T06, `jurisdictions.md` | ADR proposed |
| 4 | Abstention must be a system action, not an instruction to the model | §7.4: v1 2/5 → v2 5/5 by returning a fixed phrase without generating | Invariant 6, ADR-007, WS5.T02 | Rule / ADR proposed |
| 5 | Calibrate the coverage threshold by hand with covered/absent sets and several wordings | ADR-056: ≥ 0.76 vs ≤ 0.15; synonyms 0.06 vs 0.82 | ADR-007, WS3.T04 | Task |
| 6 | Deterministic verification: verbatim quote with accent folding and a minimum run; orphan markers | §3.18, §8.2: 46 % → 80 % verbatim quotes with a larger model; `[n]` copied from the prompt | `rag.md` validator, WS5.T01–T02, `prompts.md` | Rule / Task |
| 7 | A valid citation ≠ a faithful paraphrase (residual risk declared) | ADR-058, §8.2 | Blueprint §10, §12; PITCH §4.5 | Rule |
| 8 | Do not tell the model "add a detail / say what is missing": it invents one with a false citation | CLAUDE.md "Tutor levels"; ADR-032 | `prompts.md`, `prompt-lab` | Rule |
| 9 | Version guards: do not mix vectors/chunks; store chunking thresholds; rebuild aside and swap atomically | CLAUDE.md rule 6, "Index mechanics" | Invariant 5, ADR-006, WS2.T08 | Rule / ADR proposed |
| 10 | The embedder window truncates silently (77 chunks > 1 024 tokens) | Plan phase 4 | `rag.md`, WS2.T05, blueprint §6.2 | Task |
| 11 | Indexes/tables of contents compete with sections; title weight in BM25 favours large notes and is swept with evidence | ADR-044: weight 0.5, 12 improve, 0 worsen | `rag.md`, WS3.T07 | Task |
| 12 | Lead-ins (“…is calculated as:”) travel with what they introduce | skill `markdown-chunking` | Caput glue: `rag.md`, `legal-chunking`, WS2.T10 (to be measured) | Task |
| 13 | `text` = exact slice of the source; breadcrumb only as metadata/prefix | skill `markdown-chunking` | `rag.md`, `legal-chunking` | Rule |
| 14 | A parsing bug = a fixture first; pathological fixtures + coverage property tests | CLAUDE.md "Conventions"; 17–19 fixtures | `rag.md`, WS2.T09 | Task |
| 15 | The user writes the evaluation set; what the AI drafts is labelled and its bias measured | skill `retrieval-eval`; §8.6, §9; 0.43 → 0.81 | `eval-runner` (authorship), WS5.T05 | Rule / Task |
| 16 | Valid alternatives are judged by a human; the error may be in the reference | `biblio candidates`; §7.6 | `eval-runner` | Rule |
| 17 | Noise in small sets: 1–2 cases are not an improvement; a regression > 2 pts per type blocks | skill `retrieval-eval` | CLAUDE.md, `eval-runner`, WS5.T06 | Rule / Task |
| 18 | Evaluation tools fail too (a regex that did not read grades; sheets truncated at 400 chars) | §8.4 | `eval-runner` "review sheets", WS5.T07 | Task |
| 19 | A metric of 1.00 "by construction" does not measure what it appears to | §6.6 (child tied to its branch: 1.00; judged by hand 24–30 %) | `eval-runner` "Interpreting failures" | Rule |
| 20 | Record every run (commit, effective config) append-only | skill `retrieval-eval` | `eval-runner` | Rule |
| 21 | Watch the ADR tripwires (200 k was passed → ≈697 k chunks without noticing) | §9 external validity; ADR-064 | `decision-record`, WS7.T05 | Task |
| 22 | ADRs with states, "Pending" and "propose before implementing"; supersede, do not edit; warn if the code contradicts an ADR | skill `decision-record` | `architecture.md`, skill `decision-record`, WS0.T08 | Rule |
| 23 | Config without magic values; `Protocol` for Embedder/Reranker/LLMClient | CLAUDE.md "Conventions" | `architecture.md`, WS0.T06 | Task |
| 24 | Tests with the network blocked; guarantees in layers | ADR-002; `pytest-socket` | `style.md`, WS0.T07 | Task |
| 25 | UI: no external resources, model text as text, i18n by test, contrast token | CLAUDE.md "web UI loads nothing", ADR-041/046 | `style.md`, `Brand.md`, WS6.T08 | Task |
| 26 | A new data store must be registered in erasure (`reset.memory_items`) | ADR-040 | `multi-tenancy.md`, WS7.T06 | Task |
| 27 | Conversation plans: the first message is searched as written; a rewrite never changes the scope | ADR-038 | ADR-009, `jurisdictions.md` | ADR proposed |
| 28 | Detected gaps (abstentions) become backlog | ADR-029, 030, 047 | ROADMAP R1.6 | Future |
| 29 | Embeddings only preselect; cosine is not enough to decide "same topic" | ADR-030 | ROADMAP R1.6 | Future |
| 30 | Real memory/cost is not the theoretical one: observe growth per request, not only the peak | §8.3 | Pending: apply to per-tenant LiteLLM budgets and p95 latency under load (WS7.T03) | Observation |
| 31 | Invariants must be executable: `doctor` with OK/WARN/FAIL (extension loads, FTS folds accents, model digest, enough context) | `doctor.py` (checks of sqlite-vec, FTS5 `remove_diacritics`, digests, served context) | WS0.T09, CLAUDE.md | Task |
| 32 | One-step, idempotent launcher that asks before downloading | `scripts/start.sh`, ADR-021 | WS0.T10 | Task |
| 33 | Complete run record: `commit` + `dirty`, effective config, ranks per question, p50/p95, and a preflight that aborts if the set points to something nonexistent | `eval/runner.py` (`check_expected`, `_git_state`, `per_question`) | `eval-runner` (run record, preflight), WS5.T06 | Rule / Task |
| 34 | One ingestion run: lock with PID and takeover if the owner died, transaction per document, `failed` without aborting, statistics, lazy embedder | `index/indexer.py` (`_index_lock`, `IndexStats`, `_Lazy`) | `legal-chunking` "Ingestion run contract", WS2.T06 | Rule / Task |
| 35 | Full rebuild in a separate file and swap only if it finishes well; `index_meta` differences listed in the error | `indexer.py` (`meta_differences`, `--full` → `.tmp`) | ADR-006, WS2.T08 | ADR proposed |
| 36 | Citation validator = pure function with an immutable result; group parser `[1, 3]`; dedupe in order; tolerant abstention via "simplify" | `generate/citations.py` | `rag.md` validator, WS5.T01 | Rule |
| 37 | Stable tie-breaks in RRF; fusion uses only ranks | `retrieve/fusion.py` | `rag.md` determinism | Rule |
| 38 | Typed, frozen settings, with every LLM output budget and every threshold commented with its ADR and the measured figure; cross constraints (context + answer ≤ window) | `config/default.toml`, `config.py` | `architecture.md`, WS0.T06 | Rule / Task |
| 39 | Models pinned by revision/digest verified in their model card, not from memory | CLAUDE.md "Do not trust training memory"; `[llm] model_digest` | `architecture.md` "Pin, don't trust memory", WS4.T01 | Rule / Task |
| 40 | Test architecture: shared deterministic fakes, a config fixture that switches off the expensive pieces, a marker for tests with real models, `mypy --strict` + ruff | `tests/fakes.py`, `tests/conftest.py`, `pyproject.toml` | `style.md` | Rule |
| 41 | Accent folding in the lexical arm is a tested requirement, not a detail | CLAUDE.md "FTS5 and accents" | `rag.md`, `doctor` | Rule |
| 42 | A reset/erasure refuses to touch protected paths whatever the config (`check_safe`) | `reset.py` | `multi-tenancy.md` (erasure never touches `PUBLIC_CORPUS`), WS7.T06 | Rule |

**Observation about the source (for whoever reads Library-RAG):** the report (§8.4) says the review
sheets already show the full text of the cited fragment, but `generate/answer_eval.py`
still limits the excerpt with `_EXCERPT_CHARS = 400`. Here the full text is required by a test
(WS5.T07); it is worth checking whether the finding was fixed on another path or is still pending there.

## What was NOT imported (and why)
- **Offline guarantee / a single command with a network:** this is a SaaS with external providers; the
  applicable equivalent is egress control in tests (lesson 24) and the per-tenant data policy, not "zero
  network".
- **Memory budget < 8 GB, Ollama, quantization, model in RAM:** does not apply to models hosted behind
  LiteLLM; the analogue is the per-tenant budget and p95 latency.
- **SQLite + sqlite-vec and exact search:** Postgres/pgvector/RLS is the perimeter (ADR-001); the
  transferable lesson is only to watch the tripwire (21).
- **Spaced repetition, concept maps, "Explain it to me yourself", built-in reader:** study features
  outside the scope of a product for practising lawyers (anti-roadmap: it is not a tutor). Its
  verification technique (verbatim quote) was imported.
- **"Reason" mode with the model's general-knowledge layer:** clashes with this project's contract
  (the LLM only asserts retrieved legal content). Only the labelling of what the model adds
  (`[razonamiento estratégico]`) is kept, and it already existed.

## How to maintain this document
When a lesson is measured here and the verdict changes, **the row is not edited**: a new one is added
with the date and the ADR/run that replaces it (same rule as the ADRs).
