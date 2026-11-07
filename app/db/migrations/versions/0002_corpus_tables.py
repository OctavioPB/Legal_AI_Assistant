"""WS1.T02 — tenants, documents, doc_nodes (ltree), chunks (vector 1024 + generated tsvector +
denormalized jurisdiction columns) and their indexes.

Design notes (blueprint §3.2 refined; the differences are deliberate):
- Cross-tenant references are impossible by construction: every child table references its parent
  through a COMPOSITE foreign key that includes tenant_id, so a row of tenant A can never point at a
  row of tenant B even if someone guesses its UUID (FK checks bypass RLS, the composite key does not).
- chunks.text is the exact slice of the source; breadcrumb and embedded_text are separate columns
  (legal-chunking skill), so verbatim-quote checks and deep-links stay honest.
- chunks.jurisdiction_id/country/level/state_code/municipality/doc_type/language/legal_domain are
  DERIVED by a trigger from the document and the jurisdiction catalog: they are denormalized for
  the single-index pre-filter but can never drift from either.
- cite_id is unique PER TENANT (a global UNIQUE would let tenant A learn that tenant B holds a
  cite_id by provoking a conflict). Namespaces are disjoint: client-file ids start with 'CF-',
  public ones never do, so a tenant cannot shadow a public cite_id.
- cite_id is immutable (invariant 3). embedding_model / embedding_revision / chunker_version /
  chunk_thresholds are stored per chunk (invariant 5, ADR-006).

Revision ID: 0002
Revises: 0001
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

PUBLIC_CORPUS = "00000000-0000-0000-0000-000000000001"


def upgrade() -> None:
    # ── Accent-folding text search configs: "articulo 47" must match "artículo 47". The
    # unaccent dictionary sits before the stemmer; both documents (to_tsvector) and queries
    # (websearch_to_tsquery) must use the same config.
    for cfg, stem, base in (
        ("legal_es", "spanish_stem", "spanish"),
        ("legal_en", "english_stem", "english"),
    ):
        op.execute(f"CREATE TEXT SEARCH CONFIGURATION {cfg} (COPY = pg_catalog.{base})")
        op.execute(
            f"ALTER TEXT SEARCH CONFIGURATION {cfg} "
            f"ALTER MAPPING FOR hword, hword_part, word WITH public.unaccent, {stem}"
        )

    # ── tenants (needed by the FKs below; RLS is applied in 0003) ────────────────────────────
    op.execute(
        f"""
        CREATE TABLE tenants (
            id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            slug        text NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9][a-z0-9-]{{1,62}}$'),
            name        text NOT NULL,
            tier        text NOT NULL DEFAULT 'pooled' CHECK (tier IN ('pooled','enterprise')),
            status      text NOT NULL DEFAULT 'active' CHECK (status IN ('active','suspended')),
            created_at  timestamptz NOT NULL DEFAULT now()
        );
        INSERT INTO tenants (id, slug, name) VALUES
            ('{PUBLIC_CORPUS}', 'public-corpus', 'PUBLIC_CORPUS (synthetic tenant)');

        -- The synthetic tenant is structural: it can never be renamed, suspended or removed.
        CREATE FUNCTION tenants_protect_public() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF OLD.id = '{PUBLIC_CORPUS}'::uuid THEN
            RAISE EXCEPTION 'PUBLIC_CORPUS tenant is immutable';
          END IF;
          RETURN CASE TG_OP WHEN 'DELETE' THEN OLD ELSE NEW END;
        END $$;
        CREATE TRIGGER tenants_protect_public_trg BEFORE UPDATE OR DELETE ON tenants
            FOR EACH ROW EXECUTE FUNCTION tenants_protect_public();
        """
    )

    # ── documents ────────────────────────────────────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE documents (
            id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id        uuid NOT NULL REFERENCES tenants(id),
            jurisdiction_id  integer NOT NULL REFERENCES jurisdictions(id),
            doc_type         text NOT NULL CHECK (doc_type IN (
                               'codigo','ley','reglamento','bando','jurisprudencia_scjn',
                               'tesis_aislada','dof_publicacion','case_law','statute',
                               'regulation','client_file')),
            legal_domain     text[],
            title            text NOT NULL,
            official_id      text,
            language         text NOT NULL CHECK (language IN ('es','en')),
            valid_from       date,
            valid_to         date,                 -- NULL = in force
            version_label    text,
            source_uri       text NOT NULL,
            checksum_sha256  text NOT NULL CHECK (checksum_sha256 ~ '^[0-9a-f]{64}$'),
            ingested_at      timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT documents_validity_ck CHECK (valid_to IS NULL OR valid_from IS NULL
                                                    OR valid_to >= valid_from),
            -- private files are client_file; public doc types never carry a tenant's own id
            CONSTRAINT documents_client_file_ck CHECK (
                (doc_type = 'client_file') = (tenant_id <> '00000000-0000-0000-0000-000000000001')),
            CONSTRAINT documents_id_tenant_uq UNIQUE (id, tenant_id),
            -- idempotent ingestion: the same source bytes are never stored twice for a tenant
            CONSTRAINT documents_source_uq UNIQUE (tenant_id, source_uri, checksum_sha256)
        )
        """
    )
    op.execute("CREATE INDEX documents_jurisdiction_idx ON documents (jurisdiction_id)")

    # ── doc_nodes (the legal tree) ───────────────────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE doc_nodes (
            id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            document_id  uuid NOT NULL,
            tenant_id    uuid NOT NULL,
            parent_id    uuid,
            node_type    text NOT NULL CHECK (node_type IN (
                           'libro','titulo','capitulo','seccion','articulo','fraccion','inciso',
                           'parrafo','transitorio','holding','syllabus','opinion_section')),
            label        text NOT NULL,
            ordinal      integer NOT NULL CHECK (ordinal >= 0),
            path         ltree NOT NULL,
            full_text    text NOT NULL,
            CONSTRAINT doc_nodes_id_tenant_uq UNIQUE (id, tenant_id),
            CONSTRAINT doc_nodes_id_document_uq UNIQUE (id, document_id),
            CONSTRAINT doc_nodes_path_uq UNIQUE (document_id, path),
            -- same tenant as its document, and its parent lives in the same document
            CONSTRAINT doc_nodes_document_fk FOREIGN KEY (document_id, tenant_id)
                REFERENCES documents (id, tenant_id) ON DELETE CASCADE,
            CONSTRAINT doc_nodes_parent_fk FOREIGN KEY (parent_id, document_id)
                REFERENCES doc_nodes (id, document_id)
        )
        """
    )
    op.execute("CREATE INDEX doc_nodes_path_gist ON doc_nodes USING gist (path)")
    op.execute("CREATE INDEX doc_nodes_path_btree ON doc_nodes USING btree (path)")
    op.execute("CREATE INDEX doc_nodes_parent_idx ON doc_nodes (parent_id)")

    # ── chunks ───────────────────────────────────────────────────────────────────────────────
    op.execute(
        f"""
        CREATE TABLE chunks (
            id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id          uuid NOT NULL,
            document_id        uuid NOT NULL,
            node_id            uuid NOT NULL,
            jurisdiction_id    integer NOT NULL REFERENCES jurisdictions(id),
            -- derived by chunks_derive_columns_trg (never trust the caller):
            country            text NOT NULL,
            level              text NOT NULL,
            state_code         text,
            municipality       text,
            doc_type           text NOT NULL,
            legal_domain       text[],
            language           text NOT NULL,
            -- chunk's own state:
            is_current         boolean NOT NULL DEFAULT true,
            is_navigation      boolean NOT NULL DEFAULT false,
            cite_id            text NOT NULL
                               CHECK (cite_id ~ '^[A-Za-z0-9][A-Za-z0-9._-]*$' AND length(cite_id) <= 200),
            citation_string    text NOT NULL,
            text               text NOT NULL,     -- exact slice of the normalized source
            breadcrumb         text NOT NULL,     -- '[Ley › Título › Artículo 47 › Fracción II]'
            embedded_text      text NOT NULL,     -- breadcrumb (+ caput lead-in) + text, what is embedded
            char_start         integer NOT NULL CHECK (char_start >= 0),
            char_end           integer NOT NULL,
            token_count        integer NOT NULL CHECK (token_count > 0),
            embedding          vector(1024) NOT NULL,
            -- invariant 5 / ADR-006: what this chunk and vector were built with
            embedding_model    text NOT NULL,
            embedding_revision text NOT NULL,
            chunker_version    integer NOT NULL,
            chunk_thresholds   jsonb NOT NULL,
            fts                tsvector GENERATED ALWAYS AS (
                setweight(to_tsvector(CASE language WHEN 'en' THEN 'legal_en'::regconfig
                                                   ELSE 'legal_es'::regconfig END, breadcrumb), 'A') ||
                setweight(to_tsvector(CASE language WHEN 'en' THEN 'legal_en'::regconfig
                                                   ELSE 'legal_es'::regconfig END, text), 'B')
            ) STORED,
            created_at         timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT chunks_span_ck CHECK (char_end > char_start),
            CONSTRAINT chunks_cite_namespace_ck CHECK (
                (tenant_id = '{PUBLIC_CORPUS}') = (cite_id NOT LIKE 'CF-%')),
            CONSTRAINT chunks_tenant_cite_uq UNIQUE (tenant_id, cite_id),
            CONSTRAINT chunks_document_fk FOREIGN KEY (document_id, tenant_id)
                REFERENCES documents (id, tenant_id) ON DELETE CASCADE,
            CONSTRAINT chunks_node_fk FOREIGN KEY (node_id, tenant_id)
                REFERENCES doc_nodes (id, tenant_id)
        )
        """
    )

    # Denormalized columns are derived, so they cannot disagree with the catalog or the document.
    op.execute(
        """
        CREATE FUNCTION chunks_derive_columns() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE d record; j record;
        BEGIN
          SELECT jurisdiction_id, doc_type, language, legal_domain INTO d
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
          NEW.country := j.country;
          NEW.level := j.level;
          NEW.state_code := j.state_code;
          NEW.municipality := j.municipality;
          RETURN NEW;
        END $$;
        CREATE TRIGGER chunks_derive_columns_trg BEFORE INSERT OR UPDATE ON chunks
            FOR EACH ROW EXECUTE FUNCTION chunks_derive_columns();

        -- Invariant 3: a cite_id (and the text it points at) never changes; reforms insert a
        -- new versioned chunk and flip is_current on the old one.
        CREATE FUNCTION chunks_immutable_identity() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.cite_id IS DISTINCT FROM OLD.cite_id THEN
            RAISE EXCEPTION 'cite_id is immutable (invariant 3): % -> %', OLD.cite_id, NEW.cite_id;
          END IF;
          IF NEW.tenant_id IS DISTINCT FROM OLD.tenant_id THEN
            RAISE EXCEPTION 'chunks.tenant_id is immutable';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER chunks_immutable_identity_trg BEFORE UPDATE ON chunks
            FOR EACH ROW EXECUTE FUNCTION chunks_immutable_identity();
        """
    )

    # Indexes: the pre-filter index goes FIRST in the access path, the vector index serves the
    # semantic arm. With pgvector >= 0.8 set hnsw.iterative_scan so filtered ANN queries do not
    # come back short (the WS3 retrieval code sets it per transaction).
    op.execute(
        "CREATE INDEX chunks_prefilter_idx ON chunks "
        "(tenant_id, country, level, state_code, municipality, is_current)"
    )
    op.execute("CREATE INDEX chunks_domain_idx ON chunks USING gin (legal_domain)")
    op.execute("CREATE INDEX chunks_fts_idx ON chunks USING gin (fts)")
    op.execute(
        "CREATE INDEX chunks_hnsw_idx ON chunks USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 128)"
    )
    op.execute("CREATE INDEX chunks_document_idx ON chunks (document_id)")
    op.execute("CREATE INDEX chunks_node_idx ON chunks (node_id)")
    # Version guard lookups (ADR-006): which generation is live for a tenant.
    op.execute(
        "CREATE INDEX chunks_version_idx ON chunks "
        "(embedding_model, embedding_revision, chunker_version)"
    )


def downgrade() -> None:
    raise NotImplementedError("Forward-only: reforms version, nothing is deleted (invariant 3).")
