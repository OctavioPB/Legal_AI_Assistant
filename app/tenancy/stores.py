"""Single registry of every store that holds tenant data (WS7.T06, multi-tenancy.md).

Offboarding walks THIS registry and nothing else. A new store of tenant data must be added here:
the test fails if a Postgres table with a tenant column is not covered, or if a declared kind of
store has no registered implementation. Erasure never touches PUBLIC_CORPUS.

Postgres erasure runs on a privileged engine (a reviewed superuser step, never the app role):
`audit_log` is append-only for every role, so removing it needs the trigger bypassed, which is a
retention/legal decision (ADR-013). The default is `audit="retain"`.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Literal, Protocol

from sqlalchemy import Engine, text

from app.db.session import PUBLIC_CORPUS_TENANT_ID

# Every kind of store the product has or has declared. Adding a kind here without registering an
# implementation makes `StoreRegistry.assert_complete()` fail.
DECLARED_KINDS: tuple[str, ...] = (
    "postgres",
    "object_storage",
    "cache",
    "traces",
    "llm_gateway",
    "job_queue",
)

# Children before parents (composite FKs). `tenants` is tombstoned, never deleted: cite ids and
# audit references must not dangle.
POSTGRES_ERASE_ORDER: tuple[str, ...] = (
    "cite_lineage",
    "corpus_changes",
    "source_checks",
    "chunks",
    "doc_nodes",
    "documents",
    "matter_sources",
    "matter_notes",
    "verification_reports",
    "messages",
    "conversations",
    "matter_members",
    "matters",
    "dossiers",
    "tenant_model_policy",
    "sessions",
    "users",
)
POSTGRES_AUDIT_TABLE = "audit_log"


class ForbiddenTenantError(ValueError):
    """Erasure was requested for a tenant that must never be erased."""


def _check(tenant_id: uuid.UUID) -> None:
    if tenant_id == PUBLIC_CORPUS_TENANT_ID:
        raise ForbiddenTenantError("PUBLIC_CORPUS is never erased")


class TenantStore(Protocol):
    name: str
    kind: str

    def count(self, tenant_id: uuid.UUID) -> int:
        """How many items of this tenant remain (0 = no trace)."""
        ...

    def erase(self, tenant_id: uuid.UUID) -> int:
        """Remove every item of the tenant; returns how many were removed."""
        ...


class KeyBackend(Protocol):
    """What an external store must offer to be erasable by tenant."""

    def list_keys(self, tenant_id: uuid.UUID) -> list[str]: ...

    def delete_keys(self, tenant_id: uuid.UUID, keys: Iterable[str]) -> None: ...


@dataclass
class ExternalStore:
    """S3 prefix, Redis keys, Langfuse traces, LiteLLM team, queued Celery payloads: all reduce to
    "list this tenant's items, delete them" behind a backend."""

    name: str
    kind: str
    backend: KeyBackend

    def count(self, tenant_id: uuid.UUID) -> int:
        return len(self.backend.list_keys(tenant_id))

    def erase(self, tenant_id: uuid.UUID) -> int:
        _check(tenant_id)
        keys = self.backend.list_keys(tenant_id)
        self.backend.delete_keys(tenant_id, keys)
        return len(keys)


@dataclass
class PostgresStore:
    name: str = "postgres"
    kind: str = "postgres"
    engine: Engine | None = None  # PRIVILEGED engine: reviewed offboarding step only
    audit: Literal["retain", "erase"] = "retain"

    def _tables(self) -> list[str]:
        return [*POSTGRES_ERASE_ORDER, *([POSTGRES_AUDIT_TABLE] if self.audit == "erase" else [])]

    def count(self, tenant_id: uuid.UUID) -> int:
        assert self.engine is not None
        with self.engine.connect() as c:
            return sum(
                c.execute(
                    text(f"SELECT count(*) FROM {t} WHERE tenant_id = :t"),
                    {"t": tenant_id},
                ).scalar_one()
                for t in self._tables()
            )

    def erase(self, tenant_id: uuid.UUID) -> int:
        _check(tenant_id)
        assert self.engine is not None
        removed = 0
        with self.engine.begin() as c:
            if self.audit == "erase":  # append-only trigger bypass: superuser, in this tx only
                c.execute(text("SET LOCAL session_replication_role = replica"))
            for table in self._tables():
                removed += c.execute(
                    text(f"DELETE FROM {table} WHERE tenant_id = :t"),
                    {"t": tenant_id},
                ).rowcount
            c.execute(
                text("UPDATE tenants SET status = 'suspended' WHERE id = :t"), {"t": tenant_id}
            )
        return removed


@dataclass
class OffboardingReport:
    tenant_id: uuid.UUID
    removed: dict[str, int] = field(default_factory=dict)
    remaining: dict[str, int] = field(default_factory=dict)

    @property
    def clean(self) -> bool:
        return all(n == 0 for n in self.remaining.values())


class RegistryIncompleteError(RuntimeError):
    """A declared kind of store has no registered implementation."""


class StoreRegistry:
    def __init__(self, stores: Iterable[TenantStore] = ()) -> None:
        self._stores: list[TenantStore] = []
        for s in stores:
            self.register(s)

    def register(self, store: TenantStore) -> None:
        if any(s.name == store.name for s in self._stores):
            raise ValueError(f"store {store.name!r} registered twice")
        if store.kind not in DECLARED_KINDS:
            raise ValueError(f"unknown store kind {store.kind!r}: declare it in DECLARED_KINDS")
        self._stores.append(store)

    @property
    def stores(self) -> tuple[TenantStore, ...]:
        return tuple(self._stores)

    def missing_kinds(self) -> set[str]:
        return set(DECLARED_KINDS) - {s.kind for s in self._stores}

    def assert_complete(self) -> None:
        if missing := self.missing_kinds():
            raise RegistryIncompleteError(f"no store registered for: {sorted(missing)}")

    def offboard(
        self, tenant_id: uuid.UUID, *, on_progress: Callable[[str], None] | None = None
    ) -> OffboardingReport:
        _check(tenant_id)
        self.assert_complete()  # never offboard through a registry that forgot a store
        report = OffboardingReport(tenant_id)
        for s in self._stores:
            report.removed[s.name] = s.erase(tenant_id)
            if on_progress:
                on_progress(s.name)
        for s in self._stores:
            report.remaining[s.name] = s.count(tenant_id)
        return report
