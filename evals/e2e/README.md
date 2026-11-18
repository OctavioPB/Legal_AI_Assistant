# End-to-end (E2E) cases for lawyer review

An E2E case walks through the whole application as a lawyer would, with the real models, and produces a packet
for **a real lawyer** to judge each output. It is not the golden suite: here nothing is graded automatically. The system
reports what it verified itself (provenance of citations, verbatim quotations, coverage); legal accuracy,
fidelity to the sources and usefulness are the lawyer's grade.

## Cases

| Id | Folder | Subject | Status |
|---|---|---|---|
| E2E-LAB-01 | `laboral_01/` | Verbal dismissal of a pregnant worker (LFT, CPEUM 123-A) | Run 2026-11-17; awaiting review |

Each case has `caso.md` (facts), `guion.json` (questions, expected points and articles, references seeded in
the brief) and the case's documents. Everything is **fictional** and was written by the technical team with AI
(`authored_by: ai-draft`, `reviewed_by: null`): the «expected points» only count as a reference once a lawyer
approves or corrects them. The case files are in Spanish, like the law they use.

## Running a case

```bash
docker compose up -d postgres redis
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000          # API with the local models (Ollama)
uv run python -m evals.e2e.run_case laboral_01 --credentials .demo-credentials [--keep]
uv run python -m evals.e2e.review evals/e2e/runs/E2E-LAB-01-<UTC>.json
```

`run_case` creates the matter, its notes and its document, asks each question, runs the research file, the
Argumentar rounds, the verifier and the key-points sheet. It saves in `runs/<id>-<UTC>.json` each answer with the attempts and
validator verdicts and the **full text** of each cited unit and of its article. With `--keep` the matter
stays in the application so it can be walked through on screen. With the local model it takes 15 to 25 minutes.

`review` writes next to the run:

- `-revision.md`: the complete packet as text (to print or attach), with grading boxes;
- `-review.html`: the interactive page that is published for the lawyer; their grades are saved automatically, one
  review per person;
- `-review.json`: the same data.

## How it is shared with the lawyer

The page is published as a private artifact. So that the lawyer can **save** their grades, share it
from the Share menu with **Contributor** access (if they are from the firm) or invite them **by email as Editor** (if
they are external, and with no public link). With read-only access the page works, but keeps the grades
only in their browser and offers «Copiar mis calificaciones» so they can be sent by email. The reviews received are
read on the same page (the «Revisiones recibidas» panel, visible to the owner) or with the artifact's data tool
(`reviews` collection).

## What to do with the review

1. The expected points the lawyer approves or corrects become golden cases (`evals/golden/`), with
   `authored_by: ai-draft` and `reviewed_by: <lawyer>`.
2. Each «No» on «¿Esta fuente dice lo que la respuesta le atribuye?» is a fidelity error: it is recorded with its
   citation and used to measure improvement (the validator proves provenance, not fidelity; blueprint §12).
3. The results are noted in `docs/PLAN.md` with the date and the run id.
