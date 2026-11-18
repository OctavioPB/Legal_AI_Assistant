# PITCH.md — Legal AI Assistant
### AI-assisted legal research where every claim is verifiable — or does not exist

---

## 1. The problem

Lawyers in Mexico and the US face three realities at once:

1. **Legal research is expensive and slow.** Locating the applicable rule across
   three levels of government (federal, 32 states, thousands of municipalities in MX), checking its
   validity after constant reforms and cross-referencing it with case law consumes an enormous fraction
   of billable hours — often delegated to the people with the least judgment for it.
2. **Generic generative AI is unusable in this domain.** General-purpose LLMs
   invent articles, mix the legislation of different states and cite nonexistent theses
   with total fluency. The cases sanctioned in US courts for fabricated citations
   (from *Mata v. Avianca* onward) turned "I used ChatGPT" into a professional-liability
   risk, not an advantage.
3. **Confidentiality is non-negotiable.** Case files are protected by professional
   privilege; uploading them to tools that do not guarantee strict isolation is an ethical
   violation per se.

The market does not need "a chatbot that knows about laws". It needs a system where **the
speed of AI and a lawyer's standard of proof stop being in conflict**.

## 2. The solution

A bilingual (es-MX / en-US), multi-jurisdiction, multi-tenant legal assistant with a
product contract that is unusual in the category:

> **Every legal claim comes out with a verifiable citation to a fragment retrieved from the
> official corpus — or the system refuses to answer and says exactly which source is missing.**

Two workflows on top of that contract:

- **Dossier Builder** — compiles research files: the applicable legal framework,
  case-law criteria and analysis, with each claim anchored to a clickable `cite_id`
  that opens the official PDF at the exact paragraph, ranking sources
  (Constitution > federal law > state > municipal; binding case law > isolated thesis).
- **Adversarial Counterpart** — acts as opposing counsel: it attacks the user's strategy with cited
  normative contradictions and flagged logical fallacies, explicitly distinguishing cited law
  from strategic reasoning, and closing with the case's prioritized vulnerabilities.

The user selects **jurisdiction** (country → state → municipality: e.g. Mexico → Nuevo
León → Monterrey), **specialty** (labor, criminal, corporate/financial) and **language**;
the system structurally guarantees that it will never answer with the law of another state.

## 3. Why it can be trusted (the differentiators are not features, they are architecture)

**a) A grounded refusal is a feature, not a failure.** If the retrieved corpus does not support
the answer, the system emits a structured block: what is missing and what action resolves it.
An assistant that knows how to say "I don't have the source" is the only one a lawyer can cite in
court without fear.

**b) Jurisdictional isolation is mathematical, not probabilistic.** The state/municipality
filter happens as an SQL predicate *before* any semantic search — the law
of Jalisco is literally invisible to a Nuevo León session. We do not depend on a
model "understanding" the difference.

**c) Professional privilege lives in the database.** PostgreSQL Row-Level Security
isolates each firm's files below the application code: a bug cannot
leak data between firms. The isolation is demonstrated by an automatic cross-tenant leak test on every build,
not promised.

**d) Post-generation verification.** A validator independent of the model checks
every emitted citation against what was actually retrieved in that query; an unsupported citation
is regenerated or turned into a refusal. Hallucination is not "minimized" — it becomes
structurally inexpressible in the output.

**e) Truly bilingual.** Cross-lingual embeddings: questions in English retrieve Mexican law
in Spanish and vice versa. Verbatim quotations of rules are always kept in their original
language; translations are marked as unofficial.

## 4. Engineering specification

### 4.1 Stack

| Layer | Technology | Role |
|---|---|---|
| Frontend | Next.js + TypeScript, next-intl | Bilingual UI with toggling without a reload, SSE streaming, citation rendering with deep links |
| API | FastAPI (Python 3.12), managed with `uv` | Session validation (jurisdiction/persona/language against closed catalogs), RLS middleware |
| Data | PostgreSQL 16 + `pgvector` (HNSW) + `tsvector` + `ltree` | A single perimeter: relational, vector and full-text under the same RLS policies |
| Parsing | Docling (IBM, open source) | Multi-column legal PDFs → navigable hierarchical structure; auditable and with no external SaaS |
| Normalization | Own legal grammars per source type | Artículo/Fracción/Inciso (MX), §/subsection/holding (US) → `ltree` tree with validity |
| Embeddings | Cohere embed-v3 multilingual | A shared cross-lingual es/en vector space |
| Reranking | Multilingual cross-encoder (Cohere Rerank 3 / self-hosted bge-reranker) | Fine precision over hybrid candidates |
| LLM orchestration | LiteLLM Proxy (self-hosted) | Routing by task alias over Anthropic / OpenAI / Gemini / DeepSeek; per-tenant budgets and limits; fallbacks |
| Async | Celery + Redis | Heavy ingestion off the query path |
| Observability | Langfuse + Prometheus; immutable audit log | Every answer reconstructible: prompt, fragments, validator verdict |

### 4.2 Pipeline of a query

```
session {tenant, language, jurisdiction, persona, workflow}
  → hard jurisdiction predicate (SQL, federal→state→municipal stacking, never lateral)
  → prefiltered hybrid search: semantic arm (cosine) + lexical arm (FTS)
  → RRF fusion → cross-encoder rerank → confidence threshold τ
       └─ below τ: STRUCTURED REFUSAL without invoking the LLM
  → expansion to the parent article (ltree) → context budget labelled with cite_id
  → prompt composed in 5 versioned layers (fixed anti-hallucination core → workflow →
    persona → session → fragments; no variable comes from free user text)
  → LLM via task alias (LiteLLM)
  → CITATION VALIDATOR: existence, coverage and verbatim quotation against what was retrieved; 1
    regeneration; if it persists, a refusal (generated by code, not by the model)
  → audit log → answer with clickable citations
```

### 4.3 Data and multi-tenancy

- Every retrievable fragment carries its legal genealogy as typed, indexed columns
  (`country / level / state_code / municipality / doc_type / is_current`) and an **immutable,
  versioned `cite_id`** (`MX-FED-LFT-ART47-FRACII-v2025.06`) — the contract between model,
  validator and UI.
- Chunking respects the legal structure (citation unit = fracción/paragraph; context
  unit = the whole article), with the hierarchy embedded in the text before vectorizing.
- A pooled multi-tenant model with forced RLS (`FORCE ROW LEVEL SECURITY`) and a synthetic
  `PUBLIC_CORPUS` tenant for shared public law; private files under the firm's tenant.
  Storage with prefixes and mirrored IAM policies; per-tenant LLM budgets.
- Reforms create versions, never delete: historical queries are an explicit mode with
  a validity warning.

### 4.4 Quality measured, not declared

A golden dataset with deliberate distractors — the same article number in
another state, the repealed version of the rule — running in CI on any change of
prompt, chunker, embeddings or model:

| Metric | Release gate |
|---|---|
| Citation precision (emitted citations that exist in what was retrieved) | **= 1.00, hard** |
| Jurisdictional leakage | **= 0.00, hard** |
| Fidelity of claims to the cited fragment | ≥ 0.98 |
| Refusal accuracy (refuses when it should) | ≥ 0.95 |
| False refusals (refuses when it could have answered) | ≤ 0.05 |
| Retrieval recall @k | ≥ 0.90 |

Models compete for the task aliases on these metrics; the provider behind each
alias can change without touching application code.

How the measurement is kept honest: cases are written or approved by lawyers (each case
declares authorship and review); differences of 1–2 cases are reported as noise, not as an
improvement; each run stores its configuration and versions; fidelity is graded on sheets
that show the full text of each cited fragment; and every change to retrieval or prompts
records figures before and after.

### 4.5 Honesty about scope

The system guarantees **traceability, not legal judgment**: it is a tool for
lawyers, not a lawyer. A valid citation proves provenance, not that the paraphrase is faithful
(a correct citation with an inverted summary is a known residual risk, mitigated with verbatim quotation,
NLI and human review). The gates are computed on small sets at first: a difference
of one or two cases is noise. Its quality depends on the freshness of the corpus (watching the DOF
and gazettes is permanent operational work) and its cost per query is ~1.3–1.6× that of a naive RAG
because of the double validation pass — in this domain, that overhead *is* the product.

## 5. State and path

- **Today (2026-11-14):** a working demo that runs entirely on one machine, sending nothing outside
  (`docs/LOCAL_DEMO.md`): three workspaces (Conversar, Investigación, Argumentar) with verified
  citations and a structured refusal; a law navigator, a citation verifier for briefs, matters with
  members, a reform watcher and **the firm's own documents**, citable and isolated per matter.
  Measured on 25 AI-drafted cases, not yet reviewed by lawyers: recall@8 1.00, jurisdictional
  leakage 0, correct refusal 5/5, false refusal 1/20 with the local model. Design in
  `docs/LegalAI-System-Architecture.md`, decisions in the ADR log, progress in `PLAN.md`.
- **A complete case ready for review (2026-11-17):** a fictional labor dismissal run through all the
  tools, with a packet for a lawyer to grade each answer against the full text of what is cited
  (`docs/EVALUACION-E2E.md`). The first run already showed what only a lawyer can judge: real, verified
  citations to articles that do not govern the case.
- **Missing to close the demo:** review of the evaluation set by lawyers (until then
  the gates do not count), OCR for scanned documents and measuring the optional remote profile.
- **Afterwards:** procedural-deadline calculator, brief templates, case law and the Mexican
  citator, dossier export (`ROADMAP.md`). Reform monitoring and the
  citation verifier for external briefs, formerly on this list, are already built.

---

*From Pipeline to Decision.* — OPB
