"""Reform awareness (feature 6, ADR-017): what the reform watcher changed, and which of the caller's
conversations, matter sources and verification reports cite a unit that a reform amended, derogated
or
removed. Everything is computed from the recorded lineage at read time, under RLS.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text

from app.api.deps import PrincipalDep, StateDep
from app.corpus.lineage import successions
from app.db.session import tenant_transaction
from app.ingestion.corpus import CORPUS
from app.ingestion.indexes import INDEXES

router = APIRouter(prefix="/reforms")


class Alert(BaseModel):
    where: Literal["conversation", "matter_source", "report"]
    target_id: uuid.UUID
    target_title: str
    kind: str | None  # conversation kind (chat / research / case)
    matter_id: uuid.UUID | None
    cite_id: str
    citation_string: str
    change: str
    published_on: date | None
    current_cite_id: str | None


class Change(BaseModel):
    id: uuid.UUID
    official_id: str
    title: str
    old_version: str | None
    new_version: str | None
    published_on: date | None
    detected_at: datetime
    counts: dict[str, int]


class Check(BaseModel):
    official_id: str
    title: str | None = None  # the law's name (official_id is an internal key)
    checked_at: datetime
    outcome: str
    http_status: int | None
    detail: str | None


class Overview(BaseModel):
    changes: list[Change]
    checks: list[Check]


class IndexRead(BaseModel):
    index_id: str
    title: str
    url: str
    checked_at: datetime | None
    outcome: str | None
    detail: str | None


class IndexEntryOut(BaseModel):
    index_id: str
    entry_key: str
    title: str
    url: str
    status: Literal["available", "new", "approved", "dismissed"]
    published_on: date | None
    last_reform_on: date | None
    last_reform_text: str | None
    note: str | None
    first_seen_at: datetime
    missing: bool  # no longer listed by the index (abrogated, renamed)
    loaded: bool  # a version of it is in the corpus


class Discovery(BaseModel):
    indexes: list[IndexRead]
    entries: list[IndexEntryOut]


class LineageRow(BaseModel):
    change: str
    citation_string: str
    old_cite_id: str | None
    new_cite_id: str | None


_CHANGED = (
    "SELECT l.old_cite_id FROM cite_lineage l JOIN corpus_changes cc ON cc.id = l.change_id "
    "WHERE l.change IN ('amended','derogated','removed') AND cc.detected_at >= :since"
)


@router.get("/alerts", response_model=list[Alert])
def alerts(p: PrincipalDep, st: StateDep) -> list[Alert]:
    since = datetime.now().astimezone() - timedelta(days=st.settings.reform_alert_days)
    with tenant_transaction(st.engine, p.tenant_id, user_id=p.user_id) as conn:
        conv = conn.execute(
            text(
                "SELECT DISTINCT c.id, c.title, c.kind, c.matter_id, x->>'cite_id' AS cite_id "
                "FROM conversations c JOIN messages m ON m.conversation_id = c.id "
                "CROSS JOIN LATERAL jsonb_array_elements(m.citations) x "
                f"WHERE x->>'cite_id' IN ({_CHANGED})"
            ),
            {"since": since},
        ).all()
        src = conn.execute(
            text(
                "SELECT s.matter_id AS id, m.title, s.matter_id, s.cite_id FROM matter_sources s "
                f"JOIN matters m ON m.id = s.matter_id WHERE s.cite_id IN ({_CHANGED})"
            ),
            {"since": since},
        ).all()
        rep = conn.execute(
            text(
                "SELECT DISTINCT r.id, r.title, r.matter_id, cid AS cite_id "
                "FROM verification_reports r "
                "CROSS JOIN LATERAL jsonb_array_elements(r.items) it "
                "CROSS JOIN LATERAL jsonb_array_elements_text(it->'cite_ids') cid "
                f"WHERE cid IN ({_CHANGED})"
            ),
            {"since": since},
        ).all()
        succ = successions(conn, {r.cite_id for r in [*conv, *src, *rep]})
    out: list[Alert] = []

    def add(where: Any, r: Any, kind: str | None) -> None:
        s = succ.get(r.cite_id)
        if s is None or s.change == "unchanged":
            return
        out.append(
            Alert(
                where=where,
                target_id=r.id,
                target_title=r.title,
                kind=kind,
                matter_id=r.matter_id,
                cite_id=r.cite_id,
                citation_string=s.citation_string,
                change=s.change,
                published_on=s.published_on,
                current_cite_id=s.current,
            )
        )

    for r in conv:
        add("conversation", r, r.kind)
    for r in src:
        add("matter_source", r, None)
    for r in rep:
        add("report", r, None)
    out.sort(key=lambda a: (a.published_on or date.min, a.target_title, a.cite_id), reverse=True)
    return out[:300]


@router.get("", response_model=Overview)
def overview(p: PrincipalDep, st: StateDep) -> Overview:
    """Recorded reforms (newest first) and the latest check of every watched source."""
    with tenant_transaction(st.engine, p.tenant_id, user_id=p.user_id) as conn:
        changes = conn.execute(
            text(
                "SELECT c.id, c.official_id, d.title, c.old_version, c.new_version, "
                "c.published_on, "
                "c.detected_at, c.counts FROM corpus_changes c JOIN documents d "
                "ON d.id = c.new_document_id ORDER BY c.detected_at DESC, c.id LIMIT 100"
            )
        ).all()
        checks = conn.execute(
            text(
                "SELECT DISTINCT ON (s.official_id) s.official_id, s.checked_at, s.outcome, "
                "s.http_status, s.detail, (SELECT d.title FROM documents d "
                "WHERE d.official_id = s.official_id AND d.valid_to IS NULL "
                "ORDER BY d.ingested_at DESC LIMIT 1) AS title "
                "FROM source_checks s WHERE s.official_id NOT LIKE 'index:%' "
                "ORDER BY s.official_id, s.checked_at DESC"
            )
        ).all()
    return Overview(
        changes=[
            Change(
                id=r.id,
                official_id=r.official_id,
                title=r.title,
                old_version=r.old_version,
                new_version=r.new_version,
                published_on=r.published_on,
                detected_at=r.detected_at,
                counts=dict(r.counts),
            )
            for r in changes
        ],
        checks=[
            Check(
                official_id=r.official_id,
                title=r.title,
                checked_at=r.checked_at,
                outcome=r.outcome,
                http_status=r.http_status,
                detail=r.detail,
            )
            for r in checks
        ],
    )


@router.get("/indexes", response_model=Discovery)
def indexes(p: PrincipalDep, st: StateDep) -> Discovery:
    """ADR-026: what the official indexes list, and which of it is loaded, new or decided. Read
    only: an operator approves a law with `python -m app.ingestion.discover approve` (the public
    corpus is shared by every firm and never written by the API's role)."""
    static_urls = [d.source_url for d in CORPUS if d.source_url]
    with tenant_transaction(st.engine, p.tenant_id, user_id=p.user_id) as conn:
        checks = {
            r.official_id: r
            for r in conn.execute(
                text(
                    "SELECT DISTINCT ON (official_id) official_id, checked_at, outcome, detail "
                    "FROM source_checks WHERE official_id LIKE 'index:%' "
                    "ORDER BY official_id, checked_at DESC"
                )
            )
        }
        rows = conn.execute(
            text(
                "SELECT e.index_id, e.entry_key, e.title, e.url, e.status, e.published_on, "
                "e.last_reform_on, e.last_reform_text, e.note, e.first_seen_at, "
                "e.missing_since IS NOT NULL AS missing, "
                "(e.url = ANY(:static) OR EXISTS (SELECT 1 FROM documents d "
                " WHERE d.official_id = e.source_key AND d.valid_to IS NULL)) AS loaded "
                "FROM corpus_index_entries e ORDER BY CASE e.status WHEN 'new' THEN 0 "
                "WHEN 'approved' THEN 1 ELSE 2 END, e.index_id, e.title, e.entry_key LIMIT 2000"
            ),
            {"static": static_urls},
        ).all()
    return Discovery(
        indexes=[
            IndexRead(
                index_id=s.index_id,
                title=s.title,
                url=s.url,
                checked_at=c.checked_at if (c := checks.get(f"index:{s.index_id}")) else None,
                outcome=c.outcome if c else None,
                detail=c.detail if c else None,
            )
            for s in INDEXES.values()
        ],
        entries=[
            IndexEntryOut(
                index_id=r.index_id,
                entry_key=r.entry_key,
                title=r.title,
                url=r.url,
                status=r.status,
                published_on=r.published_on,
                last_reform_on=r.last_reform_on,
                last_reform_text=r.last_reform_text,
                note=r.note,
                first_seen_at=r.first_seen_at,
                missing=r.missing,
                loaded=r.loaded,
            )
            for r in rows
        ],
    )


@router.get("/{change_id}", response_model=list[LineageRow])
def change_detail(change_id: uuid.UUID, p: PrincipalDep, st: StateDep) -> list[LineageRow]:
    """Every unit a reform touched (unchanged units are not listed)."""
    with tenant_transaction(st.engine, p.tenant_id, user_id=p.user_id) as conn:
        rows = conn.execute(
            text(
                "SELECT change, citation_string, old_cite_id, new_cite_id FROM cite_lineage "
                "WHERE change_id = :c AND change <> 'unchanged' "
                "ORDER BY CASE change WHEN 'amended' THEN 0 WHEN 'added' THEN 1 "
                "WHEN 'derogated' THEN 2 ELSE 3 END, coalesce(new_cite_id, old_cite_id) LIMIT 2000"
            ),
            {"c": change_id},
        ).all()
        if (
            not rows
            and not conn.execute(
                text("SELECT 1 FROM corpus_changes WHERE id = :c"), {"c": change_id}
            ).first()
        ):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "change not found")
    return [
        LineageRow(
            change=r.change,
            citation_string=r.citation_string,
            old_cite_id=r.old_cite_id,
            new_cite_id=r.new_cite_id,
        )
        for r in rows
    ]
