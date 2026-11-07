"""US statute grammar (WS2.T04): the California code dump format -> the same typed node tree.

Input shape (extracted from the official leginfo bulk data, see data/corpus/MANIFEST.md):

    Division 2 Title - Part 3 Chapter 5 Article -
    § 1102.5. (a)An employer ... (b)An employer ...      <- subdivisions may start mid-line
    (c)(1)An employer ...

Mapping onto the closed node-type set (no schema change): Division->libro, Title->titulo,
Part->capitulo, Chapter(+Article)->seccion, § section->articulo, (a)->fraccion, (1)->inciso,
(A)/(i)->parrafo. Case law (holding/syllabus) is not covered here: no case-law source is in the
demo corpus yet (PLAN WS2.T04 stays [~]).
"""

from __future__ import annotations

import re

from app.ingestion.model import Node, NodeType
from app.ingestion.normalizer_mx import _freeze, _ident, _live, _Open, normalize_text

_HEADING = re.compile(
    r"^Division\s+(?P<d>\S+)\s+Title\s+(?P<t>\S+)\s+Part\s+(?P<p>\S+)\s+Chapter\s+(?P<c>\S+)"
    r"\s+Article\s+(?P<a>\S+)$"
)
_SECTION = re.compile(r"^§\s*(?P<num>\d+(?:\.\d+)*[A-Za-z]?)\.?\s*(?P<rest>.*)$")
_SUB = re.compile(r"^\((?P<tok>[A-Za-z]{1,4}|\d{1,3})\)")
_ROMAN_LOWER = {"i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x", "xi", "xii"}
_DEROGA = re.compile(r"\b(?:repealed|inoperative)\b", re.IGNORECASE)

# subdivision depth: 6 (a) / 7 (1) / 8 (A) / 9 (i)
_SUB_TYPE: dict[int, NodeType] = {6: "fraccion", 7: "inciso", 8: "parrafo", 9: "parrafo"}
# cite-token prefix per depth, so (I) uppercase and (i) roman never collide under one parent
_SUB_PREFIX = {6: "SUB", 7: "SUB", 8: "SUBU", 9: "SUBR"}


def normalize_us_text(raw: str) -> str:
    """Generic normalization + joining of soft-wrapped lines (newline followed by indentation)."""
    return re.sub(r"\n[ \t]+", " ", normalize_text(raw))


def _sub_level(tok: str, top_level: int) -> int | None:
    """Depth of a "(x)" marker given the depth of the node currently open; None = not a marker."""
    if tok.isdigit():
        return 7
    if tok.isupper() and tok.isalpha() and len(tok) == 1:
        return 8
    if tok.islower() and tok.isalpha():
        if tok in _ROMAN_LOWER and top_level >= 8:  # (i) inside an (A) is a roman, else a letter
            return 9
        return 6 if len(tok) == 1 else None
    return None


def parse(src: str) -> list[Node]:
    roots: list[_Open] = []
    stack: list[_Open] = []
    level: dict[int, int] = {}
    chain: tuple[str | None, ...] = ()

    def add(node: _Open, lvl: int) -> None:
        while stack and level[id(stack[-1])] >= lvl:
            stack.pop()
        node.parent = stack[-1] if stack else None
        (node.parent.children if node.parent else roots).append(node)
        level[id(node)] = lvl
        stack.append(node)

    def new(ntype: NodeType, label: str, token: str, start: int, end: int, lvl: int) -> _Open:
        n = _Open(ntype, label, token, ntype[0], start)
        n.end = n.own_end = end
        add(n, lvl)
        n.touch(end, own=True)
        return n

    def feed(text: str, start: int) -> None:
        """One logical line starting at absolute offset `start` (recurses on a mid-line rest)."""
        nonlocal chain
        end = start + len(text)
        s = text.strip()
        if not s:
            return
        m = _HEADING.match(s)
        if m:
            vals = [None if m[k] == "-" else m[k].rstrip(".") for k in "dtpca"]
            new_chain = (
                vals[0],
                vals[1],
                vals[2],
                f"{vals[3]}|{vals[4]}" if vals[3] or vals[4] else None,
            )
            names = ("Division", "Title", "Part", "Chapter")
            types: tuple[NodeType, ...] = ("libro", "titulo", "capitulo", "seccion")
            first = next((i for i in range(4) if i >= len(chain) or chain[i] != new_chain[i]), 4)
            # A heading line always ends the open section, even when it repeats the previous
            # chain and so creates no node (otherwise later subdivisions would hang from it).
            while stack and level[id(stack[-1])] >= 5:
                stack.pop()
            for i in range(first, 4):
                v = new_chain[i]
                if v is None:
                    continue
                if i == 3:
                    ch, art = v.split("|")
                    label = "Chapter " + ch if ch != "None" else ""
                    if art != "None":
                        label = (label + " Article " + art).strip()
                    token = "C" + _ident(
                        (ch if ch != "None" else "") + "A" + (art if art != "None" else "")
                    )
                else:
                    label = f"{names[i]} {v}"
                    token = names[i][0] + _ident(v)
                new(types[i], label, token.upper(), start, end, i + 1)
            chain = new_chain
            return
        m = _SECTION.match(s)
        if m:
            num = m["num"]
            lead = len(text) - len(text.lstrip())
            node = new(
                "articulo", f"§ {num}", "SEC" + _ident(num.replace(".", "P")).upper(),
                start + lead, start + lead + len(s[: len(s) - len(m["rest"])].rstrip()), 5,
            )  # fmt: skip
            rest = m["rest"]
            if rest:
                rest_off = start + lead + (len(s) - len(rest))
                if _SUB.match(rest):
                    feed(rest, rest_off)  # first subdivision shares the heading line
                else:
                    node.touch(rest_off + len(rest), own=True)
                    node.has_text = _live(rest)
                    if _DEROGA.search(rest):
                        node.current = False
            return
        top = stack[-1] if stack else None
        sm = _SUB.match(s)
        if sm and top is not None and level[id(top)] >= 5:
            tok = sm["tok"]
            lvl = _sub_level(tok, level[id(top)])
            if lvl is not None:
                lead = len(text) - len(text.lstrip())
                label = f"({tok})"
                # a marker directly followed by another "(x)" opens both ("(c)(1)Any ...")
                inner = s[len(sm[0]) :]
                node = new(
                    _SUB_TYPE[lvl], label, _SUB_PREFIX[lvl] + _ident(tok).upper(), start + lead,
                    start + lead + len(sm[0]), lvl,
                )  # fmt: skip
                if inner:
                    off = start + lead + len(sm[0])
                    if _SUB.match(inner):
                        feed(inner, off)
                    else:
                        node.touch(off + len(inner), own=True)
                        node.has_text = _live(inner)
                        if _DEROGA.search(inner):
                            node.current = False
                return
        # loose continuation text belongs to whatever is open
        if top is not None and level[id(top)] >= 5:
            top.touch(end, own=True)
            top.has_text = top.has_text or _live(s)
            if _DEROGA.search(s):
                top.current = False
        else:
            p = new("parrafo", "Paragraph", "PARR", start, end, 6)
            p.flagged = True

    pos = 0
    for line in src.split("\n"):
        feed(line, pos)
        pos += len(line) + 1
    out: list[Node] = []
    _freeze(roots, None, "d", out)
    return out
