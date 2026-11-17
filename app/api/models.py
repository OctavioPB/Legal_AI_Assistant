"""Model profiles a firm can use (ADR-024).

`GET /models` lists the closed catalog with, for each profile: where it runs, the pinned model id,
whether this server can serve it, whether the firm allows it, and its golden measurement (or none).
`POST /models/policy` is the firm admin's explicit opt-in for remote models. Both run under RLS.
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Connection, text

from app.api.auth import Principal
from app.api.deps import PrincipalDep, StateDep, require_admin
from app.db.session import tenant_transaction
from app.orchestration.profiles import (
    PROFILE_KEYS,
    ProfileCatalog,
    ProfileUnavailableError,
    UnknownProfileError,
)

router = APIRouter(prefix="/models")


class ModelOption(BaseModel):
    key: str
    locality: str
    model_id: str | None
    available: bool
    unavailable_reason: str | None
    allowed: bool  # by the firm's policy (local profiles are always allowed)
    default: bool
    measurement: dict[str, Any] | None  # golden run backing the label; None = not measured


class ModelsOut(BaseModel):
    options: list[ModelOption]
    allow_remote: bool
    can_manage: bool


class PolicyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow_remote: bool


def firm_allows_remote(conn: Connection) -> bool:
    """The firm's opt-in (ADR-024). No row = not allowed; RLS shows only this firm's row."""
    return bool(conn.execute(text("SELECT allow_remote FROM tenant_model_policy")).scalar())


class ProfileRefusedError(Exception):
    """A conversation may not use this profile; `code` is a catalog key the UI translates."""

    def __init__(self, status_code: int, code: str) -> None:
        super().__init__(code)
        self.status_code, self.code = status_code, code


def check_profile(conn: Connection, catalog: ProfileCatalog | None, key: str) -> None:
    """422 for an unknown or unservable profile, 403 for a remote one the firm did not allow.
    Run at creation AND before every message (a firm can withdraw its opt-in)."""
    if catalog is None:
        if key != "gemma":
            raise ProfileRefusedError(status.HTTP_422_UNPROCESSABLE_CONTENT, "model_unknown")
        return
    try:
        p = catalog.serving(key)
    except UnknownProfileError:
        raise ProfileRefusedError(status.HTTP_422_UNPROCESSABLE_CONTENT, "model_unknown") from None
    except ProfileUnavailableError:
        raise ProfileRefusedError(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "model_unavailable"
        ) from None
    if p.locality == "remote" and not firm_allows_remote(conn):
        raise ProfileRefusedError(status.HTTP_403_FORBIDDEN, "model_not_allowed")


def register_model_errors(app: Any) -> None:
    from fastapi.responses import JSONResponse

    def refused(_: Any, exc: Exception) -> JSONResponse:
        assert isinstance(exc, ProfileRefusedError)
        return JSONResponse({"detail": exc.code, "code": exc.code}, exc.status_code)

    app.add_exception_handler(ProfileRefusedError, refused)


@router.get("", response_model=ModelsOut)
def list_models(p: PrincipalDep, st: StateDep) -> ModelsOut:
    with tenant_transaction(st.engine, p.tenant_id, user_id=p.user_id) as conn:
        allow = firm_allows_remote(conn)
    cat = st.chat.profiles
    options: list[ModelOption] = []
    for key in PROFILE_KEYS:
        if cat is None or key not in cat.profiles:
            continue
        prof = cat.profiles[key]
        options.append(
            ModelOption(
                key=key,
                locality=prof.locality,
                model_id=prof.model_id,
                available=prof.llm is not None,
                unavailable_reason=None if prof.llm is not None else prof.unavailable,
                allowed=prof.locality == "local" or allow,
                default=key == cat.default,
                measurement=cat.measurements.get(key),
            )
        )
    return ModelsOut(options=options, allow_remote=allow, can_manage=p.role in ("owner", "admin"))


@router.post("/policy", response_model=ModelsOut)
def set_policy(
    body: PolicyIn, p: Annotated[Principal, Depends(require_admin)], st: StateDep
) -> ModelsOut:
    """Only an owner/admin of the firm. Withdrawing the opt-in stops remote conversations at
    their next message (checked per message)."""
    with tenant_transaction(st.engine, p.tenant_id, user_id=p.user_id) as conn:
        previous = firm_allows_remote(conn)
        conn.execute(
            text(
                "INSERT INTO tenant_model_policy (tenant_id, allow_remote, updated_by, updated_at) "
                "VALUES (:t, :a, :u, now()) ON CONFLICT (tenant_id) DO UPDATE SET "
                "allow_remote = EXCLUDED.allow_remote, updated_by = EXCLUDED.updated_by, "
                "updated_at = now()"
            ),
            {"t": p.tenant_id, "a": body.allow_remote, "u": p.user_id},
        )
        # Allowing a remote model decides that client content leaves the machine (ADR-024):
        # who changed it and from what is part of the audit trail.
        conn.execute(
            text(
                "INSERT INTO audit_log (tenant_id, user_id, event_type, filters, verdict) "
                "VALUES (:t, :u, 'admin', CAST(:f AS jsonb), CAST(:v AS jsonb))"
            ),
            {"t": p.tenant_id, "u": p.user_id, "f": json.dumps({"action": "model_policy"}),
             "v": json.dumps({"allow_remote": body.allow_remote, "previous": previous})},
        )  # fmt: skip
    return list_models(p, st)
