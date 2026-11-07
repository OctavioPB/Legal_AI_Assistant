"""Seeded fuzzing of the grammars and the chunker: any text, however broken, must parse and chunk
without raising, and the structural invariants must hold. Failures print the seed to reproduce."""

from __future__ import annotations

import itertools
import random
import re
import time

import pytest

from app.ingestion.chunker import Chunker, parse_cite
from app.ingestion.model import DocMeta
from app.ingestion.normalizer_mx import normalize_text
from app.ingestion.normalizer_mx import parse as parse_mx
from app.ingestion.normalizer_us import normalize_us_text
from app.ingestion.normalizer_us import parse as parse_us
from app.ingestion.tokens import ApproxCounter

WORDS = [
    "el",
    "patrón",
    "trabajador",
    "podrá",
    "sin",
    "causa",
    "justificada",
    "artículo",
    "derecho",
    "ley",
    "norma",
    "será",
]
ROMAN = ["I", "II", "III", "IV", "V", "IX", "X", "XIV", "XXIX", "XL", "C", "L"]
WEIRD = [" ", "​", "﻿", "‮", "\f", "\r", "\t", "é", "ñ", "Ñ", "º", "°", "…", "—", "\x00"]
HEADS = [
    "LIBRO PRIMERO",
    "TÍTULO SEGUNDO",
    "CAPÍTULO III Bis",
    "SECCIÓN 2",
    "TRANSITORIOS",
    "ÍNDICE",
]


def sentence(rnd: random.Random, n: int | None = None) -> str:
    return " ".join(rnd.choice(WORDS) for _ in range(n or rnd.randint(1, 60)))


def line_mx(rnd: random.Random) -> str:
    kind = rnd.randint(0, 17)
    body = sentence(rnd)
    return {
        0: f"Artículo {rnd.randint(1, 500)}.- {body}",
        1: f"Art. {rnd.randint(1, 500)}o. Bis.- {body}",
        2: f"ARTÍCULO {rnd.randint(1, 99)}-{rnd.choice('ABCÑ')}.- {body}",
        3: f"{rnd.choice(ROMAN)}. {body}",
        4: f"{rnd.choice(ROMAN)}-{rnd.choice('ABÑ')}.- {body}",
        5: f"{rnd.choice('abcdefg')}) {body}",
        6: f"{rnd.choice('ABCD')}. {body}",
        7: rnd.choice(HEADS),
        8: f"(REFORMADO, P.O. 13 DE OCTUBRE DE 2000) Art. {rnd.randint(1, 50)}.- {body}",
        9: f"Artículo {rnd.randint(1, 50)}.- Se deroga.",
        10: f"| {rnd.choice(ROMAN)}. | Se deroga. | Fracción derogada DOF 01-05-2019 |",
        11: f"Párrafo reformado DOF {rnd.randint(1, 28):02d}-05-2019",
        12: f"{rnd.choice(['PRIMERO', 'DÉCIMO PRIMERO', 'ÚNICO'])}.- {body}",
        13: body + rnd.choice(WEIRD) + body,
        14: "",
        15: rnd.choice(WEIRD) * rnd.randint(1, 5),
        16: f"Artículo {rnd.randint(1, 50)} ........ {rnd.randint(1, 400)}",
        17: f"{'Artículo ' * rnd.randint(1, 4)}{rnd.randint(1, 9)}.- {sentence(rnd, 700)}",
    }[kind]


def line_us(rnd: random.Random) -> str:
    kind = rnd.randint(0, 9)
    body = sentence(rnd)
    return {
        0: f"§ {rnd.randint(1, 3000)}{rnd.choice(['', '.5', '.12'])}. {body}",
        1: f"§ {rnd.randint(1, 50)}. (a){body} (b){body}",
        2: f"({rnd.choice('abcdefghijk')}){body}",
        3: f"({rnd.randint(1, 12)}){body}",
        4: f"({rnd.choice('ABCDE')}){body}",
        5: f"({rnd.choice(['i', 'ii', 'iii', 'iv', 'v'])}){body}",
        6: f"Division {rnd.randint(1, 4)} Title {rnd.choice(['-', '2.'])} Part {rnd.randint(1, 5)}. Chapter - Article -",
        7: "Repealed by Stats. 2010." if rnd.random() < 0.5 else "",
        8: body + rnd.choice(WEIRD),
        9: "\t\t  " + body,
    }[kind]


def make_text(seed: int, us: bool) -> str:
    rnd = random.Random(seed)
    gen = line_us if us else line_mx
    return "\n".join(gen(rnd) for _ in range(rnd.randint(1, 120)))


def squash(s: str) -> str:
    return re.sub(r"\s+", "", s)


def check(text: str, us: bool, seed: int) -> None:
    src = normalize_us_text(text) if us else normalize_text(text)
    nodes = (parse_us if us else parse_mx)(src)
    meta = DocMeta(
        country="US" if us else "MX",
        scope="X",
        code="FUZZ",
        title="Fuzz",
        version_label="1",
        language="en" if us else "es",
    )
    chunks = Chunker(ApproxCounter()).chunk(src, nodes, meta)
    ctx = f"seed={seed} us={us}"
    paths = [n.path for n in nodes]
    assert len(paths) == len(set(paths)), f"dup paths {ctx}"
    by = {n.path: n for n in nodes}
    for n in nodes:
        assert n.parent is None or n.parent in by, f"orphan {ctx}"
        assert 0 <= n.start <= n.own_end <= n.end <= len(src), f"span {n.path} {ctx}"
    ids = [c.cite_id for c in chunks]
    assert len(ids) == len(set(ids)), f"dup cite {ctx}"
    spans = sorted((c.char_start, c.char_end) for c in chunks)
    assert all(a[1] <= b[0] for a, b in itertools.pairwise(spans)), f"overlap {ctx}"
    for c in chunks:
        assert src[c.char_start : c.char_end] == c.text and c.text.strip(), f"slice {ctx}"
        assert parse_cite(c.cite_id) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", c.cite_id), (
            f"{c.cite_id} {ctx}"
        )
        n = by[c.node_path]
        assert n.start <= c.char_start and c.char_end <= n.end, f"crosses node {ctx}"
    covered = squash("".join(c.text for c in sorted(chunks, key=lambda c: c.char_start)))
    expected = squash(
        "".join(
            src[n.start : n.own_end]
            for n in nodes
            if n.node_type not in {"libro", "titulo", "capitulo", "seccion"}
        )
    )
    assert covered == expected, f"coverage {ctx}"


@pytest.mark.parametrize("seed", range(300))
def test_mx_grammar_never_breaks_on_random_legal_looking_text(seed: int) -> None:
    check(make_text(seed, us=False), us=False, seed=seed)


@pytest.mark.parametrize("seed", range(300))
def test_us_grammar_never_breaks_on_random_statute_looking_text(seed: int) -> None:
    check(make_text(seed, us=True), us=True, seed=seed)


@pytest.mark.parametrize(
    "payload",
    ["", "\n\n\n", " ", "\x00", "Artículo", "Art.", "§", "(", ")", "|", "||||", "I.", "a)"],
)
def test_degenerate_inputs_are_handled(payload: str) -> None:
    for us in (False, True):
        src = normalize_us_text(payload) if us else normalize_text(payload)
        nodes = (parse_us if us else parse_mx)(src)
        meta = DocMeta(country="MX", scope="X", code="F", title="T", version_label="1")
        Chunker(ApproxCounter()).chunk(src, nodes, meta)  # must not raise (may yield no chunks)


@pytest.mark.parametrize(
    "line",
    [
        "Artículo " + "1" * 5000 + ".- x",
        "I" * 10000 + ". x",
        "(" * 5000,
        "[" * 5000,
        "Artículo 1" + "-A" * 3000 + ".- x",
        ("a) x " * 3000),
        "| " + " | ".join(["c"] * 3000) + " |",
        "(REFORMADO, " * 2000,
        "Art. 1o. " * 2000,
    ],
)
def test_pathological_lines_do_not_blow_up_the_regexes(line: str) -> None:
    t0 = time.perf_counter()
    for us in (False, True):
        src = normalize_us_text(line) if us else normalize_text(line)
        (parse_us if us else parse_mx)(src)
    assert time.perf_counter() - t0 < 2.0, "possible catastrophic backtracking"
