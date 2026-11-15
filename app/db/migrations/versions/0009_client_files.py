"""ADR-025 — the firm's own documents (client files), scoped to a matter by RLS.

* `documents.matter_id` (optional): a file of a matter is visible only to that matter's creator and
  members; without a matter it belongs to the whole firm. `doc_nodes.matter_id` and
  `chunks.matter_id` are DERIVED by trigger from the document (they cannot disagree with it), and
  the document's matter cannot be changed afterwards (the derived copies would go stale).
* RESTRICTIVE template `matter_scoped` on documents, doc_nodes, chunks and client_files: stacked on
  the corpus/private policies. Public rows have no matter, so nothing changes for them.
* Deleting a matter deletes its documents (CASCADE): SET NULL would publish a privileged file to
  the whole firm.
* `client_files`: one row per upload, with the processing status. It holds no content: the text
  lives in doc_nodes/chunks under the same policies; the original file is never stored.

Revision ID: 0009
Revises: 0008
"""

from alembic import op

from app.db import rls

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

KINDS = "'contrato','escrito','resolucion','norma','otro'"


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE documents ADD COLUMN matter_id uuid,
          ADD CONSTRAINT documents_matter_fk FOREIGN KEY (matter_id, tenant_id)
              REFERENCES matters (id, tenant_id) ON DELETE CASCADE,
          ADD CONSTRAINT documents_matter_private_ck CHECK (
              matter_id IS NULL OR doc_type = 'client_file')
        """
    )
    op.execute("ALTER TABLE doc_nodes ADD COLUMN matter_id uuid")
    op.execute("ALTER TABLE chunks ADD COLUMN matter_id uuid")
    op.execute(
        "CREATE INDEX documents_matter_idx ON documents (matter_id) WHERE matter_id IS NOT NULL"
    )
    op.execute("CREATE INDEX chunks_matter_idx ON chunks (matter_id) WHERE matter_id IS NOT NULL")
    op.execute(
        """
        CREATE FUNCTION documents_matter_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.matter_id IS DISTINCT FROM OLD.matter_id THEN
            RAISE EXCEPTION 'documents.matter_id is immutable (delete and upload again)';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER documents_matter_immutable_trg BEFORE UPDATE ON documents
            FOR EACH ROW EXECUTE FUNCTION documents_matter_immutable();

        CREATE FUNCTION doc_nodes_derive_matter() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          SELECT matter_id INTO NEW.matter_id FROM documents WHERE id = NEW.document_id;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'doc_nodes: document % not visible', NEW.document_id;
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER doc_nodes_derive_matter_trg BEFORE INSERT OR UPDATE ON doc_nodes
            FOR EACH ROW EXECUTE FUNCTION doc_nodes_derive_matter();

        CREATE OR REPLACE FUNCTION chunks_derive_columns() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE d record; j record;
        BEGIN
          SELECT jurisdiction_id, doc_type, language, legal_domain, matter_id INTO d
            FROM documents WHERE id = NEW.document_id;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'chunks: document % not visible', NEW.document_id;
          END IF;
          NEW.jurisdiction_id := d.jurisdiction_id;
          SELECT country, level, state_code, municipality INTO j
            FROM jurisdictions WHERE id = NEW.jurisdiction_id;
          NEW.doc_type := d.doc_type;
          NEW.language := d.language;
          NEW.legal_domain := d.legal_domain;
          NEW.matter_id := d.matter_id;
          NEW.country := j.country;
          NEW.level := j.level;
          NEW.state_code := j.state_code;
          NEW.municipality := j.municipality;
          RETURN NEW;
        END $$;
        """
    )
    op.execute(
        f"""
        CREATE TABLE client_files (
            id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id        uuid NOT NULL,
            uploaded_by      uuid NOT NULL,
            matter_id        uuid,
            title            text NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
            kind             text NOT NULL CHECK (kind IN ({KINDS})),
            language         text NOT NULL CHECK (language IN ('es','en')),
            jurisdiction_id  integer NOT NULL REFERENCES jurisdictions(id),
            file_token       text NOT NULL CHECK (file_token ~ '^[A-Z0-9]{{6,24}}$'),
            source_format    text NOT NULL CHECK (source_format IN ('pdf','docx','text')),
            status           text NOT NULL DEFAULT 'processing'
                               CHECK (status IN ('processing','ready','failed')),
            error_key        text CHECK (error_key ~ '^[a-z_]{{1,40}}$'),
            grammar          text CHECK (grammar IN ('generic','mx','us')),
            pages            integer,
            chunk_count      integer,
            document_id      uuid,
            created_at       timestamptz NOT NULL DEFAULT now(),
            finished_at      timestamptz,
            CONSTRAINT client_files_token_uq UNIQUE (tenant_id, file_token),
            CONSTRAINT client_files_user_fk FOREIGN KEY (uploaded_by, tenant_id)
                REFERENCES users (id, tenant_id) ON DELETE CASCADE,
            CONSTRAINT client_files_matter_fk FOREIGN KEY (matter_id, tenant_id)
                REFERENCES matters (id, tenant_id) ON DELETE CASCADE,
            CONSTRAINT client_files_document_fk FOREIGN KEY (document_id, tenant_id)
                REFERENCES documents (id, tenant_id) ON DELETE CASCADE
        )
        """
    )
    op.execute("CREATE INDEX client_files_tenant_idx ON client_files (tenant_id, created_at DESC)")
    for stmt in rls.statements("private", "client_files"):
        op.execute(stmt)
    for table in ("documents", "doc_nodes", "chunks", "client_files"):
        for stmt in rls.statements("matter_scoped", table):
            op.execute(stmt)
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON client_files TO legalai_app")


def downgrade() -> None:
    raise NotImplementedError("Forward-only migrations (invariant 3).")
