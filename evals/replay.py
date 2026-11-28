"""ADR-038 (B): replay real model outputs through the current validator.

A change to the validator must not silently flip a verdict on a real answer the system already
produced. A replay record holds one attempt's raw output, the texts the model was shown for each
citable id, the workflow and the verdict recorded at the time. Replaying it re-validates the output
with today's code and reports every verdict that changed.

    uv run python -m evals.replay extract-e2e evals/e2e/runs/E2E-LAB-01-....json OUT.jsonl
    uv run python -m evals.replay check FILE.jsonl [FILE.jsonl ...]      # exit 1 on any change

Two sources:
* full golden runs write `evals/runs/<run>/replay.jsonl` with the complete retrieved set;
* `extract-e2e` reads an end-to-end run, which stores only the CITED units' texts (`evidence`). It
  keeps only attempts that can be replayed faithfully from that: accepted attempts whose citations
  are all in the evidence, and attempts rejected for ids the model was never shown. The rest are
  skipped and counted, never guessed.

What is compared: `grounded`, and the invalid and placeholder ids. Coverage and quotation findings
are compared as present / absent, because their wording may change without the verdict changing.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from app.validation.types import RetrievedSet
from app.validation.validator import validate

_WORKFLOW_BY_PROMPT = ("conversar", "dossier_builder", "adversarial", "argumentar", "hoja_clave")


def _workflow(prompt_versions: Iterable[str]) -> str | None:
    for v in prompt_versions:
        if v.startswith("workflow/"):
            name = v.split("/", 1)[1].split("@", 1)[0]
            return name if name in _WORKFLOW_BY_PROMPT else None
    return None


def _shape(verdict: Any) -> dict[str, Any]:
    v = verdict if isinstance(verdict, dict) else verdict.model_dump()
    return {
        "grounded": bool(v["grounded"]),
        "invalid": sorted(v.get("invalid") or []),
        "placeholders": sorted(v.get("placeholders") or []),
        "uncovered": bool(v.get("uncovered")),
        "bad_quotes": bool(v.get("bad_quotes")),
    }


def record(
    rid: str, source: str, workflow: str, output: str, texts: dict[str, list[str]], verdict: Any
) -> dict[str, Any]:
    return {"id": rid, "source": source, "workflow": workflow, "output": output,
            "retrieved": texts, "recorded": _shape(verdict)}  # fmt: skip


def extract_e2e(run: dict[str, Any], source: str) -> tuple[list[dict[str, Any]], int]:
    """Replayable attempts of an end-to-end run, and how many were skipped as incomplete."""
    out: list[dict[str, Any]] = []
    skipped = 0
    steps = run.get("steps", {})
    for name in ("chat", "research", "case"):
        items = steps.get(name) or []
        for item in items if isinstance(items, list) else [items]:
            trace = item.get("trace") or {}
            workflow = _workflow(trace.get("prompt_versions") or [])
            if not workflow:
                skipped += len(trace.get("attempts") or [])
                continue
            texts: dict[str, list[str]] = {}
            for e in item.get("evidence") or []:
                bucket = texts.setdefault(e["cite_id"], [])
                for t in (e.get("unit_text"), e.get("article_text")):
                    if t and t not in bucket:
                        bucket.append(t)
            for n, att in enumerate(trace.get("attempts") or [], 1):
                v = att["verdict"]
                shown_ok = set(v.get("cited") or []) <= texts.keys()
                faithful = (v["grounded"] and shown_ok) or (bool(v.get("invalid")) and shown_ok)
                if not faithful:
                    skipped += 1
                    continue
                out.append(record(f"{item['id']}#{n}", source, workflow, att["output"], texts, v))
    return out, skipped


def replay(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every record whose verdict differs today, with the recorded and the current shape."""
    changed: list[dict[str, Any]] = []
    for r in records:
        rs = RetrievedSet.build((cid, ts) for cid, ts in r["retrieved"].items())
        now = _shape(validate(r["output"], rs, workflow=r["workflow"]))
        if now != r["recorded"]:
            changed.append({"id": r["id"], "source": r["source"], "recorded": r["recorded"],
                            "now": now})  # fmt: skip
    return changed


def read(path: Path) -> list[dict[str, Any]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(ln) for ln in lines if ln.strip()]


def write(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
    path.write_text(body, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="evals.replay")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ex = sub.add_parser("extract-e2e")
    ex.add_argument("run", type=Path)
    ex.add_argument("out", type=Path)
    ck = sub.add_parser("check")
    ck.add_argument("files", type=Path, nargs="+")
    a = ap.parse_args(argv)
    if a.cmd == "extract-e2e":
        run = json.loads(a.run.read_text(encoding="utf-8"))
        records, skipped = extract_e2e(run, a.run.name)
        write(a.out, records)
        sys.stdout.write(f"{len(records)} replayable attempts written, {skipped} skipped\n")
        return 0
    records = [r for f in a.files for r in read(f)]
    changed = replay(records)
    for c in changed:
        sys.stdout.write(json.dumps(c, ensure_ascii=False) + "\n")
    sys.stdout.write(f"{len(records)} replayed, {len(changed)} verdicts changed\n")
    return 1 if changed else 0


if __name__ == "__main__":
    raise SystemExit(main())
