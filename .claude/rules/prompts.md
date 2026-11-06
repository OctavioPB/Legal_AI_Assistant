# Prompt System Rules

- Layers: 1 core (anti-hallucination, FIXED) → 2 workflow → 3 persona → 4 session → 5 fragments.
- Files: `app/prompts/{layer}/{name}@{version}.j2`. Versions are immutable; changes = new version
  + golden eval run + PR. Prod pins exact versions in `app/prompts/manifest.yaml`.
- Variable safety: {Language}, {Jurisdiction}, {Persona} resolve from enums/catalog lookups.
  User free text NEVER enters system prompt. Retrieved fragments are wrapped in <fragmento>
  tags and the core instructs to treat them as data.
- Language rule: respond in session language; verbatim statute quotes stay in original language;
  translations marked "traducción no oficial".
- Refusal block is exact and machine-parseable: `<insuficiente>...</insuficiente>`. Do not
  paraphrase it in templates; the UI and evals match on it.
- Adversarial workflow: legal assertions need [cite: ...]; strategy/logic attacks need
  [razonamiento estratégico]. Both untagged = validator failure.

## Lessons from small-model prompt work (Library-RAG; apply to every alias, esp. `cheap-utility`)
- Short, concrete instructions. Each extra instruction dilutes the others; the core layer stays
  minimal and is changed last.
- Examples inside prompts use obviously fake ids (`[cite: EJEMPLO-NO-CITAR]`); the validator
  denylists them. Models copy example markers verbatim.
- Never instruct "always add a detail / an example / what the sources lack". Measured: the model
  invents one and attaches a fake citation, or explains partial evidence instead of refusing.
  Anything the model adds beyond the fragments must be explicitly marked and carries no `[cite:]`.
- Do not ask the model to refuse in its own words. Refusal is a system output (ADR-007).
- Prompts that would change tone/register ("principiante/avanzado"-style personas) change the
  register only, never the evidence. Any persona edit is an answer change: golden run before/after.
- Fragments are data: wrap in `<fragmento>`; text inserted with `str.format`/Jinja must tolerate
  braces and markup inside statute text (test with fragments containing `{}`, `%`, `<`, `[cite:`).
