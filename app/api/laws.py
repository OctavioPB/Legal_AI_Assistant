"""Law navigator (feature 1): browse a loaded law by its own structure (libro, título, capítulo,
artículo, fracción), jump to an article by number, read its text and its version history.

No model is involved: what is shown is the stored text of `doc_nodes`, exactly as ingested. Browsing
a
law is an explicit user choice, it never widens the jurisdiction of a conversation. RLS applies
(public
corpus plus the firm's own documents; client files are not laws and are not listed here).
"""

from __future__ import annotations

import re
import uuid
from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import Connection, text

from app.api.deps import PrincipalDep, StateDep
from app.db.session import tenant_transaction
from app.validation.validator import fold

router = APIRouter(prefix="/laws")

STRUCTURE = ("libro", "titulo", "capitulo", "seccion", "articulo", "transitorio")
_OFFICIAL_ID = re.compile(r"^[A-Za-z0-9_]{1,64}$")
_PATH = re.compile(r"^[a-z0-9_]+(?:\.[a-z0-9_]+){0,15}$")
HEADING_CHARS = 140


class Law(BaseModel):
    official_id: str
    title: str
    doc_type: str
    language: str
    version_label: str | None
    valid_from: date | None
    jurisdiction_id: int
    jurisdiction_es: str
    jurisdiction_en: str
    last_reform: date | None  # the latest change the reform watcher recorded (ADR-017)


class TocNode(BaseModel):
    path: str
    node_type: str
    label: str
    heading: str
    children: list[TocNode] = []


class Citable(BaseModel):
    cite_id: str
    citation_string: str
    label: str
    text: str
    is_current: bool


class NodeView(BaseModel):
    official_id: str
    law_title: str
    version_label: str | None
    path: str
    node_type: str
    label: str
    text: str
    trail: list[dict[str, str]]  # ancestors: path, label
    children: list[dict[str, str]]  # structural children: path, node_type, label
    citables: list[Citable]
    prev_path: str | None
    next_path: str | None
    reforms: list[dict[str, Any]]  # per citable unit changed by a recorded reform


class Version(BaseModel):
    document_id: uuid.UUID
    version_label: str | None
    valid_from: date | None
    valid_to: date | None
    current: bool
    text: str | None  # None: the node did not exist in that version


def _check_id(official_id: str) -> None:
    if not _OFFICIAL_ID.match(official_id):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "bad law id")


def _current_doc(conn: Connection, official_id: str) -> Any:
    _check_id(official_id)
    row = conn.execute(
        text(
            "SELECT id, title, version_label FROM documents WHERE official_id = :o "
            "AND valid_to IS NULL AND doc_type <> 'client_file' ORDER BY ingested_at DESC LIMIT 1"
        ),
        {"o": official_id},
    ).one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "law not found")
    return row


def _heading(node_type: str, label: str, head: str) -> str:
    """A short human heading: the title's name, or the first words of an article."""
    lines = [ln.strip() for ln in head.splitlines() if ln.strip()]
    if node_type in ("libro", "titulo", "capitulo", "seccion"):
        rest = [
            ln
            for ln in lines[1:]
            if not re.match(r"^(LIBRO|T[IÍ]TULO|CAP[IÍ]TULO|SECCI)", ln, re.I)
        ]
        return rest[0][:HEADING_CHARS] if rest else ""
    body = " ".join(lines)
    body = re.sub(r"^\s*" + re.escape(label) + r"\s*[.\-–—:]*\s*", "", body, flags=re.I)
    if len(body) > HEADING_CHARS:
        body = body[:HEADING_CHARS].rsplit(" ", 1)[0] + "…"
    return body


@router.get("", response_model=list[Law])
def list_laws(p: PrincipalDep, st: StateDep) -> list[Law]:
    with tenant_transaction(st.engine, p.tenant_id, user_id=p.user_id) as conn:
        rows = conn.execute(
            text(
                "SELECT d.official_id, d.title, d.doc_type, d.language, d.version_label, "
                "d.valid_from, j.id AS jid, j.name_es, j.name_en, "
                "(SELECT max(coalesce(c.published_on, c.detected_at::date)) FROM corpus_changes c "
                " WHERE c.official_id = d.official_id) AS last_reform "
                "FROM documents d JOIN jurisdictions j ON j.id = d.jurisdiction_id "
                "WHERE d.valid_to IS NULL AND d.official_id IS NOT NULL "
                "AND d.doc_type <> 'client_file' "
                "ORDER BY j.country, "
                "CASE j.level WHEN 'federal' THEN 0 WHEN 'state' THEN 1 ELSE 2 END, "
                "j.state_code NULLS FIRST, j.municipality NULLS FIRST, d.title"
            )
        ).all()
    return [
        Law(
            official_id=r.official_id,
            title=r.title,
            doc_type=r.doc_type,
            language=r.language,
            version_label=r.version_label,
            valid_from=r.valid_from,
            jurisdiction_id=r.jid,
            jurisdiction_es=r.name_es,
            jurisdiction_en=r.name_en,
            last_reform=r.last_reform,
        )
        for r in rows
    ]


@router.get("/{official_id}/toc", response_model=list[TocNode])
def toc(official_id: str, p: PrincipalDep, st: StateDep) -> list[TocNode]:
    """The structure of the current version down to articles, in the law's own order."""
    with tenant_transaction(st.engine, p.tenant_id, user_id=p.user_id) as conn:
        doc = _current_doc(conn, official_id)
        rows = conn.execute(
            text(
                "SELECT id, parent_id, node_type, label, ordinal, path::text AS path, "
                "substring(full_text, 1, 400) AS head FROM doc_nodes "
                "WHERE document_id = :d AND node_type = ANY(:types)"
            ),
            {"d": doc.id, "types": list(STRUCTURE)},
        ).all()
    by_id = {r.id: r for r in rows}
    nodes = {
        r.id: TocNode(
            path=r.path,
            node_type=r.node_type,
            label=r.label,
            heading=_heading(r.node_type, r.label, r.head),
        )
        for r in rows
    }
    roots: list[tuple[int, TocNode]] = []
    kids: dict[uuid.UUID, list[tuple[int, TocNode]]] = {}
    for r in rows:
        parent = r.parent_id if r.parent_id in by_id else None
        (kids.setdefault(parent, []) if parent else roots).append((r.ordinal, nodes[r.id]))
    for nid, lst in kids.items():
        nodes[nid].children = [n for _, n in sorted(lst, key=lambda x: (x[0], x[1].path))]
    return [n for _, n in sorted(roots, key=lambda x: (x[0], x[1].path))]


def _article_label_key(label: str) -> str:
    return re.sub(r"[^a-z0-9]", "", fold(label).replace("articulo", ""))


@router.get("/{official_id}/find")
def find(
    official_id: str, p: PrincipalDep, st: StateDep, ref: str = Query(min_length=1, max_length=40)
) -> dict[str, str]:
    """Jump to an article by number: "47", "art. 47", "47 bis", "123"."""
    key = re.sub(r"[^a-z0-9]", "", re.sub(r"^\s*(art[ií]culo|art\.?)\s*", "", fold(ref)))
    if not key:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "bad reference")
    with tenant_transaction(st.engine, p.tenant_id, user_id=p.user_id) as conn:
        doc = _current_doc(conn, official_id)
        rows = conn.execute(
            text(
                "SELECT label, path::text AS path FROM doc_nodes WHERE document_id = :d "
                "AND node_type = 'articulo' ORDER BY ordinal"
            ),
            {"d": doc.id},
        ).all()
    for r in rows:
        if _article_label_key(r.label) == key:
            return {"path": r.path, "label": r.label}
    raise HTTPException(status.HTTP_404_NOT_FOUND, "article not found")


@router.get("/{official_id}/node", response_model=NodeView)
def node(
    official_id: str, p: PrincipalDep, st: StateDep, path: str = Query(max_length=300)
) -> NodeView:
    if not _PATH.match(path):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "bad path")
    with tenant_transaction(st.engine, p.tenant_id, user_id=p.user_id) as conn:
        doc = _current_doc(conn, official_id)
        n = conn.execute(
            text(
                "SELECT id, node_type, label, full_text, ordinal, parent_id FROM doc_nodes "
                "WHERE document_id = :d AND path = CAST(:p AS ltree)"
            ),
            {"d": doc.id, "p": path},
        ).one_or_none()
        if n is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "node not found")
        trail = conn.execute(
            text(
                "SELECT path::text AS path, label FROM doc_nodes WHERE document_id = :d "
                "AND path @> CAST(:p AS ltree) AND path <> CAST(:p AS ltree) "
                "AND node_type = ANY(:types) ORDER BY nlevel(path)"
            ),
            {"d": doc.id, "p": path, "types": list(STRUCTURE)},
        ).all()
        children = conn.execute(
            text(
                "SELECT path::text AS path, node_type, label FROM doc_nodes WHERE parent_id = :n "
                "AND node_type = ANY(:types) ORDER BY ordinal, path"
            ),
            {"n": n.id, "types": list(STRUCTURE)},
        ).all()
        cites = conn.execute(
            text(
                "SELECT c.cite_id, c.citation_string, x.label, c.text, c.is_current "
                "FROM chunks c JOIN doc_nodes x ON x.id = c.node_id WHERE c.document_id = :d "
                "AND x.path <@ CAST(:p AS ltree) AND NOT c.is_navigation "
                "ORDER BY c.char_start, c.cite_id"
            ),
            {"d": doc.id, "p": path},
        ).all()
        # neighbours: the previous / next article of the whole law, in document order
        prev_next = conn.execute(
            text(
                "WITH a AS (SELECT path::text AS path, row_number() OVER (ORDER BY "
                "(SELECT min(c.char_start) FROM chunks c WHERE c.node_id = doc_nodes.id), path) "
                "AS i "
                "FROM doc_nodes WHERE document_id = :d "
                "AND node_type IN ('articulo','transitorio')) "
                "SELECT (SELECT path FROM a WHERE i = me.i - 1) AS prev, "
                "(SELECT path FROM a WHERE i = me.i + 1) AS next FROM a me WHERE me.path = :p"
            ),
            {"d": doc.id, "p": path},
        ).one_or_none()
        # reforms that produced the citable units shown here (lineage pointing AT them)
        reforms = conn.execute(
            text(
                "SELECT l.new_cite_id, l.old_cite_id, l.change, c.published_on, c.old_version "
                "FROM cite_lineage l JOIN corpus_changes c ON c.id = l.change_id "
                "WHERE l.new_cite_id = ANY(:ids) AND l.change IN ('amended','derogated','added') "
                "ORDER BY c.detected_at DESC"
            ),
            {"ids": [c.cite_id for c in cites]},
        ).all()
    return NodeView(
        official_id=official_id,
        law_title=doc.title,
        version_label=doc.version_label,
        path=path,
        node_type=n.node_type,
        label=n.label,
        text=n.full_text,
        trail=[{"path": t.path, "label": t.label} for t in trail],
        children=[{"path": c.path, "node_type": c.node_type, "label": c.label} for c in children],
        citables=[
            Citable(
                cite_id=c.cite_id,
                citation_string=c.citation_string,
                label=c.label,
                text=c.text,
                is_current=c.is_current,
            )
            for c in cites
        ],
        prev_path=prev_next.prev if prev_next else None,
        next_path=prev_next.next if prev_next else None,
        reforms=[
            {
                "cite_id": r.new_cite_id,
                "previous_cite_id": r.old_cite_id,
                "change": r.change,
                "published_on": r.published_on,
                "previous_version": r.old_version,
            }
            for r in reforms
        ],
    )


@router.get("/{official_id}/history", response_model=list[Version])
def history(
    official_id: str, p: PrincipalDep, st: StateDep, path: str = Query(max_length=300)
) -> list[Version]:
    """The same node in every stored version of the law, oldest first (nothing is ever deleted,
    invariant 3). Nodes are matched by their structural path."""
    _check_id(official_id)
    if not _PATH.match(path):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "bad path")
    with tenant_transaction(st.engine, p.tenant_id, user_id=p.user_id) as conn:
        rows = conn.execute(
            text(
                "SELECT d.id, d.version_label, d.valid_from, d.valid_to, n.full_text "
                "FROM documents d LEFT JOIN doc_nodes n ON n.document_id = d.id "
                "AND n.path = CAST(:p AS ltree) WHERE d.official_id = :o "
                "AND d.doc_type <> 'client_file' ORDER BY d.ingested_at, d.id"
            ),
            {"o": official_id, "p": path},
        ).all()
    if not rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "law not found")
    return [
        Version(
            document_id=r.id,
            version_label=r.version_label,
            valid_from=r.valid_from,
            valid_to=r.valid_to,
            current=r.valid_to is None,
            text=r.full_text,
        )
        for r in rows
    ]
