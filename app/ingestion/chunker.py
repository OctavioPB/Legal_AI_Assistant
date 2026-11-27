"""Hierarchical legal chunker (WS2.T05). Rules: .claude/rules/rag.md, skill legal-chunking.

Unit = deepest self-contained node. A node whose children are too short (< min) and which fits in
`max` merges UP into one chunk of the parent; a node over `max` splits by paragraph (same node).
Never cuts mid-node, never crosses a node boundary. `text` is an exact slice of the source;
the breadcrumb is only an embedding prefix.

BUMP `CHUNKER_VERSION` on ANY change that alters chunks, including default thresholds.
"""

from __future__ import annotations

import re

from app.ingestion.model import (
    HEADING_TYPES,
    Chunk,
    ChunkThresholds,
    DocMeta,
    Node,
)
from app.ingestion.tokens import TokenCounter

CHUNKER_VERSION = 3  # ADR-049 headings + ADR-052 decree dates (new generation, ADR-006)

CITE_RE = re.compile(
    r"^(?P<country>[A-Z]{2})-(?P<scope>[A-Z0-9]+)-(?P<code>[A-Z0-9]+)-"
    r"(?P<node>[A-Z0-9]+(?:-[A-Z0-9]+)*)-v(?P<version>[0-9][0-9A-Za-z.]*)$"
)
CF_CITE_RE = re.compile(r"^CF-(?P<file>[A-Z0-9]+)-(?P<node>[A-Z0-9]+(?:-[A-Z0-9]+)*)$")

_ES_SHORT = {
    "articulo": "art.", "fraccion": "fracc.", "inciso": "inciso", "parrafo": "párr.",
    "transitorio": "transitorio",
}  # fmt: skip


def parse_cite(cite_id: str) -> dict[str, str] | None:
    m = CF_CITE_RE.match(cite_id) or CITE_RE.match(cite_id)
    return m.groupdict() if m else None


def _disambiguate(cite_id: str, count: int) -> str:
    """`...-ART47-v2025.06` -> `...-ART47-DUP2-v2025.06`: the suffix goes in the NODE part so the
    result still parses (the version stays last)."""
    m = CITE_RE.match(cite_id)
    if m:
        head = cite_id[: m.start("version") - 2]  # strip "-v"
        return f"{head}-DUP{count}-v{m.group('version')}"
    return f"{cite_id}-DUP{count}"


def _blocks(src: str, start: int, end: int) -> list[tuple[int, int]]:
    """Paragraph blocks of src[start:end] as (s, e) spans: blank-line separated; if there are no
    blank lines, line separated."""
    text = src[start:end]
    spans = [
        (m.start() + start, m.end() + start) for m in re.finditer(r"\S(?:.|\n(?!\s*\n))*", text)
    ]
    if len(spans) <= 1 and "\n" in text.strip():
        spans = [(m.start() + start, m.end() + start) for m in re.finditer(r"\S[^\n]*", text)]
    return spans


class Chunker:
    def __init__(self, counter: TokenCounter, thresholds: ChunkThresholds | None = None) -> None:
        self.counter = counter
        self.th = thresholds or ChunkThresholds(counter=counter.name)
        if self.th.counter != counter.name:
            raise ValueError(
                f"thresholds were set for counter {self.th.counter!r}, got {counter.name!r}"
            )

    # ------------------------------------------------------------------------------------------
    def chunk(self, src: str, nodes: list[Node], meta: DocMeta) -> list[Chunk]:
        by_path = {n.path: n for n in nodes}
        kids: dict[str | None, list[Node]] = {}
        for n in nodes:
            kids.setdefault(n.parent, []).append(n)
        out: list[Chunk] = []
        for root in kids.get(None, []):
            self._emit(src, root, kids, by_path, meta, out)
        # Ambiguous numbering in the source (e.g. an uppercase roman "(I)" next to a lettered list)
        # must not abort a whole code: the later duplicate gets a deterministic suffix and is
        # listed in the report (`duplicate_ids`).
        seen: dict[str, int] = {}
        for i, c in enumerate(out):
            count = seen.get(c.cite_id, 0) + 1
            seen[c.cite_id] = count
            if count > 1:
                out[i] = c.model_copy(
                    update={"cite_id": _disambiguate(c.cite_id, count), "ambiguous": True}
                )
        return out

    def _tok(self, src: str, s: int, e: int) -> int:
        return self.counter.count(src[s:e])

    def _emit(
        self,
        src: str,
        node: Node,
        kids: dict[str | None, list[Node]],
        by_path: dict[str, Node],
        meta: DocMeta,
        out: list[Chunk],
    ) -> None:
        children = kids.get(node.path, [])
        if node.node_type in HEADING_TYPES:
            for c in children:
                self._emit(src, c, kids, by_path, meta, out)
            return
        if not children or node.is_navigation:
            self._unit(src, node, node.start, node.end, "", by_path, meta, out)
            return
        # parent with children: merge up when a child is too short and the whole thing fits
        whole = self._tok(src, node.start, node.end)
        short_child = any(self._tok(src, c.start, c.end) < self.th.min_tokens for c in children)
        if short_child and whole <= self.th.max_tokens:
            self._unit(src, node, node.start, node.end, "", by_path, meta, out)
            return
        if src[node.start : node.own_end].strip():  # caput travels as its own unit
            self._unit(src, node, node.start, node.own_end, "CAPUT", by_path, meta, out)
        for c in children:
            self._emit(src, c, kids, by_path, meta, out)

    def _unit(
        self,
        src: str,
        node: Node,
        start: int,
        end: int,
        suffix: str,
        by_path: dict[str, Node],
        meta: DocMeta,
        out: list[Chunk],
    ) -> None:
        end = start + len(src[start:end].rstrip())
        if self._tok(src, start, end) <= self.th.max_tokens:
            parts = [(start, end)]
        else:
            parts = self._split(src, start, end)
        for k, (s, e) in enumerate(parts, 1):
            out.append(
                self._make(src, node, s, e, suffix, k if len(parts) > 1 else 0, by_path, meta)
            )

    def _split(self, src: str, start: int, end: int) -> list[tuple[int, int]]:
        blocks = _blocks(src, start, end)
        if not blocks:
            return [(start, end)]
        parts: list[tuple[int, int]] = []
        cur_s, cur_e = blocks[0]
        for s, e in blocks[1:]:
            if self._tok(src, cur_s, e) <= self.th.max_tokens:
                cur_e = e
            else:
                parts.append((cur_s, cur_e))
                cur_s, cur_e = s, e
        parts.append((cur_s, cur_e))
        # the first part owns any leading heading text: widen it to the unit start
        parts[0] = (start, parts[0][1])
        return parts

    # ------------------------------------------------------------------------------------------
    def _chain(self, node: Node, by_path: dict[str, Node]) -> list[Node]:
        chain = [node]
        while chain[-1].parent:
            chain.append(by_path[chain[-1].parent])
        return chain[::-1]

    def _make(
        self,
        src: str,
        node: Node,
        s: int,
        e: int,
        suffix: str,
        part: int,
        by_path: dict[str, Node],
        meta: DocMeta,
    ) -> Chunk:
        chain = self._chain(node, by_path)
        body = [n for n in chain if n.node_type not in HEADING_TYPES]
        # headings only disambiguate material that has no article above it (loose parrafos)
        id_nodes = body if any(n.node_type in ("articulo", "transitorio") for n in body) else chain
        if any(n.node_type == "transitorio" for n in body):
            id_nodes = [
                n for n in chain if n.node_type == "seccion" and n.token.startswith("TRANS")
            ] + body
        tokens = [n.token for n in id_nodes]
        if suffix:
            tokens.append(suffix)
        if part:
            tokens.append(f"P{part}")
        nodepart = "-".join(tokens)
        if meta.client_file:
            cite = f"CF-{meta.client_file}-{nodepart}"
        else:
            cite = f"{meta.country}-{meta.scope}-{meta.code}-{nodepart}-v{meta.version_label}"
        text = src[s:e]
        crumb = "[" + " › ".join([meta.title, *[n.label for n in chain]]) + "]"
        embedded = f"{crumb}\n{text}"
        text_tok = self.counter.count(text)
        tok = self.counter.count(embedded)
        return Chunk(
            cite_id=cite,
            citation_string=self._citation(meta, id_nodes, part),
            node_path=node.path,
            part=part or 1,
            text=text,
            breadcrumb=crumb,
            embedded_text=embedded,
            char_start=s,
            char_end=e,
            token_count=tok,
            text_tokens=text_tok,
            is_current=all(n.is_current for n in chain),
            is_navigation=node.is_navigation,
            oversized=text_tok > self.th.max_tokens,
            undersized=text_tok < self.th.min_tokens and not node.is_navigation,
            exceeds_window=tok > self.th.embedder_window,
        )

    @staticmethod
    def _citation(meta: DocMeta, nodes: list[Node], part: int) -> str:
        if meta.citation_style == "plain":
            names = ", ".join(n.label for n in nodes)
            title = f'"{meta.title}"' if meta.language == "en" else f"«{meta.title}»"
            word = "part" if meta.language == "en" else "parte"
            return f"{title}, {names}" + (f" ({word} {part})" if part else "")
        if meta.language == "en":  # "Cal. Lab. Code, § 1102.5(a)(1)": subdivisions attach directly
            out = ""
            for n in nodes:
                if n.node_type == "seccion":
                    continue
                if n.node_type == "articulo":
                    out = n.label
                elif n.label.startswith("("):
                    out += n.label
                else:
                    out += f", {n.label}"
            base = f"{meta.title}, {out}" if out else meta.title
            return base + (f" (part {part})" if part else "")
        pieces: list[str] = []
        for n in nodes:
            if n.node_type == "seccion":
                continue
            if n.node_type == "articulo":
                pieces.append("art. " + n.label.split(" ", 1)[1])
            elif n.node_type == "fraccion":
                pieces.append("fracc. " + n.label.split(" ", 1)[1])
            else:
                pieces.append(n.label)
        base = f"{meta.title}, " + ", ".join(pieces) if pieces else meta.title
        return base + (f" (parte {part})" if part else "")


def report(chunks: list[Chunk]) -> dict[str, object]:
    """Ingestion stats for oversized / undersized / window overflows (logged, never assumed)."""
    return {
        "chunks": len(chunks),
        "oversized": [c.cite_id for c in chunks if c.oversized],
        "undersized": [c.cite_id for c in chunks if c.undersized],
        "exceeds_window": [c.cite_id for c in chunks if c.exceeds_window],
        "navigation": sum(c.is_navigation for c in chunks),
        "duplicate_ids": [c.cite_id for c in chunks if c.ambiguous],
    }
