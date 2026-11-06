# Style & Conventions

- Python 3.12, ruff (lint+format; rule set `E,F,I,B,UP,SIM,RUF`), `mypy --strict` over `app/`,
  full type hints, pydantic v2 models at all boundaries; domain objects are frozen.
- Test architecture: one shared `tests/fakes.py` (deterministic `FakeEmbedder` — hashed
  bag-of-words, identical text → identical vector —, `FakeReranker`, `FakeLLM`, `WordCounter`);
  a `settings` fixture that starts from the real defaults and switches off the expensive pieces
  (reranker, planner, NLI judge) which individual tests turn on with fakes. Never call a real
  provider, switch or destructive operation from a test.
- SQL: explicit column lists, no SELECT *; migrations reviewed for RLS on every new table.
- Naming: es domain terms kept in Spanish where they are legal terms of art (fraccion, bando,
  jurisprudencia); infra/code identifiers in English.
- i18n: UI strings via next-intl catalogs (es-MX, en-US). No hardcoded user-facing strings.
- Tests: pytest; every bugfix ships with a regression test; retrieval changes ship with an
  eval delta note in the PR description (before/after numbers, per category, noise stated).
- Unit tests run with sockets disabled (`pytest-socket`); LLM, embedder and reranker are fakes.
  Only an explicitly marked `@pytest.mark.live` suite may reach providers, never in default CI.
  The eval harness and review-sheet generator are code and are tested like code.
- Commits: conventional commits; prompt changes use `prompt(scope): ...` and link eval run.
- Config over constants: thresholds, k values, budgets, aliases live in typed settings (see
  architecture.md); a literal in code needs a reason in a comment.
- i18n is enforced by tests, not goodwill: a CI test fails on (a) a key present in one of
  `es-MX`/`en-US` and missing in the other, (b) a hardcoded accented/user-facing literal outside
  the catalog. Server-originated messages (refusal reasons, validator verdicts) are keys, not text.
- Frontend safety: statute text, fragments and model output render as text (never
  `dangerouslySetInnerHTML`); no third-party fonts/CDNs/trackers in the app shell (privileged
  context + referrer leakage) — assets are self-hosted. A test fails if a static asset references
  an external URL.
- Records of decisions: non-trivial choices go to the ADR log (`decision-record` skill).

## UI/Frontend
- Todo desarrollo de UI obedece `Brand.md` (raíz). PRs de UI declaran conformidad;
  vacíos en Brand.md se resuelven extendiendo Brand.md, nunca improvisando en el componente.
