# Research: techniques to strengthen the project

This document collects the techniques proposed on 2026-11-26 to close the weaknesses that the academic review
([Thesis.pdf](Thesis.pdf)) and the end-to-end case ([EVALUACION-E2E.md](EVALUACION-E2E.md)) found. For each one it
gives the sources, why it was chosen over the alternatives, how it applies to this codebase, and what would show
that it worked.

The document has three rounds: §1–§9 (what the system cannot yet prove), §10–§19 (performance and precision, with a
baseline measured on the demo machine) and §20 (the law as of a date). Each technique was written as a **proposal**
with its own ADR (ADR-031 to ADR-050 in [.claude/rules/architecture.md](../.claude/rules/architecture.md)). The
project's rules apply to all of them: the owner chooses before implementation, a golden run is recorded before and
after any change to prompts, the chunker, embeddings, retrieval or routing, and the system verifies the model, never
the reverse (invariant 6). What has been built since is listed below; the sections keep the text as proposed.

## Status (2026-11-27)

| ADR | Section | State | What was built and measured |
|---|---|---|---|
| 031 | §1 | Accepted, warning mode | LFT regime of each cited unit, warning when it contradicts the matter; flags exactly the 3 regime errors of E2E-LAB-01 |
| 032 | §2 | Accepted (statistics) | Clopper-Pearson intervals in every run, exact McNemar in `--compare`; growing the set stays pending |
| 033 | §5 | Accepted for Dossier Builder (local) | Typed records under a JSON schema: false refusals 2 -> 1 of 20 in each of 3 runs per arm (2 cases better, 1 worse, no category worse); the prompt-only variant (C) lost twice |
| 038 | §8 | Accepted for A and B | Mutation check (15 of 15 mutants killed) and a replay suite of real model outputs |
| 041 | §11 | Accepted | Retrieval is 0.56 s; the slow turns were Ollama swapping models; `OLLAMA_MAX_LOADED_MODELS=3` |
| 043 | §13 | Accepted | The retry already reuses the cached prompt prefix (0.8 s vs 14.1 s); now guarded by a test |
| 049 | §19 | Accepted, adopted as index generation 4 | Cardinal Títulos and heading names on their own line; adopted together with ADR-052 |
| 036 | §6 | Accepted for reporting | Per-topic certified bounds in every calibration: at τ 0.58, 10 of 22 absent topics pass (≤ 61 % at 90 %); τ unchanged |
| 037 | §7 | D accepted, C rejected | Character-level precision/recall in every run (0.133 / 0.890); cross-reference expansion cost a case and was switched off |
| 045 | §15 | Rejected at this scale | Saves ~40 MB on 20k chunks; tripwire at 500k chunks |
| 048 | §18 | Rejected | Convex fusion equal to RRF at every alpha: the dense reranker re-orders the pool |
| 050 | §20 | Accepted for B and D | As-of date in the predicate, conversations and UI; a reform retires the old version at the new one's entry into force; temporal track 4/4 on retrieval |
| 034, 035, 039, 040, 042, 044, 046, 047 | | Proposed | Not built; each ADR records what unblocks it (a download, an install or lawyer labels) |

Found while building these and recorded as new decisions: ADR-051 (a decree credited with a change its own list does
not make is caught by code) and ADR-052 (decree headings are hidden units and decree dates travel in citations). The
golden suite on the current index is `golden-20261127T195859Z-after-hidden-headings-gen4`.

## The weaknesses being addressed

| Weakness | Evidence | Addressed in |
|---|---|---|
| A verified citation can point to an article that does not govern the case | 5 of 8 delivered answers in E2E-LAB-01 | §1, §3, §4 |
| The evaluation set is too small to bound the claims | 25 AI-drafted cases, none lawyer-reviewed | §2, §4 |
| Faithfulness has never been measured | `faithfulness: null` in every run | §3, §4 |
| Correct answers are refused for uncited framing sentences | ADR-015; Qwen 17/20 false refusals | §5 |
| The refusal threshold was picked by hand and does not fit firm documents | tau 0.58; a contract question scored 0.571 | §6 |
| Distractors reach the top 8; no per-arm baselines exist | distractor rate 0.16; no per-arm runs in `evals/runs/` | §7 |
| "Verification is code" is a claim, latency is high, OCR can misread figures | 35 s median; ADR-030 | §8 |
| The business value is unmeasured | only survey expectations | §9 |

## §1. Applicability from the law's own structure (ADR-031)

**Sources.** Magesh, Surani, Dahl et al. (2025), *Hallucination-Free? Assessing the Reliability of Leading AI Legal
Research Tools*, which names misgrounding (a real source that does not support the claim) as the residual error of
retrieval-based tools. Guha et al., *LegalBench* (arXiv 2308.11462), which separates issue spotting from rule
application, the step where facts are matched to a rule. de Martim (2026), *Beyond Probabilistic Similarity*
(arXiv 2606.09724), which argues that legal RAG fails structurally ("mereological blindness") when it ignores the
hierarchy of norms.

**Why chosen.** It is the only option that checks applicability without asking a model to judge another model.
An LLM judge of applicability would break invariant 6 and has no measured agreement with lawyers. It is also cheap,
because the data is already stored.

**How it applies.** The breadcrumbs of the loaded LFT already state each article's regime, and three of the five
E2E-LAB-01 errors are visible in them:

| Cited | Stored breadcrumb | The case |
|---|---|---|
| art. 283 Ter | TITULO SEXTO Trabajos Especiales › CAPITULO VIII Personas Trabajadoras del Campo | billing assistant |
| art. 341 | TITULO SEXTO Trabajos Especiales › CAPITULO XIII Personas Trabajadoras del Hogar | billing assistant |
| art. 436 | TITULO SEPTIMO Relaciones Colectivas de Trabajo | an individual dismissal |

The proposal has four parts:
- Regime tags per article, derived by code from Título and Capítulo.
- A closed-list "type of relationship" field on the case sheet.
- A check that flags or refuses a contradiction, in the same way the jurisdiction predicate excludes another state.
- Per-article "direction" tags, written once per law by a lawyer, for errors within one chapter: art. 51 (grounds
  for the worker) against art. 47 (grounds for the employer), or art. 519 against art. 518.

A defect must be fixed first: some chapter titles were dropped at ingestion ("CAPITULO IV" with no name).

**What would show it works.** A new golden category, "applicability", seeded with the five E2E-LAB-01 errors, and
reviewed by a lawyer.

## §2. Exact intervals and sample sizes (ADR-032)

**Sources.** The Clopper–Pearson exact binomial interval; McNemar's exact test for paired before/after outcomes;
the "rule of three" for zero-failure bounds.

**Why chosen.** The project already reports "1–2 cases is noise" as a rule of thumb. Exact intervals make that rule
a calculation, need no model, and change nothing in the product. A Bayesian beta-binomial posterior is equally
valid but harder to explain to lawyers.

**How it applies.** Computed on 2026-11-26 from the recorded runs:

| Claim | Result | 95 % interval |
|---|---|---|
| Correct refusals | 5/5 | 48 %–100 % |
| False refusals | 1/20 | 0.1 %–25 % |
| Leakage in the isolation cases | 0/5 | up to 52 % |
| Leakage over the whole set | 0/25 | up to 14 % |
| Misapplied answers in E2E-LAB-01 | 5/8 | 25 %–92 % |

With zero failures, a one-sided 95 % bound below 5 %, 2 % and 1 % needs 59, 149 and 299 cases. The runner
already stores per-case outcomes, so McNemar's test on the flipped cases can replace the noise rule in
`--compare`.

**What would show it works.** Every run report and the README carry intervals. The golden set grows toward 60
isolation and 60 refusal cases, each reviewed by a lawyer.

## §3. A small local fact-checker for faithfulness (ADR-034)

**Sources.** Tang, Laban and Durrett, *MiniCheck: Efficient Fact-Checking of LLMs on Grounding Documents* (EMNLP 2024,
arXiv 2404.10774): a 770M Flan-T5 checker that reaches GPT-4 accuracy on the LLM-AggreFact benchmark at about 400×
lower cost. Song, Kim and Iyyer, *VeriScore* (Findings of EMNLP 2024, arXiv 2406.19276), which extracts only
verifiable claims and uses the context between sentences; Min et al., *FActScore* (arXiv 2305.14251), its
predecessor.

**Why chosen.** It is small enough to run locally, so ADR-014's "nothing leaves the machine" still holds. It is a
separate model from the one that drafts, so it does not break invariant 6. It is open, with published benchmarks. An
LLM judge using the drafting model was rejected: it costs a second generation and is the model checking itself.

**How it applies.**
- **Claim extraction:** each sentence of an answer is first reduced to its verifiable claims, because a legal
  sentence often makes two.
- **Checking:** each claim is scored against the fragment it cites.
- **Display:** the result is shown as a warning ("Fidelidad no verificada"), not a refusal.
- **Promotion to a gate:** only after its agreement with lawyer grades is measured. rag.md already requires that
  for any judge.

**Caveat.** MiniCheck was trained on English data. Its accuracy on Spanish statute text is unknown, and measuring it
is the first step.

**What would show it works.** Its agreement with lawyer grades on E2E-LAB-01 and on the golden set, measured as in §4.

## §4. Valid intervals from few lawyer labels (ADR-035)

**Sources.** Saad-Falcon et al., *ARES: An Automated Evaluation Framework for Retrieval-Augmented Generation Systems*
(arXiv 2311.09476), which uses prediction-powered inference (PPI) to correct a judge's bias with a few hundred human
labels. Oosterhuis et al. (arXiv 2407.02464), which applies PPI and conformal risk control to retrieval evaluation
with LLM labels.

**Why chosen.** Lawyer time is the bottleneck, and no answer has been graded yet. Grading everything is
unaffordable, and a judge alone gives biased numbers with no valid interval. PPI is the method that uses both
sources honestly.

**How it applies.**
- **Labels:** lawyers grade a stratified sample: every category, plus the cases where the validator and the judge
  of §3 disagree.
- **Estimate:** the judge scores everything, and the PPI estimator gives the interval.
- **Agreement:** two lawyers grade an overlapping subset, and Cohen's κ or Krippendorff's α is reported.
- **First labels:** the review page of E2E-LAB-01 already collects per-source grades.

**What would show it works.** A faithfulness interval from about 50–100 lawyer labels, together with the agreement
between the lawyers.

## §5. Typed sentences under a JSON schema (ADR-033)

**Sources.** Structured outputs with a JSON schema, which Ollama supports, as one form of constrained decoding.
Dhuliawala et al., *Chain-of-Verification Reduces Hallucination in Large Language Models* (Findings of ACL 2024,
arXiv 2309.11495), considered and **not** adopted as a check.

**Why chosen.** ADR-015 found that every inspected failure was a correct but uncited closing sentence, not an
invented citation. Typing each sentence attacks that cause without relaxing the contract. Chain-of-Verification has
the model verify its own draft, so it may shape drafting but can never be the check (invariant 6).

**How it applies.**
- **Output:** the model emits `[{text, kind: law | reasoning | framing, cites: [...]}]`, and the validator checks
  fields instead of splitting sentences.
- **The contract:** a `law` record without a valid citation is still refused.
- **Framing:** it becomes an explicit, rendered category instead of a coverage failure. It must be defined narrowly
  so that it cannot carry uncited law.
- **Cost:** every workflow needs a new prompt version, with a golden run before and after (invariant 2).

**What would show it works.** False refusal and regeneration rate per profile, measured against a prompt-only
alternative.

## §6. A refusal threshold with a guarantee (ADR-036)

**Sources.** Abbasi-Yadkori et al., *Mitigating LLM Hallucinations via Conformal Abstention* (arXiv 2405.01563); *COIN*
(AAAI-26, arXiv 2506.20178), which bounds the error rate of selective QA with Clopper–Pearson; *Hierarchical
Group-Conditional Conformal Risk Control for Selective Prediction in Language Models* (arXiv 2607.24562), which shows
a single global budget can hide failures in a subgroup; *SAFER* (arXiv 2510.10193).

**Why chosen.** It turns "tau was picked by eye" into "the system answers an absent topic at most α of the time,
with 1−δ confidence". The group-conditional variant matches this project's measured problem: contract prose
scored just under a threshold calibrated on statutes.

**How it applies.**
- **Groups:** a threshold per group (statute, firm document, optionally per language), calibrated by **topic** rather
  than by wording. The 66 absent-topic wordings come from only 22 topics, so wordings are not independent.
- **Caveat:** the guarantee assumes calibration questions and real questions are exchangeable, which AI-drafted
  probe sets do not ensure.

**What would show it works.** The bound holds on a held-out, lawyer-reviewed probe set for each group.

## §7. Retrieval experiments (ADR-037)

**Sources.**
- Chen et al., *BGE M3-Embedding* (arXiv 2402.03216): one model gives dense, sparse and multi-vector scores.
- Jina AI, *Jina-ColBERT-v2* (arXiv 2408.16672): a multilingual late-interaction retriever with an 8,192-token
  window.
- de Martim, *An Ontology-Driven Graph RAG for Legal Norms* (SAT-Graph RAG, arXiv 2505.00039): models cross-references
  and dated versions of norms.
- Pipitone and Houir Alami, *LegalBench-RAG* (arXiv 2408.10343): scores retrieval by character-level precision and
  recall.

**Why chosen.** Each answers a measured gap. The dense signal confused same-numbered articles (ADR-022), and
distractors reach the top 8 in 16 % of cases. The cross-encoder lost on separation (AUC 0.892 against 0.934) and was
16× slower. Statutes refer to each other ("en los términos del artículo 48").

**How it applies.**
- **D, measure first:** character-level retrieval precision and recall, since chunks already store character
  offsets.
- **A, bge-m3's sparse and multi-vector scores as a reranker:** no new embedding generation, but it needs FlagEmbedding
  on CPU because Ollama serves only the dense output.
- **C, cross-reference expansion:** references are parsed with the existing citation grammar, within the 12k context
  budget.
- **B, a Jina-ColBERT-v2 arm:** it has no published Spanish legal results, and it would be a new embedding generation
  (ADR-006).

**What would show it works.** Per-arm baselines first (rag.md requires them and none is recorded), then each piece
kept only if it improves without sinking any category.

## §8. Evidence for the engineering claims (ADR-038)

**Sources.** Mutation testing (for example `mutmut`); the project's own ADR-021, where stored outputs were
re-validated by hand; KV-cache reuse of a fixed prompt prefix and speculative decoding with a small draft model of the
same family; the Mexican convention of writing contract amounts in figures and in words.

**Why chosen.** They turn statements into numbers. "Verification is code" becomes a mutation score, regressions are
caught on real outputs, and latency gets a measured target. The amount check is legal-specific, deterministic, and
catches a misread figure that the OCR confidence threshold of ADR-030 lets through.

**How it applies.**
- **A, mutation testing of `app/validation/`:** every weakened check must make a test fail.
- **B, a replay suite:** stored model outputs from the audit log are re-validated on every change.
- **D, OCR amounts:** a unit where "$15,000.00 (QUINCE MIL PESOS 00/100 M.N.)" disagrees with itself is flagged.
- **C, latency:** prefix-cache reuse and speculative decoding, measured at p50 and p95 per profile on the 8 GB GPU.

**What would show it works.** A mutation-score baseline, a replay suite in CI, latency before and after, and a test
of the amount check on generated scans.

## §9. Measuring the value with lawyers (ADR-039)

**Sources.** Thomson Reuters Institute, *Future of Professionals Report 2025*: professionals *expect* to save about
5 hours a week, a survey expectation rather than a measurement. The within-subject, counterbalanced design and
the Wilcoxon signed-rank test for paired outcomes.

**Why chosen.** The business case rests entirely on expectations today. A within-subject study needs few
participants, because each lawyer is their own control, and it measures errors introduced as well as time
saved. That matters because the end-to-end case showed the tool can introduce misapplied citations.

**How it applies.**
- **Design:** 10–12 lawyers do matched research tasks on fictional matters, with and without the tool, in
  counterbalanced order.
- **Measures:** time, errors caught and errors introduced.
- **Protocol:** written before the study, and the task set reviewed by a lawyer.
- **Afterwards:** a pilot in one firm, with logs only.

**What would show it works.** Paired differences in time and in errors, each with its interval.

## Suggested order

1. §1 applicability tags: the cheapest, and aimed at the headline weakness.
2. §2 intervals and a larger golden set: statistics only, no model change.
3. §5 typed sentences, with golden runs before and after.
4. §3 and §4 together: the fact-checker plus the first lawyer labels.
5. §6 a conformal threshold per document type.
6. §9 the time-on-task study.

§7 and §8 run alongside, as measurement work.

## References

- Abbasi-Yadkori, Y., et al. (2024). Mitigating LLM Hallucinations via Conformal Abstention. arXiv:2405.01563.
- Angelopoulos, A. N., et al. Prediction-powered inference; conformal risk control (the recipe cited by the
  selective-QA papers below).
- Chen, J., et al. (2024). BGE M3-Embedding. arXiv:2402.03216.
- COIN: Uncertainty-Guarding Selective Question Answering for Foundation Models with Provable Risk Guarantees
  (AAAI-26). arXiv:2506.20178.
- de Martim, H. (2025). An Ontology-Driven Graph RAG for Legal Norms: A Structural, Temporal, and Deterministic
  Approach. arXiv:2505.00039.
- de Martim, H. (2026). Beyond Probabilistic Similarity: Structural, Temporal, and Causal Limitations of
  Retrieval-Augmented Generation in the Legal Domain. arXiv:2606.09724.
- Dhuliawala, S., et al. (2024). Chain-of-Verification Reduces Hallucination in Large Language Models. Findings of
  ACL 2024. arXiv:2309.11495.
- Guha, N., et al. (2023). LegalBench: A Collaboratively Built Benchmark for Measuring Legal Reasoning in Large
  Language Models. arXiv:2308.11462.
- Hierarchical Group-Conditional Conformal Risk Control for Selective Prediction in Language Models (2026).
  arXiv:2607.24562.
- Jina AI (2024). Jina-ColBERT-v2: A General-Purpose Multilingual Late Interaction Retriever. arXiv:2408.16672.
- Magesh, V., Surani, F., Dahl, M., Suzgun, M., Manning, C. D., and Ho, D. E. (2025). Hallucination-Free? Assessing
  the Reliability of Leading AI Legal Research Tools. Journal of Empirical Legal Studies; arXiv:2405.20362.
- Min, S., et al. (2023). FActScore: Fine-grained Atomic Evaluation of Factual Precision in Long Form Text Generation.
  arXiv:2305.14251.
- Oosterhuis, H., et al. (2024). Reliable Confidence Intervals for Information Retrieval Evaluation Using Generative
  A.I. arXiv:2407.02464.
- Pipitone, N., and Houir Alami, G. (2024). LegalBench-RAG: A Benchmark for Retrieval-Augmented Generation in the
  Legal Domain. arXiv:2408.10343.
- Saad-Falcon, J., et al. (2023). ARES: An Automated Evaluation Framework for Retrieval-Augmented Generation Systems.
  arXiv:2311.09476.
- SAFER: Risk-Constrained Sample-then-Filter in Large Language Models (2025). arXiv:2510.10193.
- Song, Y., Kim, Y., and Iyyer, M. (2024). VeriScore: Evaluating the Factuality of Verifiable Claims in Long-Form
  Text Generation. Findings of EMNLP 2024. arXiv:2406.19276.
- Tang, L., Laban, P., and Durrett, G. (2024). MiniCheck: Efficient Fact-Checking of LLMs on Grounding Documents.
  EMNLP 2024. arXiv:2404.10774.
- Thomson Reuters Institute (2025). Future of Professionals Report 2025.

Author names marked "et al." and two titles (Oosterhuis et al., the hierarchical conformal paper) were taken from
search results and abstracts, not from the full papers; check them before quoting this document elsewhere.

# Second round: performance and precision (2026-11-26)

The first round (§1–§9) targeted what the system cannot yet prove. This round targets two things the owner asked
for: speed on the demo machine and precision of retrieval and citations. Each technique has its own ADR (ADR-040 to
ADR-049), all Proposed when this round was written; see the status table at the top for what was built since.

## The measured baseline

Measured on 2026-11-26 on the demo machine (RTX 5060, 8 GB; Ollama 0.40.13; `gemma4:26b-a4b-it-qat`):

| Measure | Result |
|---|---|
| Where the model runs | split by whole layers, 68 % CPU / 32 % GPU |
| Cold load | 46.5 s |
| Prompt processing (prefill), 4 967-token prompt | 375 tokens/s (13.2 s) |
| Generation | 27–30 tokens/s |
| Retrieval, golden run | p50 3.16 s, p95 5.55 s |
| End to end, golden run | p50 35.5 s, p95 58.0 s |

At 375 tokens/s, the 12 000-token context budget alone costs about 32 s of prefill per attempt, and a regeneration
pays it again. Most of the latency is prompt processing and the CPU/GPU split, not generation.

## §10. Experts on CPU, everything else on GPU (ADR-040)

**Sources.** The llama.cpp MoE offload guide (Hugging Face); community benchmarks of `--n-cpu-moe` on 8 GB cards; a
Hugging Face forum thread on Gemma 4 26B memory in llama.cpp.

**Why chosen.** The drafting model is a mixture of experts with about 4B active parameters per token. Ollama splits it
by whole layers, so most layers, attention included, run on the CPU. llama.cpp can keep attention and shared weights on
the GPU and only the routed experts in RAM, which is the layout MoE models are designed for.

**How it applies.** The drafting model would run under `llama-server`, which is OpenAI-compatible, with
`--n-cpu-moe N` or `-ot exps=CPU`. It is pinned by the GGUF's sha256, like any model (ADR-014). Ollama stays for the
embedder and the utility model.

**Caveat.** The reported gains (one 8 GB laptop went from about 4 to about 33 tokens/s on a 35B-A3B model) are anecdotal
and on other models; no Gemma figure was found. A runtime change counts as a model change, so it needs a golden run.

**What would show it works.** Prefill, tokens/s and p50/p95 per answer on this GPU, before and after.

## §11. Profile retrieval and keep the embedder resident (ADR-041)

**Sources.** The project's own run record (`golden-20261109T084417Z-after-quote-fixes`) and Ollama's model-residency
settings (`OLLAMA_MAX_LOADED_MODELS`, `keep_alive`).

**Why chosen.** A median of 3.16 s for retrieval is far slower than an indexed search over about 18 900 rows should be.
The suspected cause is that bge-m3 and the drafting model compete for 8 GB of VRAM; a cold load of the drafting model
takes 46.5 s. That is a hypothesis until profiled, and the profile costs nothing.

**How it applies.** Each stage gets timed: query embedding, each arm's SQL, fusion, rerank and expansion. If swapping
dominates, both models are kept resident, or bge-m3 runs on the CPU. This interacts with §10, because two resident models
leave less VRAM for the drafting model.

**What would show it works.** A per-stage profile, then retrieval p50/p95 after the fix.

## §12. Prune whole units from the context (ADR-042)

**Sources.** Chirkova et al., *Provence: efficient and robust context pruning for retrieval-augmented generation*
(ICLR 2025, arXiv 2501.16214), which prunes inside the reranker's forward pass at almost no extra cost; *XProvence*
(arXiv 2601.18886), its multilingual version.

**Why chosen.** Every 1 000 context tokens costs about 2.7 s of prefill here, and rag.md already says fewer good fragments
beat many mediocre ones. Provence shows the pruning itself can be nearly free.

**How it applies.** The project's constraint is stricter than Provence's. It may prune **whole citable units** (fracciones
or párrafos of an expanded article), keeping the unit the query hit and its caput, but never sentences inside a unit:
that would break the verbatim check and change what "the cited fragment" means. The first step is the measurement rag.md
already asks for: top-5 against top-8 expansion.

**Caveat.** A pruned unit cannot be cited, which is the failure of ADR-020 in reverse.

**What would show it works.** Prefill tokens, faithfulness and false refusals, with and without pruning.

## §13. Reuse the cached prompt on the retry (ADR-043)

**Sources.** llama.cpp's KV-cache reuse for an identical prompt prefix, which Ollama inherits.

**Why chosen.** One regeneration is allowed per answer. If the retry prompt starts with the first attempt's prompt byte
for byte, the cached prefill is reused, saving up to about 30 s on every regenerated answer.

**How it applies.** First, check whether today's composer already keeps the prefix identical; this is not verified. If
not, the retry is composed as the same system and user turns, then the original answer, then the feedback, at the end. A
test asserts the byte-for-byte prefix. The fixed prompt layers already come first (core, workflow, persona, session,
fragments).

**Caveat.** Showing the model its failed answer may anchor it to that answer, so the regeneration success rate is measured
before and after.

## §14. Speculative decoding, starting with n-gram drafts (ADR-044)

**Sources.** The llama.cpp speculative-decoding documentation (draft-model, EAGLE-3 and n-gram modes); Li et al.,
*EAGLE-3* (arXiv 2503.01840), up to 6.5× in its paper; Meta, *Efficient Speculative Decoding for Llama at Scale*
(arXiv 2508.08192), 10–30 % at batch size 1.

**Why chosen.** This project's answers copy long statute passages from the context, because quotations must match it
verbatim. The n-gram mode drafts tokens from text already in the prompt, with no second model, which fits that copying.
Speculative decoding does not change the output distribution, so quality is not at stake.

**How it applies.** It depends on §10's runtime (`llama-server`). The n-gram mode comes first; a small draft model of
the same family second; EAGLE-3 only if a checkpoint exists for the drafting model.

**Caveat.** Gains at batch size 1 on consumer hardware are uncertain: Meta's production figure is far below the paper's.

## §15. Half-precision vectors (ADR-045)

**Sources.** pgvector 0.7 `halfvec`, with AWS and Neon reports of half the storage at similar quality; AWS's write-up on
binary quantization with rescoring for large collections.

**Why chosen.** It is a storage saving that grows with firm uploads, at no expected quality cost. Binary quantization was
considered and left out: it is meant for millions of rows, and the corpus holds about 18 900.

**How it applies.** The column and the HNSW index move to `halfvec(1024)`. This changes the stored representation, so it
is built aside as a new generation (ADR-006), with a retrieval-only golden run. The τ calibration depends on these
scores and is rechecked.

**Caveat.** No latency gain is expected at this size; this is a low priority.

## §16. Adapt the embedder to this corpus's confusions (ADR-046)

**Sources.** Wang et al., *GPL: Generative Pseudo Labeling for Unsupervised Domain Adaptation of Dense Retrieval*
(NAACL 2022, arXiv 2112.07577); *R-GPL: Hard Negative Remining* (arXiv 2501.14434), which improved 13 of 14 BEIR and 9 of
12 LoTTE datasets.

**Why chosen.** The dense signal confused same-numbered articles of different laws (ADR-022), and distractors reach the
top 8 in 16 % of golden cases. GPL needs only the unlabeled corpus, and this corpus already contains its own hardest
negatives.

**How it applies.** The hard negatives are this corpus's known confusions: same-numbered articles of other laws and
states, and repealed versions of the same unit. Training produces a new embedding generation, built aside
(invariant 5).

**Caveat.** The synthetic queries are model-written, so the evaluation must use lawyer-reviewed cases, never more synthetic
ones. Training needs a GPU session.

## §17. A stronger multilingual reranker (ADR-047)

**Sources.** Zhang et al., *Qwen3 Embedding: Advancing Text Embedding and Reranking Through Foundation Models*
(arXiv 2506.05176) and the Qwen3 Embedding blog: Qwen3-Reranker-0.6B scores 66.36 on multilingual retrieval (MMTEB-R)
against 58.36 for bge-reranker-v2-m3; Apache-2.0.

**Why chosen.** The last reranker tried was discarded on measurement (AUC 0.892 against 0.934, 16 s per query on CPU,
near-zero scores for English over Spanish). A newer, multilingual model of the same size is the natural next candidate.

**How it applies.** It is measured exactly as the last one was: AUC on the probe sets, false-refusal cost, cross-lingual
scores and CPU latency. Qwen3-Embedding-0.6B as the embedder comes only if the reranker shows the first stage is the
limit, because it is a new embedding generation.

**Caveat.** The vendor's figures are its own runs on top of its own first-stage retriever. A reranker changes the score
scale, so τ must be recalibrated (ADR-036).

## §18. Tuned convex fusion instead of fixed RRF (ADR-048)

**Sources.** Bruch, Gai and Ingber, *An Analysis of Fusion Functions for Hybrid Retrieval* (ACM, 2023,
arXiv 2210.11934; DOI 10.1145/3596512).

**Why chosen.** The paper finds a convex combination of normalised scores beats RRF in and out of domain, needs only a
small sample to tune its one weight, and shows that a tuned RRF generalises poorly. It is a one-parameter change.

**How it applies.** One weight, tuned on the probe sets and validated on the golden set, so it is not fitted to the test.
This changes a project rule: rag.md says fusion must depend only on ranks, never on raw scores. Determinism (stable
tie-breaks on `cite_id`) must still hold.

**What would show it works.** Retrieval-only runs per category, RRF against convex.

## §19. Deterministic contextualisation before model-written context (ADR-049)

**Sources.** Anthropic, *Introducing Contextual Retrieval* (2024): chunk-specific context prepended before embedding and
lexical indexing; retrieval failures down about 35 % with embeddings alone and about 49 % with BM25 as well.

**Why chosen.** This project already prepends a breadcrumb, a structural version of the same idea. Two deterministic gaps
are cheaper to close than generating context with a model:
- A fracción is embedded without its article's lead-in sentence ("Son causas de rescisión…:"). rag.md lists this
  "caput glue" as pending.
- Some chapter titles were dropped at ingestion, for example "CAPITULO IV" with no name (found on 2026-11-26).

**How it applies.** The lead-in is glued and the full chapter titles are restored in the **embedded** text; the stored
`text` stays an exact slice of the source (rag.md). This also makes the regime tags of ADR-031 reliable. Model-written
context (hours of local inference for about 18 900 chunks, and generated text inside the index) comes only if this is not
enough.

**Caveat.** It is a chunker change: a new `chunker_version`, a generation built aside (ADR-006), and golden runs before
and after (invariant 2).

## Suggested order for this round

1. §11 profile retrieval and §13 check the retry prefix: measurements only, possibly the largest wins.
2. §10 expert offload, measured on this GPU.
3. §19 deterministic contextualisation, which also serves ADR-031.
4. §18 convex fusion and §17 the Qwen3 reranker, each with retrieval-only golden runs.
5. §12 pruning and §16 embedder adaptation, the most expensive.
6. §14 speculative decoding after §10, and §15 half precision at any time.

## References (second round)

- Anthropic (2024). Introducing Contextual Retrieval. anthropic.com/news/contextual-retrieval.
- Bruch, S., Gai, S., and Ingber, A. (2023). An Analysis of Fusion Functions for Hybrid Retrieval. ACM;
  arXiv:2210.11934; DOI 10.1145/3596512.
- Chirkova, N., et al. (2025). Provence: efficient and robust context pruning for retrieval-augmented generation.
  ICLR 2025. arXiv:2501.16214.
- XProvence: multilingual context pruning for RAG (2026). arXiv:2601.18886.
- Li, Y., et al. (2025). EAGLE-3: Scaling up Inference Acceleration of Large Language Models via Training-Time Test.
  arXiv:2503.01840.
- Meta (2025). Efficient Speculative Decoding for Llama at Scale: Challenges and Solutions. arXiv:2508.08192.
- llama.cpp. Speculative decoding documentation (`docs/speculative.md`) and the MoE offload guide (Hugging Face blog,
  "Performant local mixture-of-experts CPU inference with GPU acceleration in llama.cpp").
- Wang, K., Thakur, N., Reimers, N., and Gurevych, I. (2022). GPL: Generative Pseudo Labeling for Unsupervised Domain
  Adaptation of Dense Retrieval. NAACL 2022. arXiv:2112.07577.
- R-GPL: Remining Hard Negatives for Generative Pseudo Labeled Domain Adaptation (2025). arXiv:2501.14434.
- Zhang, Y., et al. (2025). Qwen3 Embedding: Advancing Text Embedding and Reranking Through Foundation Models.
  arXiv:2506.05176.
- pgvector `halfvec` and binary quantization: AWS Database Blog (binary quantization on Aurora PostgreSQL) and Neon
  ("Don't use vector, use halfvec").

Community benchmarks of MoE offload are single-author reports on other models and hardware; they motivate a measurement,
not a claim. Author lists marked "et al." and the XProvence and R-GPL entries were taken from abstracts and search results;
check them before quoting this document elsewhere.

# Third round: the law as of a date (2026-11-26)

## §20. Point-in-time retrieval with bitemporal versions (ADR-050)

**Sources.**
- Cymbler, Guez and Fabre, *Temporal Misgrounding in Legal RAG: A Versioned-Corpus Benchmark for French Tax Law*
  (ICML 2026, arXiv 2608.09393). They built a versioned corpus of 32 436 article versions of the French tax code
  (1938–2031) and found that RAG over a corpus holding only current text retrieved the date-applicable version 0 % of
  the time. They name this failure *temporal misgrounding*: citing the version in force today when the applicable one is
  earlier or later.
- *Asking For An Old Friend: Diagnosing and Mitigating Temporal Failure Modes in LLM-based Statutory Question Answering*
  (arXiv 2605.23497), which studies as-of filtering and finds that models often fail to override recent parametric
  knowledge when the facts call for a superseded provision.
- de Martim, *An Ontology-Driven Graph RAG for Legal Norms* (arXiv 2505.00039) and *Beyond Probabilistic Similarity*
  (arXiv 2606.09724). Both separate an abstract norm from its dated versions and argue for bitemporal modelling: valid
  time (when the text was in force) kept apart from transaction time (when the system learned of it).

**Why chosen.** Every legal question is about the law on the date of the facts. The project already versions laws and
never deletes old text (invariant 3), so most of the machinery exists. What is missing is the date logic, and the
current behaviour has a measurable defect.

**How it applies.** Checked on 2026-11-26:

| Finding | Detail |
|---|---|
| Version start dates are unknown | 8 of the 9 stored public documents have `valid_from` empty |
| The two kinds of date are mixed | Retiring a version sets `valid_to = current_date` (the detection day); the new version's `valid_from` is the publication date |
| Two versions claim the same days | CPF: the old version shows in force until 2026-11-25, the new one from 2026-11-13 |
| Publication is not entry into force | The decree's transitorios decide when a reform takes effect |
| The E2E case cannot show its version applied | The dismissal happened on 2026-10-03; the LFT was loaded on 2026-11-07 |
| Historical mode is all-or-nothing | `include_historical` returns every version, not the one in force on a given date |

The proposal (option B, with D):
- **Two kinds of date.** Valid time (`valid_from`/`valid_to`, from the entry into force, or explicitly "unknown") is kept
  apart from transaction time (`ingested_at` and a new `retired_at`).
- **Retiring a version** sets the old `valid_to` from the new version's `valid_from`, never from the detection day.
- **An as-of date on the session**, from a date picker on the case sheet. It is validated, never inferred from free
  text, like jurisdiction. It is applied inside `build_jurisdiction_predicate()` as `valid_from <= D < valid_to`.
- **Answers state the as-of date.** When no loaded version covers it, or its dates are unknown, the system refuses with a
  fixed reason ("no tenemos la versión vigente al …") instead of answering from today's text.
- **A temporal golden track** in the style of FiscalQA Pro (option D): questions whose correct version is not the current
  one, scored on whether the right version was retrieved and cited, with the repealed and the future versions as
  distractors.
- **Afterwards (option C):** backfill earlier versions of each loaded law from its official reform history, one law at a
  time, under ADR-017's rules.

**Caveats.**
- Entry-into-force dates come from reading each decree's transitorios, which a person must check.
- Until earlier versions are backfilled, most as-of dates before November 2026 will be refused. That is honest, but it
  limits usefulness.
- The stored CPF overlap needs an explicit, reviewed data migration that corrects dates and deletes nothing
  (invariant 3).
- The predicate's property tests and the isolation test extend to dates.

**What would show it works.** No two versions of a law share a day of validity (a database constraint or check). Version
correctness on the temporal golden track. A refusal, not a current-text answer, for a date no loaded version covers.

## References (third round)

- *Asking For An Old Friend: Diagnosing and Mitigating Temporal Failure Modes in LLM-based Statutory Question Answering*
  (2026). arXiv:2605.23497.
- Cymbler, R., Guez, D., and Fabre, L. (2026). Temporal Misgrounding in Legal RAG: A Versioned-Corpus Benchmark for
  French Tax Law. ICML 2026. arXiv:2608.09393.
- de Martim, H. (2025, 2026). arXiv:2505.00039 and arXiv:2606.09724 (see the first round).

The benchmark's headline numbers differ between the arXiv and ICML versions of the paper (209 against 35 temporal
questions); the 0 % retrieval of the date-applicable version is the figure both report. The claims attributed to
arXiv:2605.23497 come from search summaries, not the full paper.
