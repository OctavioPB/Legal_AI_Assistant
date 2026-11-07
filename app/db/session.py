"""RLS session anchor (multi-tenancy.md): every transaction that touches tenant data starts by
binding `app.tenant_id` to the validated tenant, and the binding dies with the transaction.

`SET LOCAL app.tenant_id = :t` cannot take bind parameters (SET is a utility statement), and string
interpolation is forbidden, so the anchor is `set_config('app.tenant_id', :t, true)` — the
documented equivalent of SET LOCAL (third argument = transaction-local), fully parameterized.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

from sqlalchemy import Connection, Engine, text
from sqlalchemy.orm import Session

PUBLIC_CORPUS_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")

_ANCHOR = text("SELECT set_config('app.tenant_id', :tenant_id, true)")


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


@contextmanager
def tenant_transaction(
    engine: Engine, tenant_id: UUID, *, allow_public: bool = False
) -> Iterator[Connection]:
    """One transaction anchored on `tenant_id`; commits on success, rolls back on error."""
    value = _check(tenant_id, allow_public)
    with engine.begin() as conn:
        conn.execute(_ANCHOR, {"tenant_id": value})
        yield conn


@contextmanager
def tenant_session(
    engine: Engine, tenant_id: UUID, *, allow_public: bool = False
) -> Iterator[Session]:
    """ORM flavour of `tenant_transaction`."""
    value = _check(tenant_id, allow_public)
    with Session(engine) as session, session.begin():
        session.execute(_ANCHOR, {"tenant_id": value})
        yield session
