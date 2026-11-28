"""ADR-033 (B): typed records are parsed, checked strictly and rendered by code (pure functions)."""

from __future__ import annotations

import json

import pytest

from app.validation.records import (
    FRAMING_MAX_WORDS,
    Record,
    RecordFormatError,
    allowed_framing,
    declined,
    framing_problem,
    parse,
    problems,
    render,
)
from app.validation.types import RetrievedSet
from app.validation.validator import validate

ART = "MX-FED-LFT-ART47-FRACII-v1"
RS = RetrievedSet.build([(ART, ["II. Incurrir el trabajador en faltas de probidad u honradez."])])


def rec(kind: str, text: str, cites: tuple[str, ...] = (), section: str = "II") -> Record:
    return Record(section, kind, text, cites)  # type: ignore[arg-type]


def raw(*records: dict[str, object]) -> str:
    return json.dumps({"records": list(records)}, ensure_ascii=False)


def test_a_valid_record_list_parses_and_anything_else_is_a_format_error() -> None:
    [r] = parse(raw({"section": "II", "kind": "law", "text": " Texto. ", "cites": [ART, " "]}))
    assert r == Record("II", "law", "Texto.", (ART,))
    for bad in ("no json", "[]", raw({"section": "IX", "kind": "law", "text": "x", "cites": []}),
                raw({"section": "II", "kind": "opinion", "text": "x", "cites": []}),
                raw({"section": "II", "kind": "law", "text": 3, "cites": []})):  # fmt: skip
        with pytest.raises(RecordFormatError):
            parse(bad)


@pytest.mark.parametrize(
    ("text", "why"),
    [
        ("A continuación se resumen los puntos principales.", None),
        ("En seguida se exponen los fragmentos recuperados.", None),
        ("El patrón puede rescindir la relación sin responsabilidad alguna.", "transición"),
        ("A continuación el patrón puede rescindir la relación.", "verbo normativo"),
        ("A continuación, el artículo 47 en detalle.", "artículo"),
        ("En síntesis, la Ley Federal del Trabajo en este punto.", "ley"),
        ("En síntesis, la jurisprudencia en este punto.", "término"),
        ("En síntesis, lo vigente desde 2019.", "fecha"),
        ('A continuación, "faltas de probidad".', "comillas"),
        ("A continuación " + " ".join(["palabra"] * FRAMING_MAX_WORDS), "palabras"),
    ],
)
def test_framing_is_checked_strictly_by_code(text: str, why: str | None) -> None:
    got = framing_problem(rec("framing", text))
    assert (got is None) if why is None else (got is not None and why in got)


def test_framing_with_a_citation_is_not_framing() -> None:
    assert framing_problem(rec("framing", "A continuación el resumen.", (ART,))) == "lleva cita"


def test_record_level_problems_law_without_cite_and_too_much_framing() -> None:
    probs = problems([rec("law", "El patrón puede rescindir la relación.")])
    assert probs and "sin cita" in probs[0]
    one = [rec("framing", "A continuación se resumen los puntos principales."),
           rec("law", "Texto breve.", (ART,))]  # fmt: skip
    assert problems(one) == []  # a short answer may hold one transition
    lots = [rec("framing", "A continuación se resumen los puntos principales del caso planteado.")
            for _ in range(4)] + [rec("law", "Texto breve.", (ART,))]  # fmt: skip
    assert any("demasiado texto 'framing'" in p for p in problems(lots))


def test_render_keeps_the_workflow_shape_and_code_writes_the_sources() -> None:
    records = [
        rec("analysis", "Se consulta la causal de rescisión.", section="I"),
        rec("framing", "A continuación se resumen los puntos principales."),
        rec("law", "El patrón puede rescindir por faltas de probidad u honradez.", (ART,)),
        rec("gap", "", section="III"),
    ]
    text = render(records, {ART: "Ley Federal del Trabajo, art. 47, fracc. II"})
    assert "(I) Planteamiento\n[análisis propio] Se consulta" in text
    assert f"u honradez. [cite: {ART}]" in text
    assert "(III) Criterios jurisprudenciales\n<insuficiente></insuficiente>" in text
    assert "(IV) Análisis\n<insuficiente></insuficiente>" in text  # a section with no records
    assert text.rstrip().endswith(f"* {ART}: Ley Federal del Trabajo, art. 47, fracc. II")


def test_an_approved_framing_sentence_passes_coverage_and_nothing_else_does() -> None:
    records = [
        rec("analysis", "Se consulta la causal de rescisión.", section="I"),
        rec("framing", "A continuación se resumen los puntos principales del caso."),
        rec("law", "El patrón puede rescindir por faltas de probidad u honradez.", (ART,)),
    ]
    text = render(records, {ART: "LFT, art. 47"})
    ok = validate(text, RS, workflow="dossier_builder", allowed_uncited=allowed_framing(records))
    assert ok.grounded and not ok.uncovered
    without = validate(text, RS, workflow="dossier_builder")  # free text: the same sentence fails
    assert without.uncovered


def test_a_legal_claim_labelled_framing_is_never_allowed_and_is_reported() -> None:
    """Found while writing these tests: this sentence names no catalogued legal term, so a rule that
    only excluded legal vocabulary let it through as framing."""
    bad = [rec("framing", "El patrón puede rescindir la relación sin responsabilidad alguna.")]
    assert allowed_framing(bad) == ()
    assert any("no permitido" in p for p in problems(bad))


def test_only_gaps_is_the_model_declining() -> None:
    assert declined([rec("gap", "", section="II")]) and declined([])
    assert not declined([rec("law", "x", (ART,))])
