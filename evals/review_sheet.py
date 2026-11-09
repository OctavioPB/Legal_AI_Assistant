"""Lawyer review sheet for a golden run (WS5.T07).

    uv run python -m evals.review_sheet --run <run_id>      -> evals/runs/<run_id>/review.md

Faithfulness is graded by a lawyer, here, never computed: for every answered case the sheet lists each
sentence of the answer next to the FULL text of every fragment that sentence cites (never truncated:
Library-RAG once cut fragments at 400 characters and the grading became unverifiable), plus blank
grade boxes. Refused cases list why, so a wrong refusal can be graded too.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from evals.golden import CaseResult, read_run

RUNS = Path(__file__).resolve().parents[1] / "evals" / "runs"
_CITE = re.compile(r"\[\s*cite\s*:\s*([^\]]+)\]", re.IGNORECASE)
_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-ZÁÉÍÓÚÑ¿¡\[*])")
GRADES = "[ ] fiel  [ ] parcial  [ ] infiel  [ ] no es afirmación jurídica"


def sentences(answer: str) -> list[str]:
    out: list[str] = []
    for line in answer.splitlines():
        line = line.strip()
        if line:
            out.extend(s.strip() for s in _SPLIT.split(line) if s.strip())
    return out


def cited_in(sentence: str) -> list[str]:
    ids: list[str] = []
    for m in _CITE.finditer(sentence):
        for part in re.split(r"[;,]", m.group(1)):
            p = part.strip()
            if p and p not in ids:
                ids.append(p)
    return ids


def case_section(r: CaseResult) -> str:
    lines = [
        f"## {r.id} · {r.category} · se esperaba: {r.expected}",
        "",
        f"**Consulta:** {r.query}",
        "",
    ]
    if r.outcome != "answered" or not r.answer:
        lines += [f"**Resultado:** {r.outcome or 'sin ejecutar'} ({r.refusal_reason or r.error or '-'})", "",
                  "¿El rechazo es correcto? [ ] sí  [ ] no — notas: ______", ""]  # fmt: skip
        return "\n".join(lines)
    lines += [f"**Resultado:** respondida · regeneraciones: {r.regenerations}", ""]
    for n, s in enumerate(sentences(r.answer), 1):
        lines += [f"### Oración {n}", "", f"> {s}", ""]
        ids = cited_in(s)
        if not ids:
            lines += [
                "_Sin cita (marcada como análisis/razonamiento o encabezado)._",
                "",
                GRADES,
                "",
            ]
            continue
        for cid in ids:
            texts = r.cited_texts.get(cid) or []
            lines.append(f"**Fuente `{cid}`**")
            lines.append("")
            if not texts:
                lines.append("_(texto no disponible en la corrida)_")
            for t in texts:  # the FULL fragment: never truncated
                lines.append("```text")
                lines.append(t)
                lines.append("```")
            lines.append("")
        lines += [GRADES, "", "Notas: ______", ""]
    return "\n".join(lines)


def build(report: dict[str, object]) -> str:
    cases = [CaseResult.model_validate(c) for c in report["cases"]]  # type: ignore[union-attr]
    head = [
        f"# Hoja de revisión · {report['run_id']}", "",
        f"Commit `{report.get('commit')}`{' (con cambios sin commit)' if report.get('dirty') else ''} · "
        f"modo {report.get('mode')} · {len(cases)} casos.", "",
        "Para cada oración: ¿lo que afirma está en el fragmento citado, sin invertir ni ampliar su sentido?",
        "La fidelidad se califica aquí; el sistema no la calcula.", "",
    ]  # fmt: skip
    return "\n".join(head + [case_section(r) for r in cases]) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="evals.review_sheet")
    ap.add_argument("--run", required=True)
    a = ap.parse_args(argv)
    path = Path(a.run) if Path(a.run).exists() else RUNS / a.run / "report.json"
    report = read_run(path)
    out = path.with_name("review.md")
    out.write_text(build(report), encoding="utf-8")
    sys.stdout.write(f"review sheet: {out}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
