"""ADR-006 — index generations: a rebuilt corpus is built aside and switched in atomically.

* `index_generations` (global, no tenant data): one row per generation, exactly one `active`
  (partial unique index). It records the embedder, its revision and the chunker version it was
  built with. Readable by every role; written only by the rebuild, an explicit superuser step.
* `doc_nodes.generation` and `chunks.generation`: the derived artifacts of the chunker and the
  embedder. `documents` are NOT versioned by generation: they describe the source (title, version,
  checksum, validity) and do not depend on the chunker, so reform lineage, firm files and alerts
  keep pointing at the same rows.
* New rows default to the active generation (`app_generation()`), so every existing write path
  (uploads, the reform watcher, the seed) writes the live generation without knowing about it.
* RESTRICTIVE `generation_scoped` policy (rls_templates.sql): every role under RLS sees and writes
  only the active generation; the 30-odd queries on these tables need no change.
* `cite_id` is unique per tenant AND generation (the next generation reuses the same ids), and a
  node's path per document AND generation. A chunk takes its node's generation (trigger).

The rows that exist today become generation 1, active.

Revision ID: 0015
Revises: 0014
"""

from alembic import op

from app.db import rls

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE index_generations (
            id                  integer PRIMARY KEY CHECK (id >= 1),
            status              text NOT NULL CHECK (status IN ('building','active','retired')),
            embedding_model     text,
            embedding_revision  text,
            chunker_version     integer,
            note                text CHECK (length(note) <= 400),
            created_at          timestamptz NOT NULL DEFAULT now(),
            activated_at        timestamptz
        );
        CREATE UNIQUE INDEX index_generations_one_active ON index_generations (status)
            WHERE status = 'active';
        -- The migrator is under FORCE RLS and sees no chunk, so generation 1's embedder and
        -- chunker are left unknown here; the rebuild (superuser) records them from the chunks.
        INSERT INTO index_generations (id, status, note, activated_at)
        VALUES (1, 'active', 'the corpus as it was before index generations existed (ADR-006)',
                now());

        CREATE FUNCTION app_generation() RETURNS integer LANGUAGE sql STABLE AS $$
          SELECT id FROM index_generations WHERE status = 'active'
        $$;
        GRANT SELECT ON index_generations TO legalai_app, corpus_ingestor, legalai_provisioner;

        ALTER TABLE doc_nodes ADD COLUMN generation integer NOT NULL DEFAULT 1
            REFERENCES index_generations (id);
        ALTER TABLE doc_nodes ALTER COLUMN generation SET DEFAULT app_generation();
        ALTER TABLE chunks ADD COLUMN generation integer NOT NULL DEFAULT 1
            REFERENCES index_generations (id);
        ALTER TABLE chunks ALTER COLUMN generation SET DEFAULT app_generation();

        ALTER TABLE doc_nodes DROP CONSTRAINT doc_nodes_path_uq,
            ADD CONSTRAINT doc_nodes_path_uq UNIQUE (document_id, generation, path);
        ALTER TABLE chunks DROP CONSTRAINT chunks_tenant_cite_uq,
            ADD CONSTRAINT chunks_tenant_cite_uq UNIQUE (tenant_id, generation, cite_id);
        CREATE INDEX chunks_generation_idx ON chunks (generation);

        -- a chunk belongs to the generation of the node it was cut from, never another one
        CREATE FUNCTION chunks_take_node_generation() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE g integer;
        BEGIN
          SELECT generation INTO g FROM doc_nodes WHERE id = NEW.node_id;
          IF g IS NULL THEN
            RAISE EXCEPTION 'chunks: node % not visible in this generation', NEW.node_id;
          END IF;
          NEW.generation := g;
          RETURN NEW;
        END $$;
        CREATE TRIGGER chunks_take_node_generation_trg BEFORE INSERT OR UPDATE ON chunks
            FOR EACH ROW EXECUTE FUNCTION chunks_take_node_generation();
        """
    )
    for table in ("doc_nodes", "chunks"):
        for stmt in rls.statements("generation_scoped", table):
            op.execute(stmt)


def downgrade() -> None:
    raise NotImplementedError("Forward-only migrations (invariant 3).")
