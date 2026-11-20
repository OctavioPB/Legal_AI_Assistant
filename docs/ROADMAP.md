# ROADMAP.md — Legal AI Assistant: future work

Everything listed here is **outside the scope of the demo** (see PLAN.md) but was considered
in the design: the current architecture (Postgres/RLS perimeter, immutable `cite_id`, `ltree`
tree, model aliases, golden dataset) is the base each item builds on.
Each feature says which existing piece enables it, to avoid redesigns.

Ordering criterion: value to the practising lawyer ÷ effort, with the lock that
nothing enters if it degrades the two hard gates (citation precision = 1.00, leakage = 0.00).

---

## Horizon 1 — Immediately after the demo (0–6 months): deepen the daily workflow

**R1.1 · Reform monitoring (DOF + state gazettes) with alerts per dossier.** *(Core done 2026-11-08,
WS9.T04/ADR-017: a watcher of the official diputados.gob.mx URLs + a local inbox, lineage per citable unit and
alerts per conversation/source/report. Missing: the daily DOF and state gazettes as sources, notification outside
the app.)*
A scheduled pipeline that detects new publications, ingests them and — the key point — cross-checks the reformed
`cite_id`s against each tenant's active dossiers: *"Art. 76 LFT, cited in
your file X, was reformed on 14-06; the version you cited is no longer in force"*. It is the highest-retention
feature possible for a lawyer: it turns the fresh corpus (today an operational risk, see Limitations §12 of the
blueprint) into a product. Enabled by: the existing `is_current` versioning + immutable `cite_id`.

**R1.2 · Procedural-deadline calculator per jurisdiction.**
Deadlines (answer, appeal, evidence offering) computed from the procedural code
of the active jurisdiction, with a calendar of non-working days per court. Deterministic, not
generative — but each deadline is shown with its `[cite:]` to the procedural article. Deadlines
are the #1 cause of professional liability; a citable calculator is defensible before
the client and the bar association.

**R1.3 · Citation verifier for external documents.** *(Done for statutes 2026-11-08, WS9.T02/ADR-019;
case law and theses pending a corpus.)*
Upload the opposing party's brief (or your own before filing it) and validate each
statutory/case-law citation against the corpus: it exists, it is in force, it says what is attributed to it.
It is the current validation pipeline (WS5) pointed inward at someone else's PDF — almost direct reuse of the
citation validator + retrieval. Use case after *Mata v. Avianca*, with an obvious demand: detect the
hallucinations of the other side's AI.

**R1.4 · Brief templates per subject and court (assisted drafting).**
Labor complaint, answer, indirect amparo: skeletons per persona/jurisdiction where the
Dossier Builder fills the law chapter with verified citations and the lawyer writes the
facts. Strictly assistive: the system never "files", it only pre-fills with traceability.

**R1.5 · Matter-level collaboration.** *(First step done 2026-11-08, WS9.T03/ADR-018: matters
with members under RLS, notes and saved sources. 2026-11-14, ADR-025: firm documents per matter, with
the per-matter RLS sub-scoping this item asked for. Missing: partner/associate/intern roles, comments on
fragments and dossier versions.)*
Permissions per file within the tenant (partner/associate/intern), comments on cited
fragments, dossier version history. Enabled by: the existing `dossiers` +
`audit_log`; requires per-matter RLS sub-scoping (an additional policy, not a
redesign).

**R1.6 · Corpus-gap backlog built from refusals.**
Every `<insuficiente>` caused by a missing source is added to a report of "requested topics the
corpus does not cover" (deduplicated by topic, with a count and the jurisdiction), which feeds the ingestion
queue and the R1.1 monitoring. It turns the refusal into a product signal. Lesson from
Library-RAG (gap detector): embeddings only preselect similar topics (cosine
0.41–0.81 for the same topic reworded vs 0.31–0.73 for neighbouring topics); merging topics is
decided by a separate step, not by cosine. Enabled by: `audit_log` + the structured refusal payload.

## Horizon 2 — Consolidation (6–18 months): intelligence over the corpus

**R2.1 · Mexican citator (graph of validity and contradiction).**
A directed graph over `cite_id`s: which thesis interrupts/replaces which, which case law
by contradiction resolved which conflicting criteria, which articles refer to which
("except as provided in..."). The functional equivalent of Shepard's/KeyCite, nonexistent in
Mexico at this level. Enabled by: `doc_nodes` + the normalizer's reform detection;
requires relation extraction (a batch `cheap-utility` LLM task) and an edge table.
It is probably the deepest competitive moat in the whole roadmap.

**R2.2 · Knowledge graph of statutory cross-references.**
Navigation "this article refers to these 4; it is referred to by these 12" and automatic expansion
of the retrieval context with first-degree cross-references (today parent expansion only goes up
the tree; this expands laterally). A direct recall improvement on complex questions.

**R2.3 · Analytics of criteria per judicial body.**
Resolution trends per collegiate court/chamber over the public corpus of judgments:
which criteria each body applies, how often it grants amparo per subject. Explicit ethical
restriction: aggregates per body, never profiles of individual judges aimed at
forum shopping — the line is documented in Brand.md/terms of use.

**R2.4 · Search over hearings and records (ASR).**
Transcription of hearings (with consent/local legality) → ingestion as a `client_file`
(uploading the firm's documents already exists, ADR-025; ASR and the speaking-turn grammar are missing)
with citable timestamps. The hierarchical chunker gains a new grammar (speaking turns);
everything else (RLS, citations, validator) applies unchanged.

**R2.5 · US jurisdiction expansion + case-file integrations.**
State coverage in the US beyond CA; read-only connectors to Tribunal Virtual (MX) and
PACER/CM-ECF (US) to bring records into the matter automatically.

## Horizon 3 — Platform (18+ months)

**R3.1 · Enterprise tier: physical isolation and customer-owned keys.**
Schema-per-tenant or DB-per-tenant + BYOK (envelope encryption per tenant) + configurable data
residency. The migration is pre-wired: `tenant_id` travels in every table
(ADR-002).

**R3.2 · On-prem / firm-VPC deployment.**
For firms with a ban on shared cloud. Requires the reranker and embeddings
to have self-hosted variants (already contemplated: `bge-reranker-v2-m3` as an alternative) and a
local model alias (e.g. self-hosted DeepSeek) in LiteLLM.

**R3.3 · Own legal-es model (distillation).**
With the volume of Langfuse traces + an expanded golden dataset, distilling a small model for
`cheap-utility` (classification, citator relation extraction) reduces the cost of the double
validation pass. Never for drafting until it matches the citation precision of the current alias
— the router decides it with data, not strategy.

**R3.4 · LATAM expansion (Colombia, Chile, Argentina).**
The jurisdiction design (country→level→entity, closed catalog, stacking upward) is
generalizable to other civil-law systems; the real cost is the per-country normalizer (a new
grammar) and the corpus. Enter through countries with a digitized, structured official gazette.

**R3.5 · Public API + connector marketplace.**
Expose citable retrieval and citation verification as an API for practice-management
software. The citation validator as a service is sellable on its own.

---

## Anti-roadmap (decisions NOT to do, so they are not relitigated)

- **No** legal chatbot for end consumers: it changes the risk profile (unauthorized
  advice) and the whole design assumes a professional who validates.
- **No** "trial outcome prediction" as a feature: an indefensible promise on this
  evidence; the R2.3 analytics stay at descriptive frequencies.
- **No** map-reduce / per-document summarization as the default synthesis strategy: measured in
  Library-RAG, it lowered fidelity (0.87 → 0.58) and stopped abstaining (ADR-008). Reopened only with
  a golden run that wins on fidelity AND on refusal accuracy.
- **No** equal-weight fusion of rewritten/translated queries with the original (ADR-009).
- **No** uncalibrated LLM judge as the sole fidelity verifier: the verifier is code plus
  human review; a judge must measure its agreement with lawyers before it blocks anything.
- **No** letting the model write the refusal or "explain what is missing" in its own words.
- **No** relaxing the refusal to "seem more useful": `<insuficiente>` is a product contract
  (invariant #4 in CLAUDE.md).
