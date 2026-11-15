"""Typed settings (WS0.T06 subset: database access). No credentials live in code paths other than
these dev defaults, which match docker-compose.yml and `.env.example`."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal, Self

from pydantic import SecretStr, model_validator
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
    # --- model profiles (ADR-024): chosen per conversation, fixed at creation ------------------
    model_profile_default: Literal["gemma", "qwen", "claude"] = "gemma"
    # Remote profile: usable only with a key here (from .env, never the repo) AND the firm's
    # explicit opt-in (tenant_model_policy). Without the key the profile is listed as unavailable.
    anthropic_api_key: SecretStr | None = None
    anthropic_effort: Literal["low", "medium", "high"] = "medium"
    # Claude Opus 5.5 always thinks and thinking counts toward max_tokens: the answer cap of the
    # alias is raised by this headroom so reasoning cannot eat the answer.
    anthropic_thinking_headroom_tokens: int = 8_000
    anthropic_timeout_s: float = 300.0
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

    # --- conversations (ADR-016) -------------------------------------------------------------
    conversation_history_turns: int = 3  # previous exchanges the model sees, in the USER turn
    conversation_history_chars: int = 6_000  # clip of that history (newest kept)
    conversation_carry_max: int = 12  # ids cited earlier in the conversation, carried into context
    conversation_case_max_chars: int = 8_000  # facts + claim of a case

    # --- citation validator (rag.md) ---------------------------------------------------------
    validator_max_retries: int = 1  # one regeneration, then a system-written refusal
    validator_regen_max_tokens: int = 4096  # its own cap, never the model default
    # ADR-017: reform watcher (the only component that may reach the network) and its alerts
    reform_watch_timeout_s: float = 120.0
    reform_watch_user_agent: str = "LegalAI-ReformWatcher/1.0"
    reform_watch_max_bytes: int = 60 * 1024 * 1024
    reform_alert_days: int = 365  # a reform older than this no longer raises alerts
    # Retrieval experiments (golden protocol, 2026-11-08). Off by default until measured:
    # lexical_match "any" ORs the query lexemes (websearch_to_tsquery ANDs them: a natural question
    # matched nothing); rerank_breadcrumb scores breadcrumb + text, as the chunks were embedded.
    lexical_match: Literal["all", "any"] = "all"
    # ADR-022: a unit the query names explicitly ("artículo 123, apartado B, fracción XIII de la
    # Constitución") is resolved by the citation grammar and placed first; it passes the tau gate.
    retrieval_explicit_reference: bool = True
    retrieval_explicit_max: int = (
        8  # units taken from explicit references (an article's fracciones)
    )
    rerank_breadcrumb: bool = False
    # ADR-020: the units of an expanded article are citable (they are rendered to the model)
    context_citable_units: bool = True
    # ADR-023: a unit inside an apartado expands to that apartado, not to the whole article
    context_unit_apartado: bool = True
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
