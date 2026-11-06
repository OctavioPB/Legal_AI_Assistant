# Multi-Tenancy / RLS Rules

- Every tenant-scoped table: `tenant_id UUID NOT NULL` + ENABLE + FORCE ROW LEVEL SECURITY.
- Read policy: own tenant OR PUBLIC_CORPUS ('00000000-0000-0000-0000-000000000001').
- Write policy: own tenant only. Public corpus ingestion uses dedicated `corpus_ingestor` role.
- App DB role: `legalai_app` — NO BYPASSRLS, no DDL. Migrations: `legalai_migrator` (separate creds).
- Session anchor: `SET LOCAL app.tenant_id = :t` inside a transaction — never `SET` (session-wide),
  never string interpolation.
- Object storage: `s3://bucket/{tenant_id}/...` with IAM policy mirroring RLS. Presigned URLs only.
- LiteLLM budgets/rate limits keyed by tenant_id (team = tenant).
- REQUIRED TEST: `tests/test_tenant_isolation.py` seeds tenants A/B with overlapping content and
  asserts zero cross-tenant rows in retrieval results AND in raw SQL under each context. CI-blocking.
- Tenant erasure/offboarding enumerates EVERY store holding tenant data from one registry:
  Postgres tables, `s3://bucket/{tenant_id}/`, Redis keys/caches, Langfuse traces, LiteLLM
  team/keys, Celery payloads. A new store of tenant data must be added to that registry (same
  discipline as the isolation test's table registry); erasure never touches `PUBLIC_CORPUS`.
  (Library-RAG lesson: a new memory store not registered in `reset` is silently left behind.)
- Observability is a data store: traces/audit rows carry `tenant_id`, are covered by the same
  isolation test, and prompts/fragments in traces are subject to the tenant's retention policy.
- Adding a new table? Copy the policy pair from `app/db/rls_templates.sql`; add it to the
  isolation test's table registry — the test fails if a tenant-scoped table lacks policies.
