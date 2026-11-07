"""Offboarding leaves no trace in any registered store; an unregistered store fails the build."""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable

import pytest
from sqlalchemy import Engine, text

from app.db.session import PUBLIC_CORPUS_TENANT_ID, tenant_transaction
from app.tenancy.stores import (
    DECLARED_KINDS,
    POSTGRES_AUDIT_TABLE,
    POSTGRES_ERASE_ORDER,
    ExternalStore,
    ForbiddenTenantError,
    PostgresStore,
    RegistryIncompleteError,
    StoreRegistry,
)
from app.validation.audit import write_audit
from tests import helpers


class DictBackend:
    """In-memory external store: {tenant: {key: payload}}."""

    def __init__(self) -> None:
        self.data: dict[uuid.UUID, dict[str, str]] = defaultdict(dict)

    def list_keys(self, tenant_id: uuid.UUID) -> list[str]:
        return list(self.data[tenant_id])

    def delete_keys(self, tenant_id: uuid.UUID, keys: Iterable[str]) -> None:
        for k in list(keys):
            self.data[tenant_id].pop(k, None)


def external(kind: str) -> tuple[ExternalStore, DictBackend]:
    b = DictBackend()
    return ExternalStore(name=f"{kind}-fake", kind=kind, backend=b), b


def full_registry(
    admin: Engine, audit: str = "retain"
) -> tuple[StoreRegistry, dict[str, DictBackend]]:
    backends: dict[str, DictBackend] = {}
    reg = StoreRegistry([PostgresStore(engine=admin, audit=audit)])  # type: ignore[arg-type]
    for kind in DECLARED_KINDS:
        if kind == "postgres":
            continue
        store, b = external(kind)
        backends[kind] = b
        reg.register(store)
    return reg, backends


def test_postgres_registry_covers_every_tenant_table(admin_engine: Engine) -> None:
    with admin_engine.connect() as c:
        with_tenant = set(
            c.execute(
                text(
                    "SELECT table_name FROM information_schema.columns WHERE table_schema = 'public' "
                    "AND column_name = 'tenant_id'"
                )
            )
            .scalars()
            .all()
        )
    covered = set(POSTGRES_ERASE_ORDER) | {POSTGRES_AUDIT_TABLE}
    assert with_tenant == covered, f"unregistered tenant tables: {with_tenant - covered}"


def test_offboarding_a_tenant_leaves_zero_traces_everywhere(
    app_engine: Engine,
    ingestor_engine: Engine,
    admin_engine: Engine,
    tenant_a: uuid.UUID,
    tenant_b: uuid.UUID,
) -> None:
    helpers.seed_overlapping_tenants(app_engine, ingestor_engine, tenant_a, tenant_b)
    with tenant_transaction(app_engine, tenant_a) as c:
        write_audit(c, tenant_id=tenant_a, query_text="q", filters={}, retrieved_cite_ids=[], result=None,
                    output_text="o", event_type="query")  # fmt: skip
    reg, backends = full_registry(admin_engine, audit="erase")
    for kind, b in backends.items():
        b.data[tenant_a][f"{kind}/obj1"] = "x"
        b.data[tenant_b][f"{kind}/obj1"] = "x"
    public_before = _count(admin_engine, "chunks", PUBLIC_CORPUS_TENANT_ID)
    b_before = _count(admin_engine, "chunks", tenant_b)

    report = reg.offboard(tenant_a)

    assert report.clean and all(n == 0 for n in report.remaining.values())
    assert report.removed["postgres"] > 0 and all(
        report.removed[f"{k}-fake"] == 1 for k in backends
    )
    assert (
        _count(admin_engine, "chunks", PUBLIC_CORPUS_TENANT_ID) == public_before > 0
    )  # shared law
    assert _count(admin_engine, "chunks", tenant_b) == b_before > 0  # other tenants untouched
    assert all(len(b.data[tenant_b]) == 1 for b in backends.values())
    with admin_engine.connect() as c:
        assert (
            c.execute(
                text("SELECT status FROM tenants WHERE id = :t"), {"t": tenant_a}
            ).scalar_one()
            == "suspended"
        )


def test_audit_log_is_retained_unless_erasure_is_explicit(
    app_engine: Engine,
    ingestor_engine: Engine,
    admin_engine: Engine,
    tenant_a: uuid.UUID,
    tenant_b: uuid.UUID,
) -> None:
    helpers.seed_overlapping_tenants(app_engine, ingestor_engine, tenant_a, tenant_b)
    with tenant_transaction(app_engine, tenant_a) as c:
        write_audit(c, tenant_id=tenant_a, query_text="q", filters={}, retrieved_cite_ids=[], result=None,
                    output_text="o", event_type="query")  # fmt: skip
    reg, _ = full_registry(admin_engine)  # default: retain
    report = reg.offboard(tenant_a)
    assert report.clean  # nothing REGISTERED for erasure remains...
    assert _count(admin_engine, POSTGRES_AUDIT_TABLE, tenant_a) == 1  # ...the audit trail does


def test_a_declared_kind_without_a_store_blocks_offboarding(
    admin_engine: Engine, tenant_a: uuid.UUID
) -> None:
    reg = StoreRegistry([PostgresStore(engine=admin_engine)])  # type: ignore[arg-type]
    assert reg.missing_kinds() == set(DECLARED_KINDS) - {"postgres"}
    with pytest.raises(RegistryIncompleteError):
        reg.offboard(tenant_a)


def test_a_store_of_an_undeclared_kind_is_refused() -> None:
    store, _ = external("vector_db")
    with pytest.raises(ValueError, match="DECLARED_KINDS"):
        StoreRegistry([store])


def test_public_corpus_can_never_be_erased(admin_engine: Engine) -> None:
    reg, _ = full_registry(admin_engine)
    with pytest.raises(ForbiddenTenantError):
        reg.offboard(PUBLIC_CORPUS_TENANT_ID)
    with pytest.raises(ForbiddenTenantError):
        PostgresStore(engine=admin_engine).erase(PUBLIC_CORPUS_TENANT_ID)  # type: ignore[arg-type]


def test_a_store_that_fails_to_erase_shows_up_as_a_trace() -> None:
    class Stubborn(DictBackend):
        def delete_keys(self, tenant_id: uuid.UUID, keys: Iterable[str]) -> None:
            return None  # silently keeps everything

    tenant = uuid.uuid4()
    b = Stubborn()
    b.data[tenant]["k"] = "x"
    reg = StoreRegistry(
        [
            ExternalStore(name=f"{k}-fake", kind=k, backend=b if k == "cache" else DictBackend())
            for k in DECLARED_KINDS
            if k != "postgres"
        ]
        + [_NoopPostgres()]
    )
    report = reg.offboard(tenant)
    assert not report.clean and report.remaining["cache-fake"] == 1


class _NoopPostgres:
    name = "postgres"
    kind = "postgres"

    def count(self, tenant_id: uuid.UUID) -> int:
        return 0

    def erase(self, tenant_id: uuid.UUID) -> int:
        return 0


def _count(admin: Engine, table: str, tenant: uuid.UUID) -> int:
    with admin.connect() as c:
        return int(
            c.execute(
                text(f"SELECT count(*) FROM {table} WHERE tenant_id = :t"), {"t": tenant}
            ).scalar_one()
        )
