# Architecture Rules

## Service boundaries
- `app/api/` — FastAPI routers. Thin: auth, SessionContext validation, delegation. No business logic.
- `app/rag/` — query analysis, jurisdiction predicate, hybrid search, rerank, parent expansion.
- `app/prompts/` — versioned templates (jinja), loaded read-only. Layer composer in `composer.py`.
- `app/orchestration/` — LiteLLM client, task-alias routing table, fallbacks.
- `app/validation/` — citation parser, existence/coverage checks, regeneration loop.
- `app/ingestion/` — Docling wrapper, legal normalizer grammars, chunker, embedder (Celery tasks only).
- `app/db/` — SQLAlchemy models, RLS session middleware, migrations (alembic).
- `app/api/files.py` + `app/ingestion/extract.py` + `normalizer_generic.py` (+ `pdf_read.py`, `ocr.py` for scanned
  pages, ADR-030) — the firm's own documents
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
- ADR-006 (Accepted 2026-11-26, generation column chosen by the product owner): Version guards. Chunks store `embedding_model`, `embedding_revision`,
  `chunker_version` and the chunk thresholds; retrieval filters on the active embedding version;
  re-embeds are built aside (shadow generation) and swapped atomically, never in place; a
  thresholds-only override that changes chunks without bumping `chunker_version` is the failure it
  prevents. Source: Library-RAG rule 6 / ADR-003, 004. Pending: choose generation-flip mechanism
  (`index_generation` column vs shadow table) in WS2.T08.
  Implemented 2026-11-26 (migration 0015, `app/ingestion/rebuild.py`): a `generation` column on doc_nodes and
  chunks, not a shadow table; `index_generations` holds one `active` row, `app_generation()` is the default of
  every new row, and a RESTRICTIVE `generation_scoped` policy shows and lets every role under RLS write only the
  active generation, so no existing query changed. `documents` are not versioned (lineage, firm files and alerts
  keep their rows). The rebuild runs as the superuser (an explicit step), writes generation N+1 aside and stops
  instead of writing a different corpus: a law's source must hash to its stored checksum, a firm document (whose
  original is never stored, ADR-025) is rebuilt from the text of its tree and must keep the same ids with the same
  text. `activate` (refused while a live document has no tree in N: `fill`) and `rollback` are one transaction;
  `prune` never touches the active generation. `doctor` checks one active generation, none half-built, and that
  the live index was cut by the code's chunker (otherwise ingestion refuses, invariant 5). Evidence: 8 tests in
  `tests/test_index_generations.py`; on the real corpus (20 473 chunks, 10 documents including one firm contract)
  generation 2 was built in ~15 min with bge-m3 on CPU+GPU, activated, measured and rolled back (ADR-049). Found
  while building it: the migrator is under FORCE RLS, so generation 1's model and chunker are recorded by the
  first rebuild, not by the migration; ltree orders labels as text (`s10` before `s2`), so a document's
  sections are read by `ordinal` (the rebuild's own unit check caught it before anything was written).
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

- ADR-027 (Accepted 2026-11-17, every option chosen by the product owner): A legal news feed of 30 items, from official
  sources and press, chosen by fixed rules, shown in the sources' own words. Amends ADR-017 (a) again: the job
  that may reach the network now also reads a closed list of news sources, and for the first time some are NOT
  official (press). Options asked: sources (official + press / official only / press only), relevance (fixed rules
  / rules + local model / model only), text shown (source's own / plus a model summary marked unverified).
  Decision: official + press, fixed rules, source's own text. (1) Sources (`app/news/sources.py`): internal (the
  reforms the watcher recorded; the official index's new laws and reforms of the last `news_recent_days`), SCJN
  press-release listing, and three RSS feeds (Nexos "El juego de la Corte"; El Financiero and La Jornada Política,
  which only contribute items that name legal terms). Checked 2026-11-17 and left out: Google News RSS (its
  robots.txt disallows our client), Proceso and El Economista (403), DOF (no answer), Gaceta Parlamentaria (403).
  Same rules as the watcher: https, robots.txt, one request, conditional GET, a block recorded as `news:<id>` and
  never evaded; RSS with a DTD/entities is refused before parsing. (2) Relevance (`app/news/score.py`): source
  weight (official 2.5-3, legal blog 1.5, general press 1) + legal terms from a closed catalog on folded text
  (capped at 3; "iniciativa" only in its legislative sense) + recency (half-life `news_half_life_days`); the same
  story from two sources once (title similarity across sources only: one index lists distinct laws with
  near-identical titles); at most `news_max_per_source` per source; the matched terms are stored and shown
  ("Por qué aparece"). (3) Text: title, outlet, date and the summary the feed itself publishes, clipped to
  `news_excerpt_chars`; no article body is fetched, nothing is generated, so the zero-hallucination contract is
  untouched (the model is not involved). (4) Storage: `news_items` under the public corpus tenant (one feed for
  every firm, written only by `corpus_ingestor`, the API reads it: `GET /news`); an item that leaves the top
  `news_feed_size` is retired (`in_feed = false`), never deleted; an item keeps the day it was first seen.
  (5) Runs after the reform watcher in the daily task (`dev-up.ps1 -WatchReforms`), also when Ollama is down (it
  needs no model). Consequences: press outlets appear inside a legal product, labelled as press and never as
  sources of an answer; their terms of use are the firm's to review; the feed shows official items above press by
  construction, so a slow news week shows fewer than 30 items rather than filler. Pending: a person reviews the
  terms of use of each press source; a source of legislative proposals (initiatives) that allows automated
  reading (the Gaceta Parlamentaria refuses it).

- ADR-028 (Accepted 2026-11-17, every option chosen by the product owner): Key-points sheet (hoja de puntos clave) of a
  matter, generated by the model and verified like an answer, printed on one page by the browser. Options asked:
  scope (a whole matter / one Argumentar case), verification (validated like answers / free summary marked
  unverified), PDF (browser print / server-side file). Decision: whole matter, validated, browser print.
  (1) Material (`app/chat/cheatsheet.py`): the matter's newest `cheatsheet_conversations_max` conversations (their
  last `cheatsheet_turns_per_conversation` exchanges, case facts/side/claim), notes and saved sources, read under RLS
  with the user anchor; free text travels in the USER turn only, clipped to `cheatsheet_material_chars`.
  (2) Sources: ONLY units the matter already cited (message citations + saved sources), each reloaded under the
  jurisdiction predicate and embedding generation of a conversation that cited it (`hits_by_cite`), after the
  reform lineage (`carry_forward`); most-cited first, at most `cheatsheet_max_sources`; nothing new is searched (the
  sheet summarises, it does not research). No source -> system refusal `retrieval.no_matter_sources` before any
  model call (ADR-005). (3) Prompt `workflow/hoja_clave@v1` (new layer-2 file, pinned): four fixed sections with
  line limits that fit one page; legal points cite; risks cite or are marked; questions for the client and topics
  to review are `[razonamiento estratégico]` (the workflow joins STRATEGIC_WORKFLOWS); the validator, one
  regeneration and the system refusal are unchanged. (4) `matter_cheatsheets` (private + matter_child: as visible as
  the matter; cascades with it), refusals stored as such; audit row + trace per generation. (5) Model per
  generation from the closed catalog, opt-in checked (ADR-024). (6) `/asuntos/hoja` prints on letter size; the
  browser test generates the PDF and requires one page. Consequences: the sheet can only be as good as the work in
  the matter (no sources -> no sheet); a point the model writes about the facts must be marked reasoning or the
  validator refuses; the sheet is client work, so it follows the matter's privacy and erasure. Measured (ad hoc, gemma4:26b, same matter, 3 per
  arm): with earlier answers' `[cite:]` markers in the material 3/3 sheets, all citing the WRONG fracción copied from
  the case round; markers dropped (`cheatsheet_strip_material_cites`, default on) 2/3 sheets, both citing correctly,
  1 system refusal. Fewer sheets, no wrong citation: the contract's choice. Pending: lawyer-reviewed golden cases for this workflow; whether
  the 6/4/6/4 line limits are the right shape for a trial (lawyer feedback).

- ADR-029 (Accepted 2026-11-17, option chosen by the product owner): A daily ETL canary on GitHub; the loading stays on
  the machine with the database. Options asked: (A) canary on GitHub-hosted runners that reads every source and
  never writes; (B) a self-hosted runner on the demo machine that runs the real watcher and news pipeline under
  GitHub's schedule; (C) both. Decision: A. `app/ingestion/canary.py` (standard library only, with the same readers as
  the ETL: `net`, `indexes`, `leginfo`, `app.news.sources`) runs daily in `.github/workflows/etl-canary.yml` with
  `uv run --no-project --isolated`, so nothing is installed and no secret exists. For every law of the registry it
  sends a HEAD (California: the listing plus a HEAD of the newest archive, never the archive); for every official
  index and network news source it sends one GET parsed by the real reader. robots.txt is honoured once per host;
  every outcome is named (ok / blocked / robots / unreachable / layout); a failing run opens or comments one
  tracking issue, a clean one closes it. Amends ADR-017 (a): the official URLs are now also read, once a day, from
  GitHub's (US) addresses. That is still read-only and one request each, but a source that blocks data-centre
  addresses shows as blocked there while it works from the demo machine; the report says so. Consequences: a
  layout change or a block is seen within a day even when the demo machine is off; the ETL itself (writes,
  embeddings) is unchanged. Tested by `tests/test_canary.py` (each outcome, one request per source, robots once per
  host, stdlib-only import, workflow without secrets).

- ADR-030 (Accepted 2026-11-26, every option chosen by the product owner): Scanned pages of a firm's PDF are read by
  OCR, page by page, and a page read with low confidence is left out. Context: a scanned contract failed with
  `no_text` (Docling ran with OCR off, ADR-025 listed OCR as pending); the owner's other project, Markdown-Conversor
  (tomd, its ADR-017), already reads scans locally with Tesseract. Options asked: integration (tomd as a pinned
  dependency / copy tomd's OCR module / Docling's built-in OCR), low-confidence pages (exclude and report / keep with a
  warning / reject the file), trigger (auto + an optional "read as scanned" / auto only / always). Decision: copy,
  exclude and report, auto + optional force. (1) `app/ingestion/ocr.py` is adapted from tomd's
  `readers/pdf/ocr.py` and `extract.py` at commit 4ad49eb: the gate (fewer than 20 characters under an image
  covering > 50 % of the page, or > 30 % garbage characters), rendering at 300 dpi within a 40 MP budget, contrast
  stretch, orientation by OSD, deskew by projection profile (±3°), table rules erased, hOCR words with confidence,
  speckles dropped; tomd's font size, bold, bullet and table-grid logic is not copied (this project needs paragraphs,
  not Markdown). Tesseract 5 with `spa+eng` data, via `pytesseract` (Apache-2.0), is a system dependency, found in
  settings, PATH or the default Windows install; tessdata in settings, `%LOCALAPPDATA%/legalai|tomd/tessdata`, or
  Tesseract's own. Docling's OCR was not chosen: it downloads OCR models (network at install, ADR-014) and gives no
  per-page confidence. (2) `app/ingestion/pdf_read.py` reads a PDF page by page in order: runs of pages with a text
  layer go through Docling with `page_range` (the same parser and clean-up as before; a digital PDF takes exactly the
  previous single call), flagged pages go through Tesseract. (3) An OCR page whose mean word confidence is under
  `ocr_min_confidence` (0.6, tomd's threshold) is NOT stored: a misread figure in a clause must never be citable, and
  the verbatim check would only compare a quotation with the misreading. The upload lists the pages left out and asks
  for a rescan; if every scanned page is poor the upload fails (`ocr_low_confidence`). (4) "El texto del PDF está
  dañado: leerlo como escaneado" (`force_ocr`) reads every page as an image. (5) `client_files` records `ocr_pages`,
  `ocr_excluded_pages`, `ocr_confidence`, `ocr_engine` (version and languages, as provenance) and `force_ocr`
  (migration 0013, numbers only); the source viewer and the document list say a text was read by OCR and with what
  confidence. Failures are keys: `ocr_unavailable`, `ocr_failed`, `ocr_low_confidence`, `too_many_scanned_pages`
  (`ocr_max_pages` 200, ~3-4 s per page). `doctor` warns when Tesseract or a language is missing. Consequences: the
  rendered page reaches the disk briefly, as pytesseract's temporary image (deleted when the call returns), like the
  PDF's own temporary file under ADR-025; OCR text is citable and verified against the recognised text, never the
  paper, so a kept page can still contain a misread word above the threshold (the viewer says so); the copy will drift
  from tomd and a fix in one must be ported by hand; the brief verifier (ADR-019) still reads text-layer PDFs only;
  no chunk, embedding or prompt changes (no golden run needed, invariant 2), and tau was calibrated on statute text,
  not OCR'd contracts. Evidence: `tests/test_ocr.py` (gate, rules, skew, hOCR, page-order assembly with fakes, and
  real Tesseract reads of a generated scan, upright and upside down) and two API tests in
  `tests/test_client_files.py` (a kept OCR page is citable and the viewer says so, a poor page is in no unit, forced
  OCR skips the text layer, poor / missing engine / too many pages fail closed, audit holds counts only). Pending: a
  real scanned contract from a firm, a lawyer's check of its extracted text; OCR in the brief verifier; whether 0.6 is
  the right threshold for legal text (tomd measured it on books).

- ADR-031 (Accepted 2026-11-26 in warning mode, option D chosen by the product owner; C, refusing, stays Proposed): A citation whose legal regime contradicts the case is caught by code, from the law's own
  structure. Context: in E2E-LAB-01 five of eight delivered answers cited real, verified articles that do not govern the
  facts; the validator proves provenance, not applicability (rag.md). Three of the five are visible in the stored
  breadcrumbs (checked 2026-11-26): LFT 283 Ter sits under "TITULO SEXTO Trabajos Especiales › CAPITULO VIII Personas
  Trabajadoras del Campo", 341 under "CAPITULO XIII Personas Trabajadoras del Hogar", 436 under "TITULO SEPTIMO
  Relaciones Colectivas de Trabajo", while the case is an individual relationship of a billing assistant. Options:
  (A) do nothing (applicability stays lawyer-only); (B) an LLM judge of applicability (cost: the model verifying the
  model, against invariant 6, and no measured agreement); (C) deterministic regime tags per article derived from the
  Título/Capítulo of the tree (special regimes, collective vs individual, and per-article "direction" tags such as
  "causal del trabajador" written once per law by a lawyer), plus a closed-list "type of relationship" field on the case
  sheet, and a check that flags or refuses a citation whose regime contradicts the case, like the jurisdiction
  predicate; (D) C, but only as a warning shown next to the citation. Recommendation: C, starting as D until lawyer
  review confirms the tags. Consequences: (+) the first automated check aimed at applicability, catching 3/5 of the
  observed errors with no model; (−) per-law tagging work, and it cannot catch errors inside one chapter (art. 51 vs 47,
  art. 519 vs 518 both under Prescripción) without the per-article direction tags; some chapter titles were dropped at
  ingestion ("CAPITULO IV" without its name), so the normalizer must keep them first (a chunker change: new generation,
  ADR-006, and a golden run). Pending: owner's choice C or D; lawyer review of the regime catalog for the LFT; a
  golden category "applicability" with the five E2E-LAB-01 errors as cases. Implemented 2026-11-26: `app/corpus/regimes.py` reads the regime from Título and Capítulo of LFT units only (Título Sexto chapters II-XVII are special regimes, labelled with the law's own chapter names taken from data/corpus/text/LFT.txt because the stored breadcrumbs lose some; Capítulo I applies to every special regime; Títulos Séptimo and Octavo are collective law; everything else, and every other law, has no regime; the Constitution's own "Título Sexto" is never confused); `matters.labor_regime` (migration 0014, closed list `CASE_REGIMES`, `GET /matters/regimes`) and every answer's citations carry `regime`, `regime_label` and `applicability` (`special_regime` / `collective_regime`), shown next to the source in Brand.md's notice style. Evidence: over the real stored breadcrumbs of the 19 units cited by E2E-LAB-01's eight delivered answers, with the matter declared ordinary, it flags exactly arts. 283 Ter, 341 and 436 and nothing else (`tests/test_regimes.py`); the other two misapplied citations (art. 51 for the employer, art. 519 instead of 518) are inside one chapter and need the lawyer-written direction tags. Found while building it, recorded under ADR-049: Títulos numbered with cardinal words (ONCE to DIECISEIS) are not recognised, so the LFT's whole procedural part sits under "TITULO DECIMO Prescripción" in its breadcrumbs, and Capítulo names on the line after the heading are lost. Research: docs/RESEARCH.md §1.
- ADR-032 (Accepted 2026-11-26 for the statistics, option B chosen by the product owner; growing the set stays Pending): Every reported rate carries an exact 95 % interval, and the golden set is sized for the
  claims we want to make. Context: the headline numbers are point estimates on tiny denominators; exact
  (Clopper-Pearson) intervals computed 2026-11-26: correct refusals 5/5 -> 48-100 %; false refusal 1/20 -> 0.1-25 %;
  leakage 0/5 isolation cases -> up to 52 %, 0/25 overall -> up to 14 %; misapplied E2E answers 5/8 -> 25-92 %. With zero
  failures, a one-sided 95 % bound below 5 % / 2 % / 1 % needs 59 / 149 / 299 cases. Options: (A) keep the "1-2 cases is
  noise" rule only; (B) report Clopper-Pearson intervals in every run report and README, compare before/after with an
  exact McNemar test on the per-case flips the runner already stores, and grow the set toward 60 isolation and 60 refusal
  cases (lawyer-reviewed); (C) Bayesian beta-binomial posteriors (same data, harder to explain to lawyers). Recommendation:
  B. Consequences: (+) claims become statements with bounds; the noise rule becomes a test; (−) the honest intervals are
  wide today and will look worse than the point numbers; case authoring is lawyer time. Pending: owner's choice; lawyer
  authors/reviewers for the new cases. Implemented 2026-11-26: `evals/stats.py` (Clopper-Pearson and exact McNemar, standard library, checked against scipy and a textbook value), `summary.intervals` and `intervals_by_category` in every run report, `evals.run --intervals <run>` to re-score stored runs, and `--compare` now calls a change an improvement only when McNemar gives p < 0.05 (3 better / 0 worse is p = 0.25: within noise); the README quotes every rate with its interval. Still Pending: lawyer-authored or reviewed cases toward 60 isolation and 60 refusal cases. Research: docs/RESEARCH.md §2.
- ADR-033 (Proposed, 2026-11-26): The model emits each sentence as a typed record (law / reasoning / framing, with its
  citations) under a JSON schema, and the validator checks fields. Context: every failing attempt inspected for ADR-015
  failed only on coverage (correct but uncited closing sentences); Qwen's false refusal is 17/20 with a 0.72 regeneration
  rate; the sentence splitter is heuristic (rag.md lists its special cases). Ollama accepts a JSON schema for structured
  output. Options: (A) keep free text + splitter; (B) schema-constrained output `[{text, kind, cites}]`, rendered by
  the UI, where a `law` record without a valid citation is still refused (contract unchanged) and `framing` becomes an
  explicit, rendered category instead of a coverage failure; (C) a prompt v2 against framing sentences (ADR-020 option
  B). Recommendation: B, measured against C. Not adopted: Chain-of-Verification as a check, because it is the model
  verifying itself (invariant 6). Consequences: (+) fewer false refusals without relaxing the contract; no sentence
  splitting on the validator side; (−) new prompt versions for every workflow (golden before/after, invariant 2);
  schema adherence of small models must be measured; `framing` must be defined narrowly or it becomes a loophole for
  uncited law. Pending: owner's choice; golden runs before/after per profile. Research: docs/RESEARCH.md §5.
- ADR-034 (Proposed, 2026-11-26): A small local fact-checker scores each cited sentence against its fragment, shown as a
  warning until its agreement with lawyers is measured. Context: `faithfulness` is null in every run; the validator checks
  only verbatim quotations, so a paraphrase that inverts its source passes (rag.md). MiniCheck (Tang, Laban, Durrett,
  EMNLP 2024) is a 770M Flan-T5 checker that matches GPT-4 on grounding benchmarks at ~400x lower cost. Options: (A) do
  nothing; (B) an LLM judge with the drafting model (expensive, self-verification); (C) MiniCheck locally, per verifiable
  claim (VeriScore-style extraction of only verifiable claims), as a warning "Fidelidad no verificada" next to the
  citation; promoted to a gate only after measured agreement with lawyer grades (rag.md already requires this);
  (D) C, but gating from day one. Recommendation: C. Consequences: (+) the first number for faithfulness; a second,
  independent check on paraphrase; (−) MiniCheck is trained on English: its accuracy on Spanish statute text is unknown
  and is the first thing to measure; CPU latency per claim; a judge model is pinned like any other (ADR-014 rules).
  Pending: owner's choice; agreement vs lawyer grades on E2E-LAB-01 and the golden set (ADR-035). Research:
  docs/RESEARCH.md §3.
- ADR-035 (Proposed, 2026-11-26): Faithfulness and applicability are estimated with prediction-powered inference over a
  small set of lawyer labels, with inter-rater agreement reported. Context: lawyer time is the bottleneck and today no
  answer has been graded; a judge alone gives biased numbers. Prediction-powered inference (PPI), as used by ARES
  (Saad-Falcon et al.), combines a few human labels with many judge labels and yields valid confidence intervals that
  correct the judge's bias. Options: (A) lawyer-grade everything (unaffordable at scale); (B) judge only (biased, no
  valid interval); (C) PPI: lawyers grade a stratified sample (every category, plus cases where the validator and the
  judge disagree), the judge scores everything, the PPI estimator gives the interval; two lawyers grade an overlapping
  subset and Cohen's kappa / Krippendorff's alpha is reported. Recommendation: C. Consequences: (+) defensible intervals
  from ~50-100 lawyer labels; agreement numbers say how far to trust the graders; (−) needs the judge of ADR-034; the
  estimator and the sampling are code that must be tested like the rest of the harness. Pending: owner's choice; two
  lawyers; the review page of E2E-LAB-01 as the first labels. Research: docs/RESEARCH.md §4.
- ADR-036 (Proposed, 2026-11-26): The refusal threshold is chosen by conformal risk control, per document type, with a
  stated error bound. Context: tau = 0.58 was picked by inspection (tau-20261107T212422Z: answers 87.7 % of covered,
  refuses 80.3 % of absent; no tau met both targets); in E2E-LAB-01 a contract question scored 0.571 and was refused
  because tau was calibrated on statute text. Conformal abstention (Abbasi-Yadkori et al., 2024; COIN; the Angelopoulos et al.
  recipe) picks the threshold on a calibration set with a Clopper-Pearson bound so that the rate of answering an absent
  topic is at most alpha with probability 1-delta; group-conditional control keeps one group from hiding in a global
  budget. Options: (A) keep the hand-picked tau; (B) one conformal tau; (C) conformal tau per group (statute vs firm
  document; optionally per language), calibrated by topic, not by wording. Recommendation: C. Consequences: (+) "answers
  an absent topic at most alpha of the time, with 1-delta confidence" becomes a claim with a guarantee; contracts get
  their own threshold; (−) the guarantee assumes calibration and real questions are exchangeable, which the AI-drafted
  probe sets do not ensure (66 absent wordings come from only 22 topics); needs probe sets of firm documents. Pending:
  owner's choice; lawyer-reviewed probe sets per group. Research: docs/RESEARCH.md §6.
- ADR-037 (Proposed, 2026-11-26): Four retrieval experiments are run, each kept only if the golden suite says so. Context:
  recall@8 is 1.00 on 25 easy cases, but distractors reach the top 8 in 16 % of cases and the dense signal could not
  separate same-numbered articles (ADR-022); the cross-encoder lost on AUC (0.892 vs 0.934) and was 16x slower.
  Options (independent): (A) use bge-m3's own sparse and multi-vector (ColBERT-style) scores as a reranker (same model,
  so no new generation; needs FlagEmbedding on CPU, Ollama serves dense only); (B) a Jina-ColBERT-v2 late-interaction
  arm (multilingual, 8192-token window; no published Spanish legal results); (C) cross-reference expansion: references
  such as "en los términos del artículo 48" parsed by the existing citation grammar and the referenced unit added to the
  context (SAT-Graph RAG, de Martim 2025); (D) LegalBench-RAG-style character-level precision/recall as a retrieval
  metric, since chunks already store character offsets. Recommendation: D first (measurement), then A, C, B in that
  order of cost. Consequences: (+) each piece is measured before it stays (rag.md); (−) A and B add CPU latency; B is a
  new embedding generation (ADR-006); C can widen the context beyond the 12k budget. Pending: owner's choice; baseline
  per-arm runs (rag.md requires them and none is recorded). Research: docs/RESEARCH.md §7.
- ADR-038 (Accepted 2026-11-26 for A and B, chosen by the product owner; C and D stay Proposed): The claim "verification is code" is backed by mutation testing, a validator replay suite,
  measured latency work and a deterministic check of amounts in OCR text. Context: the validator's tests were written by
  the same team; latency is 35 s median against the blueprint's < 10 s p95; OCR can misread a figure on a page above the
  confidence threshold (ADR-030). Options (independent): (A) mutation testing of `app/validation/` (e.g. mutmut): every
  weakened check must make a test fail; (B) replay: re-validate stored model outputs from the audit log on every change
  (done by hand for ADR-021); (C) latency: reuse of the KV cache of the fixed prompt prefix, speculative decoding with a
  small draft model of the same family, measured p50/p95; (D) OCR amounts: Mexican contracts write sums twice
  ("$15,000.00 (QUINCE MIL PESOS 00/100 M.N.)"), so a mismatch between the figure and the words is flagged on the unit.
  Recommendation: A and B (cheap, pure evidence), then D, then C. Consequences: (+) test strength becomes a number;
  regressions in verification are caught on real outputs; (−) mutation runs are slow; C depends on the runtime
  (Ollama/llama.cpp) supporting it on an 8 GB GPU. Pending: owner's choice; a mutation score baseline; latency
  baseline per profile. Implemented 2026-11-26: (A) `scripts/mutation_check.py`, a standard-library mutation check (mutmut does not run on Windows) with a control run of the unmutated copy and 12 mutants, one per contract check (invalid ids, placeholders, coverage, quotations, citation required, quotation checked against the fragment cited with it, minimum quote words, section gaps, prose refusals, runaway output, the regeneration, the system-written refusal): 12/12 killed, run in CI; (B) `evals/replay.py`: full golden runs now write `replay.jsonl` (raw outputs, full retrieved set, recorded verdicts), `extract-e2e` keeps only what an end-to-end run can replay faithfully, and `tests/validation/test_replay.py` replays 9 real attempts of E2E-LAB-01 (2 skipped as incomplete): 0 verdicts changed. Research: docs/RESEARCH.md §8.
- ADR-039 (Proposed, 2026-11-26): The product's value is measured with a within-subject time-on-task study with practising
  lawyers before any claim of time saved. Context: the business case rests on a survey's expectations (Thomson Reuters
  2025: ~5 h/week expected), not a measurement; no user research, pilot or pricing exists. Options: (A) keep the
  hypothesis; (B) a within-subject, counterbalanced study: 10-12 lawyers, matched research tasks with and without the
  tool, measuring time, errors caught and errors introduced, analysed with paired tests (Wilcoxon signed-rank), with a
  protocol written before the study; (C) a pilot in one firm with logs only (no control). Recommendation: B, then C.
  Consequences: (+) the first measured value; errors introduced are measured, not only time; (−) recruiting lawyers and
  preparing matched tasks; ethics and confidentiality of the tasks (fictional matters only). Pending: owner's choice;
  participants; task set reviewed by a lawyer. Research: docs/RESEARCH.md §9.

- ADR-040 (Proposed, 2026-11-26): The drafting model runs with its mixture-of-experts weights on CPU and everything else on
  GPU. Context: measured 2026-11-26 on the demo machine (RTX 5060 8 GB, Ollama 0.40.13): gemma4:26b-a4b-it-qat is split
  by whole layers, 68 % CPU / 32 % GPU; generation 27-30 tokens/s, prefill 375 tokens/s, cold load 46.5 s. The model is
  a mixture of experts (about 4B active parameters per token); llama.cpp can keep attention and shared weights on the
  GPU and only the routed experts in RAM (`--n-cpu-moe N`, `-ot exps=CPU`). Options: (A) keep Ollama's layer split;
  (B) run the drafting model under `llama-server` (OpenAI-compatible) with expert offload, pinned by the GGUF's sha256
  like any model (ADR-014), Ollama kept for the embedder and the utility model; (C) a smaller dense model. Recommendation:
  B, measured. Consequences: (+) community reports on 8 GB cards with similar 35B-A3B models show generation rising from
  about 4 to about 33 tokens/s; (−) those reports are anecdotal and on other models, no Gemma figure was found; a second
  runtime to install and pin; a runtime change is a model change (golden run, invariant 2). Pending: owner's choice;
  tokens/s, prefill and p50/p95 per answer on this GPU before and after. Research: docs/RESEARCH.md §10.
- ADR-041 (Accepted 2026-11-26, option B chosen by the product owner; the cause was measured, not assumed): Retrieval latency is profiled, and the embedder is kept resident if model swapping is the
  cause. Context: retrieval p50 is 3.16 s and p95 5.55 s (golden-20261109T084417Z), which is far slower than an indexed
  search over ~18 900 rows should be; the embedder (bge-m3) and the drafting model share 8 GB of VRAM through Ollama,
  and a cold load of the drafting model takes 46.5 s. The cause is not yet known. Options: (A) do nothing; (B) profile
  each stage (query embedding, each arm's SQL, fusion, rerank, expansion) and, if swapping dominates, keep both models
  resident (`OLLAMA_MAX_LOADED_MODELS`, `keep_alive`) or run bge-m3 on CPU; (C) cache query embeddings (helps only
  repeated queries). Recommendation: B. Consequences: (+) possibly the cheapest latency win; the profile also gives the
  per-stage numbers every later change is measured against; (−) keeping two models resident on 8 GB may push more of
  the drafting model to CPU (interacts with ADR-040). Pending: owner's choice; the per-stage profile. Research:
  docs/RESEARCH.md §11.
- ADR-042 (Proposed, 2026-11-26): Irrelevant citable units are pruned from the context before generation, never words
  inside a unit. Context: prefill runs at 375 tokens/s, so the 12 000-token context budget costs about 32 s per attempt,
  and a regeneration pays it again; rag.md already notes that fewer good fragments beat many mediocre ones. Provence
  (Chirkova et al., ICLR 2025) prunes sentences inside the reranker's own forward pass at almost no extra cost; XProvence
  (arXiv 2601.18886) is multilingual. Options: (A) keep full parent expansion; (B) prune whole citable units (fracciones,
  párrafos of an expanded article) by a pruner's relevance score, keeping the unit the query hit and its caput;
  (C) prune at sentence level as Provence does (rejected: a unit cut inside breaks the verbatim check and changes what
  "the cited fragment" means); (D) measure top-5 against top-8 expansion first (rag.md). Recommendation: D, then B.
  Consequences: (+) fewer prefill tokens and less distraction for the model; (−) a pruned unit is not citable, so a
  correct citation can become impossible (the ADR-020 failure in reverse); the pruner is a model to pin. Pending:
  owner's choice; faithfulness and false-refusal runs with and without pruning. Measured 2026-11-26 (`evals/profile_retrieval.py`): retrieval itself is 0.56 s at the median (query embedding 0.02 s, both arms < 0.1 s, the dense rerank of 21 texts 0.36 s), with or without the drafting model resident. The 3-9 s seen in full runs was Ollama keeping ONE model at a time: each turn unloaded gemma to embed the query (bge-m3 reload ~4.5-6.5 s) and reloaded gemma to generate (17-21 s); fewer GPU layers for gemma did not help, running bge-m3 on CPU made Ollama unload gemma anyway. With `OLLAMA_MAX_LOADED_MODELS=3` (tested on a second, temporary Ollama on port 11435) both stay loaded: gemma reload 0 s, embedding 0.4 s, generation unchanged (27-29 tokens/s); the same four golden cases went from a median of 61 s to 28 s end to end and retrieval from ~7 s to ~1.2 s (`golden-20261127T044716Z-adr041-latency-check` vs `...-adr041-after-coresident`; 4 cases, one regenerated in the first run). `scripts/dev-up.ps1` sets the variable when it starts Ollama and warns otherwise; `doctor` checks it; docs/LOCAL_DEMO.md documents it. Pending: setting it on each machine whose Ollama starts outside the script (the Windows tray app reads the user variable at start). Research: docs/RESEARCH.md §12.
- ADR-043 (Accepted 2026-11-26: verified already true, now guarded by a test): The regeneration prompt keeps the first attempt's prompt as an exact prefix, so its
  prefill is reused. Context: one regeneration is allowed per answer (rag.md); llama.cpp, under Ollama too, reuses the
  KV cache for an identical prompt prefix; at 375 tokens/s a 12k-token context is about 32 s of prefill. Whether today's
  composer keeps the prefix identical is not verified. Options: (A) leave as is; (B) check, and if needed compose the
  retry as the same system and user turns plus the original answer and the feedback appended at the end, with a test
  that the retry prompt starts with the first prompt byte for byte; (C) also order the fixed layers first for every
  request (already the layer order: core, workflow, persona, session, fragments). Recommendation: B. Consequences:
  (+) up to ~30 s saved on every regenerated answer; (−) appending the failed answer may anchor the model to it (measure
  the regeneration success rate before and after). Pending: owner's choice; the check of the current composer; retry
  latency before and after. Measured 2026-11-26: the retry keeps the system prompt (where the context lives) and starts its user turn with the original user text, so Ollama reused the cached prefill: 0.8 s instead of 14.1 s for a 6 400-token prompt. `tests/validation/test_loop.py` now asserts the byte-for-byte prefix, so a change that breaks it fails a test. No code change was needed. Research: docs/RESEARCH.md §13.
- ADR-044 (Proposed, 2026-11-26): Speculative decoding is measured, starting with the n-gram mode that needs no draft model.
  Context: answers copy long statute passages from the context (verbatim quotations are required to match it); llama.cpp
  offers a draft-model mode, EAGLE-3 and an n-gram mode that drafts tokens from text already in the prompt. EAGLE-3
  reports up to 6.5x in its paper, but Meta reports 10-30 % at batch size 1 on H100s, and matching EAGLE-3 draft
  checkpoints are scarce. Options: (A) none; (B) n-gram speculative decoding; (C) a small draft model of the same family;
  (D) EAGLE-3 if a checkpoint for the drafting model exists. Recommendation: B first (no new model), C if B helps little.
  Consequences: (+) possibly faster generation of quoted text at no quality cost (speculative decoding does not change the
  output distribution); (−) depends on ADR-040's runtime (`llama-server`); gains at batch size 1 on consumer hardware are
  uncertain. Pending: owner's choice; tokens/s on real answers with and without. Research: docs/RESEARCH.md §14.
- ADR-045 (Proposed, 2026-11-26): Vectors are stored as half precision (`halfvec`), and binary quantization stays unused at
  this scale. Context: pgvector 0.7+ offers `halfvec`, reported to halve vector and index storage with similar search
  quality, and binary quantization with full-precision rescoring for large collections; the corpus holds ~18 900 chunks.
  Options: (A) keep `vector(1024)`; (B) `halfvec(1024)` for the column and the HNSW index; (C) binary quantization plus
  rescoring (meant for millions of rows). Recommendation: B, low priority. Consequences: (+) half the memory for vectors
  and the index, which matters as firms upload documents; (−) a change of stored representation, built aside as a new
  generation (ADR-006) with a retrieval-only golden run; no latency gain expected at this size. Pending: owner's choice;
  recall@8 and scores before and after (the tau calibration uses these scores). Research: docs/RESEARCH.md §15.
- ADR-046 (Proposed, 2026-11-26): The embedder is adapted to this corpus with hard negatives taken from its own confusions.
  Context: the dense signal could not tell "art. 123 fracc. XIII CPF" from "art. 123-B-XIII CPEUM" (ADR-022), and
  distractors reach the top 8 in 16 % of golden cases. GPL (Wang et al., NAACL 2022) adapts a dense retriever with
  synthetic queries and cross-encoder pseudo-labels; R-GPL (arXiv 2501.14434) refreshes hard negatives during training
  and improved 13 of 14 BEIR datasets. Options: (A) keep bge-m3 as is; (B) GPL-style adaptation where the hard negatives
  are this corpus's known confusions: same-numbered articles of other laws and states, and repealed versions of the same
  unit; (C) switch embedder (ADR-047). Recommendation: B, after ADR-047's comparison. Consequences: (+) aims at the exact
  failure that cost recall before ADR-022; (−) a new embedding generation built aside (invariant 5, ADR-006); synthetic
  queries are model-written, so evaluation must use lawyer-reviewed cases, not more synthetic ones; training needs a GPU
  session. Pending: owner's choice; retrieval-only golden runs before and after, per category. Research:
  docs/RESEARCH.md §16.
- ADR-047 (Proposed, 2026-11-26): Qwen3-Reranker-0.6B, and Qwen3-Embedding-0.6B as an embedder, are measured against the
  current dense cosine. Context: the cross-encoder bge-reranker-v2-m3 was discarded (AUC 0.892 vs 0.934, 16 s per query
  on CPU, near-zero scores for English over Spanish; tau-20261107T212422Z). Qwen3-Reranker-0.6B (Apache-2.0, June 2025)
  reports 66.36 on multilingual retrieval (MMTEB-R) against 58.36 for bge-reranker-v2-m3, but in the vendor's own runs on
  top of its own first stage. Options: (A) keep dense cosine; (B) Qwen3-Reranker-0.6B as the reranker; (C) also
  Qwen3-Embedding-0.6B as the embedder. Recommendation: B, measured exactly as the last reranker was (AUC on the probe
  sets, false-refusal cost, cross-lingual scores, CPU latency); C only if B shows the first stage is the limit.
  Consequences: (+) possibly better separation of covered and absent topics, which also tightens tau; (−) a reranker
  changes the score scale, so tau must be recalibrated (ADR-036); C is a new embedding generation. Pending: owner's choice;
  the calibration run with the new reranker. Research: docs/RESEARCH.md §17.
- ADR-048 (Proposed, 2026-11-26): Lexical and semantic scores are fused by a tuned convex combination instead of fixed RRF.
  Context: fusion is RRF with k = 60, untuned (rag.md). Bruch, Gai and Ingber (ACM, 2023, arXiv 2210.11934) find that a
  convex combination of normalised scores beats RRF in and out of domain, needs only a small sample to tune its single
  parameter, and that a tuned RRF generalises poorly. Options: (A) keep RRF k = 60; (B) a convex combination with one
  weight, tuned on the probe sets and validated on the golden set; (C) tune RRF's k. Recommendation: B against A.
  Consequences: (+) a one-parameter change with published evidence; (−) fusion would depend on raw scores, which rag.md
  forbids today ("fusion depends only on ranks"), so the rule itself changes, and determinism (stable tie-breaks) must
  still hold; with 25 golden cases the weight must not be fitted to the test set. Pending: owner's choice; retrieval-only
  runs per category for A and B. Research: docs/RESEARCH.md §18.
- ADR-049 (Proposed, 2026-11-26): Each chunk is contextualised for retrieval by deterministic text (its article's lead-in and
  the full chapter titles) before any model-written context. Context: Anthropic's contextual retrieval (2024) prepends
  chunk-specific context before embedding and lexical indexing and reports retrieval failures down about 35 % with
  embeddings alone and about 49 % with BM25 as well; this project already prepends a breadcrumb, a structural version of
  that idea. Two deterministic gaps remain: a fracción is embedded without its article's lead-in sentence (rag.md lists
  "caput glue" as pending) and some chapter titles were dropped at ingestion (found 2026-11-26, e.g. "CAPITULO IV" with no
  name). Options: (A) leave as is; (B) glue the caput lead-in and restore full chapter titles in the embedded text (the
  stored `text` stays an exact slice: rag.md); (C) B plus a short model-written context per chunk (hours of local
  inference for ~18 900 chunks, and generated text inside the index). Recommendation: B; C only with evidence B is not
  enough. Consequences: (+) better retrieval of fracciones and the regime tags of ADR-031 become reliable; (−) a chunker
  change: new `chunker_version`, a generation built aside (ADR-006) and a golden run before and after (invariant 2).
  Pending: owner's choice; retrieval-only and full golden runs per category. Evidence added 2026-11-26 (while implementing ADR-031): two normalizer defects in the stored LFT. (a) Títulos written with cardinal numbers ("TITULO ONCE", "DOCE", "TRECE", "CATORCE", "QUINCE", "DIECISEIS" in data/corpus/text/LFT.txt, lines 9435-14413) are not recognised, so every article after art. 522 (authorities, procedure, evidence, strikes procedure) carries "TITULO DECIMO Prescripción" in its breadcrumb, which is embedded with its text; (b) a Capítulo whose name is on the next line after a blank line ("CAPITULO IV" / "Rescisión de las relaciones de trabajo") keeps no name. Fixing either changes chunks: it needs the generation flip of ADR-006 first. Measured 2026-11-26 and NOT adopted
  (owner's choice after the numbers): both defects fixed in the MX normalizer (chunker v2), built as index
  generation 2: 432 junk heading-name units gone, 10 article texts no longer carry the next heading, no article or
  fracción id lost, 20 473 -> 20 016 chunks. Golden `golden-20261127T060836Z-before-adr049-structure` vs
  `golden-20261127T064516Z-after-adr049-structure`: recall@8 0.95 -> 0.95, leakage 0 -> 0, refusal accuracy 5/5 ->
  5/5, false refusal 2/20 -> 4/20 (McNemar 2 worse / 0 better, p = 0.5). Repeated 3 more times per generation
  (`...-rep-gen1-{1,2,3}`, `...-rep-gen2-{1,2,3}`): gd2-024 ("art. 15-C") correct 4/4 on generation 1 and 0/4 on
  generation 2, gd2-009 4/4 vs 2/4. Cause of gd2-024, read in the outputs: the units are byte-identical in both
  generations, but with the junk units gone a long amendment list (`TRANS4-UNICO-P2`) enters the top 8 and the
  model attributes the repeal of art. 15-C to it; the citation exists, so the validator (provenance) accepts it.
  Rolled back to generation 1 and the parser change reverted; generation 2 is kept retired; the fixtures stay in
  `tests/ingestion/test_heading_names.py` as strict xfails. Pending: the transitorio misattribution (a reform
  decree's lists of article numbers) addressed first, then this fix rebuilt and measured again.
  Measured again 2026-11-27 with ADR-051's decree check on, and NOT adopted again (same rule as before): the fix was
  re-applied and checked identical to the stored generation 2 (all 9 public documents: same ids, text and
  breadcrumbs), generation 2 re-activated. `golden-20261127T081458Z-after-decree-check-gen1` vs
  `golden-20261127T083503Z-after-decree-check-gen2`: false refusal 2/20 -> 4/20 (McNemar 2 worse / 0 better, p =
  0.5), recall, leakage and refusal accuracy unchanged. gd2-024 is now refused instead of misattributed (no wrong
  answer reaches the user), but it is not answered on generation 2 in any of 5 runs, and gd2-009 is answered 2/5
  (5/5 on generation 1). Rolled back; the code is kept in git history as a commit and its revert. Pending: why the
  model declines or adds an uncited framing sentence when the junk heading units are gone (a model or prompt
  question, not a parsing one).

- ADR-050 (Proposed, 2026-11-26): The law is retrieved as of a date: versions carry valid time separately from the time we
  loaded them, and a session may fix the date of the facts. Context: legal questions are about the version in force on
  the date of the facts, not today's. Cymbler, Guez and Fabre (ICML 2026, arXiv 2608.09393) built a versioned corpus of
  32 436 article versions of the French tax code and found that RAG over a current-only corpus retrieved the
  date-applicable version 0 % of the time. This project: (1) only has a boolean `include_historical` (current text, or
  every version), not "as of date D"; (2) 8 of the 9 stored public documents have `valid_from` NULL (checked
  2026-11-26), so no answer can show that its version applied on a given date; (3) `store.ingest_document` retires the
  previous version with `valid_to = current_date`, the day the reform was DETECTED, while the new version's
  `valid_from` is the publication date read from the text: the CPF shows the old version in force until 2026-11-25 and
  the new one from 2026-11-13, twelve days claimed by both; and publication is not always entry into force (the decree's
  transitorios decide); (4) the E2E-LAB-01 dismissal happened on 2026-10-03 and was answered from the LFT loaded on
  2026-11-07, with no record that this was the version in force on the date of the facts. Options: (A) keep the boolean;
  (B) bitemporal versions: valid time (`valid_from`/`valid_to`, from the decree's entry into force, or explicitly
  "unknown") kept apart from transaction time (`ingested_at`, a new `retired_at`); retiring sets the old `valid_to` from
  the new version's `valid_from`, never from the detection day; an optional as-of date on the session (a date picker on
  the case sheet, validated, never inferred from free text, like jurisdiction), applied inside
  `build_jurisdiction_predicate()` as `valid_from <= D < valid_to`; answers state the as-of date; when no loaded version
  covers D, or its dates are unknown, the system refuses with a fixed reason ("no tenemos la versión vigente al …")
  instead of answering from today's text; (C) B plus backfilling earlier versions of each loaded law from its official
  history of reforms (corpus work per law, ADR-017 rules); (D) a temporal golden track in the style of FiscalQA Pro:
  questions whose correct version is not the current one, scored on version correctness, with repealed and future
  versions as distractors. Recommendation: B with D, then C one law at a time. Consequences: (+) a reform no longer
  creates a period in which two versions both claim validity; an answer can say which version applied on the date that
  matters, or say it cannot; (−) a schema change and a predicate change (the isolation and predicate property tests
  extend to dates); entry-into-force dates need reading the transitorios, which a person must check per reform; until
  C, most as-of dates before 2026-11 will be refused, which is honest but limits usefulness; the stored CPF overlap must
  be corrected by an explicit, reviewed data migration (invariant 3: nothing is deleted, dates are corrected). Pending:
  owner's choice; the entry-into-force dates of the loaded versions (a person reads each decree); the temporal golden
  cases, lawyer-reviewed. Research: docs/RESEARCH.md §20.

- ADR-051 (Accepted 2026-11-27, option "number + verb" chosen by the product owner): A sentence that credits a cited
  decree with a change must match that decree's own list, checked by code. Context: with index generation 2 (ADR-049)
  golden gd2-024 wrote "art. 15-C was repealed" and cited `MX-FED-LFT-TRANS4-UNICO` (the 2012 reform that ADDED
  15-A to 15-D) while the repealing decree (`TRANS13-PRIMERO`: "se derogan los artículos 15A; 15-B; 15-C, y 15-D")
  was in the same context; the citation existed, so the validator (provenance) accepted it 3 times out of 3. The
  corpus holds 430 such decree-list units (CPF 170, CPEUM 158, CCF 55, LFT 47). Options: (A) number + verb in the
  validator; (B) number only (misses "repealed" cited to a decree that reformed the article); (C) retrieval pins the
  decree that names the article (prevents it upstream, does not check the sentence); (D) A and C. Decision: A.
  `app/validation/decrees.py` parses a transitorio's list into the articles it reforms, adds and repeals (the
  numbers before the first verb of a chunk cut mid-list are `unknown`, and defer to the expanded article's own list);
  a sentence that cites a decree list and says article X was repealed / reformed / added must have X under one of
  its verbs in a cited decree; otherwise `decree_mismatches` -> one regeneration with code-written feedback ->
  system refusal. Only `-TRANS` units are decree lists; quoted text is masked; article ids are compared as written.
  Evidence: replaying all 87 stored attempts of the 8 golden runs of 2026-11-26 flags exactly the 3 misattributions
  and changes nothing else; E2E-LAB-01's replay fixture unchanged; mutation check 13/13 (new mutant); golden on
  generation 1 `golden-20261127T081458Z-after-decree-check-gen1` identical to `golden-20261127T060836Z-before-adr049-
  structure` (same three failing cases, false refusal 2/20, refusal accuracy 5/5, leakage 0); on generation 2 it
  turned the misattribution into a refusal. Consequences: (+) the first check aimed at what a decree says, not only
  that it exists; (-) a claim about a decree phrased without a change verb, or naming an article with words, is
  not checked; a wrong claim inside a list the parser cannot read passes as before. Pending: lawyer review of the
  parser on the 430 units; whether option C is still worth measuring.

## Data flow (query path)
JWT → middleware sets app.tenant_id → SessionContext validated → jurisdiction predicate →
hybrid search (prefiltered) → RRF → rerank → τ check → parent expansion → prompt composer →
LiteLLM → citation validator → audit log → response.
Never reorder: predicate BEFORE vector search, validator AFTER generation, always.
