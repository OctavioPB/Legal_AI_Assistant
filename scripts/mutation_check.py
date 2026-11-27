"""ADR-038 (A): mutation testing of the citation validator. Every mutant weakens ONE check that the
contract depends on; the validation tests must fail for each. A mutant that survives is a check the
tests do not really protect.

    uv run python scripts/mutation_check.py            # all mutants
    uv run python scripts/mutation_check.py --list     # what each mutant breaks

Each mutant runs in a throwaway copy of `app/` and `tests/` (the working tree is never modified),
with `pytest tests/validation -x -q`. Standard library only; works on Windows (mutmut needs fork).
Exit code 1 if any mutant survives.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
V = "app/validation/validator.py"
L = "app/validation/loop.py"


@dataclass(frozen=True)
class Mutant:
    name: str
    path: str
    old: str
    new: str
    breaks: str


MUTANTS = (
    Mutant("invalid-ids-ignored", V,
           "invalid = [i for i in ids if i not in placeholders and i not in retrieved.ids]",
           "invalid = []", "a citation to an id that was not retrieved passes"),
    Mutant("placeholders-ignored", V,
           "placeholders = [i for i in ids if i.upper() in _PLACEHOLDERS]",
           "placeholders = []", "a copied example marker ([cite: X]) counts as a citation"),
    Mutant("coverage-ignored", V, "        and not uncovered\n", "",
           "an uncited legal sentence is delivered"),
    Mutant("quotes-ignored", V, "        and not bad_quotes\n", "",
           "a non-verbatim quotation is delivered"),
    Mutant("needs-no-citation", V, "        and bool(cited)\n", "",
           "an answer without any valid citation is delivered"),
    Mutant("quote-any-fragment", V,
           "t for c in parse_citations(unit) if c in retrieved.ids for t in retrieved.texts[c]",
           "t for c in retrieved.ids for t in retrieved.texts[c]",
           "a quotation is checked against ANY retrieved fragment, not the one cited with it"),
    Mutant("quote-min-words-off", V, "if len(seg_f.split()) >= min_words and",
           "if len(seg_f.split()) >= min_words + 1000 and",
           "the verbatim check never runs"),
    Mutant("gap-is-refusal", V, "refused = gaps > 0 and not cited", "refused = gaps > 0",
           "a section gap inside a grounded answer refuses the whole answer"),
    Mutant("prose-refusal-ignored", V,
           "prose = (not refused) and (not cited) and any(m in folded for m in _PROSE_REFUSAL)",
           "prose = False", "a refusal in the model's own words is shown as an answer"),
    Mutant("runaway-output-accepted", V, "if len(answer) > cfg.max_answer_chars:", "if False:",
           "an oversized output is validated and delivered"),
    Mutant("no-regeneration", L, "for attempt in range(cfg.max_retries + 1):",
           "for attempt in range(1):", "the one regeneration with feedback never happens"),
    Mutant("model-writes-refusal", L,
           "refusal=_refusal(\"validation.unsupported\", verdicts),",
           "refusal=None,", "a persistent failure ends without the system's refusal block"),
)  # fmt: skip


def run_mutant(m: Mutant, pytest_args: list[str]) -> tuple[bool, float]:
    """True if the tests KILLED the mutant (failed)."""
    with tempfile.TemporaryDirectory(prefix="mut-") as tmp:
        base = Path(tmp)
        for d in ("app", "tests", "evals", "config"):
            if (ROOT / d).exists():
                shutil.copytree(ROOT / d, base / d, ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy2(ROOT / "pyproject.toml", base / "pyproject.toml")
        target = base / m.path
        src = target.read_text(encoding="utf-8")
        if src.count(m.old) != 1:
            raise SystemExit(f"mutant {m.name}: the original text is not found exactly once")
        if m.new != m.old:
            target.write_text(src.replace(m.old, m.new), encoding="utf-8")
        t = time.monotonic()
        res = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/validation", "-x", "-q",
             "-p", "no:cacheprovider", *pytest_args],
            cwd=base, capture_output=True, text=True,
        )  # fmt: skip
        return res.returncode != 0, time.monotonic() - t


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="mutation_check")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--only", action="append", help="run only this mutant (repeatable)")
    a = ap.parse_args(argv)
    if a.list:
        for m in MUTANTS:
            print(f"{m.name:26} {m.breaks}")
        return 0
    chosen = [m for m in MUTANTS if not a.only or m.name in a.only]
    # Control: the UNMUTATED copy must pass, or every "killed" below would only mean "broken copy".
    control = Mutant("control", V, "def validate(", "def validate(", "nothing")
    broken, secs = run_mutant(control, [])
    if broken:
        print(
            "control run FAILED: the copied project does not pass its own tests; nothing measured"
        )
        return 2
    print(f"control  unmutated copy passes          {secs:5.1f}s")
    survivors: list[str] = []
    for m in chosen:
        killed, secs = run_mutant(m, [])
        print(f"{'killed  ' if killed else 'SURVIVED'} {m.name:26} {secs:5.1f}s  {m.breaks}")
        if not killed:
            survivors.append(m.name)
    score = (len(chosen) - len(survivors)) / len(chosen)
    print(f"\nmutation score: {len(chosen) - len(survivors)}/{len(chosen)} = {score:.0%}")
    return 1 if survivors else 0


if __name__ == "__main__":
    raise SystemExit(main())
