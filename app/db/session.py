"""RLS session anchor (multi-tenancy.md): every transaction that touches tenant data starts by
binding `app.tenant_id` to the validated tenant, and the binding dies with the transaction.

`SET LOCAL app.tenant_id = :t` cannot take bind parameters (SET is a utility statement), and string
interpolation is forbidden, so the anchor is `set_config('app.tenant_id', :t, true)` — the
documented equivalent of SET LOCAL (third argument = transaction-local), fully parameterized.

ADR-018: the same call also binds `app.user_id` (empty when not given, i.e. NULL for
`app_user_id()`): tables shared through matters are invisible without it.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

from sqlalchemy import Connection, Engine, text
from sqlalchemy.orm import Session

PUBLIC_CORPUS_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")

_ANCHOR = text(
    "SELECT set_config('app.tenant_id', :tenant_id, true), "
    "set_config('app.user_id', :user_id, true)"
)


class TenantContextError(ValueError):
    """The requested tenant context is not allowed for this caller."""


def _check(tenant_id: UUID, allow_public: bool) -> str:
    if not isinstance(tenant_id, UUID):  # never accept a free-form string as the tenant
        raise TenantContextError("tenant_id must be a UUID")
    if tenant_id == PUBLIC_CORPUS_TENANT_ID and not allow_public:
        raise TenantContextError(
            "PUBLIC_CORPUS is not a user tenant; only corpus ingestion/read paths may anchor on it"
        )
    return str(tenant_id)


def _user(user_id: UUID | None) -> str:
    if user_id is None:
        return ""
    if not isinstance(user_id, UUID):
        raise TenantContextError("user_id must be a UUID")
    return str(user_id)


@contextmanager
def tenant_transaction(
    engine: Engine,
    tenant_id: UUID,
    *,
    allow_public: bool = False,
    user_id: UUID | None = None,
) -> Iterator[Connection]:
    """One transaction anchored on `tenant_id` (and `user_id`, ADR-018); commits on success."""
    value = _check(tenant_id, allow_public)
    with engine.begin() as conn:
        conn.execute(_ANCHOR, {"tenant_id": value, "user_id": _user(user_id)})
        yield conn


@contextmanager
def tenant_session(
    engine: Engine,
    tenant_id: UUID,
    *,
    allow_public: bool = False,
    user_id: UUID | None = None,
) -> Iterator[Session]:
    """ORM flavour of `tenant_transaction`."""
    value = _check(tenant_id, allow_public)
    with Session(engine) as session, session.begin():
        session.execute(_ANCHOR, {"tenant_id": value, "user_id": _user(user_id)})
        yield session
