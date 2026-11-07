-- RLS policy templates (multi-tenancy.md). Single source: app/db/rls.py renders these for the
-- migrations; tests/test_tenant_isolation.py proves them. Placeholders: {table}.
--
-- Fail-closed rule: without `app.tenant_id` (NULL) NOTHING is visible, not even PUBLIC_CORPUS.
-- Writes by `legalai_app` can never target PUBLIC_CORPUS; only `corpus_ingestor` writes it.

-- @template corpus
-- Tenant tables that may hold public-corpus rows (documents, doc_nodes, chunks).
ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
ALTER TABLE {table} FORCE ROW LEVEL SECURITY;

CREATE POLICY {table}_read ON {table} FOR SELECT
  USING (app_tenant_id() IS NOT NULL
         AND (tenant_id = app_tenant_id() OR tenant_id = public_corpus_id()));

CREATE POLICY {table}_app_insert ON {table} FOR INSERT TO legalai_app
  WITH CHECK (tenant_id = app_tenant_id() AND tenant_id <> public_corpus_id());
CREATE POLICY {table}_app_update ON {table} FOR UPDATE TO legalai_app
  USING (tenant_id = app_tenant_id() AND tenant_id <> public_corpus_id())
  WITH CHECK (tenant_id = app_tenant_id() AND tenant_id <> public_corpus_id());
CREATE POLICY {table}_app_delete ON {table} FOR DELETE TO legalai_app
  USING (tenant_id = app_tenant_id() AND tenant_id <> public_corpus_id());

CREATE POLICY {table}_ingest_insert ON {table} FOR INSERT TO corpus_ingestor
  WITH CHECK (tenant_id = public_corpus_id());
CREATE POLICY {table}_ingest_update ON {table} FOR UPDATE TO corpus_ingestor
  USING (tenant_id = public_corpus_id())
  WITH CHECK (tenant_id = public_corpus_id());
-- @end

-- @template private
-- Tenant-private tables (users, sessions, dossiers): own tenant only, no public rows ever.
ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
ALTER TABLE {table} FORCE ROW LEVEL SECURITY;

CREATE POLICY {table}_read ON {table} FOR SELECT
  USING (app_tenant_id() IS NOT NULL AND tenant_id = app_tenant_id());
CREATE POLICY {table}_app_insert ON {table} FOR INSERT TO legalai_app
  WITH CHECK (tenant_id = app_tenant_id() AND tenant_id <> public_corpus_id());
CREATE POLICY {table}_app_update ON {table} FOR UPDATE TO legalai_app
  USING (tenant_id = app_tenant_id() AND tenant_id <> public_corpus_id())
  WITH CHECK (tenant_id = app_tenant_id() AND tenant_id <> public_corpus_id());
CREATE POLICY {table}_app_delete ON {table} FOR DELETE TO legalai_app
  USING (tenant_id = app_tenant_id() AND tenant_id <> public_corpus_id());
-- @end

-- @template append_only
-- audit_log: readable and insertable by its own tenant; no UPDATE/DELETE policy exists, the
-- privileges are revoked, and a trigger blocks them for every role including the owner.
ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
ALTER TABLE {table} FORCE ROW LEVEL SECURITY;

CREATE POLICY {table}_read ON {table} FOR SELECT
  USING (app_tenant_id() IS NOT NULL AND tenant_id = app_tenant_id());
CREATE POLICY {table}_app_insert ON {table} FOR INSERT TO legalai_app
  WITH CHECK (tenant_id = app_tenant_id() AND tenant_id <> public_corpus_id());
-- @end
