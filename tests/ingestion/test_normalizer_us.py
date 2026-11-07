"""US (California code dump) grammar: shapes observed in the real Labor Code extract."""

from __future__ import annotations

import re

from app.ingestion.chunker import Chunker, parse_cite, report
from app.ingestion.model import DocMeta
from app.ingestion.normalizer_us import normalize_us_text, parse
from app.ingestion.tokens import ApproxCounter

META = DocMeta(
    country="US",
    scope="CA",
    code="LAB",
    title="Cal. Lab. Code",
    version_label="2025",
    language="en",
)
FILL = " ".join(["employer"] * 60)

SAMPLE = f"""Division 2 Title - Part 3 Chapter 5 Article -

§ 1102.5. (a)An employer shall not retaliate. {FILL}
(b)An employer, or any person acting on behalf of the
\t\t\t  employer, shall not retaliate further. {FILL}
(c)(1)Penalty applies. {FILL}
(2)Another rule. {FILL}
(A)First kind. {FILL}
(B)Second kind:
(i)Sub kind one. {FILL}
(ii)Sub kind two. {FILL}
(d)Short.

§ 98.6. A one-line section without subdivisions.

§ 12. (a)Repealed by Stats. 2010.
"""


def run(text: str = SAMPLE):  # type: ignore[no-untyped-def]
    src = normalize_us_text(text)
    nodes = parse(src)
    return src, nodes, Chunker(ApproxCounter()).chunk(src, nodes, META)


def test_tree_shapes() -> None:
    src, nodes, _ = run()
    by = {n.path: n for n in nodes}
    types = [n.node_type for n in nodes]
    assert types[:4] == ["libro", "capitulo", "seccion", "articulo"]
    sec = next(n for n in nodes if n.label == "§ 1102.5")
    # the first subdivision starts on the same line as the section number
    assert src[sec.start : sec.own_end].strip() == "§ 1102.5."
    labels = [n.label for n in nodes if n.parent == sec.path]
    assert labels == ["(a)", "(b)", "(c)", "(d)"]
    c = next(n for n in nodes if n.parent == sec.path and n.label == "(c)")
    assert [n.label for n in nodes if n.parent == c.path] == ["(1)", "(2)"]
    two = next(n for n in nodes if n.parent == c.path and n.label == "(2)")
    kids = [(n.label, n.node_type) for n in nodes if n.parent == two.path]
    assert kids == [("(A)", "parrafo"), ("(B)", "parrafo")]
    b = next(n for n in nodes if n.parent == two.path and n.label == "(B)")
    assert [n.label for n in nodes if n.parent == b.path] == ["(i)", "(ii)"]
    assert by[sec.parent or ""].node_type == "seccion"


def test_soft_wrapped_lines_are_joined_and_text_is_an_exact_slice() -> None:
    src, _, chunks = run()
    assert "\t" not in src
    for c in chunks:
        assert src[c.char_start : c.char_end] == c.text
    b = next(c for c in chunks if c.cite_id.endswith("SEC1102P5-SUBB-v2025"))
    assert "on behalf of the employer, shall not retaliate further" in b.text


def test_ids_citations_and_vigencia() -> None:
    _, _, chunks = run()
    ids = [c.cite_id for c in chunks]
    assert len(ids) == len(set(ids)) and all(parse_cite(i) for i in ids)
    by = {c.cite_id: c for c in chunks}
    assert by["US-CA-LAB-SEC1102P5-SUBA-v2025"].citation_string == "Cal. Lab. Code, § 1102.5(a)"
    deep = next(c for c in chunks if "SUBC-SUB2-SUBUB-SUBRI" in c.cite_id)
    assert deep.citation_string == "Cal. Lab. Code, § 1102.5(c)(2)(B)(i)"
    assert by["US-CA-LAB-SEC12-v2025"].is_current is False  # "Repealed ..."


def test_coverage_without_loss_or_duplication() -> None:
    src, nodes, chunks = run()
    covered = re.sub(
        r"\s+", "", "".join(c.text for c in sorted(chunks, key=lambda c: c.char_start))
    )
    expected = re.sub(
        r"\s+",
        "",
        "".join(
            src[n.start : n.own_end]
            for n in nodes
            if n.node_type not in {"libro", "titulo", "capitulo", "seccion"}
        ),
    )
    assert covered == expected


def test_ambiguous_numbering_is_disambiguated_not_fatal() -> None:
    text = f"§ 7. (a)One. {FILL}\n(A)Upper letter I-like. {FILL}\n(A)Same marker again. {FILL}\n"
    _, _, chunks = run(text)
    ids = [c.cite_id for c in chunks]
    assert len(ids) == len(set(ids))
    assert any(c.ambiguous for c in chunks) and report(chunks)["duplicate_ids"]
