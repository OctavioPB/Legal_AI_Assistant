"""CI-BLOCKING cross-tenant leak test (multi-tenancy.md, WS1.T04). Must pass ALWAYS.

Tenants A and B hold the SAME content under the SAME cite_ids next to the shared PUBLIC_CORPUS.
Under each tenant context we assert, for retrieval-shaped queries AND raw SQL on every
tenant-scoped table, that no row of the other tenant is visible; that without a context nothing is
visible; and that writes cannot cross the boundary. The registry below must list every
tenant-scoped table: a table that gains a `tenant_id` column without being registered here — or
without RLS policies — fails the build.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from app.db import rls
from app.db.session import (
    PUBLIC_CORPUS_TENANT_ID,
    TenantContextError,
    tenant_transaction,
)
from tests import helpers

# table -> (column holding the tenant, policy template kinds). A table may stack a RESTRICTIVE kind on
# top of its base kind (ADR-018: the user anchor).
TENANT_TABLES: dict[str, tuple[str, tuple[str, ...]]] = {
    "documents": ("tenant_id", ("corpus",)),
    "doc_nodes": ("tenant_id", ("corpus",)),
    "chunks": ("tenant_id", ("corpus",)),
    "corpus_changes": ("tenant_id", ("corpus",)),
    "cite_lineage": ("tenant_id", ("corpus",)),
    "source_checks": ("tenant_id", ("corpus",)),
    "users": ("tenant_id", ("private",)),
    "sessions": ("tenant_id", ("private",)),
    "dossiers": ("tenant_id", ("private",)),
    "conversations": ("tenant_id", ("private", "personal")),
    "messages": ("tenant_id", ("private", "message")),
    "matters": ("tenant_id", ("private", "matter_root")),
    "matter_members": ("tenant_id", ("private",)),
    "matter_notes": ("tenant_id", ("private", "matter_child")),
    "matter_sources": ("tenant_id", ("private", "matter_child")),
    "verification_reports": ("tenant_id", ("private", "personal")),
    "audit_log": ("tenant_id", ("append_only",)),
    "tenants": ("id", ("tenants",)),
}
# Tables invisible without the user anchor (ADR-018).
USER_SCOPED = [t for t, (_, kinds) in TENANT_TABLES.items() if len(kinds) > 1]
# Global, non-tenant data: read-only catalogs and alembic bookkeeping.
GLOBAL_TABLES = {"jurisdictions", "personas", "alembic_version"}
CORPUS_TABLES = [t for t, (_, kinds) in TENANT_TABLES.items() if "corpus" in kinds]


# ── registry completeness ────────────────────────────────────────────────────────────────────────
def test_registry_lists_every_tenant_scoped_table(admin_engine: Engine) -> None:
    with admin_engine.connect() as conn:
        all_tables = set(
            conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
                )
            ).scalars()
        )
        with_tenant_col = set(
            conn.execute(
                text(
                    "SELECT table_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND column_name = 'tenant_id'"
                )
            ).scalars()
        )
    unregistered = (with_tenant_col | {"tenants"}) - TENANT_TABLES.keys()
    assert not unregistered, (
        f"tenant-scoped tables missing from the isolation registry: {unregistered}"
    )
    unclassified = all_tables - TENANT_TABLES.keys() - GLOBAL_TABLES
    assert not unclassified, f"tables neither tenant-scoped nor declared global: {unclassified}"
    assert TENANT_TABLES.keys() <= all_tables, "registry lists tables that do not exist"


@pytest.mark.parametrize("table", sorted(TENANT_TABLES))
def test_every_tenant_table_has_forced_rls_and_its_policies(
    admin_engine: Engine, table: str
) -> None:
    with admin_engine.connect() as conn:
        enabled, forced = conn.execute(
            text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE relname = :t AND relnamespace = 'public'::regnamespace"
            ),
            {"t": table},
        ).one()
        policies = set(
            conn.execute(
                text("SELECT policyname FROM pg_policies WHERE tablename = :t"), {"t": table}
            ).scalars()
        )
    assert enabled, f"{table}: RLS not enabled"
    assert forced, f"{table}: RLS not FORCED (the table owner would bypass it)"
    assert policies, f"{table}: no policies"
    for kind in TENANT_TABLES[table][1]:
        if kind == "tenants":
            continue
        expected = {
            stmt.split()[2]
            for stmt in rls.statements(kind, table)  # type: ignore[arg-type]
            if stmt.startswith("CREATE POLICY")
        }
        assert expected <= policies, f"{table}: missing policies {expected - policies}"


# ── visibility under each context ────────────────────────────────────────────────────────────────
def test_raw_sql_sees_only_own_and_public_rows(
    app_engine: Engine, ingestor_engine: Engine, tenant_a: uuid.UUID, tenant_b: uuid.UUID
) -> None:
    helpers.seed_overlapping_tenants(app_engine, ingestor_engine, tenant_a, tenant_b)
    allowed_corpus = {
        tenant_a: {tenant_a, PUBLIC_CORPUS_TENANT_ID},
        tenant_b: {tenant_b, PUBLIC_CORPUS_TENANT_ID},
    }
    for me, allowed in allowed_corpus.items():
        for table in CORPUS_TABLES:
            seen = helpers.visible_tenants(app_engine, me, table)
            assert seen == allowed, f"{table} under {me}: saw tenants {seen}, allowed {allowed}"
        # tenant-private tables: strictly own rows, never public
        for table in ("users", "sessions", "dossiers", "audit_log"):
            assert helpers.visible_tenants(app_engine, me, table) <= {me}
        # client content (ADR-016/018/019): both tenants have rows, each sees exactly its own, and
        # only with the user anchor; without it (same tenant!) nothing at all
        user = helpers.SEEDED_USERS[me]
        for table in [*USER_SCOPED, "matter_members"]:
            assert helpers.visible_tenants(app_engine, me, table, user) == {me}, table
        for table in USER_SCOPED:
            assert helpers.visible_tenants(app_engine, me, table) == set(), (
                f"{table} visible without app.user_id"
            )
        with tenant_transaction(app_engine, me) as conn:
            visible = conn.execute(text("SELECT id FROM tenants")).scalars().all()
        assert visible == [me], "a tenant must see only its own tenants row"


def test_retrieval_results_never_cross_tenants(
    app_engine: Engine, ingestor_engine: Engine, tenant_a: uuid.UUID, tenant_b: uuid.UUID
) -> None:
    helpers.seed_overlapping_tenants(app_engine, ingestor_engine, tenant_a, tenant_b)
    private_ids: dict[uuid.UUID, set[object]] = {}
    for me, other in ((tenant_a, tenant_b), (tenant_b, tenant_a)):
        with tenant_transaction(app_engine, me) as conn:
            hits = helpers.search_like_retrieval(conn, "faltas de asistencia rescisión patrón")
            mine = {
                r["id"]
                for r in conn.execute(
                    text("SELECT id FROM chunks WHERE tenant_id = :t"), {"t": me}
                ).mappings()
            }
        assert hits, "the query should retrieve something (otherwise the test proves nothing)"
        assert {h["tenant_id"] for h in hits} <= {me, PUBLIC_CORPUS_TENANT_ID}
        assert other not in {h["tenant_id"] for h in hits}
        private_ids[me] = {h["id"] for h in hits if h["tenant_id"] == me}
        assert private_ids[me] <= mine
    # the two tenants hold identical text, yet the sets of private rows they retrieve are disjoint
    assert private_ids[tenant_a].isdisjoint(private_ids[tenant_b])
    assert private_ids[tenant_a] and private_ids[tenant_b]


def test_identical_cite_ids_coexist_without_an_existence_oracle(
    app_engine: Engine, ingestor_engine: Engine, tenant_a: uuid.UUID, tenant_b: uuid.UUID
) -> None:
    # seeding already stored 'CF-EXP-0001-ART47' in BOTH tenants; had cite_id been globally
    # UNIQUE, the second insert would have failed and told B that A holds that cite_id.
    helpers.seed_overlapping_tenants(app_engine, ingestor_engine, tenant_a, tenant_b)
    for me in (tenant_a, tenant_b):
        with tenant_transaction(app_engine, me) as conn:
            cites = (
                conn.execute(text("SELECT cite_id FROM chunks WHERE cite_id = 'CF-EXP-0001-ART47'"))
                .scalars()
                .all()
            )
        assert cites == ["CF-EXP-0001-ART47"]


# ── fail closed ──────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("table", sorted(TENANT_TABLES))
def test_no_context_returns_zero_rows(
    app_engine: Engine,
    ingestor_engine: Engine,
    tenant_a: uuid.UUID,
    tenant_b: uuid.UUID,
    table: str,
) -> None:
    helpers.seed_overlapping_tenants(app_engine, ingestor_engine, tenant_a, tenant_b)
    with app_engine.begin() as conn:  # deliberately NOT anchored
        assert conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 0


def test_context_does_not_outlive_its_transaction(
    app_engine: Engine, ingestor_engine: Engine, tenant_a: uuid.UUID, tenant_b: uuid.UUID
) -> None:
    helpers.seed_overlapping_tenants(app_engine, ingestor_engine, tenant_a, tenant_b)
    with tenant_transaction(app_engine, tenant_a) as conn:  # pool_size=1: same connection next
        assert conn.execute(text("SELECT count(*) FROM chunks")).scalar_one() > 0
    with app_engine.begin() as conn:
        assert conn.execute(text("SELECT count(*) FROM chunks")).scalar_one() == 0
        assert conn.execute(text("SELECT current_setting('app.tenant_id', true)")).scalar() in (
            None,
            "",
        )
    with tenant_transaction(app_engine, tenant_b) as conn:
        assert {
            r for r in conn.execute(text("SELECT DISTINCT tenant_id FROM chunks")).scalars()
        } == {
            tenant_b,
            PUBLIC_CORPUS_TENANT_ID,
        }


def test_empty_and_garbage_context(app_engine: Engine, tenant_a: uuid.UUID) -> None:
    with app_engine.begin() as conn:
        conn.execute(text("SELECT set_config('app.tenant_id', '', true)"))
        assert conn.execute(text("SELECT count(*) FROM tenants")).scalar_one() == 0
    with pytest.raises(DBAPIError):  # a non-UUID can never be silently treated as "no tenant"
        with app_engine.begin() as conn:
            conn.execute(text("SELECT set_config('app.tenant_id', 'not-a-uuid', true)"))
            conn.execute(text("SELECT count(*) FROM tenants"))


def test_row_security_cannot_be_switched_off(app_engine: Engine, tenant_a: uuid.UUID) -> None:
    with pytest.raises(DBAPIError, match="row-level security"):
        with tenant_transaction(app_engine, tenant_a) as conn:
            conn.execute(text("SET LOCAL row_security = off"))
            conn.execute(text("SELECT count(*) FROM chunks"))


# ── writes cannot cross the boundary ─────────────────────────────────────────────────────────────
def test_app_cannot_write_other_tenants_or_public_rows(
    app_engine: Engine, ingestor_engine: Engine, tenant_a: uuid.UUID, tenant_b: uuid.UUID
) -> None:
    helpers.seed_overlapping_tenants(app_engine, ingestor_engine, tenant_a, tenant_b)
    insert_doc = text(
        "INSERT INTO documents (tenant_id, jurisdiction_id, doc_type, title, language, source_uri, checksum_sha256) "
        "VALUES (:t, (SELECT id FROM jurisdictions WHERE level='federal' AND country='MX'), "
        "'client_file', 'x', 'es', 'u', repeat('a', 64))"
    )
    for target in (tenant_b, PUBLIC_CORPUS_TENANT_ID):
        with pytest.raises(DBAPIError, match="row-level security"):
            with tenant_transaction(app_engine, tenant_a) as conn:
                conn.execute(insert_doc, {"t": target})
    # even anchored ON the public corpus, the app role cannot write it
    with pytest.raises(DBAPIError, match="row-level security"):
        with tenant_transaction(app_engine, PUBLIC_CORPUS_TENANT_ID, allow_public=True) as conn:
            conn.execute(insert_doc, {"t": PUBLIC_CORPUS_TENANT_ID})

    # UPDATE / DELETE of rows it cannot see affect nothing (RLS filters them out of the target set)
    with tenant_transaction(app_engine, tenant_a) as conn:
        for sql in (
            "UPDATE chunks SET is_current = false WHERE tenant_id <> app_tenant_id()",
            "DELETE FROM chunks WHERE tenant_id <> app_tenant_id()",
            "DELETE FROM documents WHERE tenant_id = public_corpus_id()",
        ):
            assert conn.execute(text(sql)).rowcount == 0, sql
    with tenant_transaction(app_engine, tenant_b) as conn:
        assert (
            conn.execute(
                text("SELECT count(*) FROM chunks WHERE tenant_id = :t"), {"t": tenant_b}
            ).scalar_one()
            == 2
        )
        assert (
            conn.execute(
                text("SELECT count(*) FROM chunks WHERE tenant_id = public_corpus_id()")
            ).scalar_one()
            == 3
        )


def test_ingestor_writes_only_the_public_corpus(
    app_engine: Engine, ingestor_engine: Engine, tenant_a: uuid.UUID
) -> None:
    insert_doc = text(
        "INSERT INTO documents (tenant_id, jurisdiction_id, doc_type, title, language, source_uri, checksum_sha256) "
        "VALUES (:t, (SELECT id FROM jurisdictions WHERE level='federal' AND country='MX'), "
        "'client_file', 'x', 'es', 'u', repeat('a', 64))"
    )
    with pytest.raises(DBAPIError, match="row-level security"):
        with tenant_transaction(ingestor_engine, tenant_a) as conn:
            conn.execute(insert_doc, {"t": tenant_a})
    with pytest.raises(
        DBAPIError, match="permission denied"
    ):  # reforms flip is_current; never DELETE
        with tenant_transaction(
            ingestor_engine, PUBLIC_CORPUS_TENANT_ID, allow_public=True
        ) as conn:
            conn.execute(text("DELETE FROM chunks"))


def test_composite_foreign_keys_block_cross_tenant_references(
    app_engine: Engine,
    ingestor_engine: Engine,
    admin_engine: Engine,
    tenant_a: uuid.UUID,
    tenant_b: uuid.UUID,
) -> None:
    helpers.seed_overlapping_tenants(app_engine, ingestor_engine, tenant_a, tenant_b)
    with admin_engine.connect() as conn:  # an attacker who guessed B's document id
        b_doc = conn.execute(
            text("SELECT id FROM documents WHERE tenant_id = :t"), {"t": tenant_b}
        ).scalar_one()
    with pytest.raises(DBAPIError, match="doc_nodes_document_fk"):
        with tenant_transaction(app_engine, tenant_a) as conn:
            conn.execute(
                text(
                    "INSERT INTO doc_nodes (document_id, tenant_id, node_type, label, ordinal, path, full_text) "
                    "VALUES (:d, :t, 'articulo', 'x', 0, 'x', 'x')"
                ),
                {"d": b_doc, "t": tenant_a},
            )


def test_session_tenant_must_be_a_real_user_tenant() -> None:
    with pytest.raises(TenantContextError):
        with tenant_transaction(None, PUBLIC_CORPUS_TENANT_ID):  # type: ignore[arg-type]
            pass
    with pytest.raises(TenantContextError):
        with tenant_transaction(None, "00000000-0000-0000-0000-0000000000aa"):  # type: ignore[arg-type]
            pass


# ── matters (ADR-018): sharing inside a firm is membership, nothing else ─────────────────────────
def test_matter_membership_is_the_only_way_to_share(
    app_engine: Engine, ingestor_engine: Engine, tenant_a: uuid.UUID, tenant_b: uuid.UUID
) -> None:
    helpers.seed_overlapping_tenants(app_engine, ingestor_engine, tenant_a, tenant_b)
    owner = helpers.SEEDED_USERS[tenant_a]
    with tenant_transaction(app_engine, tenant_a) as conn:
        colleague = conn.execute(
            text(
                "INSERT INTO users (tenant_id, email, display_name) "
                "VALUES (:t, 'colega@firm.mx', 'Colega') RETURNING id"
            ),
            {"t": tenant_a},
        ).scalar_one()

    def visible(user: uuid.UUID) -> dict[str, int]:
        with tenant_transaction(app_engine, tenant_a, user_id=user) as conn:
            return {
                t: conn.execute(text(f"SELECT count(*) FROM {t}")).scalar_one() for t in USER_SCOPED
            }

    assert all(n == 0 for n in visible(colleague).values()), "a colleague saw a private matter"
    with tenant_transaction(app_engine, tenant_a, user_id=owner) as conn:
        matter = conn.execute(text("SELECT id FROM matters")).scalar_one()
        conn.execute(
            text(
                "INSERT INTO matter_members (tenant_id, matter_id, user_id, role) "
                "VALUES (:t, :m, :u, 'member')"
            ),
            {"t": tenant_a, "m": matter, "u": colleague},
        )
    seen = visible(colleague)
    # the matter, its note, its source, its conversation and its report; NOT the owner's private case
    assert seen == {
        "conversations": 1, "messages": 0, "matters": 1, "matter_notes": 1, "matter_sources": 1,
        "verification_reports": 1,
    }, seen  # fmt: skip
    with tenant_transaction(app_engine, tenant_a, user_id=colleague) as conn:
        title = conn.execute(text("SELECT title FROM conversations")).scalar_one()
        assert title == "Consulta del asunto"
        # a member cannot slip a private conversation of the owner into view by writing it
        with pytest.raises(DBAPIError, match="row-level security"):
            conn.execute(
                text(
                    "INSERT INTO conversations (tenant_id, user_id, kind, title, language, "
                    "jurisdiction_id, persona) VALUES (:t, :u, 'chat', 'x', 'es', "
                    "(SELECT id FROM jurisdictions WHERE country = 'MX' AND level = 'federal'), "
                    "'labor')"
                ),
                {"t": tenant_a, "u": owner},
            )
    # another firm's user id anchored on this firm sees nothing either
    assert all(n == 0 for n in visible(helpers.SEEDED_USERS[tenant_b]).values())
