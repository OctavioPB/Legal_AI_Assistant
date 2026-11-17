# Architecture Rules

## Service boundaries
- `app/api/` — FastAPI routers. Thin: auth, SessionContext validation, delegation. No business logic.
- `app/rag/` — query analysis, jurisdiction predicate, hybrid search, rerank, parent expansion.
- `app/prompts/` — versioned templates (jinja), loaded read-only. Layer composer in `composer.py`.
- `app/orchestration/` — LiteLLM client, task-alias routing table, fallbacks.
- `app/validation/` — citation parser, existence/coverage checks, regeneration loop.
- `app/ingestion/` — Docling wrapper, legal normalizer grammars, chunker, embedder (Celery tasks only).
- `app/db/` — SQLAlchemy models, RLS session middleware, migrations (alembic).
- `app/api/files.py` + `app/ingestion/extract.py` + `normalizer_generic.py` — the firm's own documents
  (ADR-025): upload, text extraction in memory, one background thread, status in `client_files`.
- `app/corpus/` — reading the corpus without a model: reform lineage (`lineage.py`, ADR-017) and the brief
  verifier's grammar (`verify.py`, ADR-019). `app/ingestion/watch.py` + `reform.py`: the reform watcher, the only
  network-using component (official URLs of `corpus.py`).

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
- ADR-016 (Accepted 2026-11-08, chosen by the product owner): Three workspaces (Conversar, Investigación,
  Argumentar) with persisted conversations. Context: the single-query page became three pages modeled on the
  Library-RAG layout (top navigation, history sidebar, composer, sources panel). Decisions: (a) structure from
  Library-RAG, identity from Brand.md v1 (no colour/type change); (b) conversations and cases persist in
  Postgres (`conversations`, `messages`): tenant-private RLS template, listed per user, in the isolation
  registry and the offboarding erase order; a user may delete a conversation (the audit log keeps its own
  copy per ADR-013); (c) follow-ups: every message is searched AS WRITTEN (ADR-009); the ids already cited
  in the same conversation are carried, loaded under the same jurisdiction predicate and embedding
  generation, placed AFTER the current hits and only into the remaining context budget; the tau gate looks
  at the current query's hits only (invariant 4); jurisdiction, persona and language are fixed when the
  conversation is created (changing them is a new conversation, never an inference); the model sees the
  last N exchanges in the USER turn, clipped (free text never enters the system prompt); (d) sparring: a
  case holds facts, the user's side (closed list) and claim; each round the user writes an argument and
  asks either for the opposing counsel's attack (`adversarial@v1`) or to strengthen it (`argumentar@v1`);
  the retrieval query is the argument as written. Consequences: two new workflow prompts
  (`conversar@v1`, `argumentar@v1`) measured with ad-hoc real-model runs until the golden suite exists
  (WS5.T04); N, the history clip and the carry limit live in settings. Pending: golden before/after for
  both prompts; lawyer review of the side catalog.
- ADR-017 (Accepted 2026-11-08, chosen by the product owner): Scheduled reform watcher (ETL) with network
  egress confined to ingestion. Context: ADR-014 says the demo makes no network call; the product owner chose
  a scheduled download of official sources over a local inbox only or a manual button. Options: (A) local
  inbox only (ADR-014 untouched; a change is detected only if someone brings the file), (B) inbox + explicit
  download button, (C) scheduled download. Decision: C, amending ADR-014 for ingestion ONLY: (a) the query
  path (retrieval, models, validator, UI) still makes no external call; only `app.ingestion.watch` does, from
  a registry of official URLs in `app/ingestion/corpus.py` (closed list, never a user-supplied URL);
  (b) conditional GET (ETag / Last-Modified, then sha256); a 401/403/429 or a robots/bot block is recorded
  and the source is skipped, never evaded; generic User-Agent, one request per source per run; (c) a changed
  file is parsed by the same Docling path as the seed and ingested as a NEW document version through
  `ingest_document` (same embedding generation, invariant 5; the old version is retired, never deleted,
  invariant 3); (d) the change is diffed per citable unit (`cite_id` without its version suffix) into
  `corpus_changes` + `cite_lineage` (unchanged / amended / derogated / removed / added); (e) the processes
  adapt from the lineage, not from guesses: carried ids of a conversation follow `unchanged` successors,
  every conversation, matter source and verification that cites an amended/derogated/removed id raises an
  alert, the navigator shows the version history, and `doctor` warns when tau was calibrated on an older
  corpus; (f) a local inbox (`data/corpus/inbox/`) is also read, for sources without an official URL.
  Consequences: the machine needs internet for that job only; a reform re-embeds the whole law (minutes on
  CPU; reusing vectors of unchanged text is an optimization left open); the golden set and probe sets cite
  versioned ids and must be re-pointed after a reform (alert, not silent). Pending: per-source robots/terms
  review by a person; reuse of unchanged vectors measured.
- ADR-018 (Accepted 2026-11-08, chosen by the product owner): Matters (asuntos) with per-matter membership,
  enforced by RLS. Context: conversations were private to their author by an application filter only.
  Decision: (a) a second anchor `app.user_id` (set with `app.tenant_id` in the same `set_config` call,
  fail-closed: unset = NULL) and SQL helper `app_user_id()`; (b) `matters` is visible to its creator and its
  members (`matter_members`); notes and saved sources inherit the matter's visibility; (c) conversations and
  verification reports gain an optional `matter_id` and a RESTRICTIVE policy: visible to their author OR to
  the members of their matter; `messages` inherit their conversation's visibility; (d) `matter_members` stays
  tenant-private (a policy on it that read `matters` would recurse): it holds only ids, and the API lists
  members only of matters the caller can see; (e) only the matter owner adds/removes members, renames or
  deletes it (application rule over the RLS floor). Consequences: every product query on these tables must
  anchor the user; an unanchored transaction sees none of them (tested). The isolation registry lists more
  than one policy kind per table.
- ADR-019 (Accepted 2026-11-08, chosen by the product owner): Citation verifier for external documents keeps
  the report, never the document. Context: a brief can hold privileged client content. Decision: the text or
  PDF is processed in memory (a PDF goes through a temporary file deleted in `finally`); citations are
  extracted by deterministic code against a closed catalog of law names and resolved to `cite_id`s; checks
  are code only (exists, is current, quoted text matches verbatim after folding, with the same
  `min_quote_words` as the validator); no LLM judge (anti-roadmap). The stored report holds normalized
  references, statuses and cite ids, NOT the brief's text nor its quotations; the audit row records counts
  only. Consequences: a quotation that does not match cannot be shown later (only that it did not match);
  references to laws outside the corpus are reported as such, never guessed.
- ADR-020 (Accepted 2026-11-08, option A chosen by the product owner): Every citable unit rendered inside an
  expanded article is citable. Context: Argumentar answered 0/4 with the local model; the audit log showed the
  whole art. 47 rendered (parent expansion) while only the retrieved fracciones were in `also`, so a correct
  citation to fracción II, whose text the model had read, was rejected as invalid; the core already says "use
  only the ids of cite_id and also", so the model was guessing a real id that was not offered. Options: (A) list
  the units of an expanded article in `also` and make them citable (verbatim check against the article text, as
  for any `also` id); (B) a v2 of the case prompts against unmarked framing sentences; (C) both. Decision: A
  first, alone (B only if coverage failures remain), behind `context_citable_units` (default on). It aligns the
  code with rag.md ("retrieved set = the ids actually rendered into the context"); provenance is unchanged: no id
  is citable whose text was not shown. Units: same stored document version, no navigation chunks, non-current
  only when the session asked for historical text. Consequences: more ids in `also` (tokens counted in the
  budget); the gate still looks only at the query's own hits. Measured (ad hoc, 10 turns x 2 reps, gemma4:26b,
  `evals/runs/adr020-{before,after}-20261108.jsonl`): attempts with invalid citations 7/20 -> 0/20; answered
  Conversar 4/4 -> 4/4, Investigación 0/2 -> 0/2, Argumentar 0/4 -> 0/4 (no regression, no gain in outcomes:
  2 reps is noise-level). Every remaining failure is coverage: unmarked framing/strategy sentences in the case
  workflows (B's target) and, in research, list items split off a cited sentence; plus quotations of the USER's
  own argument flagged as non-verbatim statute text. Pending: golden suite (WS5.T04).
- ADR-021 (Accepted 2026-11-08, authorized by the product owner): A tag at the start of a line labels that line.
  Context: in Argumentar the real model writes "* **[razonamiento estratégico] Falacia:** Usted da por sentado...
  No obstante, ..."; only the first sentence counted as tagged, the rest as uncited legal assertions. Decision: a
  `[razonamiento estratégico]` (strategic workflows only) or `[análisis propio]` written before the line's first
  sentence terminator labels every sentence of THAT line (a tag alone on its line keeps labelling the paragraph,
  as before). Also closed a pre-existing hole: a tag written after a period used to stay inside the PREVIOUS
  sentence's unit (the splitter never cut before "["); now it starts the next unit (it never cuts before
  `[cite:`). Trade-off, accepted knowingly: this relaxes ADR-015's strict coverage for tagged lines; a legal
  claim inside a tagged line is shown as model reasoning (Brand.md §3, "sin respaldo normativo"), never as a
  citation, and any `[cite:]` in it is still validated. Evidence: re-validating the 16 stored attempts of the
  last real-model run with the same outputs: 0 accepted -> rejected; Argumentar's uncovered sentences drop
  (e.g. 14 -> 11) but no attempt passes: what remains is untagged framing, "Fuente faltante:" lines and action
  lists (option B). No live run was made for this rule alone.
- ADR-022 (Accepted 2026-11-08, option C chosen by the product owner): Explicit references are resolved, not
  ranked. Context: golden gd2-009 ("artículo 123, apartado B, fracción XIII de la Constitución") and gd2-025
  ("¿Qué decía el artículo 525 de la LFT?", historical session) were lost: the dense reranker cannot tell
  "art. 123 fracc. XIII CPF" from "art. 123-B-XIII CPEUM" (gold at rerank position 8-9 either way) and it scored
  the bare text, without the breadcrumb that names the unit; and a short unit stays under tau. Measured
  alternatives (retrieval-only, `evals/runs/golden-*-retrieval-{before,A,B,AB}`): (A) OR the lexemes of the
  lexical arm (websearch_to_tsquery ANDs them and returned 0 rows for natural questions): fixed gd2-009 by one
  rank position (noise), flooded the arm with long transitorios; (B) rerank breadcrumb + text: fixed gd2-025,
  changes the score scale (tau must be recalibrated); A+B lost gd2-009 again. Decision: (C) the citation grammar
  of the brief verifier (ADR-019) resolves references whose law is NAMED in the query to `cite_id` stems; the
  units come from `hits_by_stem` under the SAME jurisdiction predicate, embedding generation and vigencia
  filter (a named law outside the session is never pinned), go first, and an existing named unit passes the
  tau gate (the validator still checks every citation and quotation). "la Constitución" alone now names the
  CPEUM (not a state constitution). Retrieval-only result: recall@8 0.90 -> 1.00, 0 cases worse, leakage 0,
  gate refusals on refuse cases unchanged. A and B stay off (`lexical_match`, `rerank_breadcrumb`), measured
  and recorded; B is a separate experiment with tau recalibration. Full run (`golden-20261109T065813Z-after-
  explicit-reference` vs `golden-20261109T061348Z-baseline`, gemma4:26b): 0 cases worse; recall@8 0.90 -> 1.00;
  false refusal 5/20 -> 3/20 (gd2-011 "§ 203" now answered; gd2-009 answered); leakage 0; refusal accuracy 5/5
  unchanged (gd2-017/024 flips are the corrected cases, not this change). On by default
  (`retrieval_explicit_reference`). FOUND BY READING, NOT BY ANY METRIC: gd2-009 cites the right id (123-B-XIII)
  but paraphrases the text of 123-A-XIII: parent expansion renders the whole art. 123 (both apartados) as one
  fragment, so the validator (provenance, not faithfulness) accepts it. Pending: context unit = apartado when
  the unit is inside one (measured), lawyer review of the ai-draft golden set.
- ADR-023 (Accepted 2026-11-08, authorized by the product owner): The context unit of a unit inside an apartado is
  that apartado. Context: found by READING the review sheet of the ADR-022 run, not by any metric: gd2-009 cited
  123-B-XIII but paraphrased 123-A-XIII ("capacitación"), because parent expansion rendered all of art. 123
  (~34k characters, both apartados) as one fragment and the validator proves provenance, not faithfulness.
  Decision: with `context_unit_apartado` (default on) a unit under an "Apartado X" node (typed `parrafo` in the
  tree) expands to that apartado; a unit sitting directly in an article split into apartados (its caput) is its
  own context, so the whole article never comes back through it; the citable units of ADR-020 follow the same
  parent (apartado B never offers apartado A's ids). Affects 15 articles of the corpus (CPEUM 2, 6, 20, 26, 30,
  37, 72, 102, 122, 123; LFT 872, 968; three in the CPF). No re-chunking (context only). Measured (full run,
  `golden-20261109T073336Z-after-context-apartado` vs `golden-20261109T065813Z-after-explicit-reference`): gd2-009 now states the
  text of 123-B-XIII (no "capacitación"); 0 cases worse; false refusal 3/20 -> 2/20 (gd2-006 is not under an
  apartado: model variance, noise); recall, leakage and refusal accuracy unchanged. The harness gained
  `must_not_contain` (a narrow alarm on known confusions, not a faithfulness metric).
- ADR-024 (Accepted 2026-11-09, chosen by the product owner): The model is chosen per conversation from a closed
  catalog of profiles; the remote one is a firm opt-in. Amends ADR-014 for the `claude` profile ONLY.
  Context: the product owner asked to switch between Qwen, Gemma and Claude. Options considered for each axis:
  scope (per conversation / per user / per firm), remote access (forbidden / firm opt-in / free choice),
  default (measured before it becomes a default / by preference). Decision: (a) profiles `gemma` (default, local),
  `qwen` (local, every alias on the small model, no fallbacks) and `claude` (remote, `claude-opus-5-5` pinned from
  platform.claude.com/docs/en/about-claude/models/overview.md read 2026-11-09, all aliases, NO fallbacks: a
  failure or a provider decline never hands the conversation to another model); (b) `conversations.model_profile`
  is fixed at creation like jurisdiction and persona, recorded in the trace session and in every audit verdict;
  (c) `claude` is served only when `ANTHROPIC_API_KEY` is in `.env` (never the repo) AND the SDK is installed by
  whoever enables it (`uv add anthropic`; the local demo has no new dependency), and usable by a firm only after
  its admin opts in (`tenant_model_policy`, tenant-private RLS, in the isolation registry and the erase order;
  `tenants` stays provisioner-only); the opt-in is checked at creation AND before every message (withdrawing it
  stops remote conversations); (d) the same prompts, validator, refusal gate and citation rules apply to every
  profile (the system verifies the model, invariant 6); sampling parameters are not sent to Claude (not accepted),
  depth is `anthropic_effort`, and thinking headroom is added to the answer cap because thinking counts toward
  max_tokens; (e) the UI labels each profile "Medido" with the golden run that backs it
  (`config/model_measurements.yaml`, copied from the run report) or "Sin medir", and shows what leaves the machine
  before a remote conversation starts; the default changes only with a measurement. Consequences: with `claude`
  the question, history, case facts and retrieved fragments go to Anthropic (the query path is no longer local for
  that conversation; ADR-014 still holds for gemma/qwen and for retrieval, embeddings and the validator); a
  remote run of the golden suite costs money and needs the key, so `claude` stays "Sin medir" until then.
  `evals.run --model-profile` records the profile and its resolved ids. Measured (golden_v2, 25 ai-draft cases,
  same retrieval, so recall@8 1.00 and leakage 0 for both): gemma `golden-20261109T084417Z-after-quote-fixes`
  false refusal 1/20, refusal accuracy 5/5, p50 35 s; qwen `golden-20261109T170221Z-profile-qwen` false refusal
  17/20 (16 rejected by the validator, regeneration rate 0.72; the 3 answers had valid citations), refusal
  accuracy 5/5, p50 19 s. Qwen is safe (it refuses, it does not invent) but rarely useful for drafting; gemma
  stays the default. Pending: the Claude golden run; lawyer
  review of the golden set before any profile becomes the default; per-firm data-processing terms with Anthropic
  (legal, not code).

- ADR-025 (Proposed, implemented 2026-11-14 under the product owner's instruction "desarrolla los módulos
  faltantes"; the choices below await confirmation): The firm's own documents (client files), scoped to a matter.
  Context: the demo's exit criterion includes "upload de expediente" (WS6.T07, WS2.T01) and nothing let a lawyer
  work on their own contract, brief or ruling; the schema already reserved `doc_type='client_file'` and the `CF-`
  namespace (ADR-010). Options and choices: (1) processing: Celery worker (WS2.T01 as planned) vs ONE in-process
  background thread with the status in a table. Chosen: the thread (`WorkerJobs`), because the demo is one machine
  and parsing/embedding are CPU-bound; a job interrupted by a restart is reported as such after
  `client_file_stale_minutes`, nothing half-written exists (one transaction per document). Celery stays the path
  for a multi-process deployment. (2) what is kept: the ORIGINAL file is never stored (read in memory; a PDF goes
  through a temporary file deleted in `finally`, like ADR-019); the extracted text is stored in doc_nodes/chunks,
  which is what a citation needs. Consequence: no page deep-link for firm documents, only the text. (3) visibility:
  firm-wide only vs per matter. Chosen: optional `documents.matter_id`; `doc_nodes.matter_id`/`chunks.matter_id`
  derived by trigger and immutable; a RESTRICTIVE template `matter_scoped` (`matter_id IS NULL OR
  app_matter_visible(matter_id)`) on documents, doc_nodes, chunks and client_files, so a file of a matter is
  invisible to a colleague outside it and to any transaction without the user anchor (ADR-018). Deleting a matter
  CASCADES to its files: SET NULL would publish a privileged file to the whole firm. (4) retrieval scope: RLS
  alone would let a lawyer who belongs to two matters get client A's contract in client B's conversation.
  Chosen: `SessionContext.matter_id` and one more clause in `build_jurisdiction_predicate()` (the only place scope
  is decided): chunks without a matter, or of the conversation's matter; never another matter's. The hard
  jurisdiction rule is unchanged: a file is filed under a catalog jurisdiction and stacks like any other source.
  (5) grammar: statutes keep their grammars; any other document uses `normalizer_generic` (sections opened by
  clause openers or heading lines, typed `articulo` so they are the context unit; paragraphs typed `parrafo`;
  loose text grouped into "Parte n"; no schema change, no meaning inferred). A file declared as a law uses the
  statute grammar only if that grammar finds >= `client_file_min_articles` articles. Citations of a generic
  document name its own sections ("«Contrato», CUARTA.- PENA CONVENCIONAL, párr. 2"), never "art.".
  (6) generation: the same embedder, revision, chunker version and thresholds as the live corpus; the guard of
  `ingest_document` now also reads the PUBLIC live set, so a firm file can never be written in another generation
  than the corpus it is searched with (invariant 5). `CHUNKER_VERSION` is not bumped: no existing chunk changes
  (the `plain` citation style and the generic grammar only apply to new client files). (7) deletion: the uploader
  or a firm admin deletes a file with its text and vectors (a client's document must be erasable; invariant 3 is
  about reforms of the public corpus). A conversation that cited it keeps the citation's name; the source is 404.
  (8) formats: PDF with a text layer (Docling, OCR off), .docx (standard library, body paragraphs only; DTD and
  oversized XML refused), plain text; recognised by content, 25 MB, sent through the gateway as
  `application/octet-stream` (not a CORS-simple type). Scanned PDFs fail with `no_text`. Evidence: 12 tests in
  `tests/test_client_files.py` (RLS floor for a colleague, another firm and an unanchored transaction; retrieval
  scope for a member of two matters; cascade; generation mismatch; audit rows without title or text); real stack
  (bge-m3, seeded corpus): a .docx contract ready in 7 s, its clause first at 0.753 inside the matter, a refusal
  outside it. Retrieval of the public corpus is unchanged by construction (every existing chunk has no matter):
  `golden-20261114T210010Z-after-matter-scope` vs `golden-20261109T064012Z-retrieval-C` (retrieval-only, 25 ai-draft cases): recall@8 1.00 -> 1.00, leakage 0 -> 0, 0 cases flipped. tau was calibrated on statute text; scores of contract prose were not part of the probe sets.
  Pending: owner confirmation of (1), (3) and (7); lawyer review of the generic grammar on real contracts and
  rulings; probe sets with firm documents; OCR; moving a file between matters; footnotes of .docx files.

- ADR-026 (Proposed, 2026-11-17; options A + C chosen by the product owner, the approval channel awaits
  confirmation): Discover laws from official indexes; give every loaded source an official route. Amends ADR-017
  (a) for the network list. Context: the watcher only followed reforms of the 8 laws loaded, 4 of them through the
  hand-fed inbox; nothing told a lawyer that a law exists that is not loaded. Options: (A) read official indexes
  daily and PROPOSE, a person approves; (B) discover and load automatically; (C) only add URLs for the 4 inbox
  sources. Decision: A + C. (1) Indexes are a closed registry too (`app/ingestion/indexes.py`, today only
  Cámara de Diputados "Leyes Federales Vigentes", 318 entries on 2026-11-17), read with the same rules as a law
  (https, robots.txt, one request, conditional GET, a block recorded and never evaded) and parsed by
  deterministic code; a parse with fewer than `min_entries` rows fails (`IndexLayoutError`) instead of marking the
  catalog gone. Reading an index downloads NO law. (2) `corpus_index_entries` (public corpus): the first read files
  everything as `available`, a later new entry is `new`, an absent one gets `missing_since` (never deleted).
  (3) Approval: `python -m app.ingestion.discover approve INDEX:KEY --domain ... --by ...` writes
  `corpus_sources`; jurisdiction, scope, language and grammar come from the index's closed spec, legal domains from
  the closed `LEGAL_DOMAINS`, the doc type from the title's first word (else `--doc-type`), the cite_id code from
  the official file name; a clash with an existing code or URL is refused. The watcher reads the static registry
  plus `corpus_sources` and ingests an approved law's FIRST version (no lineage), then its reforms like any other.
  Approval is an operator command, not a button: the public corpus is shared by every firm and the API's role
  can never write it (ADR-010 c, tested); the web shows the catalog read-only (`GET /reforms/indexes`).
  (4) C: CC_NL (hcnl.gob.mx) and both Monterrey regulations (monterrey.gob.mx) return byte-for-byte the files in
  SHA256SUMS; robots.txt of both hosts allows those paths. California's site answers 403 to automation, so the
  route is its official bulk directory: the newest `pubinfo_YYYY.zip` (~1.2 GB, weekly, ETag, byte ranges) is
  checked with a HEAD daily and, when it changed, read by HTTP ranges (central directory, the 6 MB section table,
  the code's section texts), never downloaded whole; a cache of section texts makes a weekly update re-read only
  the changed sections (the first run reads every section once, ~2 700 requests, capped by
  `leginfo_max_requests`). The extraction is shared with `scripts/extract_ca_code.py`, so the text matches the seed.
  Consequences: the watcher's network list grows by one index page and three hosts; a law approved this way is
  searchable and citable, but the brief verifier and the explicit-reference resolver (ADR-019/022) do not know its
  NAME until a person adds it to their grammar; tau was calibrated on the current corpus (doctor warns after the
  next ingestion, ADR-017 e). Pending: confirm the operator-only approval (alternative: a platform-admin role in
  the web, which needs a separate privileged service); a person reviews the terms of use of hcnl.gob.mx,
  monterrey.gob.mx and leginfo; state and municipal indexes (Periódico Oficial de NL, Gaceta Municipal de
  Monterrey) one at a time, each after that review.

## Data flow (query path)
JWT → middleware sets app.tenant_id → SessionContext validated → jurisdiction predicate →
hybrid search (prefiltered) → RRF → rerank → τ check → parent expansion → prompt composer →
LiteLLM → citation validator → audit log → response.
Never reorder: predicate BEFORE vector search, validator AFTER generation, always.
