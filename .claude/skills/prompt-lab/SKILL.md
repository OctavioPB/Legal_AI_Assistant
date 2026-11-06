---
name: prompt-lab
description: Iterate on prompt templates (core, workflow, persona, session layers) safely — create new versions, A/B against the golden dataset, and promote via manifest. Use for any prompt wording change, new persona, or new workflow.
---

# Prompt Lab

## Workflow (never edit a live version in place)
1. Copy `app/prompts/{layer}/{name}@{vN}.j2` → `{name}@{vN+1}.j2`; edit the copy.
2. Local sandbox: `uv run python -m evals.prompt_sandbox --template {layer}/{name}@{vN+1} \
   --case gd-0001` renders the fully composed prompt (all 5 layers) with a golden case's
   context — inspect exactly what the model sees.
3. Eval: `uv run python -m evals.run --suite golden --override-template {layer}/{name}@{vN+1}`.
4. Compare against baseline run; attach the diff to the PR.
5. Promote by pinning the version in `app/prompts/manifest.yaml`.

## Guardrails
- Layer 1 (anti-hallucination core) changes require ALL categories green, not just the target
  category, plus explicit review — it is shared by every workflow/persona.
- New persona = new layer-3 file + at least 3 new golden cases exercising its domain vocabulary
  and its soft-boost behavior (a labor persona must still retrieve Código Civil when needed).
- Bilingual check: every template change runs the suite in both es-MX and en-US sessions.
- Small/cheap aliases: keep instructions short; examples use denylisted fake cite ids; never ask
  for "an extra detail/example" or for refusals "in your own words" (see `rules/prompts.md`).
- Sandbox check with hostile fragments: braces, `%`, `<`, and a literal `[cite: X]` inside a
  fragment must render safely and must not be accepted by the validator as a real citation.
- Persona/register edits change tone only; the golden diff must show no change in citation
  precision, refusal accuracy or leakage.
- Never remove or weaken: the <insuficiente> block contract, the [cite:] format, the
  fragment-as-data instruction. These are load-bearing for the validator and the UI.
