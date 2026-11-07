"""Mexican legal grammar (WS2.T03): normalized text -> typed node tree.

Docling gives typography and reading order; this module owns the legal semantics (ADR-004).
Input is plain text (what the Docling wrapper will emit); every span indexes the normalized text
returned by `normalize_text`, so chunk `text` can be an exact slice of it.
Unparseable material is never guessed: loose text under a heading becomes a `parrafo` node.
"""

from __future__ import annotations

import re
import unicodedata

from app.ingestion.model import HEADING_TYPES, Node, NodeType

_RANK: dict[str, int] = {
    "libro": 1, "titulo": 2, "capitulo": 3, "seccion": 4,
    "articulo": 5, "transitorio": 5, "fraccion": 6, "inciso": 7,
}  # fmt: skip

_ORD = (
    r"(?:(?:D[eé]cimo|Vig[eé]simo)\s+)?(?:Primero|Segundo|Tercero|Cuarto|Quinto|Sexto|S[eé]ptimo"
    r"|Octavo|Noveno)|D[eé]cimo|Vig[eé]simo|[UÚ]nico"
)
_ROMAN = r"(?=[IVXLC])M{0,3}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})"

_HEADING = re.compile(
    r"^(?P<kind>LIBRO|T[IÍ]TULO|CAP[IÍ]TULO|SECCI[OÓ]N)\s+(?P<id>[^\s.]+)\b.*$", re.IGNORECASE
)
_ARTICLE = re.compile(
    r"^Art[ií]culo\s+(?P<num>\d+)(?:\s*[º°o]\b)?(?:[\s-]+(?P<suf>Bis|Ter|Qu[aá]ter|Quintus|Sexies"
    r"|[A-Z])\b)?\s*[.\-–—]+\s*(?P<rest>.*)$",
    re.IGNORECASE,
)
_TRANS_HEAD = re.compile(r"^TRANSITORIOS?\s*:?\s*$", re.IGNORECASE)
_TRANS_ITEM = re.compile(rf"^(?:Art[ií]culo\s+)?(?P<ord>{_ORD})\s*[.\-–—]+\s*(?P<rest>.*)$", re.I)
_FRACCION = re.compile(rf"^(?P<num>{_ROMAN})\s*[.\-–—)]+\s*(?P<rest>.*)$")
_INCISO = re.compile(r"^(?P<l>[a-z])\)\s*(?P<rest>.*)$")
_NAV_TITLE = re.compile(r"^(?:[IÍ]NDICE|TABLA DE CONTENIDOS?|CONTENIDO)\s*$", re.IGNORECASE)
_NAV_LINE = re.compile(r"(?:\.{3,}|\s{2,})\s*\d+\s*$|\.{4,}")
_DEROGA = re.compile(r"\(\s*se\s+deroga\s*\)|^\s*\(?derogad[oa]\)?\.?\s*$", re.IGNORECASE)
_REFORM = re.compile(r"\((?:Reformad[oa]|Adicionad[oa]|Derogad[oa]|F\. de E\.)[^)]*\)", re.I)


def normalize_text(raw: str) -> str:
    """NFC, LF newlines, no form feeds / trailing spaces, <= 1 consecutive blank line."""
    t = unicodedata.normalize("NFC", raw).replace("\r\n", "\n").replace("\r", "\n")
    t = t.replace("\f", "\n").replace(" ", " ")
    t = "\n".join(line.rstrip() for line in t.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", t).strip("\n")


def _ident(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]", "", s)


class _Open:
    """Mutable builder state; frozen into `Node` at the end."""

    def __init__(self, ntype: NodeType, label: str, token: str, prefix: str, start: int) -> None:
        self.ntype, self.label, self.token, self.prefix = ntype, label, token, prefix
        self.start, self.end, self.own_end = start, start, start
        self.parent: _Open | None = None
        self.children: list[_Open] = []
        self.current = True
        self.nav = False
        self.flagged = False
        self.notes: list[str] = []
        self.path = ""

    def touch(self, end: int, own: bool) -> None:
        node: _Open | None = self
        while node is not None:
            node.end = max(node.end, end)
            node = node.parent
        if own and not self.children:
            self.own_end = max(self.own_end, end)


def parse(src: str) -> list[Node]:
    """Parse NORMALIZED text (see `normalize_text`). Returns nodes in document order."""
    roots: list[_Open] = []
    stack: list[_Open] = []
    in_trans = False
    n_trans = 0
    nav: _Open | None = None
    pos = 0
    lines = src.split("\n")
    i = 0
    blank_before = True

    def add(node: _Open, rank: int) -> _Open:
        nonlocal stack
        while stack and _RANK.get(stack[-1].ntype, 99) >= rank:
            stack.pop()
        node.parent = stack[-1] if stack else None
        (node.parent.children if node.parent else roots).append(node)
        stack.append(node)
        node.touch(node.end, own=True)
        return node

    def body_target() -> _Open | None:
        """Where loose text goes; None => open an implicit `parrafo` under the headings."""
        if stack and stack[-1].ntype not in HEADING_TYPES:
            return stack[-1]
        return None

    while i < len(lines):
        line = lines[i]
        start = pos
        end = pos + len(line)
        pos = end + 1
        stripped = line.strip()
        i += 1
        if not stripped:
            blank_before = True
            continue
        was_blank = blank_before
        blank_before = False

        # --- navigation (índice / tabla de contenidos): one flagged node, never structure ------
        if _NAV_TITLE.match(stripped) or (nav is not None and _NAV_LINE.search(stripped)):
            if nav is None or _NAV_TITLE.match(stripped):
                nav = _Open("parrafo", "Índice", "NAV", "n", start)
                nav.nav = True
                stack = []
                roots.append(nav)
            nav.touch(end, own=True)
            continue
        if _NAV_LINE.search(stripped) and (_HEADING.match(stripped) or _ARTICLE.match(stripped)):
            if nav is None:
                nav = _Open("parrafo", "Índice", "NAV", "n", start)
                nav.nav = True
                stack = []
                roots.append(nav)
            nav.touch(end, own=True)
            continue
        nav = None

        m = _HEADING.match(stripped)
        if m:
            kind = m["kind"].lower().replace("í", "i").replace("ó", "o")
            ntype: NodeType = kind  # type: ignore[assignment]
            label = stripped
            # title on the next line: non-blank, no lowercase letters, not itself structure
            if i < len(lines):
                nxt = lines[i].strip()
                if (
                    nxt
                    and nxt == nxt.upper()
                    and not _HEADING.match(nxt)
                    and not _TRANS_HEAD.match(nxt)
                ):
                    label = f"{stripped} {nxt}"
                    pos += len(lines[i]) + 1
                    end = pos - 1
                    i += 1
            node = _Open(
                ntype, label, f"{ntype[0].upper()}{_ident(m['id']).upper()}", ntype[0], start
            )
            node.end = node.own_end = end
            in_trans = False
            add(node, _RANK[ntype])
            node.touch(end, own=True)
            continue

        if _TRANS_HEAD.match(stripped):
            n_trans += 1
            in_trans = True
            node = _Open("seccion", "TRANSITORIOS", f"TRANS{n_trans}", "s", start)
            node.end = node.own_end = end
            add(node, _RANK["seccion"] - 3)  # closes everything: a decree's transitorios end it
            node.touch(end, own=True)
            continue

        m = _TRANS_ITEM.match(stripped) if in_trans else None
        if m:
            ordw = _ident(m["ord"]).upper()
            label = f"Artículo {m['ord'].title()} transitorio"
            node = _Open("transitorio", label, ordw, "tr", start)
            node.end = node.own_end = end
            add(node, _RANK["transitorio"])
            node.touch(end, own=True)
            _flags(node, stripped)
            continue

        m = _ARTICLE.match(stripped) if not in_trans else None
        if m:
            suf = (
                (m["suf"] or "").title()
                if (m["suf"] or "").lower()
                in {"bis", "ter", "quáter", "quater", "quintus", "sexies"}
                else (m["suf"] or "").upper()
            )
            label = f"Artículo {m['num']}" + (f" {suf}" if suf else "")
            node = _Open("articulo", label, f"ART{m['num']}{_ident(suf).upper()}", "a", start)
            node.end = node.own_end = end
            add(node, _RANK["articulo"])
            node.touch(end, own=True)
            _flags(node, stripped)
            continue

        top = stack[-1] if stack else None
        m = _FRACCION.match(stripped)
        if m and top is not None and any(s.ntype in ("articulo", "transitorio") for s in stack):
            node = _Open("fraccion", f"Fracción {m['num']}", f"FRAC{m['num']}", "f", start)
            node.end = node.own_end = end
            add(node, _RANK["fraccion"])
            node.touch(end, own=True)
            _flags(node, stripped)
            continue

        m = _INCISO.match(stripped)
        if m and top is not None and top.ntype in ("fraccion", "inciso", "articulo"):
            node = _Open("inciso", f"inciso {m['l']})", f"INC{m['l'].upper()}", "i", start)
            node.end = node.own_end = end
            add(node, _RANK["inciso"])
            node.touch(end, own=True)
            _flags(node, stripped)
            continue

        # --- loose body text -----------------------------------------------------------------
        target = body_target()
        if target is None:
            parent = stack[-1] if stack else None
            siblings = parent.children if parent else roots
            n = sum(1 for s in siblings if s.ntype == "parrafo" and not s.nav) + 1
            para = _Open("parrafo", f"Párrafo {n}", f"PARR{n}", "p", start)
            para.flagged = parent is None  # text before any structure: flag, do not guess
            para.parent = parent
            siblings.append(para)
            para.end = para.own_end = end
            para.touch(end, own=True)
            stack.append(para)
            target = para
            _flags(para, stripped)
        elif target.ntype == "parrafo" and was_blank:
            # a new blank-separated block under headings is a new parrafo
            stack.pop()
            parent = stack[-1] if stack else None
            siblings = parent.children if parent else roots
            n = sum(1 for s in siblings if s.ntype == "parrafo" and not s.nav) + 1
            para = _Open("parrafo", f"Párrafo {n}", f"PARR{n}", "p", start)
            para.parent = parent
            siblings.append(para)
            para.end = para.own_end = end
            para.touch(end, own=True)
            stack.append(para)
            _flags(para, stripped)
        else:
            target.touch(end, own=True)
            _flags(target, stripped)

    out: list[Node] = []
    _freeze(roots, None, "d", out)
    return out


def _flags(node: _Open, line: str) -> None:
    if _DEROGA.search(line):
        node.current = False
    node.notes.extend(m.group(0) for m in _REFORM.finditer(line))


def _freeze(nodes: list[_Open], parent: str | None, base: str, out: list[Node]) -> None:
    seen: dict[str, int] = {}
    for ordinal, n in enumerate(nodes):
        key = _ident(n.token).lower()
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:
            key = f"{key}_{seen[key]}"
        n.path = f"{base}.{key}"
        # own text ends where the first child begins (children carry the rest)
        own_end = n.children[0].start if n.children else n.end
        out.append(
            Node(
                path=n.path,
                parent=parent,
                node_type=n.ntype,
                label=n.label,
                token=n.token,
                ordinal=ordinal,
                start=n.start,
                end=n.end,
                own_end=own_end,
                is_current=n.current,
                is_navigation=n.nav,
                flagged=n.flagged,
                reform_notes=tuple(n.notes),
            )
        )
        _freeze(n.children, n.path, n.path, out)
