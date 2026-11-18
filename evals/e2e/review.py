"""Build the lawyer's review packet from an end-to-end run (`evals/e2e/run_case.py`).

    uv run python -m evals.e2e.review evals/e2e/runs/E2E-LAB-01-<UTC>.json

Writes next to the run:
  * `<run>-revision.md`   the whole packet as text: every answer next to the FULL text of each cited
                          unit and its article (never truncated), the automatic checks, the expected
                          points (AI draft, for the lawyer to correct) and the grading rubric;
  * `<run>-review.json`   the same content as data;
  * `<run>-review.html`   the interactive review page: `review_page.html` with that data inserted,
                          the file that is published for the lawyer.

Nothing here judges legal correctness: the automatic checks are what the system verified
(provenance, quotations, coverage); faithfulness and usefulness are the lawyer's grades.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

PAGE = Path(__file__).with_name("review_page.html")
_CITE = re.compile(r"\[\s*cite\s*:\s*([^\]\[]+)\]", re.IGNORECASE)


def _auto(step: dict[str, Any]) -> dict[str, Any]:
    """What the system itself checked, from the stored trace."""
    tr = step.get("trace") or {}
    atts = tr.get("attempts") or []
    last = atts[-1]["verdict"] if atts else {}
    return {
        "status": step.get("status"),
        "refusal_reason": step.get("refusal_reason"),
        "seconds": step.get("seconds"),
        "attempts": len(atts),
        "regenerated": len(atts) > 1,
        "first_attempt_problems": _problems(atts[0]["verdict"]) if len(atts) > 1 else [],
        "final_problems": _problems(last) if last else [],
        "analysis_share": last.get("analysis_share"),
        "model": atts[-1]["model"] if atts else None,
        "prompt_versions": tr.get("prompt_versions"),
    }


def _problems(v: dict[str, Any]) -> list[str]:
    out = []
    if v.get("invalid"):
        out.append(f"citas a fuentes no mostradas: {', '.join(v['invalid'])}")
    if v.get("bad_quotes"):
        out.append(f"citas textuales que no coinciden: {len(v['bad_quotes'])}")
    if v.get("uncovered"):
        out.append(f"oraciones sin cita ni marca: {len(v['uncovered'])}")
    if v.get("placeholders"):
        out.append("marcadores de ejemplo copiados")
    if not v.get("cited") and not v.get("refused"):
        out.append("ninguna cita válida")
    return out


def _item(kind: str, step: dict[str, Any], ref: dict[str, Any], title: str) -> dict[str, Any]:
    ev = step.get("evidence") or []
    numbers = {e["cite_id"]: i + 1 for i, e in enumerate(ev)}
    return {
        "id": step.get("id") or ref.get("id"),
        "kind": kind,
        "title": title,
        "question": step.get("question") or step.get("text"),
        "action": step.get("action"),
        "answer": step.get("answer") or step.get("content"),
        "sources": [{**e, "n": numbers[e["cite_id"]]} for e in ev],
        "expected_points": ref.get("expected_points", []),
        "expected_articles": ref.get("expected_articles", []),
        "auto": _auto(step),
    }


def build(run: dict[str, Any]) -> dict[str, Any]:
    g = run["guion"]
    steps = run["steps"]
    items: list[dict[str, Any]] = []
    for s, ref in zip(steps.get("chat", []), g["chat"], strict=False):
        items.append(_item("chat", s, ref, f"Conversar · {ref['id']}"))
    if "research" in steps:
        items.append(_item("research", steps["research"], g["research"], "Investigación · I1"))
    for s in steps.get("case", []):
        label = (
            "reforzar nuestro argumento"
            if s["action"] == "strengthen"
            else "ataque de la contraparte"
        )
        ref = {"id": s["id"], "expected_points": g["case"]["expected_points"]}
        items.append(_item("case", s, ref, f"Argumentar · {s['id']} ({label})"))
    for key, label in (("cheatsheet", "H1"), ("cheatsheet_retry", "H2")):
        sheet = steps.get(key)
        if not sheet:
            continue
        atts = sheet.get("validator") or []
        step = {**sheet, "id": label, "question": "Hoja de puntos clave del asunto", "trace": None}
        it = _item("sheet", step, {"id": label}, f"Hoja de puntos clave · {label}"
                   + (" (regenerada)" if key == "cheatsheet_retry" else ""))  # fmt: skip
        it["auto"].update({
            "attempts": len(atts), "regenerated": len(atts) > 1,
            "first_attempt_problems": _problems(atts[0]) if len(atts) > 1 else [],
            "final_problems": _problems(atts[-1]) if atts else [],
        })  # fmt: skip
        items.append(it)
    verify = steps.get("verify")
    return {
        "case_id": run["case_id"],
        "run_file": run.get("_file"),
        "started_at": run["started_at"],
        "git": run["git"],
        "models": run.get("models"),
        "matter": g["matter"],
        "case": g["case"],
        "authored_by": g["authored_by"],
        "note": g["note"],
        "file": steps.get("file"),
        "items": items,
        "verify": {
            "title": g["verify"]["title"],
            "text": (Path(__file__).parent / run["case_dir"] / g["verify"]["path"]).read_text(
                "utf-8"
            )
            if run.get("case_dir")
            else None,
            "seeded": g["verify"]["seeded"],
            "summary": verify.get("summary") if verify else None,
            "findings": verify.get("findings") if verify else [],
        },
    }


def _md_answer(text: str | None, numbers: dict[str, int]) -> str:
    if not text:
        return "_(sin texto: el sistema rechazó la consulta)_"
    return _CITE.sub(
        lambda m: "[" + ", ".join(str(numbers.get(i.strip(), i.strip()))
                                  for i in re.split(r"[;,]", m.group(1)) if i.strip()) + "]",
        text,
    )  # fmt: skip


RUBRIC = """\
**Califique (1 = muy deficiente · 5 = como lo haría un abogado con experiencia):**

| Criterio | 1 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|
| Exactitud jurídica (lo que afirma es correcto) | ☐ | ☐ | ☐ | ☐ | ☐ |
| Fidelidad a la fuente (cada cita sostiene lo que se le atribuye) | ☐ | ☐ | ☐ | ☐ | ☐ |
| Completitud (cubre lo esencial para el caso) | ☐ | ☐ | ☐ | ☐ | ☐ |
| Utilidad práctica | ☐ | ☐ | ☐ | ☐ | ☐ |

¿Contiene un error que podría perjudicar al cliente? ☐ No ☐ Sí → ¿cuál?

Comentarios y correcciones a los puntos esperados:
"""


HOWTO = [
    "1. Lea la pregunta y la respuesta del sistema. Los números entre corchetes remiten a las "
    "fuentes de abajo.",
    "2. Para cada fuente, compruebe que su texto (completo, sin recortes) dice lo que la respuesta "
    "le atribuye.",
    "3. Lo marcado `[razonamiento estratégico]` o `[análisis propio]` es razonamiento del "
    "asistente, sin respaldo normativo: júzguelo como opinión, no como cita.",
    "4. Un rechazo (`<insuficiente>`) es una salida válida: indique si debió responder.",
    "5. Califique con la rúbrica y corrija los puntos esperados.",
]
POINT_ASK = "¿lo dijo el sistema? ☐ Sí ☐ En parte ☐ No · ¿el punto es correcto? ☐ Sí ☐ No"
TRUST = "¿Confiaría en este sistema como apoyo para preparar este caso? ☐ Sí ☐ Con reservas ☐ No"


def _outcome(a: dict[str, Any]) -> str:
    if a["status"] == "answered":
        return "respondió"
    return "rechazó" + (f" ({a['refusal_reason']})" if a["refusal_reason"] else "")


def markdown(r: dict[str, Any]) -> str:
    m = r["matter"]
    out = [
        f"# Paquete de revisión · {r['case_id']}",
        "",
        "> **CASO FICTICIO** para evaluar el sistema. Los «puntos esperados» son un **borrador del "
        "equipo técnico redactado con IA**: corríjalos o apruébelos; solo con su aprobación sirven "
        "como referencia.",
        "",
        f"Corrida: {r['started_at']} · commit `{(r['git'] or {}).get('commit', '')[:10]}`"
        f"{' (con cambios sin commit)' if (r['git'] or {}).get('dirty') else ''}",
        "",
        "## El caso",
        "",
        f"**{m['title']}** · {m['client_ref']}",
        "",
        m["description"],
        "",
        *[f"- {n}" for n in m["notes"]],
        "",
        "## Cómo revisar",
        "",
        *HOWTO,
        "",
    ]
    for it in r["items"]:
        numbers = {s["cite_id"]: s["n"] for s in it["sources"]}
        a = it["auto"]
        out += [
            f"## {it['title']}",
            "",
            f"**Pregunta:** {it['question']}",
            "",
            f"**Resultado del sistema:** {_outcome(a)} · {a['seconds']} s · "
            f"{a['attempts'] or '—'} intento(s)"
            + (
                f" · el 1.er intento falló: {'; '.join(a['first_attempt_problems'])}"
                if a["regenerated"]
                else ""
            ),
            "",
            "### Respuesta",
            "",
            _md_answer(it["answer"], numbers),
            "",
        ]
        if it["sources"]:
            out += ["### Fuentes citadas (texto completo)", ""]
            for s in it["sources"]:
                out += [
                    f"**[{s['n']}] {s['citation_string']}** · `{s['cite_id']}` · "
                    f"versión {s.get('version_label')}",
                    "",
                    "Texto de la unidad citada:",
                    "",
                    "> " + (s.get("unit_text") or "(no disponible)").replace("\n", "\n> "),
                    "",
                ]
                if s.get("article_text") and s.get("article_text") != s.get("unit_text"):
                    out += [f"Artículo completo ({s.get('article_label')}):", "",
                            "> " + s["article_text"].replace("\n", "\n> "), ""]  # fmt: skip
                out += ["¿Sostiene lo que se le atribuye? ☐ Sí ☐ En parte ☐ No", ""]
        if it["expected_points"]:
            out += ["### Puntos esperados (borrador IA: corríjalos)", ""]
            out += [f"- {p}  → {POINT_ASK}" for p in it["expected_points"]]
            out += [""]
        out += [RUBRIC, "", "---", ""]
    v = r["verify"]
    out += ["## Verificador de escritos", "", f"**{v['title']}**", ""]
    if v.get("text"):
        out += ["```text", v["text"].strip(), "```", ""]
    out += ["| Referencia sembrada | Resultado esperado |", "|---|---|"]
    out += [f"| {s['ref']} | {s['expected']} |" for s in v["seeded"]]
    out += ["", f"**Resumen del sistema:** {json.dumps(v.get('summary'), ensure_ascii=False)}", "",
            "| Referencia | Estado | Cita textual | ¿Correcto? |", "|---|---|---|---|"]  # fmt: skip
    for f in v.get("findings") or []:
        quote = f.get("quote") or "—"
        out.append(f"| {f.get('ref')} | {f.get('status')} | {quote} | ☐ Sí ☐ No |")
    out += ["", "## Valoración general", "", TRUST, "", "Comentarios:", ""]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    path = Path(args[0])
    run = json.loads(path.read_text("utf-8"))
    run["_file"] = path.name
    run.setdefault("case_dir", {"E2E-LAB-01": "laboral_01"}.get(run["case_id"]))
    r = build(run)
    stem = path.with_suffix("")
    Path(f"{stem}-revision.md").write_text(markdown(r), "utf-8")
    data = json.dumps(r, ensure_ascii=False)
    Path(f"{stem}-review.json").write_text(data, "utf-8")
    # the data sits inside a <script>: a "</" in statute or model text must not close it
    page = PAGE.read_text("utf-8").replace("/*REVIEW_DATA*/null", data.replace("</", "<\\/"), 1)
    Path(f"{stem}-review.html").write_text(page, "utf-8")
    sys.stdout.write(
        f"wrote {stem}-revision.md, -review.json, -review.html ({len(r['items'])} items)\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
