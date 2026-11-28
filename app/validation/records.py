"""ADR-033 (B): the model answers as typed records under a JSON schema; code renders and checks.

Each record is one sentence of the answer: `{"section", "kind", "text", "cites"}`.

* `law`      a legal assertion: needs >= 1 citation (the same contract as free text, invariant 6);
* `analysis` the model's own reasoning: rendered with `[análisis propio]`, no citation;
* `framing`  a transition that asserts nothing legal. It is the only kind allowed without a citation
             or a tag, so code checks it STRICTLY: it opens with a closed list of transition phrases
             ("A continuación", "En síntesis", ...), uses no normative verb (puede, debe, procede,
             establece, ...), and has no legal
             term or law name from the closed catalogues, no article/fracción/section number, no
             date, no quotation marks, no citation, at most `FRAMING_MAX_WORDS` words, and all
             framing together at most `FRAMING_MAX_SHARE` of the answer's words. A record that
             fails is a validator problem (regenerate once, then the system refusal);
* `gap`      a section with no sources: rendered as the system's `<insuficiente></insuficiente>`.

The rendered text has the same shape as a free-text answer, so the UI, the audit log, the citation
validator, the quotation check and the decree check (ADR-051) all run on it unchanged. Section (V)
"Fuentes citadas" is written by code from the cited ids, never by the model.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from app.corpus.verify import _LAWS
from app.news.score import fold, legal_terms

Kind = Literal["law", "analysis", "framing", "gap"]
SECTIONS: tuple[tuple[str, str], ...] = (
    ("I", "Planteamiento"),
    ("II", "Marco normativo aplicable"),
    ("III", "Criterios jurisprudenciales"),
    ("IV", "Análisis"),
)
FRAMING_MAX_WORDS = 25
FRAMING_MAX_SHARE = 0.15
OWN_ANALYSIS_TAG = "[análisis propio]"

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "records": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "section": {"type": "string", "enum": [s for s, _ in SECTIONS]},
                    "kind": {"type": "string", "enum": ["law", "analysis", "framing", "gap"]},
                    "text": {"type": "string"},
                    "cites": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["section", "kind", "text", "cites"],
            },
        }
    },
    "required": ["records"],
}

_LAW_NAMES = tuple(re.compile(law.pattern) for law in _LAWS)
_NUMBERED = re.compile(
    r"\b(?:art(?:[ií]culo)?s?|fracc(?:i[oó]n)?(?:es)?|incisos?|apartados?|p[aá]rrafos?|t[ií]tulos?"
    r"|cap[ií]tulos?|secci[oó]n(?:es)?|transitorios?)\b\.?|§|\b\d{1,4}\s*(?:bis|ter)\b",
    re.IGNORECASE,
)
_DATE = re.compile(r"\b(?:19|20)\d{2}\b|\b\d{1,2}[-/]\d{1,2}[-/]\d{2,4}\b")
_QUOTES = re.compile(r"[\"“”«»]")
_CITE = re.compile(r"\[cite:", re.IGNORECASE)
# A framing record must BE a transition, recognised positively: it opens with one of these (folded),
# and it contains none of the verbs a norm is stated with. Excluding legal vocabulary alone is not
# enough ("El patrón puede rescindir la relación sin responsabilidad" names no catalogued term).
_OPENERS = re.compile(
    r"^(?:a continuacion|en seguida|enseguida|en lo que sigue|en sintesis|en resumen|"
    r"por otra parte|por lo anterior|de lo expuesto|se presenta[n]?|se expone[n]?|se resume[n]?)\b"
)
_NORMATIVE = re.compile(
    r"\b(?:puede[n]?|podra[n]?|podria[n]?|debe[n]?|debera[n]?|deberia[n]?|tiene[n]? derecho|"
    r"esta[n]? obligad[oa]s?|procede[n]?|procedera|establece[n]?|dispone[n]?|preve[n]?|"
    r"senala[n]?|determina[n]?|regula[n]?|obliga[n]?|prohibe[n]?|permite[n]?|sanciona[n]?|"
    r"faculta[n]?|corresponde[n]?|aplica[n]?|exige[n]?|requiere[n]?|implica[n]?)\b"
)


class RecordFormatError(ValueError):
    """The output is not a valid record list: a format violation, like prose without the block."""


@dataclass(frozen=True)
class Record:
    section: str
    kind: Kind
    text: str
    cites: tuple[str, ...]


def parse(raw: str) -> list[Record]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RecordFormatError(f"not JSON: {exc.msg}") from exc
    items = data.get("records") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise RecordFormatError("no 'records' list")
    out: list[Record] = []
    sections = {s for s, _ in SECTIONS}
    for n, it in enumerate(items, 1):
        if not isinstance(it, dict):
            raise RecordFormatError(f"record {n} is not an object")
        section, kind, text, cites = (
            it.get("section"),
            it.get("kind"),
            it.get("text"),
            it.get("cites"),
        )
        if section not in sections or kind not in ("law", "analysis", "framing", "gap"):
            raise RecordFormatError(f"record {n}: unknown section or kind")
        if not isinstance(text, str) or not isinstance(cites, list):
            raise RecordFormatError(f"record {n}: text must be a string and cites a list")
        if not all(isinstance(c, str) for c in cites):
            raise RecordFormatError(f"record {n}: cites must be strings")
        out.append(
            Record(section, kind, text.strip(), tuple(c.strip() for c in cites if c.strip()))
        )
    return out


def framing_problem(rec: Record) -> str | None:
    """Why a `framing` record is not allowed, or None. Shape only, decided by code."""
    t = rec.text
    if rec.cites or _CITE.search(t):
        return "lleva cita"
    if _QUOTES.search(t):
        return "lleva comillas"
    if len(t.split()) > FRAMING_MAX_WORDS:
        return f"tiene más de {FRAMING_MAX_WORDS} palabras"
    if _NUMBERED.search(t):
        return "menciona un artículo, fracción o sección"
    if _DATE.search(t):
        return "menciona una fecha"
    folded = fold(t)
    if not _OPENERS.match(folded):
        return "no empieza con una transición reconocida"
    if _NORMATIVE.search(folded):
        return "usa un verbo normativo"
    if any(rx.search(folded) for rx in _LAW_NAMES):
        return "nombra una ley"
    found, _ = legal_terms(t)
    if found:
        return f"usa un término jurídico ({found[0]})"
    return None


def problems(records: list[Record]) -> list[str]:
    """Record-level failures the text validator cannot see (it never knows a record's kind)."""
    out: list[str] = []
    words = sum(len(r.text.split()) for r in records if r.kind != "gap") or 1
    framing_words = 0
    for r in records:
        if r.kind == "law" and not r.cites:
            out.append(f"registro 'law' sin cita: {r.text[:140]}")
        elif r.kind == "framing":
            why = framing_problem(r)
            if why:
                out.append(f"registro 'framing' no permitido ({why}): {r.text[:140]}")
            framing_words += len(r.text.split())
    # a short answer may still hold one transition: the cap is the larger of one framing record's
    # word limit and the share of the answer
    if framing_words > max(FRAMING_MAX_WORDS, FRAMING_MAX_SHARE * words):
        out.append(
            f"demasiado texto 'framing' ({framing_words / words:.0%} de la respuesta; máximo "
            f"{FRAMING_MAX_SHARE:.0%})"
        )
    return out


def allowed_framing(records: list[Record]) -> tuple[str, ...]:
    """Texts of the framing records that passed the strict rules: the only uncited, untagged
    sentences coverage may accept."""
    return tuple(r.text for r in records if r.kind == "framing" and framing_problem(r) is None)


def declined(records: list[Record]) -> bool:
    """Nothing but gaps (or nothing at all): the model declined, the system writes the refusal."""
    return not any(r.kind in ("law", "analysis") for r in records)


def render(records: list[Record], citations: Mapping[str, str]) -> str:
    """The records as a free-text answer in the workflow's own shape. `citations` maps each
    citable id to its citation string (the fragment's `cita`), for section (V)."""
    lines: list[str] = []
    for code, title in SECTIONS:
        rows = [r for r in records if r.section == code]
        lines.append(f"({code}) {title}")
        if not rows or all(r.kind == "gap" for r in rows):
            lines.append("<insuficiente></insuficiente>")
        for r in rows:
            if r.kind == "law":
                lines.append(f"{r.text} [cite: {'; '.join(r.cites)}]")
            elif r.kind == "analysis":
                lines.append(f"{OWN_ANALYSIS_TAG} {r.text}")
            elif r.kind == "framing":
                lines.append(r.text)
        lines.append("")
    lines.append("(V) Fuentes citadas")
    cited: dict[str, None] = {}
    for r in records:
        if r.kind == "law":
            for c in r.cites:
                cited.setdefault(c, None)
    lines.extend(f"* {c}: {citations.get(c, c)}" for c in cited)
    return "\n".join(lines).rstrip() + "\n"
