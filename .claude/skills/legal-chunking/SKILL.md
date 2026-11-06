---
name: legal-chunking
description: Procedure for parsing, normalizing and chunking legal documents into doc_nodes/chunks. Use ALWAYS when working in app/ingestion/ (normalizer grammars, chunker, embedder), changing chunk thresholds, investigating a retrieval result that looks cut off or misplaced, or adding ingestion fixtures — even if the change looks small.
---

# Legal-structure chunking

Retrieval quality is bounded by chunk quality: a fracción cut from its caput, or text split
mid-node, is unrecoverable for the reranker and misleading for the LLM. Rules: `rules/rag.md`.
Base decisions: ADR-004, ADR-006.

## Data model (per chunk)
`cite_id` (immutable), `node_id` + `parent_node_id` (ltree), `text` (EXACT slice of the normalized
source, no breadcrumb), `breadcrumb`, `embedded_text` (= breadcrumb + caput lead-in + text),
`char_start/char_end` (+ page/bbox for the PDF deep-link), `token_count` (embedder tokenizer),
jurisdiction columns, `is_current`, `is_navigation`, `embedding_model`, `embedding_revision`,
`chunker_version`, `chunk_thresholds` (JSON).

## Procedure
1. **Docling** → layout model (reading order, tables). Log pages with order uncertainty.
2. **Normalizer grammar** types nodes: libro/título/capítulo/sección/artículo (incl. Bis/Ter/A-B)/
   párrafo/fracción/inciso/transitorio/reforma note (MX); §/subsection/holding (US). Derogated
   nodes ("Se deroga") are kept with `is_current=false`. Unparseable regions are reported, not
   guessed: a node the grammar cannot type becomes a flagged generic node.
3. **Unit selection:** deepest self-contained node; merge-up < 80 tokens; split > 800 tokens by
   párrafo (same parent). Never cut mid-node, never cross a node boundary.
4. **Lead-ins:** a caput sentence ending in `:` is glued to each child's embedded text (pending
   golden measurement → `chunker_version` bump). The displayed/cited `text` stays the node's own.
5. **Embedder window check:** `token_count ≤ embedder window`; oversized chunks are logged with
   their cite_id and counted in the ingestion report.
6. **Navigation:** flag índices/tablas de contenido (`is_navigation`).
7. **Identifiers:** build `cite_id` + `citation_string`; assert uniqueness and parseability.
8. **Idempotence:** content checksum per document; one transaction per document; a reform creates
   a new version, flips `is_current` on the prior one, deletes nothing (invariant 3).
9. **Generation swap:** a changed embedder/chunker/threshold builds a new generation aside; flip
   atomically only after integrity checks pass (ADR-006).

## Ingestion run contract
- One ingestion job per source at a time: advisory lock keyed by `(tenant_id, source_id)`;
  a lock held by a dead worker is released/taken over (lease with heartbeat), never left stuck.
- A document that fails to parse is recorded in `failed` with its reason and does NOT abort the
  run; the live generation is unchanged for that document.
- The run returns a stats record: `added, updated, deleted, unchanged, failed[], chunks_written,
  oversized, seconds, embed_seconds` — shown in the job status screen and stored in `audit_log`.
- Post-ingest integrity check (also in `doctor`): every chunk has its embedding and `tsvector`,
  its `parent_node_id` exists, `cite_id` unique, counts per document match the node tree, no
  chunk from an inactive generation is visible to retrieval.
- Embedder is created lazily (only if something changed) and closed when done; an unchanged
  re-run costs seconds and no provider calls.

## Versioning
`CHUNKER_VERSION` lives in `app/ingestion/chunker.py`; bump on ANY change that alters chunks,
including default thresholds. The thresholds are also stored with each chunk, so a settings
override that changes chunks without a version bump is detected and refused at ingestion.

## Tests
- Fixture per pathological case (list in `rules/rag.md`); every parsing bug → fixture first, fix second.
- Property tests: node boundaries never crossed; per-article text coverage with no loss or
  duplication; `text` is an exact slice of the source; `cite_id` unique/parseable; no non-
  document chunk below the minimum except whole-node chunks.
- Golden tree fixtures (full law → exact expected tree diff) for each grammar.
- Manual inspection: `uv run python -m app.ingestion.cli chunks <pdf>` prints ordinal, node path,
  tokens, breadcrumb and char range per chunk. Review by eye before closing changes in this layer,
  and run the golden suite (invariant 2).
