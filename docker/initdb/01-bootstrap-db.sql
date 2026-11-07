-- Per-database bootstrap, run as superuser: by the postgres image on first start AND by the
-- test-database fixture (tests/conftest.py), so both stay identical.
-- Extensions need superuser; every other object is created by alembic as legalai_migrator.
CREATE EXTENSION IF NOT EXISTS vector;     -- pgvector (HNSW)
CREATE EXTENSION IF NOT EXISTS ltree;      -- document tree paths
CREATE EXTENSION IF NOT EXISTS unaccent;   -- accent-folding text search configs
CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- gen_random_uuid() safety net

-- Nobody but the migrator may create objects: the app role has no DDL (WS0.T03).
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA public TO legalai_migrator;
GRANT USAGE ON SCHEMA public TO legalai_app, corpus_ingestor, legalai_provisioner;
