# RAG Rules

## Chunker
- Retrieval unit: deepest self-contained node (fraccion/parrafo MX; subsection US).
- Merge-up nodes < 80 tokens; split nodes > 800 tokens by paragraph (same parent node_id).
- Prepend hierarchy breadcrumb to `content` BEFORE embedding:
  `[Ley › Título › Capítulo › Artículo N › Fracción X]`.
- Never cut mid-node. Never chunk across node boundaries.
- Count tokens with the EMBEDDER's tokenizer, not characters or the LLM's. `max_tokens` (800) must
  not exceed the embedder's input window: text past it is silently ignored by the vector arm.
  Verify Cohere embed-v3's limit (and `truncate` setting) in WS2.T05; if lower, lower the split
  threshold or split by párrafo earlier. Log every oversized chunk; never assume it is fine.
- Chunk `text` is an EXACT slice of the normalized source (store char offsets / page+bbox for the
  deep-link). The breadcrumb is metadata + embedding prefix, never stored inside `text`, so the
  verbatim-quote check and the citation string stay honest.
- Lead-ins travel with what they introduce: a fracción embedded without its caput
  ("Son causas de rescisión de la relación de trabajo, sin responsabilidad para el patrón:")
  is meaningless. Glue the caput lead-in (sentence ending in `:`) to each fracción's embedded
  text — pending measurement on the golden suite (rule 2 in CLAUDE.md), `chunker_version` bump.
- Derogated/“(Se deroga)” nodes, bis/ter/A-B articles, transitorios and reform notes are real
  node types, not noise; derogated text is stored with `is_current=false`, never dropped.
- Navigation chunks (índice, tabla de contenidos, lists of articles) get `is_navigation=true` and
  are excluded at query time. They list every heading of the code and out-rank the articles they
  point to.
- Byte-identical text across versions is expected (a reform touches art. 47, not art. 48). The
  eval treats identical text as equally valid gold only within the session's vigencia scope.
- Every parsing bug becomes a fixture first, then the fix. Required pathological fixtures: DOF
  multi-column page, article without fracciones, nested inciso a)/b), fracción with sub-párrafos,
  “Artículo Xº Bis”, derogated article, transitorios, duplicate headings across books,
  empty node, unclosed quote/table. Property tests: no chunk crosses a node; concatenated chunk
  text covers each article with no loss or duplication; every `cite_id` unique and parseable.

## Versioning (CLAUDE.md invariant 5, ADR-006)
- Chunk rows carry `embedding_model`, `embedding_revision`, `chunker_version` and the chunk
  thresholds. Query embeddings must use the same model+revision as the rows they search.
- Re-embed / re-chunk builds a new generation aside and flips it atomically; failure leaves the
  live generation untouched. Incremental ingestion is by content checksum, one transaction per
  document.

## Retrieval defaults (tune only via eval evidence)
- Semantic arm: top 40, cosine, prefiltered. Lexical arm: top 40, websearch_to_tsquery, prefiltered.
- Fusion: RRF k=60 → top 20. Rerank: multilingual cross-encoder → top 8.
- Determinism: RRF and every sort break ties by a stable secondary key (`cite_id`), never by
  insertion or DB order, so a run is reproducible and `--compare` diffs are real. Property test:
  same inputs → same ranking, and fusion depends only on ranks (never on raw scores).
- The lexical arm folds accents and case per language (`unaccent` + the `spanish`/`english`
  tsvector config; “articulo 47” must match “artículo 47”, “regresion”-style misses are bugs).
  Do not emulate stemming with hacks; the semantic arm covers morphology. Covered by a test that
  runs in `doctor` too.
- Refusal threshold τ: rerank score; if max < τ → structured refusal, NO LLM call (ADR-005, 007).
- Parent expansion via ltree ancestors; dedupe by parent node_id; context budget 12k tokens.
  Fewer good fragments beat many mediocre ones (more context degrades weaker models): measure
  top-8 vs top-5 expansion on faithfulness before raising any budget.
- Hard filters: tenant (RLS), country/level/state/municipality, is_current. Soft boost: legal_domain.
- Lexical breadcrumb weight (`setweight` A/B on the title/hierarchy vs body) is a tunable, not a
  constant of nature: sweep it (e.g. 0.1…4) and keep the value that improves without sinking any
  category. Library-RAG chose 0.5; heavy title weight favors large codes.
- Baselines before optimization: record semantic-only, lexical-only, RRF and RRF+rerank as
  separate runs. A piece that does not improve the metrics is removed (Library-RAG: reranker +0.12
  recall@8, but a CPU-reranker fallback was 20× slower for no recall gain).
- Query translation / follow-up rewrite (ADR-009): the original query keeps its top-N ranks;
  translated/rewritten searches only fill remaining slots. No equal-weight fusion. Translation
  applies to the lexical arm only (WS3.T06) and never touches the jurisdiction predicate.
- Calibrating τ (ADR-007): hand-built probe sets first (covered vs absent topics, several
  wordings each), then the golden suite. Report the per-topic best score; synonyms can differ by
  0.7+. Store τ and the probe-set run id with every release.

## Citation validator
- Extract all `[cite: X]`. Each X must be in THIS query's retrieved set — else regenerate once
  with explicit feedback; if it persists → refusal. Log every verdict to audit_log.
- The validator is a pure function returning a frozen result
  (`cited`, `invalid`, `placeholders`, `refused`, `grounded`); `grounded` = not refused, ≥ 1 valid
  cite, zero invalid, zero placeholders. The parser accepts grouped ids (`[cite: A; B]`,
  `[cite: A, B]`), tolerates case/whitespace variants, and dedupes in order of first appearance.
- Refusal detection: the exact `<insuficiente>…</insuficiente>` block is the contract. Prose that
  reads as a refusal without the block ("no encontré…", "I could not find…") is a format
  violation: the system discards it and emits the real block (it never reaches the UI as an
  answer, and it is logged as a verdict).
- Reject orphan placeholders: a literal `[cite: X]`, `[cite: CITE_ID]` or any example marker copied
  from a prompt is a failure, not a citation. Prompt examples use obviously fake ids that the
  validator denylists.
- Verbatim quotes: any text the answer puts in quotation marks as statute/case text must appear in
  the cited fragment after folding accents, case and whitespace, with a minimum run of N
  consecutive words (config) so a trivial match does not pass. Non-matching quote → same
  regenerate-once-then-refuse path.
- Coverage: every sentence asserting legal content needs a cite or a `[razonamiento estratégico]`
  tag (adversarial workflow only).
- Refusal text is produced by the system, from the fixed `<insuficiente>` structure; the validator
  never asks the model to write it.
- The validator proves provenance, not faithfulness: right cite + inverted summary passes. The
  NLI/judge tier is a net, and its agreement with lawyer grading is measured and logged
  (`eval-runner` skill) before it is allowed to gate anything.
- cite_id format: `{COUNTRY}-{SCOPE}-{CODE}-{NODE}-v{VERSION}` e.g. `MX-FED-LFT-ART47-FRACII-v2025.06`.
  Client-file chunks use the disjoint namespace `CF-{FILE}-{NODE}` (DB CHECK, ADR-010); a cite_id
  is unique per tenant, and the validator only ever resolves ids inside THIS query's retrieved set.
- Vector queries set `hnsw.iterative_scan = 'relaxed_order'` per transaction (ADR-011). A selective
  jurisdiction filter is answered by the prefilter index with exact ranking; HNSW serves the
  unselective case. Do not "fix" the former into an ANN scan.
