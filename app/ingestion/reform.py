"""Reform diff (ADR-017): compare the citable units of a retired version with the new one and record
the lineage, so every process that holds a `cite_id` can follow (or flag) it.

Units are matched by their `cite_id` WITHOUT the version suffix ("MX-FED-LFT-ART47-FRACII"): the
stem
is the unit's legal address, the suffix is the version. Text is compared after collapsing
whitespace;
a reform note added to a fracción ("Fracción reformada DOF ...") makes it `amended`, as it should.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy import Connection, text

from app.corpus.lineage import Change

_VERSION = re.compile(r"-v[^-]+$")
_DEROGATED = re.compile(r"^\W*(?:art[ií]culo\s+[\w\s-]{1,15}[.\-–:]*\s*)?\(?\s*se\s+deroga", re.I)


def stem_of(cite_id: str) -> str:
    return _VERSION.sub("", cite_id)


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", t).strip()


@dataclass(frozen=True)
class Unit:
    cite_id: str
    text: str
    citation_string: str


@dataclass(frozen=True)
class Line:
    old: str | None
    new: str | None
    change: Change
    citation_string: str


def diff(old: list[Unit], new: list[Unit]) -> list[Line]:
    """One line per unit of either version, old order first then the added ones."""
    by_new = {stem_of(u.cite_id): u for u in new}
    seen: set[str] = set()
    out: list[Line] = []
    for u in old:
        st = stem_of(u.cite_id)
        n = by_new.get(st)
        if n is None:
            out.append(Line(u.cite_id, None, "removed", u.citation_string))
            continue
        seen.add(st)
        if _norm(n.text) == _norm(u.text):
            change: Change = "unchanged"
        elif _DEROGATED.match(n.text) and not _DEROGATED.match(u.text):
            change = "derogated"
        else:
            change = "amended"
        out.append(Line(u.cite_id, n.cite_id, change, n.citation_string))
    for u in new:
        if stem_of(u.cite_id) not in seen:
            out.append(Line(None, u.cite_id, "added", u.citation_string))
    return out


def counts(lines: list[Line]) -> dict[str, int]:
    c = {k: 0 for k in ("unchanged", "amended", "derogated", "removed", "added")}
    for ln in lines:
        c[ln.change] += 1
    return c


_UNITS = text(
    "SELECT cite_id, text, citation_string FROM chunks WHERE document_id = :d "
    "AND NOT is_navigation ORDER BY char_start, cite_id"
)


def units(conn: Connection, document_id: uuid.UUID) -> list[Unit]:
    return [
        Unit(r.cite_id, r.text, r.citation_string) for r in conn.execute(_UNITS, {"d": document_id})
    ]


def record(
    conn: Connection,
    tenant_id: uuid.UUID,
    *,
    official_id: str,
    old_document_id: uuid.UUID,
    new_document_id: uuid.UUID,
    source_uri: str,
    published_on: date | None,
) -> tuple[uuid.UUID, dict[str, int]]:
    """Diff the two versions and write `corpus_changes` + `cite_lineage` in the caller's transaction
    (the ingestor role, anchored on the tenant that owns both documents)."""
    versions: dict[uuid.UUID, str | None] = dict(
        conn.execute(
            text("SELECT id, version_label FROM documents WHERE id = ANY(:ids)"),
            {"ids": [old_document_id, new_document_id]},
        ).all()
    )
    lines = diff(units(conn, old_document_id), units(conn, new_document_id))
    c = counts(lines)
    change_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO corpus_changes (id, tenant_id, official_id, old_document_id, "
            "new_document_id, old_version, new_version, published_on, source_uri, counts) "
            "VALUES (:id, :t, :o, :old, "
            ":new, :ov, :nv, :p, :u, CAST(:c AS jsonb))"
        ),
        {
            "id": change_id,
            "t": tenant_id,
            "o": official_id,
            "old": old_document_id,
            "new": new_document_id,
            "ov": versions.get(old_document_id),
            "nv": versions.get(new_document_id),
            "p": published_on,
            "u": source_uri,
            "c": json.dumps(c),
        },
    )
    rows = [
        {
            "t": tenant_id,
            "ch": change_id,
            "o": ln.old,
            "n": ln.new,
            "k": ln.change,
            "cs": ln.citation_string,
        }
        for ln in lines
    ]
    for i in range(0, len(rows), 1000):
        conn.execute(
            text(
                "INSERT INTO cite_lineage (tenant_id, change_id, old_cite_id, new_cite_id, change, "
                "citation_string) VALUES (:t, :ch, :o, :n, :k, :cs)"
            ),
            rows[i : i + 1000],
        )
    return change_id, c


def missing_lineage(conn: Connection) -> list[tuple[str, uuid.UUID, uuid.UUID, str]]:
    """Versions that replaced another but have no recorded change (the watcher died between the
    ingestion and the diff): (official_id, old_document_id, new_document_id, source_uri)."""
    rows = conn.execute(
        text(
            "SELECT n.official_id, o.id AS old_id, n.id AS new_id, n.source_uri FROM documents n "
            "JOIN LATERAL (SELECT id FROM documents p WHERE p.tenant_id = n.tenant_id "
            "  AND p.official_id = n.official_id AND p.ingested_at < n.ingested_at "
            "  ORDER BY p.ingested_at DESC LIMIT 1) o ON true "
            "WHERE n.official_id IS NOT NULL AND n.tenant_id = public_corpus_id() "
            "AND NOT EXISTS (SELECT 1 FROM corpus_changes c WHERE c.new_document_id = n.id)"
        )
    ).all()
    return [(r.official_id, r.old_id, r.new_id, r.source_uri) for r in rows]
