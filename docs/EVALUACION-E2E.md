# End-to-end evaluation with lawyer review

The golden suite measures separate pieces (retrieval, citations, refusals) with short cases. An end-to-end (E2E)
evaluation walks through **the whole application on a matter**, as a lawyer would, and lets **a real
lawyer** judge each output. The grades that matter here are legal accuracy and the fidelity of each
citation; the system reports only what it verified itself.

Tools: `evals/e2e/run_case.py` (runs the case against the API) and `evals/e2e/review.py` (builds the review
packet). How to repeat it: `evals/e2e/README.md`.

> The case itself is written in Spanish, because the law, the interface and the lawyers are Mexican. Legal terms
> of art, tool names and quotations from the statute are kept in Spanish in this document.

## Case E2E-LAB-01 · Dismissal of a pregnant worker

> **Fictional case.** Written by the technical team with the help of AI (`authored_by: ai-draft`, `reviewed_by: null`).
> The "expected points" are a draft: they count as a reference only once a lawyer approves or corrects them.

**Martínez Solís vs. Distribuidora Ficticia del Norte, S.A. de C.V.** We represent the worker. Billing assistant
since 1 March 2019, daily wage of $650.00, a real working week of 54 hours against 45 agreed. Pregnant since
August 2026, she reported it with an IMSS certificate. On 3 October 2026 she was dismissed verbally for alleged
alteration of invoices, without written notice. She was not paid vacation, vacation premium or proportional
year-end bonus (aguinaldo). Applicable law: LFT and CPEUM 123-A, versions loaded as of 17-11-2026.

Material: `evals/e2e/laboral_01/` (`caso.md`, `guion.json`, `contrato.txt`, `escrito.txt`).

### What was run

| Tool | What was asked | What it tests |
|---|---|---|
| Matter and documents | A matter with notes; an individual contract uploaded to the matter (11 citable parts) | The firm's documents (ADR-025) |
| Conversar C1–C6 | Termination for lack of probity and notice; deadline to sue; protection for pregnancy; vacation after the 2023 reform; pre-trial conciliation and its exception; what the contract says | Retrieval, citations, coverage |
| Investigación I1 | Benefits and compensation for unjustified dismissal | A long file with many provisions |
| Argumentar A1, A2 | Strengthen our argument; the opposing party's attack | Strategic-reasoning marks |
| Verificar | An extract of a complaint with 9 references, some wrong on purpose | Existence, validity, verbatim quotation, no model |
| Key-points sheet | A one-page sheet of the matter | ADR-028 |

### Results of the run

Run `evals/e2e/runs/E2E-LAB-01-20261117T225129Z` · 17-11-2026 · local model `gemma4:26b-a4b-it-qat@2dd70431afed`
· 17 minutes. The matter was left in the demo so it can be walked through on screen.

| Step | Result | Seconds | Attempts | Sources cited |
|---|---|---|---|---|
| C1 termination and notice | Answered | 104 | 2 | LFT 51; 47-II; 47-XV |
| C2 deadline to sue | Answered | 76 | 1 | LFT 48; 519; 47-XV |
| C3 pregnancy | Answered | 79 | 1 | LFT 133-XV; 283 Ter; CPEUM 123-A-V |
| C4 vacation | Answered | 78 | 1 | LFT 76; 79; contract, clause SÉPTIMA |
| C5 conciliation | Answered | 52 | 1 | LFT 684-B; 685 Ter |
| C6 the contract | Refused (threshold) | 6 | 0 | — |
| I1 benefits | Answered | 67 | 1 | LFT 436; 162; 87; 79; 50 |
| A1 strengthen | Answered | 108 | 2 | LFT 47; 341; 133-XV |
| A2 attack | Answered | 108 | 2 | LFT 47; 47-II; 47-XV |
| Sheet H1 | Refused (validator) | 92 | 2 | — |
| Sheet H2, regenerated | Refused (validator) | 55 | 2 | — |

**Verifier** (9 references):

| Reference in the brief | Expected | Result |
|---|---|---|
| LFT 47, quotation about the lack of notice | In force; quotation matches | In force; matches |
| LFT 48, quotation about reinstatement or compensation | In force; quotation does not match* | In force; does not match |
| LFT 76, quotation with «seis días» (pre-2023 text) | In force; quotation does not match | In force; does not match |
| LFT 162 and 68 | In force | In force |
| LFT 133-XV and CPEUM 123-A-XXII | In force | In force |
| Ley del Seguro Social, art. 15 | Outside the loaded corpus | Outside the loaded corpus |
| LFT 47 Bis 9 | Does not exist | Does not exist (reported as «art. 47BIS») |

\* The original expectation said "matches". See finding 2.

### Findings

1. **The question about the contract was not answered.** The best score was 0.571 against a threshold of 0.58, and
   the contract's clauses scored 0.546 and 0.541. The threshold was calibrated with statute text, not with
   contract prose (a pending item already noted in ADR-025). In C4 the contract was cited, because the question
   also touched the law. Next step: a probe set with firm documents and a recalibrated threshold.
2. **The verifier found an error of ours.** We copied the quotation of art. 48 in the brief from a text prior
   to the 2019 reform. The current text says «ante la Autoridad Conciliadora, o ante el Tribunal si no existe arreglo
   conciliatorio». The system flagged it as not matching. We corrected the expectation in `guion.json` after reading
   the current text, and it is recorded that way.
3. **The key-points sheet was not delivered in this matter.** There were 4 attempts, all rejected by the validator:
   the model cited units that were not shown to it (fracciones of art. 162, art. 436), with or without a version
   suffix. The validator did the right thing. In a small matter the sheet is delivered (2 of 3, PLAN 17-11-2026), but
   with 19 sources and 3 conversations it is not. Next step: `hoja_clave@v2`, measured before and after.
4. **Citations that exist but do not apply.** See the technical reading below.

### Prior technical reading (non-binding)

> **Do not show to the reviewing lawyer before they grade**: the review packet leaves it out so as not to bias them.
> It was written by the technical team, not by a lawyer.

Several answers cite real articles that do not apply to the case. All of them passed the validator, which proves
that the citation exists and that the cited text is in the source, not that the rule is the applicable one:

| Step | What is seen | What would be expected |
|---|---|---|
| C1 | Uses art. 51 (grounds on which **the worker** terminates) as a requirement for the employer to terminate | Art. 47-II and the final paragraphs of 47 |
| C2 | Gives the two-year period of art. 519 (enforcement of judgments) and does not mention the two months | Art. 518: two months from the day after the separation |
| C3 | Applies art. 283 Ter, which is for agricultural workers | Arts. 133-XV and 170 |
| I1 | Bases the three-month compensation on art. 436 (collective termination) | Arts. 48 and 50; CPEUM 123-A-XXII |
| A1 | Cites art. 341, which is for domestic work | Arts. 47, 133-XV and 784 |

Also: in C1 and in the Argumentar rounds, the notice of termination is cited to «fracc. XV». The final paragraphs
of art. 47 ended up inside the block of that fracción when the law was loaded; it is a chunker defect already noted in
the PLAN.

It is the limitation of §12 of the blueprint («a valid citation is not a faithful paraphrase»), now with concrete
cases. If the lawyer's review confirms it, each case becomes a golden case that measures it.

## Review by a lawyer

**Status: pending.** Nobody has graded it yet.

- **Review page:** https://claude.ai/artifact/R2kVEQhyDDRvBmhk1jupFR (private; shared from its menu).
  Each answer appears next to the full text of each cited unit and of its article. The lawyer grades:
  - per source: does it say what the answer attributes to it? (yes / partly / no / don't know);
  - per answer: legal accuracy, fidelity, completeness and usefulness (1–5), and whether it contains an error that could
    harm the client;
  - per expected point: whether the system covered it and whether the point is correct;
  - the verifier, per reference;
  - an overall assessment.

  Grades are saved automatically, one review per person, in the artifact's `reviews` collection.
- **Printable version:** `evals/e2e/runs/E2E-LAB-01-20261117T225129Z-revision.md`, with the same boxes.
- **Access:** if the lawyer is from the firm, share as Contributor; if external, invite them by email as
  Editor, with no public link. With read-only access the page saves in the browser and offers to copy the
  grades so they can be sent by email.

**What is done with the review:**

1. Expected points that are approved or corrected move to `evals/golden/` with `reviewed_by: <lawyer>`. They will be the
   first golden cases reviewed by a lawyer (today there are 0).
2. Each "no" on fidelity is recorded with its citation and enters the fidelity measurement.
3. The results are summarised here and in `docs/PLAN.md`, with the date and the run id.

## Limits of this evaluation

- **It is one case and one run.** The local model varies between runs (PLAN 17-11-2026: the same sheet came out
  differently in consecutive runs), so none of this is a rate.
- **The case, the questions and the expectations were written by the technical team with AI.** They may contain errors; we
  already found one (finding 2).
- **Federal labor law only, and only the local model** (Gemma 4 26B). The Claude profile was not run.
- **The answers will be graded in context, not against a single correct answer:** two different answers
  can be equally good.
