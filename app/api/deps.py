"""Application state and request dependencies. Thin: auth and delegation, no business logic."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import Engine, text

from app.api.auth import Principal, TokenError, decode_token
from app.chat.service import ChatDeps
from app.db.session import tenant_transaction
from app.orchestration.resilient import TenantUserLimits
from app.settings import Settings


@dataclass
class AppState:
    settings: Settings
    engine: Engine  # the application role: DML under RLS, never DDL
    chat: ChatDeps
    limits: TenantUserLimits


def state(request: Request) -> AppState:
    st: AppState = request.app.state.legal
    return st


StateDep = Annotated[AppState, Depends(state)]


def principal(st: StateDep, authorization: Annotated[str | None, Header()] = None) -> Principal:
    """Verify the bearer token and load the user under the TOKEN's tenant (RLS applies)."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    try:
        user_id, tenant_id, _role = decode_token(authorization[7:].strip(), st.settings)
    except TokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or expired token") from exc
    with tenant_transaction(st.engine, tenant_id) as conn:
        row = conn.execute(
            _USER_SQL,
            {"id": user_id},
        ).one_or_none()
    if row is None:  # deleted user, or a token for a tenant that no longer owns it
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "unknown user")
    return Principal(
        user_id=row.id, tenant_id=tenant_id, role=row.role, email=row.email,
        display_name=row.display_name, preferred_language=row.preferred_language,
    )  # fmt: skip


_USER_SQL = text(
    "SELECT id, role, email, display_name, preferred_language FROM users WHERE id = :id"
)

PrincipalDep = Annotated[Principal, Depends(principal)]


def require_admin(p: PrincipalDep) -> Principal:
    if p.role not in ("owner", "admin"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin role required")
    return p


def new_request_id() -> uuid.UUID:
    return uuid.uuid4()
