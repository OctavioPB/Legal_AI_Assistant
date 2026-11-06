# Architecture Rules

## Service boundaries
- `app/api/` — FastAPI routers. Thin: auth, SessionContext validation, delegation. No business logic.
- `app/rag/` — query analysis, jurisdiction predicate, hybrid search, rerank, parent expansion.
- `app/prompts/` — versioned templates (jinja), loaded read-only. Layer composer in `composer.py`.
- `app/orchestration/` — LiteLLM client, task-alias routing table, fallbacks.
- `app/validation/` — citation parser, existence/coverage checks, regeneration loop.
- `app/ingestion/` — Docling wrapper, legal normalizer grammars, chunker, embedder (Celery tasks only).
- `app/db/` — SQLAlchemy models, RLS session middleware, migrations (alembic).

## Interfaces and config
- The pieces most likely to change are `Protocol`s injected from settings, never imported
  concretely: `Embedder`, `Reranker`, `LLMClient` (LiteLLM-backed), `ObjectStore`.
- No magic values in code: τ, k values, RRF k, context budget, chunk thresholds, validator retries
  and model aliases live in typed settings (pydantic-settings), overridable per environment.
  A threshold that changes chunks/vectors is also stored with the data (CLAUDE.md invariant 5).
- Settings are validated at startup, not at first use: for each alias, `context_budget +
  max_answer_tokens ≤ model context window`; every LLM call has its own `*_max_tokens`
  (validator regeneration, utility tasks, translation); `rerank_top ≤ rrf_top ≤ arm_top`.
  An invalid combination refuses to boot.
- Pin, don't trust memory: each alias resolves to an exact provider model id (dated snapshot
  where offered), recorded in settings with the date it was verified against the provider's own
  documentation (never from training knowledge). Every eval run stores the resolved ids; a change
  of resolved id is a model change and requires a golden run (invariant 2). Embedder and reranker
  are pinned by name + revision/version the same way.
- Domain objects (`Chunk`, `Hit`, `Verdict`, `SessionContext`, `Answer`) are frozen typed models;
  layers never exchange loose dicts. `mypy --strict` over `app/`.
- Every ADR that names a tripwire ("revisit at >N") registers it as an ops alert (PLAN WS7.T05).

## ADR log (append-only; new decisions go here with date + rationale)
States: **Proposed** · **Accepted** · **Superseded by ADR-NNN** · **Rejected**. An accepted ADR is
never edited except its state; a change is a NEW ADR that supersedes it. A decision that rests on
missing data stays Proposed with a "Pending: <exact datum that closes it>" line. A measured
decision cites the numbers and the run id (`evals/runs/...`). New ADRs use the full format
(Context · Options · Decision · Consequences) from the `decision-record` skill; ADR-001–005
below are the original one-liners and are Accepted.

- ADR-001: Postgres as single perimeter (relational+vector+FTS under one RLS). Revisit at >50M chunks.
- ADR-002: Pooled multi-tenancy + PUBLIC_CORPUS synthetic tenant. Migration path: schema-per-tenant for enterprise tier.
- ADR-003: LiteLLM proxy self-hosted; task aliases, provider-agnostic app code.
- ADR-004: Docling for parsing; custom legal normalizer owns node typing. Docling gives typography, we give legal semantics.
- ADR-005: Refusal threshold τ applied post-rerank; refusal short-circuits before LLM call.
- ADR-006 (Proposed, 2026-11-02): Version guards. Chunks store `embedding_model`, `embedding_revision`,
  `chunker_version` and the chunk thresholds; retrieval filters on the active embedding version;
  re-embeds are built aside (shadow generation) and swapped atomically, never in place; a
  thresholds-only override that changes chunks without bumping `chunker_version` is the failure it
  prevents. Source: Library-RAG rule 6 / ADR-003, 004. Pending: choose generation-flip mechanism
  (`index_generation` column vs shadow table) in WS2.T08.
- ADR-007 (Proposed, 2026-11-02): The refusal is a system action, and τ is calibrated, not guessed.
  (a) The `<insuficiente>` block is emitted by code on τ-miss and on validator failure; the LLM is
  never asked to "decline in its own words" (measured: it explains that nothing was found and
  still fills layers). (b) τ is set from two hand-built probe sets (covered / absent topics, ≥ 20
  each, several wordings per topic) before the golden suite, and stored with the run. (c) Evaluate
  whether the gate should require ≥ m fragments above τ instead of max ≥ τ (Library-RAG gate:
  ≥ 2 sections ≥ 0.4). Pending: probe-set scores on the seeded corpus (WS3.T04). Does NOT change
  invariant 4.
- ADR-008 (Proposed, 2026-11-02): Multi-source synthesis stays direct generation over the
  expanded context. No map-reduce / per-document summarization as default in Dossier Builder or
  Adversarial. Evidence (Library-RAG ADR-017): fidelity 0.87 → 0.58, correct abstention 2/3 → 0/3,
  4× latency. Reopen only with a golden-suite run that beats direct on faithfulness AND refusal
  accuracy.
- ADR-009 (Proposed, 2026-11-02): Rewritten, translated and follow-up queries never replace the
  user's own query. The original query's top-N keep their ranks; secondary searches only fill the
  remaining slots; equal-weight RRF across variants is not used. First messages are searched as
  written. A rewrite can never alter jurisdiction/tenant (those come only from SessionContext).
  Evidence (Library-RAG ADR-038, 052): equal weight cost 6 pts recall@8 on the original set and
  synthesis recall 0.54 → 0.43–0.48. Pending: measure on the bilingual golden category (WS3.T06).

## Data flow (query path)
JWT → middleware sets app.tenant_id → SessionContext validated → jurisdiction predicate →
hybrid search (prefiltered) → RRF → rerank → τ check → parent expansion → prompt composer →
LiteLLM → citation validator → audit log → response.
Never reorder: predicate BEFORE vector search, validator AFTER generation, always.
