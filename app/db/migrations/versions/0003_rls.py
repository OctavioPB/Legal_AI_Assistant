"""WS1.T03 — Row-Level Security on every tenant-scoped table, grants per role, helper functions.

Revision ID: 0003
Revises: 0002
"""

from alembic import op

from app.db import rls

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

PUBLIC_CORPUS = "00000000-0000-0000-0000-000000000001"


def upgrade() -> None:
    # `app.tenant_id` is set per transaction with set_config(..., true) (== SET LOCAL). When it is
    # missing or empty the function returns NULL and every policy below fails closed.
    op.execute(
        """
        CREATE FUNCTION app_tenant_id() RETURNS uuid
        LANGUAGE sql STABLE PARALLEL SAFE
        AS $$ SELECT NULLIF(current_setting('app.tenant_id', true), '')::uuid $$
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION public_corpus_id() RETURNS uuid
        LANGUAGE sql IMMUTABLE PARALLEL SAFE
        AS $$ SELECT '{PUBLIC_CORPUS}'::uuid $$
        """
    )

    # tenants: a tenant sees only itself; the provisioner (signup/offboarding) manages rows.
    op.execute("ALTER TABLE tenants ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tenants FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenants_read ON tenants FOR SELECT "
        "USING (app_tenant_id() IS NOT NULL AND id = app_tenant_id())"
    )
    op.execute(
        "CREATE POLICY tenants_provision ON tenants FOR ALL TO legalai_provisioner "
        "USING (true) WITH CHECK (id <> public_corpus_id())"
    )

    for table in ("documents", "doc_nodes", "chunks"):
        for stmt in rls.statements("corpus", table):
            op.execute(stmt)

    op.execute("GRANT SELECT ON tenants TO legalai_app")
    op.execute("GRANT SELECT, INSERT, UPDATE ON tenants TO legalai_provisioner")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON documents, doc_nodes, chunks TO legalai_app"
    )
    # The ingestor never deletes: reforms flip is_current and add a new version.
    op.execute("GRANT SELECT, INSERT, UPDATE ON documents, doc_nodes, chunks TO corpus_ingestor")


def downgrade() -> None:
    raise NotImplementedError("Forward-only: reforms version, nothing is deleted (invariant 3).")
