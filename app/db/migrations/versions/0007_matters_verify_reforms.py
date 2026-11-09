"""ADR-017/018/019 — matters with members, verification reports, and the reform lineage.

* `app_user_id()` / `app_matter_visible()`: the user anchor (fail-closed: unset = NULL = nothing).
* `matters`, `matter_members`, `matter_notes`, `matter_sources` (ADR-018). Conversations gain an optional
  `matter_id`; conversations and messages gain RESTRICTIVE policies (author OR matter member).
* `verification_reports` (ADR-019): normalized references and verdicts, never the brief's text.
* `corpus_changes`, `cite_lineage`, `source_checks` (ADR-017): what the reform watcher saw and how each
  citable unit of the old version maps to the new one. Corpus template: the public rows are written
  only by `corpus_ingestor`, read by every tenant.

Revision ID: 0007
Revises: 0006
"""

from alembic import op

from app.db import rls

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION app_user_id() RETURNS uuid
        LANGUAGE sql STABLE PARALLEL SAFE
        AS $$ SELECT NULLIF(current_setting('app.user_id', true), '')::uuid $$
        """
    )

    # ---- matters (ADR-018) ---------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE matters (
            id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id    uuid NOT NULL,
            created_by   uuid NOT NULL,
            title        text NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
            client_ref   text CHECK (length(client_ref) <= 200),
            description  text CHECK (length(description) <= 4000),
            status       text NOT NULL DEFAULT 'open' CHECK (status IN ('open','closed')),
            created_at   timestamptz NOT NULL DEFAULT now(),
            updated_at   timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT matters_id_tenant_uq UNIQUE (id, tenant_id),
            CONSTRAINT matters_creator_fk FOREIGN KEY (created_by, tenant_id)
                REFERENCES users (id, tenant_id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE matter_members (
            tenant_id   uuid NOT NULL,
            matter_id   uuid NOT NULL,
            user_id     uuid NOT NULL,
            role        text NOT NULL CHECK (role IN ('owner','member')),
            added_at    timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (matter_id, user_id),
            CONSTRAINT matter_members_matter_fk FOREIGN KEY (matter_id, tenant_id)
                REFERENCES matters (id, tenant_id) ON DELETE CASCADE,
            CONSTRAINT matter_members_user_fk FOREIGN KEY (user_id, tenant_id)
                REFERENCES users (id, tenant_id) ON DELETE CASCADE
        )
        """
    )
    op.execute("CREATE INDEX matter_members_user_idx ON matter_members (tenant_id, user_id)")
    # Plain SQL, SECURITY INVOKER: evaluated under the caller's RLS on `matters`.
    op.execute(
        """
        CREATE FUNCTION app_matter_visible(m uuid) RETURNS boolean
        LANGUAGE sql STABLE
        AS $$ SELECT EXISTS (SELECT 1 FROM matters WHERE id = m) $$
        """
    )
    op.execute(
        """
        CREATE TABLE matter_notes (
            id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id   uuid NOT NULL,
            matter_id   uuid NOT NULL,
            author_id   uuid NOT NULL,
            body        text NOT NULL CHECK (length(body) BETWEEN 1 AND 8000),
            created_at  timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT matter_notes_matter_fk FOREIGN KEY (matter_id, tenant_id)
                REFERENCES matters (id, tenant_id) ON DELETE CASCADE,
            CONSTRAINT matter_notes_author_fk FOREIGN KEY (author_id, tenant_id)
                REFERENCES users (id, tenant_id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE matter_sources (
            id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id   uuid NOT NULL,
            matter_id   uuid NOT NULL,
            cite_id     text NOT NULL CHECK (length(cite_id) BETWEEN 1 AND 200),
            note        text CHECK (length(note) <= 2000),
            added_by    uuid NOT NULL,
            added_at    timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT matter_sources_uq UNIQUE (matter_id, cite_id),
            CONSTRAINT matter_sources_matter_fk FOREIGN KEY (matter_id, tenant_id)
                REFERENCES matters (id, tenant_id) ON DELETE CASCADE,
            CONSTRAINT matter_sources_user_fk FOREIGN KEY (added_by, tenant_id)
                REFERENCES users (id, tenant_id) ON DELETE CASCADE
        )
        """
    )
    op.execute("ALTER TABLE conversations ADD COLUMN matter_id uuid")
    op.execute(
        "ALTER TABLE conversations ADD CONSTRAINT conversations_matter_fk "
        "FOREIGN KEY (matter_id, tenant_id) REFERENCES matters (id, tenant_id) ON DELETE SET NULL "
        "(matter_id)"
    )
    op.execute("CREATE INDEX conversations_matter_idx ON conversations (tenant_id, matter_id)")

    # ---- verification reports (ADR-019) --------------------------------------------------------
    op.execute(
        """
        CREATE TABLE verification_reports (
            id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id    uuid NOT NULL,
            user_id      uuid NOT NULL,
            matter_id    uuid,
            title        text NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
            source_kind  text NOT NULL CHECK (source_kind IN ('text','pdf')),
            summary      jsonb NOT NULL,
            items        jsonb NOT NULL,
            created_at   timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT verification_reports_user_fk FOREIGN KEY (user_id, tenant_id)
                REFERENCES users (id, tenant_id) ON DELETE CASCADE,
            CONSTRAINT verification_reports_matter_fk FOREIGN KEY (matter_id, tenant_id)
                REFERENCES matters (id, tenant_id) ON DELETE SET NULL (matter_id)
        )
        """
    )

    for table in ("matters", "matter_members", "matter_notes", "matter_sources",
                  "verification_reports"):  # fmt: skip
        for stmt in rls.statements("private", table):
            op.execute(stmt)
    for kind, table in (
        ("matter_root", "matters"),
        ("matter_child", "matter_notes"),
        ("matter_child", "matter_sources"),
        ("personal", "verification_reports"),
        ("personal", "conversations"),
        ("message", "messages"),
    ):
        for stmt in rls.statements(kind, table):  # type: ignore[arg-type]
            op.execute(stmt)
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON matters, matter_members, matter_notes, "
        "matter_sources, verification_reports TO legalai_app"
    )

    # ---- reform lineage (ADR-017) --------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE corpus_changes (
            id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id        uuid NOT NULL REFERENCES tenants(id),
            official_id      text NOT NULL,
            old_document_id  uuid NOT NULL,
            new_document_id  uuid NOT NULL,
            old_version      text,
            new_version      text,
            published_on     date,
            source_uri       text NOT NULL,
            counts           jsonb NOT NULL,
            detected_at      timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT corpus_changes_id_tenant_uq UNIQUE (id, tenant_id),
            CONSTRAINT corpus_changes_new_uq UNIQUE (tenant_id, new_document_id),
            CONSTRAINT corpus_changes_old_fk FOREIGN KEY (old_document_id, tenant_id)
                REFERENCES documents (id, tenant_id),
            CONSTRAINT corpus_changes_new_fk FOREIGN KEY (new_document_id, tenant_id)
                REFERENCES documents (id, tenant_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE cite_lineage (
            id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id         uuid NOT NULL,
            change_id         uuid NOT NULL,
            old_cite_id       text,
            new_cite_id       text,
            change            text NOT NULL CHECK (change IN
                                ('unchanged','amended','derogated','removed','added')),
            citation_string   text NOT NULL,
            CONSTRAINT cite_lineage_change_fk FOREIGN KEY (change_id, tenant_id)
                REFERENCES corpus_changes (id, tenant_id),
            CONSTRAINT cite_lineage_ends CHECK (
                (change = 'added' AND old_cite_id IS NULL AND new_cite_id IS NOT NULL)
                OR (change = 'removed' AND old_cite_id IS NOT NULL AND new_cite_id IS NULL)
                OR (change IN ('unchanged','amended','derogated')
                    AND old_cite_id IS NOT NULL AND new_cite_id IS NOT NULL))
        )
        """
    )
    op.execute("CREATE INDEX cite_lineage_old_idx ON cite_lineage (tenant_id, old_cite_id)")
    op.execute("CREATE INDEX cite_lineage_new_idx ON cite_lineage (tenant_id, new_cite_id)")
    op.execute(
        """
        CREATE TABLE source_checks (
            id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id      uuid NOT NULL REFERENCES tenants(id),
            official_id    text NOT NULL,
            source         text NOT NULL,
            checked_at     timestamptz NOT NULL DEFAULT now(),
            outcome        text NOT NULL CHECK (outcome IN
                             ('unchanged','ingested','failed','blocked','skipped')),
            http_status    integer,
            etag           text,
            last_modified  text,
            sha256         text CHECK (sha256 ~ '^[0-9a-f]{64}$'),
            detail         text CHECK (length(detail) <= 2000)
        )
        """
    )
    op.execute(
        "CREATE INDEX source_checks_latest_idx ON source_checks (tenant_id, official_id, checked_at DESC)"
    )
    for table in ("corpus_changes", "cite_lineage", "source_checks"):
        for stmt in rls.statements("corpus", table):
            op.execute(stmt)
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON corpus_changes, cite_lineage, source_checks "
        "TO legalai_app"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON corpus_changes, cite_lineage, source_checks "
        "TO corpus_ingestor"
    )


def downgrade() -> None:
    raise NotImplementedError("Forward-only migrations (invariant 3).")
