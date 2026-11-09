"""Parent expansion + context assembly (WS3.T05).

Retrieval unit = chunk (deepest node); context unit = parent article (rag.md). Hits sharing a
parent are deduped into ONE fragment that lists every hit's cite_id. The rendered context never
exceeds the token budget: an article that does not fit falls back to the chunk text, and if that
does not fit either, assembly stops (rank order is preserved; fewer good fragments beat many).
Statute text is data: it is wrapped in <fragmento> and cannot close the wrapper.

ADR-020: when a whole article is rendered, every citable unit inside it (its fracciones, incisos,
paragraphs) is listed in `also` and is citable: the model read their text, so a citation to one of
them is provenance, not invention. Only units of the same stored document version, never navigation
chunks, and never non-current units unless the session asked for historical text.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from html import escape

from sqlalchemy import Connection, text

from app.ingestion.tokens import TokenCounter
from app.rag.types import Fragment, Hit

_WRAPPER = re.compile(r"<(/?)(fragmento)", re.IGNORECASE)
_CONTEXT_TYPES = ("articulo", "transitorio")


def neutralize(statute_text: str) -> str:
    """Make `<fragmento`/`</fragmento` inside statute text inert; everything else stays verbatim."""
    return _WRAPPER.sub(lambda m: f"&lt;{m.group(1)}{m.group(2)}", statute_text)


def render_fragment(f: Fragment) -> str:
    also = f' also="{escape(" ".join(f.also_cite_ids), quote=True)}"' if f.also_cite_ids else ""
    return (
        f'<fragmento cite_id="{escape(f.cite_id, quote=True)}"{also} '
        f'cita="{escape(f.citation_string, quote=True)}">\n{neutralize(f.text)}\n</fragmento>'
    )


def render_context(fragments: Sequence[Fragment]) -> str:
    return "\n\n".join(render_fragment(f) for f in fragments)


def _parent_units(
    conn: Connection, hits: Sequence[Hit], *, apartado: bool = False
) -> dict[uuid.UUID, tuple[uuid.UUID, str]]:
    """node_id of each hit -> (parent unit node id, parent full_text); self if no article above.

    `apartado=True` (ADR-023): a unit inside an "Apartado A/B" stops at that apartado instead of the
    whole article (art. 123 CPEUM is ~34k characters; rendered whole, the model paraphrased apartado
    A's fracción XIII under apartado B's id). Apartados are typed `parrafo` in the tree, labelled
    "Apartado X"."""
    out: dict[uuid.UUID, tuple[uuid.UUID, str]] = {}
    for h in hits:
        row = conn.execute(
            text(
                "SELECT n.id, n.full_text FROM doc_nodes n JOIN doc_nodes h ON h.id = :nid "
                "WHERE n.document_id = h.document_id AND n.path @> h.path "
                "AND (n.node_type = ANY(:types) OR (:ap AND n.label ~* '^apartado [a-z]$')) "
                "ORDER BY nlevel(n.path) DESC LIMIT 1"
            ),
            {"nid": h.node_id, "types": list(_CONTEXT_TYPES), "ap": apartado},
        ).first()
        if row and apartado and _has_apartados(conn, row.id):
            # a unit sitting directly in an article that is split into apartados (its caput): the
            # whole article would bring every apartado back, so the unit is its own context
            row = None
        out[h.node_id] = (row.id, row.full_text) if row else (h.node_id, h.text)
    return out


def _has_apartados(conn: Connection, node_id: uuid.UUID) -> bool:
    return bool(
        conn.execute(
            text(
                "SELECT 1 FROM doc_nodes WHERE parent_id = :n "
                "AND label ~* '^apartado [a-z]$' LIMIT 1"
            ),
            {"n": node_id},
        ).first()
    )


_UNITS = text(
    "SELECT c.cite_id, c.citation_string FROM chunks c JOIN doc_nodes x ON x.id = c.node_id "
    "JOIN doc_nodes p ON p.id = :pid WHERE x.document_id = p.document_id AND x.path <@ p.path "
    "AND c.document_id = p.document_id AND NOT c.is_navigation AND (c.is_current OR :hist) "
    "ORDER BY c.char_start, c.cite_id"
)


def _units(
    conn: Connection, parent_id: uuid.UUID, skip: str, include_historical: bool
) -> tuple[tuple[str, str], ...]:
    rows = conn.execute(_UNITS, {"pid": parent_id, "hist": include_historical}).all()
    return tuple((r.cite_id, r.citation_string) for r in rows if r.cite_id != skip)


def build_context(
    conn: Connection,
    hits: Sequence[Hit],
    *,
    budget_tokens: int,
    counter: TokenCounter,
    citable_units: bool = False,
    include_historical: bool = False,
    apartado: bool = False,
) -> list[Fragment]:
    parents = _parent_units(conn, hits, apartado=apartado)
    fragments: list[Fragment] = []
    index: dict[uuid.UUID, int] = {}
    used = 0
    for h in hits:
        pid, parent_text = parents[h.node_id]
        if pid in index:  # same parent already in context: just register the extra cite_id
            i = index[pid]
            prev = fragments[i]
            if h.cite_id != prev.cite_id and h.cite_id not in prev.also_cite_ids:
                fragments[i] = prev.model_copy(
                    update={"also_cite_ids": (*prev.also_cite_ids, h.cite_id)}
                )
            continue
        chosen: Fragment | None = None
        for body, expanded in ((parent_text, True), (h.text, False)):
            if expanded and body == h.text:
                continue  # the parent is the chunk itself: one candidate is enough
            units = _units(conn, pid, h.cite_id, include_historical) if (
                expanded and citable_units
            ) else ()  # fmt: skip
            cand = Fragment(
                cite_id=h.cite_id, also_cite_ids=tuple(u[0] for u in units),
                citation_string=h.citation_string, text=body, expanded=expanded, tokens=0,
                unit_names=units,
            )  # fmt: skip
            t = counter.count(render_fragment(cand)) + 1  # +1: the joining blank line
            if used + t <= budget_tokens:
                chosen = cand.model_copy(update={"tokens": t})
                break
        if chosen is None:
            break
        index[pid] = len(fragments)
        fragments.append(chosen)
        used += chosen.tokens
    return fragments
