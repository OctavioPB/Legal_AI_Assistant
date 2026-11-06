Onboard a new jurisdiction (state or municipality): 1) add catalog row(s) to `jurisdictions`
via migration; 2) extend normalizer grammar if the source formatting differs
(app/ingestion/grammars/); 3) add ≥2 golden cases: one answerable, one jurisdiction_isolation
pairing it against a sibling; 4) run property tests + golden suite; 5) document quirks in
.claude/rules/jurisdictions.md.
