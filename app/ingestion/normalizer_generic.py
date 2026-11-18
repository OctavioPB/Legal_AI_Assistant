"""Grammar for documents that are not statutes (ADR-025): contracts, briefs, rulings, memos.

A statute has a legal tree; these have headings and paragraphs. The same closed node-type set is
reused (no schema change): a SECTION of the document (a clause, a numbered heading, a block of
loose paragraphs) is typed `articulo`, so it is the context unit of parent expansion exactly like
an article, and each paragraph inside it is a `parrafo`, the retrieval unit. Nothing is inferred
about what the text means: the rules below only look at the shape of a line.

A section opens at
* a clause opener: "CLÁUSULA TERCERA", "PRIMERA.-", "DÉCIMA SEGUNDA.", "Artículo 5", "ARTICLE 2",
  "Section 4.1" (the clause's text may continue on the same line);
* a heading line: short, alone in its block, without final punctuation, and either mostly upper
  case ("HECHOS", "DECLARACIONES") or numbered ("3. Vigencia", "IV. CONSIDERANDOS").
Loose paragraphs before any opener, or under none, are grouped into "Parte n" sections of bounded
size so a section stays a useful context unit. Deterministic: same text, same tree.
"""

from __future__ import annotations

import re

from app.ingestion.model import Node
from app.ingestion.normalizer_mx import normalize_text

SECTION_CHARS = 3000  # loose paragraphs are grouped into sections of about this size
PARAGRAPH_CHARS = 4000  # a longer block is cut at sentence ends (a PDF without line breaks)
LABEL_CHARS = 80
TITLE_CHARS = 60  # a clause title longer than this is a sentence, not a title

_ORDINAL = (
    r"(?:PRIMER[AO]?|SEGUND[AO]|TERCER[AO]?|CUART[AO]|QUINT[AO]|SEXT[AO]|S[EÉ]PTIM[AO]|OCTAV[AO]|"
    r"NOVEN[AO]|D[EÉ]CIM[AO]|UND[EÉ]CIM[AO]|DUOD[EÉ]CIM[AO]|VIG[EÉ]SIM[AO]|TRIG[EÉ]SIM[AO]|"
    r"CUADRAG[EÉ]SIM[AO]|[UÚ]NIC[AO])"
)
_OPENER = re.compile(
    rf"^(?:(?:CL[AÁ]USULA|Cl[aá]usula)\s+\S+|{_ORDINAL}(?:\s+{_ORDINAL})?\s*[.:\-–—]|"
    r"(?:ART[IÍ]CULO|Art[ií]culo|ARTICLE|Article|SECTION|Section|CLAUSE|Clause)\s+\d[\w.]*)"
)
_NUMBERED = re.compile(r"^(?:\d{1,3}(?:\.\d{1,3})*|[IVXLC]{1,7})[.)\-–—]\s+\S")
_SENTENCE_END = re.compile(r"(?<=[.;:?!])\s+")
_BLOCK = re.compile(r"\S(?:.|\n(?!\s*\n))*")


def normalize_generic_text(raw: str) -> str:
    """The shared normalization, plus markdown heading marks dropped (Docling writes '## ')."""
    return re.sub(r"(?m)^#{1,6}[ \t]+", "", normalize_text(raw))


def _is_heading(block: str) -> bool:
    if "\n" in block or len(block) > LABEL_CHARS or block[-1] in ".;,":
        return False
    letters = [c for c in block if c.isalpha()]
    if len(letters) < 3:
        return False
    upper = sum(c.isupper() for c in letters) / len(letters)
    return upper >= 0.8 or bool(_NUMBERED.match(block))


def _label(block: str) -> str:
    """The name a citation shows. A clause is named by its opener and its own title
    ("PRIMERA.- OBJETO"), never by the sentence that follows."""
    first = " ".join(block.split("\n", 1)[0].split())
    m = _OPENER.match(first)
    if m:
        head = first[: m.end()]
        rest = first[m.end() :]
        lead = len(rest) - len(rest.lstrip(" .:-–—"))
        stop = rest.find(". ", lead)
        if stop == -1 and len(rest) <= TITLE_CHARS:
            stop = len(rest)
        first = first[: m.end() + stop] if lead < stop <= TITLE_CHARS else head
    elif len(first) > LABEL_CHARS:
        first = first[:LABEL_CHARS].rsplit(" ", 1)[0] + "…"
    return first.rstrip(" .:-–—") or first


def _pieces(src: str, start: int, end: int) -> list[tuple[int, int]]:
    """A block as one span, or cut at sentence ends when it is longer than PARAGRAPH_CHARS."""
    if end - start <= PARAGRAPH_CHARS:
        return [(start, end)]
    out: list[tuple[int, int]] = []
    cur = start
    prev: tuple[int, int] | None = None  # the last sentence end seen: (end of text, next start)
    for m in _SENTENCE_END.finditer(src, start, end):
        if m.start() - cur > PARAGRAPH_CHARS and prev is not None and prev[0] > cur:
            out.append((cur, prev[0]))
            cur = prev[1]
        prev = (m.start(), m.end())
    if prev is not None and end - cur > PARAGRAPH_CHARS and prev[0] > cur:
        out.append((cur, prev[0]))
        cur = prev[1]
    out.append((cur, end))
    return [(s, e) for s, e in out if src[s:e].strip()]


def parse(src: str) -> list[Node]:
    blocks = [(m.start(), m.end()) for m in _BLOCK.finditer(src)]
    # sections: (label | None, [paragraph spans]); None = loose text, labelled "Parte n" below
    sections: list[tuple[str | None, list[tuple[int, int]]]] = []
    for s, e in blocks:
        e = s + len(src[s:e].rstrip())
        block = src[s:e]
        opens = bool(_OPENER.match(block)) or _is_heading(block)
        if opens:
            sections.append((_label(block), _pieces(src, s, e)))
            continue
        if not sections:
            sections.append((None, []))
        label, spans = sections[-1]
        size = spans[-1][1] - spans[0][0] if spans else 0
        if label is None and spans and size + (e - s) > SECTION_CHARS:
            sections.append((None, []))
            spans = sections[-1][1]
        spans.extend(_pieces(src, s, e))

    out: list[Node] = []
    loose = 0
    for i, (label, spans) in enumerate(sections, 1):
        if not spans:
            continue
        if label is None:
            loose += 1
            label = f"Parte {loose}"
        elif len(spans) > 1 and spans[0][1] - spans[0][0] <= LABEL_CHARS:
            # a bare heading travels with the paragraph it introduces (alone it is noise)
            spans = [(spans[0][0], spans[1][1]), *spans[2:]]
        path = f"d.s{i}"
        start, end = spans[0][0], spans[-1][1]
        single = len(spans) == 1
        out.append(
            Node(
                path=path,
                parent=None,
                node_type="articulo",
                label=label,
                token=f"S{i}",
                ordinal=i - 1,
                start=start,
                end=end,
                own_end=end if single else start,
            )
        )
        if single:
            continue
        for k, (s, e) in enumerate(spans, 1):
            out.append(
                Node(
                    path=f"{path}.p{k}",
                    parent=path,
                    node_type="parrafo",
                    label=f"párr. {k}",
                    token=f"P{k}",
                    ordinal=k - 1,
                    start=s,
                    end=e,
                    own_end=e,
                )
            )
    return out
