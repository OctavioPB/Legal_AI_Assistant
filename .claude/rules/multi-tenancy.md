# Multi-Tenancy / RLS Rules

- Every tenant-scoped table: `tenant_id UUID NOT NULL` + ENABLE + FORCE ROW LEVEL SECURITY.
- Read policy on corpus tables (documents, doc_nodes, chunks): own tenant OR PUBLIC_CORPUS
  ('00000000-0000-0000-0000-000000000001'). Product tables (users, sessions, dossiers, audit_log,
  tenants): own tenant only, never public. Policies are templates in `app/db/rls_templates.sql`
  rendered by `app/db/rls.py`; never hand-write a policy in a migration.
- Fail closed: without `app.tenant_id` NOTHING is visible (public corpus included). The helper
  `app_tenant_id()` returns NULL for unset/empty; every policy requires it to be non-NULL.
- Write policy: own tenant only, and never PUBLIC_CORPUS. Public corpus ingestion uses the
  dedicated `corpus_ingestor` role (INSERT/UPDATE of public rows only, no DELETE: reforms flip
  `is_current`). Only `legalai_provisioner` writes `tenants`; PUBLIC_CORPUS row is immutable.
- Roles (none superuser, none BYPASSRLS): `legalai_app` (DML under RLS, no DDL), `legalai_migrator`
  (owns the schema; separate creds; also subject to FORCE RLS), `corpus_ingestor`,
  `legalai_provisioner`. Created by `docker/initdb/00-roles.sh`; extensions by `01-bootstrap-db.sql`.
  Data migrations that must touch tenant rows run as an explicit, reviewed superuser step.
- Session anchor: `app.tenant_id` is bound per transaction with `set_config('app.tenant_id', :t,
  true)` (the transaction-local equivalent of `SET LOCAL`, which cannot take bind parameters) via
  `app/db/session.py::tenant_transaction` / `tenant_session` — never `SET` (session-wide), never
  string interpolation. Those helpers reject non-UUIDs and PUBLIC_CORPUS unless `allow_public`.
- Every cross-table reference carries `tenant_id` in a composite FK, so a row can never point at
  another tenant's row. `cite_id` is unique per tenant and client-file ids are prefixed `CF-`
  (no cross-tenant existence oracle, no shadowing of public ids). `audit_log` is append-only for
  every role (privileges + trigger).
- Known limit: `app.tenant_id` is a plain setting. RLS defends against application bugs and
  missing filters, not against arbitrary SQL executed AS `legalai_app` (e.g. SQL injection could
  call `set_config`). Mitigation today: parameterized queries only, no raw SQL from request data.
  Tighter option for the enterprise tier: signed context verified in-database (future ADR).
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

## Implementation notes (WS7)
- `app/tenancy/stores.py` is the single registry of tenant data stores (kinds in `DECLARED_KINDS`:
  postgres, object_storage, cache, traces, llm_gateway, job_queue). `StoreRegistry.offboard` refuses to run
  with a declared kind unregistered and reports what remains per store. Postgres erasure runs on a
  privileged engine; the audit log is retained unless erasure is explicit (ADR-013).
- `app/storage/objects.py`: keys are always `{tenant_id}/{validated name}`; presigned URLs are for one key,
  GET only, at most 15 minutes; `iam_policy` mirrors RLS (the bucket policy is the real guarantee).
- Traces (`app/observability/trace.py`) carry the tenant and are an erasable store.
