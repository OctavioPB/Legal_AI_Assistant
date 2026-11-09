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

-- ADR-018: the user anchor. These RESTRICTIVE policies are added ON TOP of `private` (they AND with
-- it): without `app.user_id` (NULL) nothing below is visible, not even to the same tenant.

-- @template personal
-- Rows owned by one user that may be shared through a matter (conversations, verification_reports):
-- visible to their author, or to the members of their matter.
CREATE POLICY {table}_owner_or_matter ON {table} AS RESTRICTIVE FOR ALL
  USING (app_user_id() IS NOT NULL
         AND (user_id = app_user_id() OR (matter_id IS NOT NULL AND app_matter_visible(matter_id))))
  WITH CHECK (app_user_id() IS NOT NULL
         AND (user_id = app_user_id() OR (matter_id IS NOT NULL AND app_matter_visible(matter_id))));
-- @end

-- @template matter_root
-- `matters`: its creator and its members. matter_members is plain `private` (a policy on it that read
-- `matters` back would recurse).
CREATE POLICY {table}_member ON {table} AS RESTRICTIVE FOR ALL
  USING (app_user_id() IS NOT NULL AND (created_by = app_user_id() OR EXISTS (
         SELECT 1 FROM matter_members mm WHERE mm.matter_id = {table}.id
         AND mm.tenant_id = {table}.tenant_id AND mm.user_id = app_user_id())))
  WITH CHECK (app_user_id() IS NOT NULL AND (created_by = app_user_id() OR EXISTS (
         SELECT 1 FROM matter_members mm WHERE mm.matter_id = {table}.id
         AND mm.tenant_id = {table}.tenant_id AND mm.user_id = app_user_id())));
-- @end

-- @template matter_child
-- Notes and saved sources of a matter: exactly as visible as the matter.
CREATE POLICY {table}_via_matter ON {table} AS RESTRICTIVE FOR ALL
  USING (app_matter_visible(matter_id))
  WITH CHECK (app_matter_visible(matter_id));
-- @end

-- @template message
-- `messages`: exactly as visible as their conversation.
CREATE POLICY {table}_via_conversation ON {table} AS RESTRICTIVE FOR ALL
  USING (EXISTS (SELECT 1 FROM conversations c WHERE c.id = {table}.conversation_id
         AND c.tenant_id = {table}.tenant_id))
  WITH CHECK (EXISTS (SELECT 1 FROM conversations c WHERE c.id = {table}.conversation_id
         AND c.tenant_id = {table}.tenant_id));
-- @end
