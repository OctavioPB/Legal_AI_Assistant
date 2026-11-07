"""WS0.T03 / WS1.T03 — the runtime roles cannot escape RLS or change the schema."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from app.db.session import PUBLIC_CORPUS_TENANT_ID

ROLES = ("legalai_migrator", "legalai_app", "corpus_ingestor", "legalai_provisioner")


@pytest.mark.parametrize("role", ROLES)
def test_no_role_is_superuser_or_bypasses_rls(admin_engine: Engine, role: str) -> None:
    with admin_engine.connect() as conn:
        sup, bypass, createrole, createdb = conn.execute(
            text(
                "SELECT rolsuper, rolbypassrls, rolcreaterole, rolcreatedb FROM pg_roles WHERE rolname = :r"
            ),
            {"r": role},
        ).one()
    assert (sup, bypass, createrole, createdb) == (False, False, False, False)


def test_app_role_has_no_ddl(app_engine: Engine) -> None:
    for sql in (
        "CREATE TABLE evil (id int)",
        "ALTER TABLE chunks DISABLE ROW LEVEL SECURITY",
        "ALTER TABLE chunks NO FORCE ROW LEVEL SECURITY",
        "DROP POLICY chunks_read ON chunks",
        "DROP TABLE chunks",
        "TRUNCATE chunks",
        "CREATE POLICY allow_all ON chunks USING (true)",
        "CREATE EXTENSION hstore",
    ):
        with pytest.raises(DBAPIError, match=r"permission denied|must be owner"):
            with app_engine.begin() as conn:
                conn.execute(text(sql))


def test_app_role_cannot_become_a_more_privileged_role(app_engine: Engine) -> None:
    for role in ("legalai_migrator", "corpus_ingestor", "legalai_provisioner", "postgres"):
        with pytest.raises(DBAPIError, match="permission denied"):
            with app_engine.begin() as conn:
                conn.execute(text(f"SET ROLE {role}"))


def test_owner_is_subject_to_rls_too(migrator_engine: Engine, tenant_a: uuid.UUID) -> None:
    # FORCE ROW LEVEL SECURITY: even the table owner sees nothing without a policy that admits it.
    with migrator_engine.begin() as conn:
        for table in ("documents", "doc_nodes", "chunks", "users", "sessions", "dossiers"):
            assert conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 0
        conn.execute(
            text("SELECT set_config('app.tenant_id', :t, true)"),
            {"t": str(PUBLIC_CORPUS_TENANT_ID)},
        )
        with pytest.raises(DBAPIError, match="row-level security"):
            conn.execute(
                text(
                    "INSERT INTO documents (tenant_id, jurisdiction_id, doc_type, title, language, source_uri, checksum_sha256) "
                    "VALUES (public_corpus_id(), 1, 'ley', 't', 'es', 'u', repeat('a', 64))"
                )
            )


def test_only_the_provisioner_manages_tenants(
    app_engine: Engine, provisioner_engine: Engine, admin_engine: Engine, tenant_a: uuid.UUID
) -> None:
    with pytest.raises(DBAPIError, match="permission denied"), app_engine.begin() as conn:
        conn.execute(text("INSERT INTO tenants (slug, name) VALUES ('rogue', 'rogue')"))
    # the provisioner cannot forge or touch the synthetic tenant
    with pytest.raises(DBAPIError, match="row-level security"):
        with provisioner_engine.begin() as conn:
            conn.execute(
                text("INSERT INTO tenants (id, slug, name) VALUES (public_corpus_id(), 'p2', 'p2')")
            )
    with pytest.raises(DBAPIError, match="immutable"), provisioner_engine.begin() as conn:
        conn.execute(text("UPDATE tenants SET name = 'renamed' WHERE id = public_corpus_id()"))
    with pytest.raises(DBAPIError, match="permission denied"):  # the provisioner never deletes
        with provisioner_engine.begin() as conn:
            conn.execute(text("DELETE FROM tenants WHERE id = public_corpus_id()"))
    with pytest.raises(DBAPIError, match="immutable"):  # and not even a superuser can remove it
        with admin_engine.begin() as conn:
            conn.execute(text("DELETE FROM tenants WHERE id = public_corpus_id()"))
    with provisioner_engine.begin() as conn:  # a normal tenant can be suspended
        assert (
            conn.execute(
                text("UPDATE tenants SET status = 'suspended' WHERE id = :t"), {"t": tenant_a}
            ).rowcount
            == 1
        )
