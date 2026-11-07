"""Alembic environment. Migrations run as `legalai_migrator` (DDL owner), never as the app role."""

from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine

from app.settings import get_settings

config = context.config


def run_migrations_online() -> None:
    url = config.attributes.get("url") or get_settings().migrator_url()
    engine = create_engine(url)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    raise RuntimeError(
        "Offline (SQL-script) migrations are not supported: RLS DDL is verified live."
    )
run_migrations_online()
