---
name: decision-record
description: How to propose, write and update architecture decisions (ADR) in the ADR log (.claude/rules/architecture.md). Use ALWAYS when choosing between technical alternatives (model, library, schema, threshold with impact), when wanting to revert or change something already decided, when closing an open decision, or when the user asks why something was done a certain way.
---

# Decision record

The ADR log is the memory of the why, so a future session does not reopen a settled discussion
without knowing what was weighed. Location: `.claude/rules/architecture.md` (append-only).

## When an ADR is needed
- Choosing between two or more reasonable alternatives (model, library, schema, algorithm).
- Fixing a value with architectural impact (τ, embedding dimension, chunk thresholds, context budget).
- Accepting a known limitation.
- Changing or reverting an accepted decision — ALWAYS a new ADR that supersedes the old one.
Not needed for implementation details reversible at no cost (names, internal module layout).

## Format
```markdown
- ADR-NNN (State, YYYY-MM-DD): Title that states the decision, not the topic.
  Context: the problem and what forces the decision now.
  Options: each serious alternative with main advantage and cost (include "do nothing").
  Decision: what was chosen, in one or two sentences.
  Consequences: (+) gained, (−) paid; the condition (tripwire) that would force revisiting.
  Evidence: run ids / numbers when measured.   Pending: the exact datum that closes it.
```
States: Proposed · Accepted · Superseded by ADR-NNN · Rejected.

## Rules
1. Sequential numbering, never reused.
2. Measured decisions cite numbers and the `evals/runs/` id.
3. Superseding: the old ADR changes only its state; the new one explains what changed in the
   evidence. Never edit an accepted ADR's content.
4. Missing information → stays Proposed with a "Pending" line naming the datum.
5. Non-trivial decisions are proposed to the user BEFORE implementing: options, cost, a justified
   recommendation; wait for their choice. Trivial choices inside an accepted ADR need no consult.
6. Every tripwire in an ADR ("revisit at >N") is registered as an ops alert (PLAN WS7.T05).
7. If the code contradicts an accepted ADR, point it out; do not silently fix either side.
8. Negative results are decisions too: an idea that was measured and rejected gets an ADR
   (Rejected) so nobody re-proposes it blind (see ADR-008, ADR-009).
