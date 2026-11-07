"""Invariants on the REAL demo corpus (data/corpus, gitignored): fixtures never exercise the shapes
that official PDFs produce, so every structural property is re-checked on the actual documents.
Skipped only when the data files are absent (a fresh clone without the downloaded corpus)."""

from __future__ import annotations

import itertools
import re
from pathlib import Path

import pytest

from app.ingestion.chunker import Chunker, parse_cite
from app.ingestion.model import DocMeta
from app.ingestion.normalizer_mx import normalize_text
from app.ingestion.normalizer_mx import parse as parse_mx
from app.ingestion.normalizer_us import normalize_us_text
from app.ingestion.normalizer_us import parse as parse_us
from app.ingestion.tokens import ApproxCounter

ROOT = Path(__file__).parents[2] / "data" / "corpus"
# name, folder, country, scope, code, language
DOCS = [
    ("LFT", "text", "MX", "FED", "LFT", "es"),
    ("CPEUM", "text", "MX", "FED", "CPEUM", "es"),
    ("CCF", "text", "MX", "FED", "CCF", "es"),
    ("CPF", "text", "MX", "FED", "CPF", "es"),
    ("CC_NL", "text", "MX", "NL", "CCNL", "es"),
    ("REG_ADMIN_MTY", "text", "MX", "MTY", "RAPMTY", "es"),
    ("REG_JUSTICIA_CIVICA_MTY", "text", "MX", "MTY", "RJCMTY", "es"),
    ("CA_LABOR_CODE", "raw", "US", "CA", "LAB", "en"),
]
CITE_DB = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")  # the CHECK on chunks.cite_id
LTREE = re.compile(r"^[A-Za-z0-9_]+(\.[A-Za-z0-9_]+)*$")


def squash(s: str) -> str:
    return re.sub(r"\s+", "", s)


@pytest.mark.parametrize("name,folder,country,scope,code,lang", DOCS, ids=[d[0] for d in DOCS])
def test_real_document_invariants(
    name: str, folder: str, country: str, scope: str, code: str, lang: str
) -> None:
    path = ROOT / folder / f"{name}.txt"
    if not path.exists():
        pytest.skip(f"{path} not downloaded")
    raw = path.read_text(encoding="utf-8")
    if country == "US":
        src = normalize_us_text(raw)
        nodes = parse_us(src)
    else:
        src = normalize_text(raw)
        nodes = parse_mx(src)
    meta = DocMeta(
        country=country,
        scope=scope,
        code=code,
        title=name,
        version_label="1",
        language=lang,  # type: ignore[arg-type]
    )
    chunks = Chunker(ApproxCounter()).chunk(src, nodes, meta)
    by = {n.path: n for n in nodes}

    # tree: valid ltree, unique paths, parents exist, spans nested and inside the source
    assert len({n.path for n in nodes}) == len(nodes)
    for n in nodes:
        assert LTREE.match(n.path), n.path
        assert n.parent is None or n.parent in by
        assert 0 <= n.start <= n.own_end <= n.end <= len(src)

    # chunks: exact slices, ordered, no overlap, never crossing a node, ids valid for the DB
    assert chunks
    ids = [c.cite_id for c in chunks]
    assert len(ids) == len(set(ids))
    spans = sorted((c.char_start, c.char_end) for c in chunks)
    assert all(a[1] <= b[0] for a, b in itertools.pairwise(spans)), "overlapping chunks"
    for c in chunks:
        assert src[c.char_start : c.char_end] == c.text and c.text.strip()
        assert CITE_DB.match(c.cite_id) and len(c.cite_id) <= 200, c.cite_id
        assert parse_cite(c.cite_id), f"unparseable: {c.cite_id}"
        n = by[c.node_path]
        assert n.start <= c.char_start and c.char_end <= n.end

    # coverage: the concatenated chunks hold every non-heading character, once
    covered = squash("".join(c.text for c in sorted(chunks, key=lambda c: c.char_start)))
    expected = squash(
        "".join(
            src[n.start : n.own_end]
            for n in nodes
            if n.node_type not in {"libro", "titulo", "capitulo", "seccion"}
        )
    )
    assert covered == expected


def test_known_articles_exist_with_the_expected_text() -> None:
    lft = ROOT / "text" / "LFT.txt"
    if not lft.exists():
        pytest.skip("LFT not downloaded")
    src = normalize_text(lft.read_text(encoding="utf-8"))
    nodes = parse_mx(src)
    meta = DocMeta(country="MX", scope="FED", code="LFT", title="LFT", version_label="1")
    chunks = {c.cite_id: c for c in Chunker(ApproxCounter()).chunk(src, nodes, meta)}
    fx = chunks["MX-FED-LFT-ART47-FRACX-v1"]
    assert "faltas de asistencia" in fx.text and fx.is_current
    labels = [n.label for n in nodes if n.node_type == "articulo"]
    assert {"Artículo 1", "Artículo 47", "Artículo 123", "Artículo 1010"} <= set(labels)
    assert any(not n.is_current and n.label == "Artículo 611" for n in nodes)  # derogated, kept


def test_ambiguous_ids_still_parse() -> None:
    from app.ingestion.chunker import _disambiguate

    assert parse_cite(_disambiguate("MX-FED-LFT-ART47-v2025.06", 2)) is not None
    assert _disambiguate("MX-FED-LFT-ART47-v2025.06", 2) == "MX-FED-LFT-ART47-DUP2-v2025.06"
    assert parse_cite(_disambiguate("CF-EXP1-ART47", 3)) is not None
