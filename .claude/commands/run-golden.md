Run the golden eval suite and summarize: `uv run python -m evals.run --suite golden --tag <before|after>-<change>`.
Report per-category metrics vs release gates, list failing case ids with one-line diagnosis
each (classify: chunking · vocabulary · ambiguity · jurisdiction/vigencia · wrong eval), and
state clearly whether the change is mergeable. If jurisdiction_leakage > 0, mark sev-1 and stop.
If a previous run exists, `--compare` it: show per-category deltas and the cases that flipped
hit↔miss. A difference of 1–2 cases is noise — say so; a regression > 2 points in any category
blocks. Report `ai-draft` cases separately (they do not count toward gates). Run both es-MX and
en-US sessions.
