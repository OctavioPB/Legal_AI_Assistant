"""Persisted conversations for the three workspaces (ADR-016): Conversar (`chat`), Investigación
(`research`) and Argumentar (`case`).

Every statement runs inside `tenant_transaction` anchored on the firm AND the user (ADR-018): RLS
shows
a conversation to its author, or to the members of the matter it belongs to; a colleague outside the
matter gets 404. Only the author renames, moves or deletes it. The session context (jurisdiction,
persona, language) is fixed at creation and validated against the catalog; a message can only use
the
actions its kind allows.
"""

from __future__ import annotations

import json
import queue
import threading
import uuid
from collections.abc import Iterator
from datetime import date, datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import Connection, text

from app.api.deps import CodedError, PrincipalDep, StateDep
from app.api.models import check_profile
from app.api.routes import log, stream_error_code
from app.chat.service import (
    MAX_QUERY_CHARS,
    CaseBrief,
    CaseSide,
    ChatRequest,
    ChatResult,
    Turn,
    answer_query,
)
from app.corpus.lineage import carry_forward
from app.db.session import tenant_transaction
from app.orchestration.profiles import ProfileKey
from app.prompts.composer import Persona, Workflow
from app.rag.context import UnknownJurisdictionError, load_session_context

router = APIRouter(prefix="/conversations")

Kind = Literal["chat", "research", "case"]
Action = Literal["ask", "research", "attack", "strengthen"]
# kind -> action -> workflow. Anything else is refused before any retrieval or model call.
ACTIONS: dict[str, dict[str, Workflow]] = {
    "chat": {"ask": "conversar"},
    "research": {"research": "dossier_builder"},
    "case": {"attack": "adversarial", "strengthen": "argumentar"},
}
TITLE_MAX = 200
CASE_TEXT_MAX = 8000


class CaseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    side: CaseSide
    facts: str = Field(min_length=1, max_length=CASE_TEXT_MAX)
    claim: str = Field(default="", max_length=CASE_TEXT_MAX)


class ConversationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Kind
    title: str = Field(min_length=1, max_length=TITLE_MAX)
    jurisdiction_id: int = Field(gt=0)
    persona: Persona
    language: Literal["es", "en"] = "es"
    include_historical: bool = False
    case: CaseIn | None = None
    matter_id: uuid.UUID | None = None
    model_profile: ProfileKey | None = None  # ADR-024; None = the server's local default
    # ADR-050: the date of the facts; the law is read as of it (None: today's current text)
    as_of: date | None = None

    @model_validator(mode="after")
    def _case_only_for_cases(self) -> ConversationIn:
        if (self.kind == "case") != (self.case is not None):
            raise ValueError("a case needs `case`; other kinds must not send it")
        if self.as_of is not None and self.include_historical:
            raise ValueError("as_of and include_historical exclude each other")
        if self.as_of is not None and not date(1900, 1, 1) <= self.as_of <= date.today():
            raise ValueError("as_of must be a past date (the date of the facts)")
        return self


class RenameIn(BaseModel):
    """Rename and/or move to a matter (`move` with `matter_id: null` takes it out of its matter)."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=TITLE_MAX)
    matter_id: uuid.UUID | None = None
    move: bool = False


class MessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=MAX_QUERY_CHARS)
    action: Action


class ConversationOut(BaseModel):
    id: uuid.UUID
    kind: Kind
    title: str
    language: str
    jurisdiction_id: int
    persona: str
    include_historical: bool
    case: dict[str, str] | None
    created_at: datetime
    updated_at: datetime
    message_count: int = 0
    matter_id: uuid.UUID | None = None
    mine: bool = True
    model_profile: str = "gemma"
    as_of: date | None = None  # ADR-050


class MessageOut(BaseModel):
    seq: int
    role: Literal["user", "assistant"]
    action: Action
    content: str
    status: Literal["answered", "refused"] | None
    refusal_reason: str | None
    citations: list[dict[str, str | None]]
    request_id: uuid.UUID | None
    created_at: datetime


class ConversationDetail(BaseModel):
    conversation: ConversationOut
    messages: list[MessageOut]


_CONV_COLS = (
    "c.id, c.kind, c.title, c.language, c.jurisdiction_id, c.persona, c.include_historical, "
    "c.case_side, c.case_facts, c.case_claim, c.created_at, c.updated_at, c.matter_id, "
    "c.user_id = app_user_id() AS mine, c.model_profile, c.as_of_date, "
    # ADR-031: the matter's declared regime (RLS: NULL when the matter is not visible)
    "(SELECT m.labor_regime FROM matters m WHERE m.id = c.matter_id) AS labor_regime"
)


def _conv(row: Any, count: int = 0) -> ConversationOut:
    case = (
        {"side": row.case_side, "facts": row.case_facts, "claim": row.case_claim or ""}
        if row.kind == "case"
        else None
    )
    return ConversationOut(
        id=row.id, kind=row.kind, title=row.title, language=row.language,
        jurisdiction_id=row.jurisdiction_id, persona=row.persona,
        include_historical=row.include_historical, case=case, created_at=row.created_at,
        updated_at=row.updated_at, message_count=count, matter_id=row.matter_id, mine=row.mine,
        model_profile=row.model_profile, as_of=row.as_of_date,
    )  # fmt: skip


def _own(conn: Connection, conv_id: uuid.UUID, *, author: bool = False) -> Any:
    """The conversation if RLS shows it (author or matter member); `author=True` also requires the
    caller to be its author. Anything else looks like a conversation that does not exist."""
    row = conn.execute(
        text(f"SELECT {_CONV_COLS} FROM conversations c WHERE c.id = :id"), {"id": conv_id}
    ).one_or_none()
    if row is None or (author and not row.mine):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "conversation not found")
    return row


def check_matter(conn: Connection, matter_id: uuid.UUID | None) -> None:
    """404 unless the caller can see the (open) matter: RLS hides the others (ADR-018)."""
    if (
        matter_id is not None
        and not conn.execute(
            text("SELECT 1 FROM matters WHERE id = :m AND status = 'open'"), {"m": matter_id}
        ).first()
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "matter not found")


def _cites_files_outside(conn: Connection, conv_id: uuid.UUID, target: uuid.UUID) -> bool:
    """True if the conversation cited a firm document that may not be read in `target` (ADR-025):
    a unit of another matter's file, or a CF- unit the caller can no longer resolve (its file was
    deleted, or the caller left its matter). The stored answers quote and paraphrase what they
    cite, so moving them would show another client's document to the members of `target`."""
    row = conn.execute(
        text(
            "SELECT 1 FROM messages m CROSS JOIN LATERAL jsonb_array_elements(m.citations) AS x(c) "
            "LEFT JOIN chunks k ON k.cite_id = x.c->>'cite_id' "
            "WHERE m.conversation_id = :id AND x.c->>'cite_id' LIKE 'CF-%' "
            "AND (k.id IS NULL OR (k.matter_id IS NOT NULL AND k.matter_id <> :t)) LIMIT 1"
        ),
        {"id": conv_id, "t": target},
    ).first()
    return row is not None


def _messages(conn: Connection, conv_id: uuid.UUID) -> list[MessageOut]:
    rows = conn.execute(
        text(
            "SELECT seq, role, action, content, status, refusal_reason, citations, request_id, "
            "created_at FROM messages WHERE conversation_id = :c ORDER BY seq"
        ),
        {"c": conv_id},
    ).all()
    return [
        MessageOut(
            seq=r.seq,
            role=r.role,
            action=r.action,
            content=r.content,
            status=r.status,
            refusal_reason=r.refusal_reason,
            citations=list(r.citations),
            request_id=r.request_id,
            created_at=r.created_at,
        )
        for r in rows
    ]


@router.get("", response_model=list[ConversationOut])
def list_conversations(
    p: PrincipalDep,
    st: StateDep,
    kind: Kind | None = None,
    q: str | None = Query(default=None, max_length=200),
    matter: uuid.UUID | None = None,
) -> list[ConversationOut]:
    """The caller's conversations of one kind, newest first; `q` searches titles and messages.
    With `matter`: every conversation of that matter (any member's)."""
    if kind is None and matter is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "kind or matter required")
    params: dict[str, Any] = {"u": p.user_id, "m": matter}
    where = "c.matter_id = :m" if matter is not None else "c.user_id = :u"
    if kind is not None:
        params["k"] = kind
        where += " AND c.kind = :k"
    if q and q.strip():
        # parameterized ILIKE; % and _ typed by the user are matched literally
        params["q"] = (
            "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        )
        where += (
            " AND (c.title ILIKE :q OR EXISTS (SELECT 1 FROM messages m WHERE "
            "m.conversation_id = c.id AND m.content ILIKE :q))"
        )
    with tenant_transaction(st.engine, p.tenant_id, user_id=p.user_id) as conn:
        rows = conn.execute(
            text(
                f"SELECT {_CONV_COLS}, (SELECT count(*) FROM messages m "
                f"WHERE m.conversation_id = c.id) AS n FROM conversations c WHERE {where} "
                "ORDER BY c.updated_at DESC, c.id LIMIT 200"
            ),
            params,
        ).all()
    return [_conv(r, r.n) for r in rows]


@router.post("", response_model=ConversationOut, status_code=status.HTTP_201_CREATED)
def create_conversation(body: ConversationIn, p: PrincipalDep, st: StateDep) -> ConversationOut:
    with tenant_transaction(st.engine, p.tenant_id, user_id=p.user_id) as conn:
        check_matter(conn, body.matter_id)
        profile = body.model_profile or st.settings.model_profile_default
        check_profile(conn, st.chat.profiles, profile)  # ADR-024: catalog, server, firm opt-in
        try:  # the catalog decides: an unknown id never becomes a conversation
            load_session_context(conn, p.tenant_id, body.jurisdiction_id, language=body.language)
        except UnknownJurisdictionError:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "unknown jurisdiction"
            ) from None
        c = body.case
        row = conn.execute(
            text(
                "INSERT INTO conversations AS c (tenant_id, user_id, kind, title, language, "
                "jurisdiction_id, persona, include_historical, case_side, case_facts, case_claim, "
                "matter_id, model_profile, as_of_date) "
                "VALUES (:t, :u, :k, :title, :lang, :j, :persona, :h, :side, :facts, :claim, :m, "
                ":profile, :asof) "
                f"RETURNING {_CONV_COLS}"
            ),
            {
                "t": p.tenant_id,
                "u": p.user_id,
                "k": body.kind,
                "title": body.title.strip(),
                "lang": body.language,
                "j": body.jurisdiction_id,
                "persona": body.persona,
                "h": body.include_historical,
                "side": c.side if c else None,
                "facts": c.facts if c else None,
                "claim": c.claim if c else None,
                "m": body.matter_id,
                "profile": profile,
                "asof": body.as_of,
            },
        ).one()
    return _conv(row)


@router.get("/{conv_id}", response_model=ConversationDetail)
def get_conversation(conv_id: uuid.UUID, p: PrincipalDep, st: StateDep) -> ConversationDetail:
    with tenant_transaction(st.engine, p.tenant_id, user_id=p.user_id) as conn:
        row = _own(conn, conv_id)
        msgs = _messages(conn, conv_id)
    return ConversationDetail(conversation=_conv(row, len(msgs)), messages=msgs)


@router.patch("/{conv_id}", response_model=ConversationOut)
def rename_conversation(
    conv_id: uuid.UUID, body: RenameIn, p: PrincipalDep, st: StateDep
) -> ConversationOut:
    if body.title is None and not body.move:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "nothing to change")
    with tenant_transaction(st.engine, p.tenant_id, user_id=p.user_id) as conn:
        cur = _own(conn, conv_id, author=True)
        if body.move:
            check_matter(conn, body.matter_id)
            if body.matter_id is not None and _cites_files_outside(conn, conv_id, body.matter_id):
                raise CodedError(status.HTTP_409_CONFLICT, "cites_matter_files")
        row = conn.execute(
            text(
                "UPDATE conversations AS c SET title = :title, matter_id = :m, updated_at = now() "
                f"WHERE c.id = :id AND c.user_id = :u RETURNING {_CONV_COLS}"
            ),
            {
                "title": body.title.strip() if body.title else cur.title,
                "m": body.matter_id if body.move else cur.matter_id,
                "id": conv_id,
                "u": p.user_id,
            },
        ).one()
    return _conv(row)


@router.delete("/{conv_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(conv_id: uuid.UUID, p: PrincipalDep, st: StateDep) -> Response:
    """Deletes the conversation and its messages. The audit log keeps its own copy (ADR-013)."""
    with tenant_transaction(st.engine, p.tenant_id, user_id=p.user_id) as conn:
        _own(conn, conv_id, author=True)
        conn.execute(
            text("DELETE FROM conversations WHERE id = :id AND user_id = :u"),
            {"id": conv_id, "u": p.user_id},
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _request_for(row: Any, msgs: list[MessageOut], body: MessageIn) -> ChatRequest:
    workflow = ACTIONS[row.kind][body.action]
    history: tuple[Turn, ...] = ()
    carry: tuple[str, ...] = ()
    if row.kind != "research":  # a dossier answers its own question; chat and cases build on turns
        turns: list[Turn] = []
        for m in msgs:
            if m.role == "user":
                turns.append(Turn(role="user", content=m.content))
            elif m.status == "answered":  # a refusal carries no content worth showing the model
                turns.append(Turn(role="assistant", content=m.content))
        history = tuple(turns)
        seen: list[str] = []
        for m in reversed(msgs):  # newest first, so the carry limit keeps the recent sources
            for c in m.citations:
                cid = c["cite_id"]
                if cid and cid not in seen:
                    seen.append(cid)
        carry = tuple(seen)
    case = (
        CaseBrief(side=row.case_side, facts=row.case_facts, claim=row.case_claim or "")
        if row.kind == "case"
        else None
    )
    return ChatRequest(
        query=body.text, persona=row.persona, workflow=workflow, history=history, carry=carry,
        case=case, labor_regime=row.labor_regime,
    )  # fmt: skip


def _store_turn(
    conn: Connection, p: Any, conv_id: uuid.UUID, body: MessageIn, result: ChatResult, seq: int
) -> None:
    conn.execute(
        text(
            "INSERT INTO messages (tenant_id, conversation_id, seq, role, action, content) "
            "VALUES (:t, :c, :s, 'user', :a, :content)"
        ),
        {"t": p.tenant_id, "c": conv_id, "s": seq, "a": body.action, "content": body.text},
    )
    conn.execute(
        text(
            "INSERT INTO messages (tenant_id, conversation_id, seq, role, action, content, status, "
            "refusal_reason, citations, request_id) VALUES (:t, :c, :s, 'assistant', :a, :content, "
            ":status, :reason, CAST(:cites AS jsonb), :rid)"
        ),
        {
            "t": p.tenant_id,
            "c": conv_id,
            "s": seq + 1,
            "a": body.action,
            "content": result.answer if result.status == "answered" else result.refusal_block,
            "status": result.status,
            "reason": result.refusal_reason,
            "cites": json.dumps([c.model_dump() for c in result.citations], ensure_ascii=False),
            "rid": result.request_id,
        },
    )
    conn.execute(text("UPDATE conversations SET updated_at = now() WHERE id = :c"), {"c": conv_id})


@router.post("/{conv_id}/messages/stream")
def post_message(
    conv_id: uuid.UUID, body: MessageIn, p: PrincipalDep, st: StateDep
) -> StreamingResponse:
    """SSE like /chat/stream: stages, then ONE `result` event with the stored messages. The answer
    is shown only after the validator accepted it; nothing is stored for a failed call."""
    st.limits.check(str(p.tenant_id), str(p.user_id))
    with tenant_transaction(
        st.engine, p.tenant_id, user_id=p.user_id
    ) as conn:  # fail fast, before the stream
        row = _own(conn, conv_id)
        if body.action not in ACTIONS[row.kind]:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, f"action {body.action!r} not allowed here"
            )
        # ADR-024: the conversation's model, still servable and still allowed by the firm
        check_profile(conn, st.chat.profiles, row.model_profile)
    deps = st.chat.for_profile(row.model_profile)
    events: queue.Queue[tuple[str, Any] | None] = queue.Queue()

    def work() -> None:
        try:
            with tenant_transaction(st.engine, p.tenant_id, user_id=p.user_id) as conn:
                row = _own(conn, conv_id)
                msgs = _messages(conn, conv_id)
                req = _request_for(row, msgs, body)
                # ADR-017: after a reform, carry only what the lineage says is unchanged
                req = req.model_copy(update={"carry": carry_forward(conn, req.carry)})
                ctx = load_session_context(
                    conn, p.tenant_id, row.jurisdiction_id, language=row.language,
                    include_historical=row.include_historical, matter_id=row.matter_id,
                    as_of=row.as_of_date,
                )  # fmt: skip
                result = answer_query(conn, ctx, req, deps, lambda s: events.put(("stage", s)))
                seq = (msgs[-1].seq if msgs else 0) + 1
                _store_turn(conn, p, conv_id, body, result, seq)
                stored = _messages(conn, conv_id)[-2:]
            events.put(("result", {
                "result": result.model_dump(mode="json"),
                "messages": [m.model_dump(mode="json") for m in stored],
            }))  # fmt: skip
        except HTTPException:
            events.put(("error", {"code": "not_found"}))
        except UnknownJurisdictionError:
            events.put(("error", {"code": "unknown_jurisdiction"}))
        except Exception as exc:
            code = stream_error_code(exc)
            if code == "internal":
                log.exception("conversation stream failed")
            events.put(("error", {"code": code}))
        finally:
            events.put(None)

    threading.Thread(target=work, daemon=True).start()

    def gen() -> Iterator[str]:
        while (item := events.get()) is not None:
            name, data = item
            yield f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        gen(), media_type="text/event-stream", headers={"Cache-Control": "no-store"}
    )
