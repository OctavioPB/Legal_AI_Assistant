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

- ADR-010 (Proposed, 2026-11-06): Deviations from the blueprint's DDL made while building WS1.
  Context: the blueprint's `chunks.cite_id UNIQUE` is a cross-tenant existence oracle (tenant A
  inserting a cite_id learns from the conflict that B holds it), its policies let a session
  anchored on PUBLIC_CORPUS write it, and `current_setting('app.tenant_id')` raises (or, once
  set, falls back to public rows) instead of failing closed. Decision: (a) `UNIQUE (tenant_id,
  cite_id)` plus disjoint namespaces — client-file ids start `CF-`, public ids never do (CHECK);
  (b) policies are fail-closed: no `app.tenant_id` ⇒ zero rows, public corpus included; (c) the
  app role can never write PUBLIC_CORPUS, only `corpus_ingestor` (insert/update, no delete);
  (d) a fourth role `legalai_provisioner` is the only writer of `tenants`; (e) product tables
  (users, sessions, dossiers, audit_log) are tenant-private — the "own tenant OR PUBLIC_CORPUS"
  read rule applies only to documents, doc_nodes and chunks; (f) every child row references its
  parent through a composite FK that includes `tenant_id`; (g) `chunks.country/level/state_code/
  municipality/doc_type/language/legal_domain/jurisdiction_id` are derived by trigger from the
  document and the catalog; (h) the anchor is `set_config('app.tenant_id', :t, true)` because
  `SET LOCAL` cannot take bind parameters. Consequence: citations to client files need the
  `CF-` prefix (rag.md); client-file cite_ids may repeat across tenants. Pending: user
  confirmation of (a) and (d), the two that change contracts other workstreams consume.
- ADR-011 (Proposed, 2026-11-06): Retrieval plan shapes under RLS, measured on 3 000 chunks
  (tests/test_schema.py). A selective jurisdiction filter is served by `chunks_prefilter_idx`
  and the survivors are ranked exactly (no ANN); an unselective query uses HNSW with the RLS
  predicate as a scan filter, so every vector query sets `hnsw.iterative_scan` per transaction
  (else a filtered top-k can come back short). Consequence for WS3: do not force HNSW for
  selective sessions (a municipal session is exact search, by design). Pending: re-measure at
  corpus scale (WS2.T07 seed) and at 50M-chunk tripwire (ADR-001).
- ADR-012 (Superseded by ADR-014 for the embedder window, 2026-11-07): Chunk size vs the embedder window. Context: Cohere
  embed-multilingual-v3.0 accepts 512 tokens (1024 dims) and `truncate` defaults to `END`, i.e.
  text past the window is dropped silently (docs.cohere.com/docs/cohere-embed and /reference/embed,
  read 2026-11-06). rag.md's `max_tokens` 800 would be truncated by the vector arm. Options:
  (A) keep embed-v3, lower `max_tokens` to 400 so breadcrumb + text fit in 512, always send
  `truncate=NONE` so an overflow fails loudly; (B) embed-v4.0 (128k window) — its default is 1536
  dims, 1024 is selectable, and it changes the model pinned in ADR-006; (C) keep 800 and accept
  truncation (rejected: violates the zero-silent-loss rule). Decision (provisional): A, implemented
  as `ChunkThresholds(max_tokens=400, embedder_window=512)`, refusing any max > window; thresholds
  are stored per chunk. Consequence: more, smaller chunks (re-measure parent expansion budget);
  token counts use `ApproxCounter` (x1.3 words) until the Cohere tokenizer is wired in WS2.T06.
  Pending: golden run comparing A vs B once WS5.T04 exists; user choice A/B.
- ADR-013 (Proposed, 2026-11-07): Tenant offboarding and the audit log. Context: `audit_log` is
  append-only for every role (privileges + trigger, ADR-010) and holds `query_text`/`output_text`, i.e.
  client content; offboarding must "leave no trace" (WS7.T06) yet a firm may owe a retention period.
  Options: (A) erase it with the rest, as an explicit superuser step that bypasses the trigger in one
  transaction; (B) retain it for a contractual period, then erase; (C) retain forever (rejected: client
  content outlives the contract). Decision (provisional): the code supports A and B; the DEFAULT is
  retain (`PostgresStore(audit="retain")`), erasure must be asked for explicitly, and the tenant row is
  tombstoned (`status='suspended'`), never deleted. Consequence: offboarding is only "zero traces" once
  the retention period is chosen. Pending: user/legal choice of A vs B and the retention length.
- ADR-014 (Accepted 2026-11-07, chosen by the product owner): The demo runs 100% locally. No provider,
  API key or network call is involved. Stack: Ollama on loopback serves (a) the embedder `bge-m3`
  (1024 dimensions = the existing column, multilingual es/en, 8192-token window; revision = model
  digest `790764642607`), (b) `gemma4:26b-a4b-it-qat` for drafting/adversarial/heavy and
  `qwen3:4b-instruct-2507-q4_K_M` for utility/fallback. Pins (digest, context length, date, source) are
  generated from the runtime into `config/models.local.yaml` and checked for drift. Consequences:
  (1) ADR-012 is superseded for the window: chunk thresholds return to rag.md's 80/800 tokens, counted
  with the REAL bge-m3 tokenizer (about 1.4 tokens per word; the old x1.3 approximation under-counted
  ~8%); the real corpus now has 0 chunks over the window and 46-90% fewer sub-minimum chunks;
  (2) Cohere embed-v3 is not used, so its 512-token window and `truncate=END` hazard disappear;
  (3) changing embedder or thresholds is a new generation (ADR-006), built aside; the first real
  ingestion defines the baseline, so there is no golden "before" for this switch;
  (4) the installed torch is CPU-only (no CUDA): a cross-encoder runs on CPU. Rerank = dense cosine
  (baseline) vs bge-reranker-v2-m3 cross-encoder is decided by `evals.calibrate_tau` (AUC and the
  false-refusal cost), not by assumption. Pending: lawyer review of the AI-drafted probe sets; golden
  suite before/after for any later change of model, prompt or threshold.
- ADR-015 (Accepted 2026-11-07, chosen by the product owner): Coverage stays STRICT. Context: with the real
  local model (gemma4:26b), 7 of 12 long-form queries were answered (baseline and after the verification
  fixes, identical: 1-2 cases of difference is noise). Every one of the 10 failing attempts inspected failed
  ONLY on coverage: correct closing sentences ("Asimismo, existe una figura de intereses moratorios...") with
  no citation; zero invented ids, zero non-verbatim quotations after the fixes. Options: (A) keep strict,
  (B) demote an uncited sentence to a shaded "Assistant analysis - no normative support" and deliver the
  answer, (C) allow a second regeneration. Decision: A. The contract "no uncited legal assertion reaches the
  user" is not relaxed; the answer rate is improved only by instructions (workflow prompt versions) and by
  verification bugs fixed, each measured. Consequence: long enumerations (art. 47 has 15 fracciones) are
  refused some of the time; the refusal is a first-class output, never an error. Revisit with a golden run
  (WS5.T04) and lawyer-reviewed cases before reconsidering B. Evidence: PLAN.md log 2026-11-07, runs in
  `evals/runs/`.

## Data flow (query path)
JWT → middleware sets app.tenant_id → SessionContext validated → jurisdiction predicate →
hybrid search (prefiltered) → RRF → rerank → τ check → parent expansion → prompt composer →
LiteLLM → citation validator → audit log → response.
Never reorder: predicate BEFORE vector search, validator AFTER generation, always.
