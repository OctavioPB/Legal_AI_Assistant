# Jurisdiction Rules

- Source of truth: `jurisdictions` table (closed catalog). UI selectors and SessionContext
  validate against it. Free-text state/city anywhere in a query path is a bug.
- Stacking semantics: municipal selection ⇒ include its state + federal. State ⇒ + federal.
  NEVER include sibling states/municipalities. Federal-only sessions see federal only.
- All predicate logic lives in `app/rag/jurisdiction.py::build_jurisdiction_predicate()`.
  Property tests in `tests/test_jurisdiction_predicate.py` enumerate: (a) no sibling leakage,
  (b) stacking correctness, (c) US/MX symmetry, (d) is_current default.
- Cross-jurisdiction questions: do not silently widen scope. The assistant tells the user to
  change the jurisdiction selector. Widening is a user action, never an inference.
- Query rewriting, translation and follow-up planning (ADR-009) operate on text only. They never
  produce, change or infer `state_code`/`municipality`/`level`; the predicate is built from the
  validated SessionContext alone. Test: a rewrite that names another state leaves the predicate
  untouched.
- Eval distractors for isolation pair same-numbered articles across states (and the repealed
  version of the same article); a case without them measures nothing.
- Temporal queries ("qué decía antes de la reforma") set `include_historical=true` explicitly;
  responses must carry vigencia warnings.
