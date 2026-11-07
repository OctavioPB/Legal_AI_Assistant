"""WS1.T05 — product tables: users, sessions, dossiers, audit_log (append-only), all under RLS.

Every cross-reference carries tenant_id in a composite FK (a session cannot point at another
tenant's user). `sessions` stores catalog keys only (jurisdiction_id, persona): SessionContext
has no free-text fields.

Revision ID: 0004
Revises: 0003
"""

from alembic import op

from app.db import rls

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE users (
            id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id           uuid NOT NULL REFERENCES tenants(id),
            email               text NOT NULL CHECK (email = lower(email) AND email LIKE '%_@_%'),
            display_name        text NOT NULL,
            role                text NOT NULL DEFAULT 'member'
                                CHECK (role IN ('owner','admin','member')),
            preferred_language  text NOT NULL DEFAULT 'es-MX'
                                CHECK (preferred_language IN ('es-MX','en-US')),
            created_at          timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT users_id_tenant_uq UNIQUE (id, tenant_id),
            CONSTRAINT users_email_uq UNIQUE (tenant_id, email)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE sessions (
            id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id           uuid NOT NULL,
            user_id             uuid NOT NULL,
            language            text NOT NULL CHECK (language IN ('es-MX','en-US')),
            jurisdiction_id     integer NOT NULL REFERENCES jurisdictions(id),
            persona             text NOT NULL REFERENCES personas(code),
            workflow            text NOT NULL
                                CHECK (workflow IN ('dossier_builder','adversarial_counterpart')),
            include_historical  boolean NOT NULL DEFAULT false,
            created_at          timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT sessions_id_tenant_uq UNIQUE (id, tenant_id),
            CONSTRAINT sessions_user_fk FOREIGN KEY (user_id, tenant_id)
                REFERENCES users (id, tenant_id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE dossiers (
            id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id   uuid NOT NULL,
            user_id     uuid NOT NULL,
            session_id  uuid,
            title       text NOT NULL,
            workflow    text NOT NULL
                        CHECK (workflow IN ('dossier_builder','adversarial_counterpart')),
            status      text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','final','archived')),
            content     jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at  timestamptz NOT NULL DEFAULT now(),
            updated_at  timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT dossiers_user_fk FOREIGN KEY (user_id, tenant_id)
                REFERENCES users (id, tenant_id) ON DELETE CASCADE,
            CONSTRAINT dossiers_session_fk FOREIGN KEY (session_id, tenant_id)
                REFERENCES sessions (id, tenant_id) ON DELETE SET NULL (session_id)
        )
        """
    )
    # One row per answered/refused query (WS5.T03 fills it). Append-only.
    op.execute(
        """
        CREATE TABLE audit_log (
            id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            tenant_id          uuid NOT NULL REFERENCES tenants(id),
            user_id            uuid,
            session_id         uuid,
            request_id         uuid NOT NULL DEFAULT gen_random_uuid(),
            occurred_at        timestamptz NOT NULL DEFAULT now(),
            event_type         text NOT NULL CHECK (event_type IN
                                 ('query','refusal','validation','ingestion','admin')),
            query_text         text,
            filters            jsonb NOT NULL DEFAULT '{}'::jsonb,
            retrieved_cite_ids text[] NOT NULL DEFAULT '{}',
            output_text        text,
            verdict            jsonb NOT NULL DEFAULT '{}'::jsonb,
            model_alias        text,
            resolved_model_id  text,
            regenerations      smallint NOT NULL DEFAULT 0 CHECK (regenerations >= 0)
        )
        """
    )
    op.execute("CREATE INDEX audit_log_tenant_time_idx ON audit_log (tenant_id, occurred_at DESC)")
    op.execute("CREATE INDEX audit_log_request_idx ON audit_log (request_id)")
    op.execute("CREATE INDEX dossiers_user_idx ON dossiers (tenant_id, user_id)")
    op.execute("CREATE INDEX sessions_user_idx ON sessions (tenant_id, user_id)")

    # Append-only for EVERY role (the owner included): UPDATE/DELETE/TRUNCATE are rejected.
    op.execute(
        """
        CREATE FUNCTION audit_log_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'audit_log is append-only (% not allowed)', TG_OP;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER audit_log_no_row_change BEFORE UPDATE OR DELETE ON audit_log "
        "FOR EACH ROW EXECUTE FUNCTION audit_log_append_only()"
    )
    op.execute(
        "CREATE TRIGGER audit_log_no_truncate BEFORE TRUNCATE ON audit_log "
        "FOR EACH STATEMENT EXECUTE FUNCTION audit_log_append_only()"
    )

    for table in ("users", "sessions", "dossiers"):
        for stmt in rls.statements("private", table):
            op.execute(stmt)
    for stmt in rls.statements("append_only", "audit_log"):
        op.execute(stmt)

    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON users, sessions, dossiers TO legalai_app")
    op.execute("REVOKE ALL ON audit_log FROM PUBLIC")
    op.execute("GRANT SELECT, INSERT ON audit_log TO legalai_app")
    op.execute("GRANT USAGE ON SEQUENCE audit_log_id_seq TO legalai_app")


def downgrade() -> None:
    raise NotImplementedError("Forward-only: reforms version, nothing is deleted (invariant 3).")
