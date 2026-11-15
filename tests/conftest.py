"""Database fixtures: a throwaway database built by the REAL migrations, with the four production
roles. Nothing is mocked here — RLS is only worth testing against Postgres itself.

A CI-blocking suite must not skip silently, so an unreachable database FAILS the run with the
command to start it (docker compose up -d postgres).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import OperationalError

from app.settings import Settings, get_settings
from tests import helpers

ROOT = Path(__file__).resolve().parent.parent
TEST_DB = "legalai_test"


@pytest.fixture(scope="session")
def settings() -> Settings:
    return get_settings()


@pytest.fixture(scope="session")
def migrated_db(settings: Settings) -> Iterator[str]:
    """Create `legalai_test`, bootstrap it exactly like docker/initdb, run `alembic upgrade head`."""
    admin = create_engine(settings.superuser_url("postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)'))
            conn.execute(text(f'CREATE DATABASE "{TEST_DB}"'))
    except OperationalError as exc:
        pytest.fail(
            "Postgres is not reachable. Start the dev infrastructure first:\n"
            "  docker compose up -d postgres redis\n"
            f"({exc.orig})",
            pytrace=False,
        )
    finally:
        admin.dispose()

    boot = create_engine(settings.superuser_url(TEST_DB), isolation_level="AUTOCOMMIT")
    with boot.connect() as conn:
        conn.exec_driver_sql((ROOT / "docker/initdb/01-bootstrap-db.sql").read_text("utf-8"))
    boot.dispose()

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "app/db/migrations"))
    cfg.attributes["url"] = settings.migrator_url(TEST_DB)
    command.upgrade(cfg, "head")
    yield TEST_DB


def _engine(url: object) -> Iterator[Engine]:
    engine = create_engine(url)  # type: ignore[arg-type]
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def admin_engine(settings: Settings, migrated_db: str) -> Iterator[Engine]:
    """Superuser: bypasses RLS. Used ONLY for cleanup and for assertions about the catalog."""
    yield from _engine(settings.superuser_url(migrated_db))


@pytest.fixture(scope="session")
def migrator_engine(settings: Settings, migrated_db: str) -> Iterator[Engine]:
    yield from _engine(settings.migrator_url(migrated_db))


@pytest.fixture(scope="session")
def app_engine(settings: Settings, migrated_db: str) -> Iterator[Engine]:
    """legalai_app. pool_size=1 so consecutive tenants REUSE one connection: a context that leaked
    across transactions would show up as cross-tenant rows."""
    engine = create_engine(settings.app_url(migrated_db), pool_size=1, max_overflow=0)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def ingestor_engine(settings: Settings, migrated_db: str) -> Iterator[Engine]:
    yield from _engine(settings.ingestor_url(migrated_db))


@pytest.fixture(scope="session")
def provisioner_engine(settings: Settings, migrated_db: str) -> Iterator[Engine]:
    yield from _engine(settings.provisioner_url(migrated_db))


@pytest.fixture(autouse=True)
def clean_data(admin_engine: Engine) -> Iterator[None]:
    """Every test starts from the migrated baseline (catalogs + PUBLIC_CORPUS tenant only)."""
    yield
    with admin_engine.begin() as conn:
        # replica role disables user triggers (audit_log is append-only even for the owner).
        conn.execute(text("SET LOCAL session_replication_role = replica"))
        conn.execute(
            text(
                "TRUNCATE client_files, cite_lineage, corpus_changes, source_checks, chunks, doc_nodes, documents, "
                "matter_sources, matter_notes, verification_reports, messages, conversations, "
                "matter_members, matters, dossiers, tenant_model_policy, sessions, users, audit_log "
                "RESTART IDENTITY"
            )
        )
        conn.execute(text("DELETE FROM tenants WHERE id <> public_corpus_id()"))


@pytest.fixture
def tenant_a(provisioner_engine: Engine) -> uuid.UUID:
    return helpers.make_tenant(provisioner_engine, "firm-a")


@pytest.fixture
def tenant_b(provisioner_engine: Engine) -> uuid.UUID:
    return helpers.make_tenant(provisioner_engine, "firm-b")
