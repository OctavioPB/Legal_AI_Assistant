"""Application state and request dependencies. Thin: auth and delegation, no business logic."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import Engine, text

from app.api.auth import Principal, TokenError, decode_token
from app.chat.service import ChatDeps
from app.db.session import tenant_transaction
from app.orchestration.resilient import TenantUserLimits
from app.settings import Settings


class InlineJobs:
    """Runs a job in the caller's thread: tests, and anything that must finish before returning."""

    def submit(self, job: Callable[[], None]) -> None:
        job()


class WorkerJobs:
    """ONE background thread (ADR-025): parsing and embedding a file are CPU-heavy on a local
    machine, so uploads queue behind each other instead of starving the answers."""

    def __init__(self) -> None:
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="client-files")

    def submit(self, job: Callable[[], None]) -> None:
        self._pool.submit(job)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


class CodedError(Exception):
    """A refusal the client should translate: answered as {"detail": code, "code": code}."""

    def __init__(self, status_code: int, code: str) -> None:
        super().__init__(code)
        self.status_code, self.code = status_code, code


@dataclass
class AppState:
    settings: Settings
    engine: Engine  # the application role: DML under RLS, never DDL
    chat: ChatDeps
    limits: TenantUserLimits
    jobs: InlineJobs | WorkerJobs = field(default_factory=InlineJobs)
    # ADR-025 runs client-file jobs in THIS process: a job still `processing` from before this
    # moment was lost in a restart and can never finish.
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))


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
    # Deleted user, a token for a tenant that no longer owns it, or a firm suspended after the
    # token was issued: a still-unexpired token must not outlive the suspension.
    if row is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "unknown user")
    return Principal(
        user_id=row.id, tenant_id=tenant_id, role=row.role, email=row.email,
        display_name=row.display_name, preferred_language=row.preferred_language,
    )  # fmt: skip


_USER_SQL = text(
    "SELECT u.id, u.role, u.email, u.display_name, u.preferred_language "
    "FROM users u JOIN tenants t ON t.id = u.tenant_id "
    "WHERE u.id = :id AND t.status = 'active'"
)

PrincipalDep = Annotated[Principal, Depends(principal)]


def require_admin(p: PrincipalDep) -> Principal:
    if p.role not in ("owner", "admin"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin role required")
    return p
