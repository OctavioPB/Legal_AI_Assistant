#!/bin/sh
# Cluster-level roles (WS0.T03, multi-tenancy.md). None of them is superuser or BYPASSRLS.
#   legalai_migrator     owns the schema, runs alembic (DDL). Separate credentials from the app.
#   legalai_app          the API/workers at query time: DML only, subject to RLS, no DDL.
#   corpus_ingestor      writes the PUBLIC_CORPUS tenant only (policy-enforced).
#   legalai_provisioner  creates/updates rows of `tenants` (signup/offboarding path).
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<EOSQL
CREATE ROLE legalai_migrator    LOGIN PASSWORD '${LEGALAI_MIGRATOR_PASSWORD}'    NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
CREATE ROLE legalai_app         LOGIN PASSWORD '${LEGALAI_APP_PASSWORD}'         NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
CREATE ROLE corpus_ingestor     LOGIN PASSWORD '${LEGALAI_INGESTOR_PASSWORD}'    NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
CREATE ROLE legalai_provisioner LOGIN PASSWORD '${LEGALAI_PROVISIONER_PASSWORD}' NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
EOSQL
