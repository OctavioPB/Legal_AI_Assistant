"""Pure citation validator: existence, orphan markers, coverage, verbatim quotes (WS5.T01/T02)."""

from __future__ import annotations

import random

import pytest

from app.validation.types import RetrievedSet, ValidationConfig
from app.validation.validator import fold, parse_citations, validate

ART47 = "MX-FED-LFT-ART47-FRACX-v2025.06"
ART51 = "MX-FED-LFT-ART51-FRACX-v2025.06"
TEXT47 = (
    "X. Tener el trabajador más de tres faltas de asistencia en un período de treinta días, "
    "sin permiso del patrón o sin causa justificada."
)
TEXT51 = "X. Exigir la persona trabajadora una remuneración distinta de la convenida."
RS = RetrievedSet.build([(ART47, [TEXT47]), (ART51, [TEXT51])])
GOOD = f"La rescisión procede por más de tres faltas en treinta días [cite: {ART47}]."


def v(answer: str, workflow: str = "dossier_builder", rs: RetrievedSet = RS, **cfg: int):  # type: ignore[no-untyped-def]
    return validate(answer, rs, workflow=workflow, config=ValidationConfig(**cfg))


def test_grounded_happy_path() -> None:
    out = v(GOOD)
    assert out.grounded and out.cited == (ART47,) and not out.problems


@pytest.mark.parametrize("seed", range(50))
def test_every_invented_cite_is_detected(seed: int) -> None:
    rnd = random.Random(seed)
    fake = f"MX-{rnd.choice(['FED', 'NL', 'JAL'])}-{rnd.choice(['LFT', 'CCF', 'CPF'])}-ART{rnd.randint(1, 999)}-v{rnd.randint(2000, 2026)}"
    out = v(f"El patrón puede rescindir sin responsabilidad alguna por causa grave [cite: {fake}].")
    assert fake in out.invalid and not out.grounded


def test_a_valid_chunk_that_this_query_did_not_retrieve_is_invalid() -> None:
    only47 = RetrievedSet.build([(ART47, [TEXT47])])
    out = v(
        f"El patrón responde por la remuneración distinta de la convenida [cite: {ART51}].",
        rs=only47,
    )
    assert out.invalid == (ART51,) and not out.grounded


@pytest.mark.parametrize(
    "marker", ["EJEMPLO-NO-CITAR", "CITE_ID", "X", "...", "", "cite_id", "N/A"]
)
def test_copied_example_markers_are_rejected_even_next_to_a_valid_cite(marker: str) -> None:
    out = v(
        f"La rescisión procede por más de tres faltas en treinta días [cite: {ART47}] [cite: {marker}]."
    )
    assert out.placeholders == (marker,) and not out.grounded


def test_parser_accepts_groups_variants_and_dedupes_in_order() -> None:
    assert parse_citations(f"a [cite: {ART51}; {ART47}] b [ Cite : {ART47} , Z ] c [CITE:{ART51}]") == [
        ART51, ART47, "Z",
    ]  # fmt: skip
    assert parse_citations("sin citas [cite sin dos puntos]") == []


def test_refusal_block_is_the_contract_prose_is_a_format_violation() -> None:
    assert v("<insuficiente></insuficiente>").refused
    assert v("<insuficiente>\nNo hay fuentes.\n</insuficiente>").refused
    prose = v("No encontré información suficiente en las fuentes para responder.")
    assert prose.prose_refusal and not prose.refused and not prose.grounded
    assert v("I could not find anything relevant in the sources.").prose_refusal
    # an answer that merely mentions not finding something, but is cited, is not a refusal
    assert not v(
        f"No encontré excepción alguna: el patrón puede rescindir por faltas [cite: {ART47}]."
    ).prose_refusal


def test_coverage_sentence_without_cite_or_tag_fails() -> None:
    out = v(
        f"{GOOD} Además el patrón siempre debe pagar una indemnización adicional al trabajador."
    )
    assert out.uncovered and "indemnización adicional" in out.uncovered[0] and not out.grounded


def test_coverage_cite_after_the_period_belongs_to_its_sentence() -> None:
    assert v(
        f"La rescisión procede por más de tres faltas en treinta días. [cite: {ART47}]"
    ).grounded


def test_coverage_tags_headings_and_listings() -> None:
    answer = (
        "## (I) Planteamiento\n"
        f"La rescisión procede por más de tres faltas en treinta días [cite: {ART47}].\n"
        "Esto es una valoración mía sobre la fuerza probatoria de las faltas [análisis propio].\n"
        "Fuentes citadas:\n"
        f"- {ART47} — Ley Federal del Trabajo, art. 47, fracc. X\n"
    )
    out = v(answer)
    assert out.grounded and out.analysis_share > 0


def test_strategic_tag_counts_only_in_the_adversarial_workflow() -> None:
    answer = (
        f"{GOOD} Tu teoría del caso presume hechos que nunca se probaron en el expediente "
        "[razonamiento estratégico]."
    )
    assert v(answer, workflow="adversarial").grounded
    assert not v(answer, workflow="dossier_builder").grounded


def test_a_response_of_only_own_analysis_is_never_grounded() -> None:
    out = v(
        "Creo que el caso favorece claramente al trabajador por varias razones de equidad [análisis propio]."
    )
    assert not out.grounded and out.cited == () and out.analysis_share == 1.0


def test_quote_must_appear_verbatim_in_the_cited_fragment() -> None:
    ok = f'La norma dice "más de tres faltas de asistencia en un período de treinta días" [cite: {ART47}].'
    assert v(ok).grounded
    folded = (
        f'Dice "MAS DE TRES FALTAS de  asistencia en un periodo de treinta dias" [cite: {ART47}].'
    )
    assert v(folded).grounded  # accents, case and whitespace are folded
    paraphrase = f'La norma dice "más de tres inasistencias injustificadas en un mes calendario" [cite: {ART47}].'
    bad = v(paraphrase)
    assert bad.bad_quotes and not bad.grounded


def test_quote_checked_against_the_cited_fragment_not_any_fragment() -> None:
    wrong = (
        f'La norma dice "exigir la persona trabajadora una remuneración distinta" [cite: {ART47}].'
    )
    assert v(wrong).bad_quotes  # the words exist, but in ART51


def test_quote_with_ellipsis_checks_each_long_segment_and_ignores_short_ones() -> None:
    ok = f'Dice "más de tres faltas de asistencia ... sin permiso del patrón o sin causa justificada" [cite: {ART47}].'
    assert v(ok).grounded
    scare = f'El "debido proceso" aplica aquí por la norma de rescisión citada [cite: {ART47}].'
    assert v(scare, quote_min_words=5).grounded  # short quoted runs are not statute quotes


def test_quote_without_a_cite_in_its_sentence_fails() -> None:
    out = v(
        f'Dice "más de tres faltas de asistencia en un período de treinta días". Y nada más aquí [cite: {ART47}].'
    )
    assert out.bad_quotes


def test_quote_may_come_from_the_expanded_article_offered_for_that_cite() -> None:
    article = (
        "Artículo 47.- Son causas de rescisión de la relación de trabajo sin responsabilidad para el patrón: "
        + TEXT47
    )
    rs = RetrievedSet.build([(ART47, [TEXT47]), (ART47, [article])])
    ok = f'Dice "son causas de rescisión de la relación de trabajo sin responsabilidad para el patrón" [cite: {ART47}].'
    assert v(ok, rs=rs).grounded
    assert v(ok).bad_quotes  # the chunk alone does not contain the caput


def test_validation_is_pure_and_deterministic() -> None:
    assert v(GOOD) == v(GOOD)


def test_fold_is_accent_case_and_whitespace_insensitive() -> None:
    assert fold("  Artículo 47 ”X” ") == fold("articulo 47 ”x”")
