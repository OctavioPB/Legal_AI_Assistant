# Legal AI Assistant — CLAUDE.md

## WHAT

Multi-tenant Legal AI SaaS for lawyers in **Mexico (primary)** and **USA (secondary)**.
Two workflows: **Dossier Builder** (grounded legal research with verifiable citations)
and **Adversarial Counterpart** (opposing-counsel sparring). Fully bilingual (es-MX / en-US).

Zero-hallucination contract: the LLM may only assert legal content present in retrieved
fragments, every legal claim carries a `[cite: CITE_ID]`, and a structured refusal
(`<insuficiente>`) is a first-class output — never an error.

## WHY (key architectural decisions — do not relitigate casually)

- **Postgres is the security AND truth perimeter.** One database (PostgreSQL 16) holds
  relational data, vectors (`pgvector` HNSW) and full-text (`tsvector`). Reason: RLS covers
  all three with a single verifiable isolation mechanism. No external vector DB until
  scale demands it (>50M chunks) — a second store means a second isolation boundary to keep
  in sync.
- **Multi-tenancy = pooled model + RLS** (`FORCE ROW LEVEL SECURITY`, `SET LOCAL
  app.tenant_id` per transaction). Public laws live under the synthetic `PUBLIC_CORPUS`
  tenant; client files under the firm's real tenant. Attorney-client privilege is why this
  lives in the DB, not in app code.
- **Jurisdiction filtering is HARD SQL pre-filtering, never post-filtering.** Columns
  `country / level / state_code / municipality / is_current` are typed + indexed on
  `chunks`. Selecting a municipality stacks UP (municipal + its state + federal), never
  sideways (never a sibling state). Logic lives ONLY in `build_jurisdiction_predicate()`.
- **Chunking follows legal structure, not token windows.** Docling parses PDF layout →
  legal normalizer maps to node types (libro/título/capítulo/artículo/fracción/inciso,
  or US §/subsection) in an `ltree`. Retrieval unit = deepest self-contained node
  (fracción); context unit = parent artículo (parent expansion). Chunk text is prefixed
  with its hierarchy breadcrumb before embedding.
- **Hybrid search is mandatory:** semantic (Cohere embed-v3 multilingual, cross-lingual
  by design) + lexical (`tsvector`) fused with RRF, then cross-encoder rerank. Legal IDs
  ("art. 123 apartado B", "§ 1983") are lexical signals embeddings blur.
- **LiteLLM proxy routes task aliases, not providers** (`drafting-primary`,
  `adversarial-primary`, `cheap-utility`). Which model backs an alias is decided by
  golden-dataset metrics (citation precision, refusal accuracy), not vibes.
- **Prompts are versioned code**, composed in layers: fixed anti-hallucination core →
  workflow → persona → session ({Language}/{Jurisdiction} from closed catalogs only —
  never free user text inside a system prompt).
- **Post-generation citation validator** rejects any `[cite: X]` not in the retrieved set
  of THAT query; one regeneration retry, then refusal. See `.claude/rules/rag.md`.

## HOW (uv-managed)

```bash
uv sync                                   # install deps (locked)
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000   # API (local only); `fastapi dev app/main.py` also works
docker compose up -d postgres redis       # infra (pg16 + pgvector)
uv run alembic upgrade head               # migrations (separate role, BYPASSRLS-free app role)
uv run celery -A app.workers worker -Q ingestion   # ingestion workers   [NOT BUILT YET: WS2.T01]

uv run python -m app.doctor               # executable invariants: RLS/roles/extensions/versions/aliases (WS0.T09)
scripts/dev-up.sh | scripts/dev-up.ps1    # idempotent bootstrap: compose, migrate, seed, doctor (WS0.T10)   [NOT BUILT YET]
uv run mypy app                           # strict typing
uv run python -m app.prompts.composer verify       # prompt versions match their pinned hashes
uv run python -m evals.prompt_sandbox --case gd-0001   # render the full composed prompt

uv run pytest                             # full suite: needs `docker compose up -d postgres redis` (an unreachable DB FAILS, never skips);
                                          # sockets disabled except loopback; LLM/embedder are fakes
uv run pytest tests/test_tenant_isolation.py       # cross-tenant leak test (must pass ALWAYS)
uv run pytest tests/test_jurisdiction_predicate.py # property tests for the hard pre-filter

uv run python -m evals.run --suite golden          # golden dataset (required for prompt PRs)   [NOT BUILT YET: WS5.T04]
uv run python -m app.ingestion.seed [--only LFT]   # embed + load the demo corpus (needs `ollama pull bge-m3`; ~9 min for all)
uv run python -m app.orchestration.pins [--check]  # (re)pin the local models from the Ollama runtime / detect drift
uv run python -m app.demo                          # demo firm + user (random password shown once -> .demo-credentials)
uv run python -m evals.calibrate_tau [--reranker cross] [--write]   # calibrate the refusal threshold from the probe sets
uv run pytest -m live                              # real local models (Ollama must be running)
uv run python -m app.ingestion.cli parse|clean|chunks ...   # `ingest <pdf>` is NOT BUILT YET (WS2.T01/T06); store.ingest_document is the library entry point
uv run ruff check --fix && uv run ruff format      # lint/format

cd web && npm install && npm run dev      # frontend (Next.js), http://localhost:3000
cd web && npm test && npx tsc --noEmit    # UI guards (catalog parity, literals, external URLs, Brand.md tokens/contrast)
cd web && npm run build && npx next start -p 3100 & npm run e2e   # real-browser checks (Edge): no-reload language switch, a11y (axe), CSP, keyboard
```

## Non-negotiable invariants

1. Never write a query path that bypasses RLS or takes `state_code`/`municipality` from
   free text (catalog lookups only).
2. Never merge changes to prompts, chunker, embeddings or router without running the
   golden eval suite. Prompt files live in `app/prompts/` and are immutable per version.
3. `cite_id` is immutable once ingested. Reforms create a new version and flip
   `is_current`; nothing is deleted.
4. The refusal path is a feature. Do not "improve helpfulness" by weakening it.
5. **Never mix vectors or chunks from different versions.** Every chunk records
   `embedding_model`, `embedding_revision`, `chunker_version` and the chunking thresholds it was
   built with; retrieval filters on the ACTIVE embedding version and ingestion refuses to write a
   different one into the live set. A model/chunker/threshold change = full re-embed built aside
   and swapped in, never in place (ADR-006).
6. **The system verifies the model; the model never verifies itself.** Refusal text, citation
   existence, verbatim-quote checks and coverage are code, not instructions. A refusal is never
   "the model explaining in its own words that nothing was found" (ADR-007).

## Per-session workflow

1. Read `PLAN.md`; find the first pending task (`[ ]`/`[~]`) of the active workstream.
2. If the task needs a decision not in the ADR log, propose it (options + cost + recommendation)
   and wait for the user's choice before implementing. Use the `decision-record` skill.
3. Implement with tests. Run `pytest`, `ruff`, and the isolation test.
4. If you touched prompts, chunker, embeddings, retrieval params or the router: golden eval
   BEFORE and AFTER, both numbers recorded (`eval-runner` skill). 1–2 cases of difference is noise
   — say so, do not claim an improvement.
5. Mark the task in `PLAN.md` and add a dated line to its Log.
6. If the code contradicts an accepted ADR, point it out; never silently fix it either way.

## Lessons carried over from Library-RAG (details: `docs/lessons-from-library-rag.md`)

Measured on a sibling local-RAG project (same zero-hallucination contract). Treat as known pitfalls.

- **Measure each piece, keep only what earns its place.** Baseline arms separately (semantic →
  +lexical → +RRF → +rerank) before tuning. Plausible ideas lost when measured there: map-reduce
  as default synthesis (fidelity 0.87 → 0.58, stopped abstaining), equal-weight fusion of
  rewritten/translated queries (−6 to −11 pts recall). See ADR-008/009.
- **Small eval sets are noisy.** With ~25–70 cases, one case ≈ 1.5–4 points. Report per category;
  a gain that sinks one category (e.g. exact-term lookups) is a regression in disguise.
- **Golden sets drift toward what the system already does well.** Cases written by the model from
  the corpus inherit its vocabulary. Lawyers author or approve cases; every case carries
  `authored_by` / `reviewed_by`. A reference error looks like a system error (recall 0.43 → 0.81
  once missing alternative sources were credited): fix the eval before optimizing.
- **Eval tooling has bugs too.** Review sheets must show the FULL text of each cited fragment
  (a 400-char truncation made faithfulness unverifiable); the harness itself has tests.
- **Small/cheap models copy placeholders and invent citations** when allowed to add unmarked
  content. The validator rejects orphan placeholders (`[cite: X]`, `[cite: CITE_ID]`), and any
  model addition must be marked (`[razonamiento estratégico]`), never silently uncited.
- **A citation that exists ≠ a faithful paraphrase.** Check quoted statute text verbatim against
  the cited fragment (accent/case/whitespace-folded, ≥ N consecutive words). Residual risk (right
  cite, inverted summary) is declared in the blueprint §12, not hidden.
- **Embedding windows truncate silently.** `chunk max_tokens` must not exceed the embedder's input
  limit; oversized chunks are logged and measured, never assumed fine.
- **Navigation is not content.** Índices, tablas de contenido and similar chunks are flagged and
  excluded at query time; big codes win lexical arms by title weight — tune the breadcrumb weight
  with evidence, do not guess.
- **Calibrate τ by hand first** with two probe sets (covered vs absent topics) and several
  wordings per topic: scores swing 0.06 vs 0.82 for synonyms of the same subject.
- **Invariants must be executable.** Library-RAG's `doctor` turns rules into checks (extension
  loads, accent folding works, model digest matches, context is large enough). Ours checks roles,
  FORCE RLS, extensions, active embedding generation, pinned aliases; CI and demos run it.
- **Pin models from the provider's docs, not from memory.** Verified name, version and date go
  in settings; the resolved id is stored with every eval run and trace.
- **Make runs reproducible.** Stable tie-breaks, commit + `dirty` flag, per-case results, and
  repeated runs for LLM-dependent categories; otherwise a "gain" may be run-to-run variance.
- **One ingest per source, failures isolated, rebuilds aside.** Lock with lease, per-document
  transactions, `failed[]` without aborting, post-ingest integrity check, unchanged re-run = free.
- **Thresholds named in an ADR must be watched.** Library-RAG passed its own "revisit at 200k
  chunks" tripwire (≈697k) without noticing. Ops tracks every ADR tripwire (WS7.T05).

## Progressive disclosure — read on demand, not upfront

- `.claude/rules/architecture.md` — component map, service boundaries, ADR log
- `.claude/rules/multi-tenancy.md` — RLS policies, roles, tenant test harness
- `.claude/rules/rag.md` — chunker rules, retrieval params, citation validator spec
- `.claude/rules/jurisdictions.md` — catalog semantics, predicate builder contract
- `.claude/rules/prompts.md` — layer composition, versioning, injection rules
- `.claude/rules/style.md` — Python/SQL conventions, i18n rules
- `.claude/skills/eval-runner/` — how to run/extend the golden dataset; before/after protocol
- `.claude/skills/prompt-lab/` — prompt iteration workflow (never edit prod templates)
- `.claude/skills/decision-record/` — how to propose/write/supersede ADRs
- `.claude/skills/legal-chunking/` — legal-structure chunker procedure, fixtures, versioning
- `docs/lessons-from-library-rag.md` — lessons imported from the sibling project, with sources
- `docs/LegalAI-System-Architecture.md` — full blueprint (source of truth for design)

- `PLAN.md` — workstreams y tasks de 0 a demo
- `Brand.md` — fuente de verdad de UI/Frontend (obligatoria para todo PR de UI)
- `ROADMAP.md` — features futuros fuera de scope (con anti-roadmap)
- `PITCH.md` — propósito de la solución + spec de ingeniería
