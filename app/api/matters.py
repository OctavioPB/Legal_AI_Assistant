"""Matters (asuntos, ADR-018): a folder per client matter that groups conversations, research,
cases,
verification reports, notes and saved sources, shared with the colleagues the owner adds.

RLS is the floor (a matter is visible only to its creator and members, and everything inside
inherits
that); the owner-only rules (rename, close, delete, manage members) are application rules on top.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Connection, text

from app.api.deps import PrincipalDep, StateDep
from app.corpus.lineage import successions
from app.corpus.regimes import CASE_REGIMES, case_regime_label
from app.db.session import tenant_transaction

router = APIRouter(prefix="/matters")

TITLE_MAX = 200


class MatterIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=TITLE_MAX)
    client_ref: str = Field(default="", max_length=200)
    description: str = Field(default="", max_length=4000)
    labor_regime: str | None = None  # ADR-031: a code of CASE_REGIMES, or None


class MatterPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=TITLE_MAX)
    client_ref: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    status: Literal["open", "closed"] | None = None
    labor_regime: str | None = None  # ADR-031: a code; "" clears it; None leaves it unchanged


class MemberIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: uuid.UUID


class NoteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str = Field(min_length=1, max_length=8000)


class SourceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cite_id: str = Field(min_length=1, max_length=200)
    note: str = Field(default="", max_length=2000)


class MatterOut(BaseModel):
    id: uuid.UUID
    title: str
    client_ref: str
    description: str
    labor_regime: str | None = None
    status: Literal["open", "closed"]
    role: Literal["owner", "member"]
    members: int
    items: int
    created_at: datetime
    updated_at: datetime


class Member(BaseModel):
    user_id: uuid.UUID
    display_name: str
    email: str
    role: Literal["owner", "member"]


class Note(BaseModel):
    id: uuid.UUID
    body: str
    author: str
    mine: bool
    created_at: datetime


class SavedSource(BaseModel):
    id: uuid.UUID
    cite_id: str
    citation_string: str
    note: str
    added_by: str
    added_at: datetime
    change: str | None  # ADR-017: amended / derogated / removed since it was saved
    current_cite_id: str | None


class Report(BaseModel):
    id: uuid.UUID
    title: str
    summary: dict[str, int]
    created_at: datetime


class MatterDetail(BaseModel):
    matter: MatterOut
    members: list[Member]
    notes: list[Note]
    sources: list[SavedSource]
    reports: list[Report]


class Colleague(BaseModel):
    user_id: uuid.UUID
    display_name: str
    email: str


_MATTER_SQL = (
    "SELECT m.id, m.title, coalesce(m.client_ref, '') AS client_ref, "
    "coalesce(m.description, '') AS description, m.labor_regime, m.status, m.created_at, "
    "m.updated_at, "
    "CASE WHEN m.created_by = app_user_id() OR EXISTS (SELECT 1 FROM matter_members o "
    "  WHERE o.matter_id = m.id AND o.user_id = app_user_id() AND o.role = 'owner') "
    "  THEN 'owner' ELSE 'member' END AS role, "
    "(SELECT count(*) FROM matter_members mm WHERE mm.matter_id = m.id) AS members, "
    "(SELECT count(*) FROM conversations c WHERE c.matter_id = m.id) "
    "+ (SELECT count(*) FROM matter_notes n WHERE n.matter_id = m.id) "
    "+ (SELECT count(*) FROM matter_sources s WHERE s.matter_id = m.id) "
    "+ (SELECT count(*) FROM verification_reports r WHERE r.matter_id = m.id) AS items "
    "FROM matters m"
)


def _out(r: Any) -> MatterOut:
    return MatterOut(
        id=r.id,
        title=r.title,
        client_ref=r.client_ref,
        description=r.description,
        labor_regime=r.labor_regime,
        status=r.status,
        role=r.role,
        members=r.members,
        items=r.items,
        created_at=r.created_at,
        updated_at=r.updated_at,
    )


def _matter(conn: Connection, matter_id: uuid.UUID, *, owner: bool = False) -> MatterOut:
    row = conn.execute(text(f"{_MATTER_SQL} WHERE m.id = :id"), {"id": matter_id}).one_or_none()
    if row is None or (owner and row.role != "owner"):  # RLS: not a member = does not exist
        raise HTTPException(status.HTTP_404_NOT_FOUND, "matter not found")
    return _out(row)


def _touch(conn: Connection, matter_id: uuid.UUID) -> None:
    conn.execute(text("UPDATE matters SET updated_at = now() WHERE id = :m"), {"m": matter_id})


class RegimeOption(BaseModel):
    code: str
    label: str | None  # the law's own chapter name; None for ordinary and collective (UI text)


def _regime(code: str | None) -> str | None:
    """ADR-031: a closed list, like jurisdiction; anything else is a 422, never stored."""
    if code is None or code == "":
        return None
    if code not in CASE_REGIMES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "unknown labor regime")
    return code


@router.get("/regimes", response_model=list[RegimeOption])
def list_regimes(p: PrincipalDep) -> list[RegimeOption]:
    """The labour regimes a matter may declare (ADR-031), in the law's order."""
    return [RegimeOption(code=c, label=case_regime_label(c)) for c in CASE_REGIMES]


@router.get("", response_model=list[MatterOut])
def list_matters(p: PrincipalDep, st: StateDep) -> list[MatterOut]:
    with tenant_transaction(st.engine, p.tenant_id, user_id=p.user_id) as conn:
        rows = conn.execute(
            text(f"{_MATTER_SQL} ORDER BY m.status, m.updated_at DESC, m.id LIMIT 500")
        ).all()
    return [_out(r) for r in rows]


@router.post("", response_model=MatterOut, status_code=status.HTTP_201_CREATED)
def create_matter(body: MatterIn, p: PrincipalDep, st: StateDep) -> MatterOut:
    matter_id = uuid.uuid4()
    with tenant_transaction(st.engine, p.tenant_id, user_id=p.user_id) as conn:
        conn.execute(
            text(
                "INSERT INTO matters (id, tenant_id, created_by, title, client_ref, description, "
                "labor_regime) VALUES (:id, :t, :u, :title, NULLIF(:ref, ''), NULLIF(:d, ''), :lr)"
            ),
            {
                "id": matter_id,
                "t": p.tenant_id,
                "u": p.user_id,
                "title": body.title.strip(),
                "ref": body.client_ref.strip(),
                "d": body.description.strip(),
                "lr": _regime(body.labor_regime),
            },
        )
        conn.execute(
            text(
                "INSERT INTO matter_members (tenant_id, matter_id, user_id, role) "
                "VALUES (:t, :m, :u, 'owner')"
            ),
            {"t": p.tenant_id, "m": matter_id, "u": p.user_id},
        )
        return _matter(conn, matter_id)


@router.get("/colleagues", response_model=list[Colleague])
def colleagues(p: PrincipalDep, st: StateDep) -> list[Colleague]:
    """Users of the same firm who can be added to a matter."""
    with tenant_transaction(st.engine, p.tenant_id, user_id=p.user_id) as conn:
        rows = conn.execute(
            text(
                "SELECT id, display_name, email FROM users WHERE id <> :u "
                "ORDER BY display_name, email LIMIT 500"
            ),
            {"u": p.user_id},
        ).all()
    return [Colleague(user_id=r.id, display_name=r.display_name, email=r.email) for r in rows]


@router.get("/{matter_id}", response_model=MatterDetail)
def get_matter(matter_id: uuid.UUID, p: PrincipalDep, st: StateDep) -> MatterDetail:
    with tenant_transaction(st.engine, p.tenant_id, user_id=p.user_id) as conn:
        m = _matter(conn, matter_id)
        members = conn.execute(
            text(
                "SELECT u.id, u.display_name, u.email, mm.role FROM matter_members mm "
                "JOIN users u ON u.id = mm.user_id WHERE mm.matter_id = :m "
                "ORDER BY mm.role DESC, u.display_name"
            ),
            {"m": matter_id},
        ).all()
        notes = conn.execute(
            text(
                "SELECT n.id, n.body, u.display_name, n.author_id = app_user_id() AS mine, "
                "n.created_at FROM matter_notes n JOIN users u ON u.id = n.author_id "
                "WHERE n.matter_id = :m ORDER BY n.created_at DESC, n.id"
            ),
            {"m": matter_id},
        ).all()
        sources = conn.execute(
            text(
                "SELECT s.id, s.cite_id, "
                "coalesce(c.citation_string, s.cite_id) AS citation_string, "
                "coalesce(s.note, '') AS note, u.display_name, s.added_at FROM matter_sources s "
                "JOIN users u ON u.id = s.added_by LEFT JOIN chunks c ON c.cite_id = s.cite_id "
                "WHERE s.matter_id = :m ORDER BY s.added_at DESC, s.id"
            ),
            {"m": matter_id},
        ).all()
        succ = successions(conn, [s.cite_id for s in sources])
        reports = conn.execute(
            text(
                "SELECT id, title, summary, created_at FROM verification_reports "
                "WHERE matter_id = :m ORDER BY created_at DESC, id"
            ),
            {"m": matter_id},
        ).all()
    return MatterDetail(
        matter=m,
        members=[
            Member(user_id=r.id, display_name=r.display_name, email=r.email, role=r.role)
            for r in members
        ],
        notes=[
            Note(id=r.id, body=r.body, author=r.display_name, mine=r.mine, created_at=r.created_at)
            for r in notes
        ],
        sources=[
            SavedSource(
                id=r.id,
                cite_id=r.cite_id,
                citation_string=r.citation_string,
                note=r.note,
                added_by=r.display_name,
                added_at=r.added_at,
                change=(
                    succ[r.cite_id].change
                    if r.cite_id in succ and succ[r.cite_id].change != "unchanged"
                    else None
                ),
                current_cite_id=succ[r.cite_id].current if r.cite_id in succ else r.cite_id,
            )
            for r in sources
        ],
        reports=[
            Report(id=r.id, title=r.title, summary=dict(r.summary), created_at=r.created_at)
            for r in reports
        ],
    )


@router.patch("/{matter_id}", response_model=MatterOut)
def update_matter(
    matter_id: uuid.UUID, body: MatterPatch, p: PrincipalDep, st: StateDep
) -> MatterOut:
    with tenant_transaction(st.engine, p.tenant_id, user_id=p.user_id) as conn:
        cur = _matter(conn, matter_id, owner=True)
        conn.execute(
            text(
                "UPDATE matters SET title = :title, client_ref = NULLIF(:ref, ''), "
                "description = NULLIF(:d, ''), status = :s, labor_regime = :lr, "
                "updated_at = now() WHERE id = :id"
            ),
            {
                "id": matter_id,
                "title": (body.title or cur.title).strip(),
                "ref": (cur.client_ref if body.client_ref is None else body.client_ref).strip(),
                "d": (cur.description if body.description is None else body.description).strip(),
                "s": body.status or cur.status,
                "lr": cur.labor_regime if body.labor_regime is None else _regime(body.labor_regime),
            },
        )
        return _matter(conn, matter_id)


@router.delete("/{matter_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_matter(matter_id: uuid.UUID, p: PrincipalDep, st: StateDep) -> Response:
    """Deletes the matter, its notes, saved sources, memberships and its uploaded files (ADR-025:
    a matter's file must never fall back to the whole firm). Conversations and reports are NOT
    deleted: they go back to being private to their authors."""
    with tenant_transaction(st.engine, p.tenant_id, user_id=p.user_id) as conn:
        _matter(conn, matter_id, owner=True)
        conn.execute(text("DELETE FROM matters WHERE id = :id"), {"id": matter_id})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{matter_id}/members", status_code=status.HTTP_204_NO_CONTENT)
def add_member(matter_id: uuid.UUID, body: MemberIn, p: PrincipalDep, st: StateDep) -> Response:
    with tenant_transaction(st.engine, p.tenant_id, user_id=p.user_id) as conn:
        _matter(conn, matter_id, owner=True)
        if not conn.execute(text("SELECT 1 FROM users WHERE id = :u"), {"u": body.user_id}).first():
            raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")  # RLS: other firms
        conn.execute(
            text(
                "INSERT INTO matter_members (tenant_id, matter_id, user_id, role) "
                "VALUES (:t, :m, :u, 'member') ON CONFLICT (matter_id, user_id) DO NOTHING"
            ),
            {"t": p.tenant_id, "m": matter_id, "u": body.user_id},
        )
        _touch(conn, matter_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{matter_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_member(
    matter_id: uuid.UUID, user_id: uuid.UUID, p: PrincipalDep, st: StateDep
) -> Response:
    """The owner removes a member, or a member leaves. The owner cannot be removed."""
    with tenant_transaction(st.engine, p.tenant_id, user_id=p.user_id) as conn:
        m = _matter(conn, matter_id)
        if user_id != p.user_id and m.role != "owner":
            raise HTTPException(status.HTTP_404_NOT_FOUND, "matter not found")
        n = conn.execute(
            text(
                "DELETE FROM matter_members WHERE matter_id = :m AND user_id = :u "
                "AND role = 'member'"
            ),
            {"m": matter_id, "u": user_id},
        ).rowcount
        if not n:
            raise HTTPException(status.HTTP_409_CONFLICT, "not a removable member")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{matter_id}/notes", response_model=Note, status_code=status.HTTP_201_CREATED)
def add_note(matter_id: uuid.UUID, body: NoteIn, p: PrincipalDep, st: StateDep) -> Note:
    with tenant_transaction(st.engine, p.tenant_id, user_id=p.user_id) as conn:
        _matter(conn, matter_id)
        r = conn.execute(
            text(
                "INSERT INTO matter_notes (tenant_id, matter_id, author_id, body) "
                "VALUES (:t, :m, :u, :b) RETURNING id, created_at"
            ),
            {"t": p.tenant_id, "m": matter_id, "u": p.user_id, "b": body.body.strip()},
        ).one()
        _touch(conn, matter_id)
    return Note(
        id=r.id, body=body.body.strip(), author=p.display_name, mine=True, created_at=r.created_at
    )


@router.delete("/{matter_id}/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_note(
    matter_id: uuid.UUID, note_id: uuid.UUID, p: PrincipalDep, st: StateDep
) -> Response:
    with tenant_transaction(st.engine, p.tenant_id, user_id=p.user_id) as conn:
        _matter(conn, matter_id)
        n = conn.execute(
            text("DELETE FROM matter_notes WHERE id = :n AND matter_id = :m AND author_id = :u"),
            {"n": note_id, "m": matter_id, "u": p.user_id},
        ).rowcount
        if not n:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "note not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{matter_id}/sources", status_code=status.HTTP_204_NO_CONTENT)
def save_source(matter_id: uuid.UUID, body: SourceIn, p: PrincipalDep, st: StateDep) -> Response:
    """Save a citable unit to the matter. Only ids that exist (and the caller can see) are saved."""
    with tenant_transaction(st.engine, p.tenant_id, user_id=p.user_id) as conn:
        _matter(conn, matter_id)
        # ADR-025: a unit of a matter's file belongs to THAT matter. A lawyer in two matters sees
        # both files, so without this clause client A's contract could be saved into matter B.
        if not conn.execute(
            text(
                "SELECT 1 FROM chunks WHERE cite_id = :c AND (matter_id IS NULL OR matter_id = :m)"
            ),
            {"c": body.cite_id, "m": matter_id},
        ).first():
            raise HTTPException(status.HTTP_404_NOT_FOUND, "source not found")
        conn.execute(
            text(
                "INSERT INTO matter_sources (tenant_id, matter_id, cite_id, note, added_by) "
                "VALUES (:t, :m, :c, NULLIF(:n, ''), :u) ON CONFLICT (matter_id, cite_id) "
                "DO UPDATE SET note = coalesce(EXCLUDED.note, matter_sources.note)"
            ),
            {
                "t": p.tenant_id,
                "m": matter_id,
                "c": body.cite_id,
                "n": body.note.strip(),
                "u": p.user_id,
            },
        )
        _touch(conn, matter_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{matter_id}/sources/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_source(
    matter_id: uuid.UUID, source_id: uuid.UUID, p: PrincipalDep, st: StateDep
) -> Response:
    with tenant_transaction(st.engine, p.tenant_id, user_id=p.user_id) as conn:
        _matter(conn, matter_id)
        n = conn.execute(
            text("DELETE FROM matter_sources WHERE id = :s AND matter_id = :m"),
            {"s": source_id, "m": matter_id},
        ).rowcount
        if not n:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "source not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
