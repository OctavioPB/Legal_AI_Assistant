"""The key-points sheet of a matter (ADR-028). Any member of the matter generates and reads its
sheets (RLS `matter_child`: as visible as the matter). Generation streams stages like a message (the
local model takes tens of seconds), then ONE `result` event with the stored sheet."""

from __future__ import annotations

import json
import queue
import threading
import uuid
from collections.abc import Iterator
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Connection, text

from app.api.deps import PrincipalDep, StateDep
from app.api.models import check_profile
from app.api.routes import log, stream_error_code
from app.chat.cheatsheet import MatterNotFoundError, as_row, build_cheatsheet
from app.db.session import tenant_transaction
from app.orchestration.profiles import ProfileKey
from app.rag.context import UnknownJurisdictionError

router = APIRouter(prefix="/matters/{matter_id}/cheatsheets")


class CheatsheetIn(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    language: Literal["es", "en"] = "es"
    model_profile: ProfileKey | None = None  # ADR-024; None = the server's local default


class CheatsheetOut(BaseModel):
    id: uuid.UUID
    matter_id: uuid.UUID
    created_at: datetime
    created_by_name: str | None
    language: str
    model_profile: str
    status: Literal["answered", "refused"]
    content: str | None
    refusal_reason: str | None
    citations: list[dict[str, str | None]]
    material: dict[str, int]


_COLS = (
    "s.id, s.matter_id, s.created_at, u.display_name AS created_by_name, s.language, "
    "s.model_profile, s.status, s.content, s.refusal_reason, s.citations, s.material"
)


def _out(r: Any) -> CheatsheetOut:
    return CheatsheetOut(
        id=r.id,
        matter_id=r.matter_id,
        created_at=r.created_at,
        created_by_name=r.created_by_name,
        language=r.language,
        model_profile=r.model_profile,
        status=r.status,
        content=r.content,
        refusal_reason=r.refusal_reason,
        citations=list(r.citations),
        material={k: int(v) for k, v in dict(r.material).items()},
    )


def _visible_matter(conn: Connection, matter_id: uuid.UUID) -> None:
    if conn.execute(text("SELECT 1 FROM matters WHERE id = :m"), {"m": matter_id}).first() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "matter not found")


@router.get("", response_model=list[CheatsheetOut])
def list_cheatsheets(matter_id: uuid.UUID, p: PrincipalDep, st: StateDep) -> list[CheatsheetOut]:
    with tenant_transaction(st.engine, p.tenant_id, user_id=p.user_id) as conn:
        _visible_matter(conn, matter_id)
        rows = conn.execute(
            text(
                f"SELECT {_COLS} FROM matter_cheatsheets s "
                "LEFT JOIN users u ON u.id = s.created_by "
                "WHERE s.matter_id = :m ORDER BY s.created_at DESC, s.id LIMIT 20"
            ),
            {"m": matter_id},
        ).all()
    return [_out(r) for r in rows]


@router.get("/{sheet_id}", response_model=CheatsheetOut)
def get_cheatsheet(
    matter_id: uuid.UUID, sheet_id: uuid.UUID, p: PrincipalDep, st: StateDep
) -> CheatsheetOut:
    with tenant_transaction(st.engine, p.tenant_id, user_id=p.user_id) as conn:
        r = conn.execute(
            text(
                f"SELECT {_COLS} FROM matter_cheatsheets s "
                "LEFT JOIN users u ON u.id = s.created_by "
                "WHERE s.matter_id = :m AND s.id = :s"
            ),
            {"m": matter_id, "s": sheet_id},
        ).one_or_none()
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "sheet not found")
    return _out(r)


@router.post("/stream")
def generate(
    matter_id: uuid.UUID, body: CheatsheetIn, p: PrincipalDep, st: StateDep
) -> StreamingResponse:
    st.limits.check(str(p.tenant_id), str(p.user_id))
    profile = body.model_profile or st.settings.model_profile_default
    with tenant_transaction(st.engine, p.tenant_id, user_id=p.user_id) as conn:  # fail fast
        _visible_matter(conn, matter_id)
        check_profile(conn, st.chat.profiles, profile)  # ADR-024: servable and allowed by the firm
    deps = st.chat.for_profile(profile)
    events: queue.Queue[tuple[str, Any] | None] = queue.Queue()

    def work() -> None:
        try:
            with tenant_transaction(st.engine, p.tenant_id, user_id=p.user_id) as conn:
                res = build_cheatsheet(
                    conn, p.tenant_id, p.user_id, matter_id, body.language, deps,
                    lambda s: events.put(("stage", s)),
                )  # fmt: skip
                row = as_row(res)
                sid = conn.execute(
                    text(
                        "INSERT INTO matter_cheatsheets (tenant_id, matter_id, created_by, "
                        "language, "
                        "model_profile, status, content, citations, refusal_reason, material, "
                        "request_id) VALUES (:t, :m, :u, :lang, :prof, :status, :content, "
                        "CAST(:cites AS jsonb), :reason, CAST(:material AS jsonb), :rid) "
                        "RETURNING id"
                    ),
                    {
                        "t": p.tenant_id,
                        "m": matter_id,
                        "u": p.user_id,
                        "lang": body.language,
                        "prof": deps.model_profile,
                        "status": row["status"],
                        "content": row["content"],
                        "cites": json.dumps(row["citations"], ensure_ascii=False),
                        "reason": row["refusal_reason"],
                        "material": json.dumps(row["material"]),
                        "rid": row["request_id"],
                    },
                ).scalar_one()
                stored = conn.execute(
                    text(
                        f"SELECT {_COLS} FROM matter_cheatsheets s "
                        "LEFT JOIN users u ON u.id = s.created_by WHERE s.id = :s"
                    ),
                    {"s": sid},
                ).one()
            events.put(("result", _out(stored).model_dump(mode="json")))
        except MatterNotFoundError:
            events.put(("error", {"code": "not_found"}))
        except UnknownJurisdictionError:
            events.put(("error", {"code": "unknown_jurisdiction"}))
        except Exception as exc:
            code = stream_error_code(exc)
            if code == "internal":
                log.exception("cheatsheet stream failed")
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
