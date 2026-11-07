"""FastAPI application: `uv run fastapi dev app/main.py` (http://127.0.0.1:8000).

Everything runs locally (ADR-014): Postgres, Ollama models, and this API on loopback. `create_app`
accepts prebuilt state so tests run the real routes with fakes.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import yaml
from fastapi import FastAPI
from sqlalchemy import create_engine

from app.api.deps import AppState
from app.api.routes import register_errors, router
from app.chat.service import ChatDeps
from app.ingestion.embedders import OllamaEmbedder
from app.ingestion.tokens import HFTokenCounter
from app.observability.trace import InMemoryTracer
from app.orchestration.ollama import OllamaClient
from app.orchestration.pins import load_registry
from app.orchestration.resilient import RateLimiter, ResilientClient, TenantUserLimits
from app.prompts.composer import Composer
from app.rag.rerank import DenseReranker
from app.settings import Settings, get_settings

TAU_PATH = Path(__file__).resolve().parents[1] / "config" / "retrieval.local.yaml"
DEFAULT_JWT_SECRET = Settings.model_fields["jwt_secret"].default


class UnsafeConfigurationError(RuntimeError):
    """The configuration is not acceptable outside a local development machine."""


def check_configuration(settings: Settings) -> None:
    if settings.environment != "dev" and settings.jwt_secret == DEFAULT_JWT_SECRET:
        raise UnsafeConfigurationError("JWT_SECRET is the development default; set a real secret")
    if len(settings.jwt_secret) < 32:
        raise UnsafeConfigurationError("JWT_SECRET must have at least 32 characters")


def load_tau(settings: Settings, path: Path = TAU_PATH) -> Settings:
    """tau comes from the calibration record (ADR-007), never from a guess in code."""
    if settings.refusal_tau is not None or not path.exists():
        return settings
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return settings.model_copy(update={"refusal_tau": float(data["tau"])})


def build_state(settings: Settings) -> AppState:
    """Wire the local stack. Importing nothing heavy until the app actually starts."""
    settings = load_tau(settings)
    registry = load_registry()
    embedder = OllamaEmbedder(settings.embedder_model, base_url=settings.ollama_url)
    llm = ResilientClient(
        OllamaClient(registry, base_url=settings.ollama_url, num_ctx=settings.llm_num_ctx,
                     timeout_s=settings.llm_timeout_s),
        registry, timeout_s=settings.llm_timeout_s,
    )  # fmt: skip
    chat = ChatDeps(
        embedder=embedder, reranker=DenseReranker(embedder), counter=HFTokenCounter(),
        settings=settings, composer=Composer(), llm=llm, aliases=registry, tracer=InMemoryTracer(),
    )  # fmt: skip
    limits = TenantUserLimits(
        RateLimiter(settings.rate_tenant_per_s, settings.rate_tenant_burst),
        RateLimiter(settings.rate_user_per_s, settings.rate_user_burst),
    )
    engine = create_engine(settings.app_url(), pool_size=8, pool_pre_ping=True)
    return AppState(settings=settings, engine=engine, chat=chat, limits=limits)


def create_app(state: AppState | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        st = state or build_state(get_settings())
        check_configuration(st.settings)
        app.state.legal = st
        yield
        st.engine.dispose()

    app = FastAPI(title="Legal AI Assistant", version="0.1.0", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)  # fmt: skip
    app.include_router(router)
    register_errors(app)
    return app


app = create_app()
