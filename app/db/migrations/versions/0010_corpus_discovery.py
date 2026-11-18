"""ADR-026 — discovery of laws not yet loaded, approved by a person before anything is downloaded.

* `corpus_index_entries`: every entry of an official index (e.g. the Cámara de Diputados list of
  federal laws in force) as last read: title, official PDF, publication and last-reform dates. The
  first read of an index files everything as `available`; an entry that appears later is `new`. An
  operator `approved` or `dismissed` it. Nothing in this table is ever downloaded by itself.
* `corpus_sources`: the laws an operator approved. The reform watcher reads them next to the static
  registry (`app/ingestion/corpus.py`) and ingests their first version. Their jurisdiction comes from
  the index (closed list), their legal domains from the closed catalog; nothing is inferred.
* `source_checks.outcome` gains `indexed` (an index was read).

Both tables are public corpus: written only by `corpus_ingestor`, read by every tenant.

Revision ID: 0010
Revises: 0009
"""

from alembic import op

from app.db import rls

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE corpus_index_entries (
            id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id        uuid NOT NULL REFERENCES tenants(id),
            index_id         text NOT NULL CHECK (index_id ~ '^[A-Z][A-Z0-9_]{1,30}$'),
            entry_key        text NOT NULL CHECK (entry_key ~ '^[A-Za-z0-9][A-Za-z0-9_.-]{0,80}$'),
            title            text NOT NULL CHECK (length(title) BETWEEN 1 AND 600),
            url              text NOT NULL CHECK (url ~ '^https://' AND length(url) <= 500),
            published_on     date,
            last_reform_on   date,
            last_reform_text text CHECK (length(last_reform_text) <= 200),
            note             text CHECK (length(note) <= 300),
            status           text NOT NULL CHECK (status IN ('available','new','approved','dismissed')),
            source_key       text,
            decided_by       text CHECK (length(decided_by) <= 120),
            decided_at       timestamptz,
            first_seen_at    timestamptz NOT NULL DEFAULT now(),
            last_seen_at     timestamptz NOT NULL DEFAULT now(),
            missing_since    timestamptz,
            CONSTRAINT corpus_index_entries_uq UNIQUE (tenant_id, index_id, entry_key),
            CONSTRAINT corpus_index_entries_decision_ck CHECK (
                (status IN ('approved','dismissed')) = (decided_at IS NOT NULL)
                AND (status = 'approved') = (source_key IS NOT NULL))
        )
        """
    )
    op.execute(
        """
        CREATE TABLE corpus_sources (
            id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id      uuid NOT NULL REFERENCES tenants(id),
            key            text NOT NULL CHECK (key ~ '^[A-Z][A-Z0-9_]{1,40}$'),
            index_id       text NOT NULL,
            entry_key      text NOT NULL,
            title          text NOT NULL CHECK (length(title) BETWEEN 1 AND 600),
            source_url     text NOT NULL CHECK (source_url ~ '^https://' AND length(source_url) <= 500),
            country        text NOT NULL CHECK (country IN ('MX','US')),
            level          text NOT NULL,
            state_code     text,
            municipality   text,
            scope          text NOT NULL CHECK (scope ~ '^[A-Z]{2,5}$'),
            code           text NOT NULL CHECK (code ~ '^[A-Z][A-Z0-9]{1,30}$'),
            language       text NOT NULL CHECK (language IN ('es','en')),
            doc_type       text NOT NULL CHECK (doc_type IN ('codigo','ley','reglamento','statute')),
            legal_domain   text[] NOT NULL CHECK (cardinality(legal_domain) BETWEEN 1 AND 4),
            grammar        text NOT NULL CHECK (grammar IN ('mx','us')),
            approved_by    text NOT NULL CHECK (length(approved_by) BETWEEN 1 AND 120),
            approved_at    timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT corpus_sources_key_uq UNIQUE (tenant_id, key),
            CONSTRAINT corpus_sources_code_uq UNIQUE (tenant_id, country, scope, code),
            CONSTRAINT corpus_sources_entry_uq UNIQUE (tenant_id, index_id, entry_key),
            CONSTRAINT corpus_sources_entry_fk FOREIGN KEY (tenant_id, index_id, entry_key)
                REFERENCES corpus_index_entries (tenant_id, index_id, entry_key)
        )
        """
    )
    for table in ("corpus_index_entries", "corpus_sources"):
        for stmt in rls.statements("corpus", table):
            op.execute(stmt)
    # Same grants as the other corpus tables (0007): RLS confines the app role to its own tenant's
    # rows and never lets it write PUBLIC_CORPUS, which is where these rows live.
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON corpus_index_entries, corpus_sources TO legalai_app"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON corpus_index_entries, corpus_sources TO corpus_ingestor"
    )

    op.execute("ALTER TABLE source_checks DROP CONSTRAINT source_checks_outcome_check")
    op.execute(
        "ALTER TABLE source_checks ADD CONSTRAINT source_checks_outcome_check CHECK (outcome IN "
        "('unchanged','ingested','failed','blocked','skipped','indexed'))"
    )


def downgrade() -> None:
    raise NotImplementedError("Forward-only migrations (invariant 3).")
