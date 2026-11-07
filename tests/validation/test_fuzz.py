"""Property/fuzz tests of the citation validator: it must never raise, never accept a forged or
copied citation, and stay fast on hostile input (a slow validator is a denial-of-service lever)."""

from __future__ import annotations

import random
import re
import time

import pytest

from app.prompts.composer import EXAMPLE_CITE_IDS
from app.validation.types import RetrievedSet, ValidationConfig
from app.validation.validator import parse_citations, validate

REAL = [
    "MX-FED-LFT-ART47-FRACX-v2025.06",
    "MX-FED-LFT-ART51-FRACX-v2025.06",
    "MX-NL-CCNL-ART1148-v2023",
]
TEXTS = {
    REAL[0]: (
        "X. Tener el trabajador más de tres faltas de asistencia en un período de treinta días.",
    ),
    REAL[1]: ("X. Exigir la persona trabajadora una remuneración distinta de la convenida.",),
    REAL[2]: ("La posesión de buena fe se pierde por la prescripción adquisitiva en cinco años.",),
}
RS = RetrievedSet(texts=TEXTS)
FORGED = [
    "MX-FED-LFT-ART999-v1",
    "MX-JAL-CCJAL-ART1148-v2022",
    "US-CA-LAB-SEC1-v2025",
    "ART47",
    "mx-fed-lft-art47-fracx-v2025.06",
]
PLACEHOLDERS = ["EJEMPLO-NO-CITAR", "CITE_ID", "X", "...", "", "N/A"]
WORDS = [
    "el",
    "patrón",
    "puede",
    "rescindir",
    "sin",
    "responsabilidad",
    "cuando",
    "el",
    "trabajador",
    "incurra",
    "en",
    "faltas",
    "graves",
]
WEIRD = [" ", "​", "‮", "\x00", "\r", "ñ", "é", "“", "”", "«", "»", '"', "[", "]", "<", ">"]


def sentence(rnd: random.Random) -> str:
    return " ".join(rnd.choice(WORDS) for _ in range(rnd.randint(1, 20)))


def cite_marker(rnd: random.Random, pool: list[str]) -> str:
    ids = rnd.sample(pool, k=rnd.randint(1, min(2, len(pool))))
    sep = rnd.choice(["; ", ", "])
    return rnd.choice(["[cite: {}]", "[ Cite : {} ]", "[CITE:{}]"]).format(sep.join(ids))


def random_answer(seed: int) -> tuple[str, list[str], list[str]]:
    """The answer plus the forged and placeholder ids that were deliberately inserted."""
    rnd = random.Random(seed)
    parts: list[str] = []
    forged: list[str] = []
    placeholders: list[str] = []
    for _ in range(rnd.randint(1, 12)):
        kind = rnd.random()
        s = sentence(rnd)
        if kind < 0.35:
            parts.append(f"{s} {cite_marker(rnd, REAL)}.")
        elif kind < 0.50:
            f = rnd.choice(FORGED)
            forged.append(f)
            parts.append(f"{s} [cite: {f}].")
        elif kind < 0.60:
            p = rnd.choice(PLACEHOLDERS)
            placeholders.append(p)
            parts.append(f"{s} [cite: {p}].")
        elif kind < 0.70:
            parts.append(f"{s} [análisis propio].")
        elif kind < 0.78:
            parts.append(f'Dice "{sentence(rnd)}" {cite_marker(rnd, REAL)}.')
        elif kind < 0.84:
            parts.append(
                rnd.choice(
                    [
                        "<insuficiente></insuficiente>",
                        "<insuficiente>texto</insuficiente>",
                        "<insuficiente>",
                    ]
                )
            )
        elif kind < 0.92:
            parts.append(s + rnd.choice(WEIRD) + s)
        else:
            parts.append(rnd.choice(["", "\n", "## Encabezado", "- " + s, "1. " + s]))
    return rnd.choice(["\n", " ", "\n\n"]).join(parts), forged, placeholders


@pytest.mark.parametrize("seed", range(600))
def test_invariants_hold_for_random_model_output(seed: int) -> None:
    answer, forged, placeholders = random_answer(seed)
    for wf in ("dossier_builder", "adversarial"):
        v = validate(answer, RS, workflow=wf)  # must not raise
        assert v == validate(answer, RS, workflow=wf)  # deterministic
        if v.grounded:
            assert v.cited and not v.invalid and not v.placeholders and not v.uncovered
            assert not v.bad_quotes and not v.refused and not v.prose_refusal
            assert set(v.cited) <= RS.ids
        # a deliberately inserted forged id is ALWAYS reported (the "100%" claim, randomized)
        # ids inside a gap block are discarded with the block (they never reach the user)
        outside = re.sub(
            r"<insuficiente>.*?</insuficiente>", "", answer, flags=re.DOTALL | re.IGNORECASE
        )
        shown = {
            i.strip()
            for m in re.findall(r"\[\s*cite\s*:([^\]\[]*)\]", outside, re.I)
            for i in re.split(r"[;,]", m)
        }
        for f in forged:
            if f not in RS.ids and f in shown:
                assert f in v.invalid, (seed, f)
        for p in placeholders:
            if p in shown:
                assert p in v.placeholders, (seed, p)
        if (set(forged) | set(placeholders)) & shown:
            assert not v.grounded
        assert len(set(v.cited)) == len(v.cited)  # deduped
        blocks = len(
            re.findall(r"<insuficiente>.*?</insuficiente>", answer, re.DOTALL | re.IGNORECASE)
        )
        assert v.gaps == blocks  # independent oracle for the number of well-formed blocks
        assert v.refused == (blocks > 0 and not v.cited)  # a block beside grounded cites is a gap
        # order of first appearance
        ids = [i for i in parse_citations(outside) if i in RS.ids]
        assert list(v.cited) == ids


@pytest.mark.parametrize("seed", range(200))
def test_appending_any_forged_or_example_cite_destroys_a_grounded_answer(seed: int) -> None:
    rnd = random.Random(seed)
    good = f"La rescisión procede por más de tres faltas en treinta días [cite: {REAL[0]}]."
    assert validate(good, RS, workflow="dossier_builder").grounded
    bad_id = rnd.choice(FORGED + PLACEHOLDERS + sorted(EXAMPLE_CITE_IDS))
    spoiled = good + rnd.choice([" ", "\n", " Además "]) + f"[cite: {bad_id}]"
    assert not validate(spoiled, RS, workflow="dossier_builder").grounded


def test_a_realistic_good_answer_is_not_rejected_by_noise_alone() -> None:
    answer = (
        "## (I) Planteamiento\n"
        f"La rescisión procede por más de tres faltas de asistencia en treinta días [cite: {REAL[0]}].\n"
        f'El texto dice "más de tres faltas de asistencia en un período de treinta días" [cite: {REAL[0]}].\n'
        "Valoración mía sobre la carga de la prueba en este supuesto concreto [análisis propio].\n"
        "Fuentes citadas:\n"
        f"- {REAL[0]} — Ley Federal del Trabajo, art. 47, fracc. X\n"
    )
    v = validate(answer, RS, workflow="dossier_builder")
    assert v.grounded, v


@pytest.mark.parametrize(
    "payload",
    [
        "[cite: " * 50_000,
        "[cite: A" + ", A" * 50_000 + "]",
        '"' * 100_000,
        "“" * 50_000 + "”" * 50_000,
        "<insuficiente>" * 20_000,
        ("x. " * 200_000),
        ("Palabra " * 150_000) + f"[cite: {REAL[0]}]",
        "\n".join(f"Oración número {i} con contenido [cite: {REAL[0]}]." for i in range(20_000)),
        f'Dice "{"palabra " * 100_000}" [cite: {REAL[0]}].',
        "[" * 100_000,
    ],
)
def test_hostile_or_huge_output_is_validated_quickly(payload: str) -> None:
    t0 = time.perf_counter()
    v = validate(payload, RS, workflow="dossier_builder", config=ValidationConfig())
    elapsed = time.perf_counter() - t0
    assert elapsed < 3.0, (
        f"{elapsed:.1f}s for {len(payload)} chars: possible catastrophic backtracking"
    )
    assert isinstance(v.grounded, bool)


def test_a_runaway_output_is_refused_before_any_heavy_check() -> None:
    huge = (f"La rescisión procede por faltas graves [cite: {REAL[0]}]. ") * 10_000  # ~560k chars
    t0 = time.perf_counter()
    v = validate(huge, RS, workflow="dossier_builder")
    assert time.perf_counter() - t0 < 0.5
    assert not v.grounded and "exceeds" in v.uncovered[0]
    ok = validate(
        huge, RS, workflow="dossier_builder", config=ValidationConfig(max_answer_chars=10**7)
    )
    assert isinstance(ok.grounded, bool)  # the cap is configuration, not a hard-coded wall
