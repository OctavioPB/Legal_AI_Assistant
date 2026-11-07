"""Typed settings (WS0.T06 subset: database access). No credentials live in code paths other than
these dev defaults, which match docker-compose.yml and `.env.example`."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    legalai_pg_host: str = "127.0.0.1"
    legalai_pg_port: int = 5432
    legalai_db_name: str = "legalai"

    postgres_password: str = "dev-superuser-only"
    legalai_migrator_password: str = "dev-migrator"
    legalai_app_password: str = "dev-app"
    legalai_ingestor_password: str = "dev-ingestor"
    legalai_provisioner_password: str = "dev-provisioner"

    def url(self, user: str, password: str, database: str | None = None) -> URL:
        return URL.create(
            "postgresql+psycopg",
            username=user,
            password=password,
            host=self.legalai_pg_host,
            port=self.legalai_pg_port,
            database=database or self.legalai_db_name,
        )

    def superuser_url(self, database: str | None = None) -> URL:
        return self.url("postgres", self.postgres_password, database)

    def migrator_url(self, database: str | None = None) -> URL:
        return self.url("legalai_migrator", self.legalai_migrator_password, database)

    def app_url(self, database: str | None = None) -> URL:
        return self.url("legalai_app", self.legalai_app_password, database)

    def ingestor_url(self, database: str | None = None) -> URL:
        return self.url("corpus_ingestor", self.legalai_ingestor_password, database)

    def provisioner_url(self, database: str | None = None) -> URL:
        return self.url("legalai_provisioner", self.legalai_provisioner_password, database)


@lru_cache
def get_settings() -> Settings:
    return Settings()
