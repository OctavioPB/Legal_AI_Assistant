"""Chunker + MX normalizer properties and pathological fixtures (WS2.T03/T05/T09)."""

from __future__ import annotations

import itertools
import re

import pytest

from app.ingestion.chunker import CHUNKER_VERSION, Chunker, parse_cite, report
from app.ingestion.model import ChunkThresholds, DocMeta, Node
from app.ingestion.normalizer_mx import normalize_text, parse
from app.ingestion.tokens import ApproxCounter
from tests.ingestion.fixtures import FIXTURES

META = DocMeta(country="MX", scope="FED", code="LFT", title="Ley X", version_label="2025.06")


def run(name: str) -> tuple[str, list[Node], list]:  # type: ignore[type-arg]
    src = normalize_text(FIXTURES[name])
    nodes = parse(src)
    return src, nodes, Chunker(ApproxCounter()).chunk(src, nodes, META)


def squash(s: str) -> str:
    return re.sub(r"\s+", "", s)


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_properties_hold_for_every_fixture(name: str) -> None:
    src, nodes, chunks = run(name)
    assert chunks, name
    # text is an exact slice; offsets ordered and non-overlapping
    for c in chunks:
        assert src[c.char_start : c.char_end] == c.text
        assert c.breadcrumb not in c.text
    spans = sorted((c.char_start, c.char_end) for c in chunks)
    assert all(a[1] <= b[0] for a, b in itertools.pairwise(spans)), "overlap"
    # cite ids unique and parseable
    ids = [c.cite_id for c in chunks]
    assert len(ids) == len(set(ids)), "duplicate cite_id"
    assert all(parse_cite(i) for i in ids)
    # no chunk crosses a node: its span lies inside its node's span
    by_path = {n.path: n for n in nodes}
    for c in chunks:
        n = by_path[c.node_path]
        assert n.start <= c.char_start and c.char_end <= n.end
    # coverage: concatenated chunk text == all non-heading text, no loss, no duplication
    covered = squash("".join(c.text for c in sorted(chunks, key=lambda c: c.char_start)))
    expected = squash(
        "".join(
            src[n.start : n.own_end]
            for n in nodes
            if n.node_type not in {"libro", "titulo", "capitulo", "seccion"}
        )
    )
    assert covered == expected, name


def test_version_constant_and_thresholds_stored_shape() -> None:
    assert CHUNKER_VERSION == 3  # ADR-049, ADR-052
    with pytest.raises(ValueError):
        ChunkThresholds(max_tokens=800)  # > 512 window (ADR-012)


def test_bis_ordinal_and_suffix_articles() -> None:
    _, nodes, chunks = run("bis_suffix_ordinal")
    assert [n.label for n in nodes] == ["Artículo 47 Bis", "Artículo 123 A", "Artículo 5 Ter"]
    assert [c.cite_id for c in chunks] == [
        "MX-FED-LFT-ART47BIS-v2025.06",
        "MX-FED-LFT-ART123A-v2025.06",
        "MX-FED-LFT-ART5TER-v2025.06",
    ]


def test_derogated_is_kept_not_current() -> None:
    _, nodes, chunks = run("derogated")
    assert [(n.label, n.is_current) for n in nodes] == [("Artículo 8", False), ("Artículo 9", True)]
    assert [c.is_current for c in chunks] == [False, True]


def test_transitorios_ids_do_not_collide_across_decrees() -> None:
    _, _, chunks = run("two_decrees_transitorios")
    ids = [c.cite_id for c in chunks if "TRANS" in c.cite_id]
    assert ids == ["MX-FED-LFT-TRANS1-PRIMERO-v2025.06", "MX-FED-LFT-TRANS2-PRIMERO-v2025.06"]
    _, _, chunks = run("transitorios")
    assert any(c.cite_id.endswith("TRANS1-DECIMOPRIMERO-v2025.06") for c in chunks)


def test_duplicate_headings_across_books_get_distinct_paths() -> None:
    _, nodes, _ = run("duplicate_headings")
    paths = [n.path for n in nodes]
    assert len(paths) == len(set(paths))
    assert sum(n.node_type == "capitulo" for n in nodes) == 2


def test_fracciones_stay_separate_when_long_and_merge_up_when_short() -> None:
    _, _, chunks = run("nested_inciso")
    assert any(c.cite_id.endswith("ART20-FRACI-INCA-v2025.06") for c in chunks)
    _, _, short = run("short_fracciones_merge_up")
    assert [c.cite_id for c in short] == ["MX-FED-LFT-ART40-v2025.06"]


def test_oversized_article_splits_by_paragraph_and_never_exceeds_max() -> None:
    _, _, chunks = run("oversized_article_splits")
    assert len(chunks) > 1
    assert all(c.cite_id.count("-P") == 1 for c in chunks)
    assert not any(c.oversized for c in chunks)
    assert not any(c.exceeds_window for c in chunks)


def test_single_giant_paragraph_is_reported_not_cut() -> None:
    src = normalize_text("Artículo 1.- " + " ".join(["x"] * 2000))
    nodes = parse(src)
    chunks = Chunker(ApproxCounter()).chunk(src, nodes, META)
    assert len(chunks) == 1 and chunks[0].oversized and chunks[0].exceeds_window
    assert report(chunks)["oversized"] == [chunks[0].cite_id]


def test_navigation_is_flagged() -> None:
    _, nodes, chunks = run("navigation_index")
    nav = [c for c in chunks if c.is_navigation]
    assert len(nav) == 1 and "Artículo 2 ........" in nav[0].text
    assert sum(n.node_type == "articulo" for n in nodes) == 2  # the index created no articles


def test_empty_node_and_unclosed_quote_do_not_break_the_tree() -> None:
    _, nodes, _ = run("empty_node")
    assert [n.label for n in nodes] == ["Artículo 3", "Artículo 4"]
    _, nodes, _ = run("unclosed_quote")
    assert [n.label for n in nodes] == ["Artículo 6", "Artículo 7"]


def test_braces_markup_and_fake_cites_survive_verbatim() -> None:
    src, _, chunks = run("braces_and_markup")
    assert "{llaves}" in chunks[0].text and "[cite: X]" in chunks[0].text
    assert chunks[0].text == src.strip()


def test_deterministic() -> None:
    assert run("transitorios")[2] == run("transitorios")[2]


def test_counter_mismatch_with_stored_thresholds_is_refused() -> None:
    with pytest.raises(ValueError):
        Chunker(ApproxCounter(), ChunkThresholds(counter="cohere-tokenizer"))


def labels(name: str) -> list[str]:
    return [n.label for n in run(name)[1]]


def test_state_code_forms_annotation_prefix_abbreviation_and_bis_roman() -> None:
    _, nodes, chunks = run("lead_annotation_abbrev")
    assert [n.label for n in nodes] == [
        "Artículo 22",
        "Artículo 22 Bis",
        "Artículo 21 Bis I",
        "Artículo 1",
    ]
    assert nodes[0].reform_notes and "REFORMADO" in nodes[0].reform_notes[0]
    assert "ART21BISI" in chunks[2].cite_id


def test_ordinal_o_nuance_n_tilde_and_compound_suffix() -> None:
    assert labels("ordinal_o_bis_and_n_tilde") == [
        "Artículo 3 Bis", "Artículo 353 Ñ", "Artículo 153 F Bis",
    ]  # fmt: skip


def test_fraccion_letter_suffix_gives_distinct_nodes() -> None:
    assert labels("fraccion_with_letter_suffix") == [
        "Artículo 73", "Fracción XXIX", "Fracción XXIX A", "Fracción XXIX Ñ", "Fracción XXX",
    ]  # fmt: skip


def test_apartados_nest_fracciones_and_ids_are_unique() -> None:
    _, nodes, chunks = run("apartados")
    assert [n.label for n in nodes if n.label.startswith("Apartado")] == [
        "Apartado A", "Apartado B", "Apartado C",
    ]  # fmt: skip
    ids = {c.cite_id for c in chunks}
    assert "MX-FED-LFT-ART123-APARTADOA-FRACI-v2025.06" in ids
    assert "MX-FED-LFT-ART123-APARTADOB-FRACI-v2025.06" in ids
    assert not any(c.ambiguous for c in chunks)


def test_restarted_numbering_opens_a_new_parrafo_not_a_duplicate_id() -> None:
    _, _nodes, chunks = run("second_list_restarts_numbering")
    assert not any(c.ambiguous for c in chunks)
    ids = [c.cite_id for c in chunks]
    assert any("ART139-FRACI-" in i for i in ids) and any("ART139-PARR1-FRACI-" in i for i in ids)
    lead_in = next(c for c in chunks if "Las sanciones se aumentarán" in c.text)
    assert "FRACII" not in lead_in.cite_id.split("PARR1")[0]  # not glued to the first list


def test_annotation_starting_with_titulo_is_not_a_heading() -> None:
    assert [n.node_type for n in run("annotation_is_not_a_heading")[1]][:3] == [
        "titulo",
        "capitulo",
        "articulo",
    ]
