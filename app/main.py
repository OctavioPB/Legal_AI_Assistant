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

from app.api.conversations import router as conversations_router
from app.api.deps import AppState
from app.api.laws import router as laws_router
from app.api.matters import router as matters_router
from app.api.models import register_model_errors
from app.api.models import router as models_router
from app.api.reforms import router as reforms_router
from app.api.routes import register_errors, router
from app.api.verify import router as verify_router
from app.chat.service import ChatDeps
from app.ingestion.embedders import OllamaEmbedder
from app.ingestion.tokens import HFTokenCounter
from app.observability.trace import InMemoryTracer
from app.orchestration.aliases import AliasRegistry
from app.orchestration.anthropic_client import AnthropicClient, MissingSDKError
from app.orchestration.ollama import OllamaClient
from app.orchestration.pins import load_registry
from app.orchestration.profiles import (
    ANTHROPIC_PINS,
    ModelProfile,
    ProfileCatalog,
    load_measurements,
    load_pins,
    qwen_registry,
)
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


def rerank_label(name: str, settings: Settings) -> str:
    """What tau was calibrated against: the scorer AND what it reads (scores of another input are
    another scale, ADR-007)."""
    return f"{name}+breadcrumb" if settings.rerank_breadcrumb else name


def check_tau_provenance(
    settings: Settings, *, embedder_model: str, embedder_revision: str, reranker: str,
    path: Path = TAU_PATH,
) -> None:  # fmt: skip
    """A tau read from the calibration record is only valid for the embedder revision and the
    reranker it was measured with (ADR-007): scores of another model are another scale."""
    if settings.refusal_tau is not None or not path.exists():
        return  # tau set explicitly (env/tests) or no record: nothing to compare against
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    rec = data.get("embedder") or {}
    found = (str(rec.get("model")), str(rec.get("revision")), str(data.get("reranker")))
    active = (embedder_model, embedder_revision, reranker)
    if found != active:
        raise UnsafeConfigurationError(
            f"tau in {path.name} was calibrated with {found} but the active stack is {active}; "
            "re-run `python -m evals.calibrate_tau --write`"
        )


def build_profiles(settings: Settings, local: AliasRegistry) -> ProfileCatalog:
    """ADR-024: the local profiles always; `claude` only with a key in .env (and the SDK
    installed). Without them it stays in the catalog as unavailable, never half-configured."""

    def ollama(reg: AliasRegistry) -> ResilientClient:
        inner = OllamaClient(reg, base_url=settings.ollama_url, num_ctx=settings.llm_num_ctx,
                             timeout_s=settings.llm_timeout_s)  # fmt: skip
        return ResilientClient(inner, reg, timeout_s=settings.llm_timeout_s)

    qwen = qwen_registry(local)
    remote = load_pins(ANTHROPIC_PINS)
    claude_llm: ResilientClient | None = None
    reason: str | None = None
    if settings.anthropic_api_key is None or not settings.anthropic_api_key.get_secret_value():
        reason = "no_api_key"
    else:
        try:
            inner = AnthropicClient(
                remote, api_key=settings.anthropic_api_key.get_secret_value(),
                effort=settings.anthropic_effort,
                thinking_headroom_tokens=settings.anthropic_thinking_headroom_tokens,
                timeout_s=settings.anthropic_timeout_s,
            )  # fmt: skip
            claude_llm = ResilientClient(inner, remote, timeout_s=settings.anthropic_timeout_s)
        except MissingSDKError:
            reason = "sdk_missing"
    return ProfileCatalog(
        profiles={
            "gemma": ModelProfile("gemma", local, ollama(local)),
            "qwen": ModelProfile("qwen", qwen, ollama(qwen)),
            "claude": ModelProfile("claude", remote, claude_llm, unavailable=reason),
        },
        default=settings.model_profile_default,
        measurements=load_measurements(),
    )


def build_state(settings: Settings) -> AppState:
    """Wire the local stack. Importing nothing heavy until the app actually starts."""
    registry = load_registry()
    embedder = OllamaEmbedder(settings.embedder_model, base_url=settings.ollama_url)
    reranker = DenseReranker(embedder)
    check_tau_provenance(
        settings, embedder_model=embedder.model, embedder_revision=embedder.revision,
        reranker=rerank_label(reranker.name, settings),
    )  # fmt: skip
    settings = load_tau(settings)
    profiles = build_profiles(settings, registry)
    default = profiles.serving(settings.model_profile_default)
    assert default.llm is not None
    chat = ChatDeps(
        embedder=embedder, reranker=reranker, counter=HFTokenCounter(),
        settings=settings, composer=Composer(), llm=default.llm, aliases=default.registry,
        tracer=InMemoryTracer(), profiles=profiles, model_profile=default.key,
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
    app.include_router(conversations_router)
    for r in (matters_router, laws_router, verify_router, reforms_router, models_router):
        app.include_router(r)
    register_errors(app)
    register_model_errors(app)
    return app


app = create_app()
