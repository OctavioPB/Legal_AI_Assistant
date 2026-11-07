"""Typed settings (WS0.T06 subset: database access). No credentials live in code paths other than
these dev defaults, which match docker-compose.yml and `.env.example`."""

from __future__ import annotations

from functools import lru_cache
from typing import Self

from pydantic import model_validator
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

    # --- retrieval (rag.md defaults; tune only with eval evidence) ---------------------------
    retrieval_arm_top: int = 40
    retrieval_rrf_k: int = 60
    retrieval_rrf_top: int = 20
    retrieval_rerank_top: int = 8
    retrieval_context_budget_tokens: int = 12_000
    # tau is CALIBRATED from probe sets (ADR-007), never guessed: None = the gate refuses to run.
    refusal_tau: float | None = None
    refusal_min_fragments: int = 1  # ADR-007(c): evaluate >= m fragments above tau

    # --- local model stack (ADR-014): every model runs on this machine through Ollama -------
    ollama_url: str = "http://localhost:11434"
    embedder_model: str = "bge-m3"
    embedder_window: int = 8192  # bge-m3 context length, read from the runtime (/api/show)
    llm_num_ctx: int = (
        20_480  # per-call context for the chat model (context budget + answer + prompt)
    )
    llm_timeout_s: float = 600.0
    # --- chunking (rag.md); stored with every chunk, changing them = new generation (ADR-006) ---
    chunk_min_tokens: int = 80
    chunk_max_tokens: int = 800
    # --- demo auth -------------------------------------------------------------------------------
    jwt_secret: str = "dev-only-change-me-32-bytes-minimum!!"
    jwt_ttl_minutes: int = 480
    environment: str = "dev"  # anything else refuses the default JWT secret (see main.create_app)
    rate_tenant_per_s: float = 2.0
    rate_tenant_burst: int = 30
    rate_user_per_s: float = 0.5
    rate_user_burst: int = 10

    # --- citation validator (rag.md) ---------------------------------------------------------
    validator_max_retries: int = 1  # one regeneration, then a system-written refusal
    validator_regen_max_tokens: int = 4096  # its own cap, never the model default
    validator_quote_min_words: int = 5  # a quoted run shorter than this is not checked
    validator_coverage_min_words: int = 6  # shorter units (headings, labels) need no citation

    @model_validator(mode="after")
    def _retrieval_consistent(self) -> Self:
        if not 0 < self.retrieval_rerank_top <= self.retrieval_rrf_top <= self.retrieval_arm_top:
            raise ValueError("need 0 < rerank_top <= rrf_top <= arm_top")
        if self.retrieval_rrf_k < 1 or self.refusal_min_fragments < 1:
            raise ValueError("rrf_k and refusal_min_fragments must be >= 1")
        return self

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
