"""HTTP routes. Each handler validates input, opens ONE tenant transaction and delegates."""

from __future__ import annotations

import json
import logging
import queue
import threading
import uuid
from collections.abc import Iterator
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from app.api.auth import AuthError, Principal, authenticate, issue_token
from app.api.deps import AppState, CodedError, PrincipalDep, StateDep, require_admin
from app.chat.service import ChatRequest, ChatResult, answer_query
from app.corpus.lineage import successions
from app.db.session import tenant_transaction
from app.observability.trace import TraceRecord
from app.prompts.composer import Persona, Workflow
from app.rag.context import UnknownJurisdictionError, load_session_context

router = APIRouter()
log = logging.getLogger(__name__)


def stream_error_code(exc: Exception) -> str:
    """The public error code for a failure inside the SSE worker (mirrors `register_errors`)."""
    from app.orchestration.aliases import UnpinnedAliasError
    from app.orchestration.resilient import LLMUnavailableError, RateLimitExceededError

    if isinstance(exc, RateLimitExceededError):
        return "rate_limited"
    if isinstance(exc, LLMUnavailableError):
        return "llm_unavailable"
    if isinstance(exc, UnpinnedAliasError):
        return "alias_unpinned"
    from app.orchestration.anthropic_client import ModelDeclinedError
    from app.orchestration.profiles import ProfileUnavailableError

    if isinstance(exc, ModelDeclinedError):
        return "model_declined"
    if isinstance(exc, ProfileUnavailableError):
        return "model_unavailable"
    return "internal"


class LoginBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant: str = Field(min_length=2, max_length=63)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class UserOut(BaseModel):
    email: str
    display_name: str
    role: str
    preferred_language: str


class LoginOut(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    user: UserOut


class ChatBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=4000)
    jurisdiction_id: int = Field(gt=0)
    persona: Persona
    workflow: Workflow
    language: Literal["es", "en"] = "es"
    include_historical: bool = False


def _out(p: Principal) -> UserOut:
    return UserOut(email=p.email, display_name=p.display_name, role=p.role,
                   preferred_language=p.preferred_language)  # fmt: skip


@router.get("/health")
def health(st: StateDep) -> dict[str, Any]:
    with st.engine.connect() as c:
        c.execute(text("SELECT 1"))
    return {"status": "ok", "db": True}


@router.post("/auth/login", response_model=LoginOut)
def login(body: LoginBody, st: StateDep) -> LoginOut:
    try:
        p = authenticate(st.engine, body.tenant.strip().lower(), body.email, body.password)
    except AuthError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials") from None
    return LoginOut(
        access_token=issue_token(p, st.settings),
        expires_in=st.settings.jwt_ttl_minutes * 60,
        user=_out(p),
    )


@router.get("/me", response_model=UserOut)
def me(p: PrincipalDep) -> UserOut:
    return _out(p)


@router.get("/catalog/jurisdictions")
def jurisdictions(_: PrincipalDep, st: StateDep) -> list[dict[str, Any]]:
    """The closed catalog the selectors are built from (no free text can name a jurisdiction)."""
    with st.engine.connect() as c:
        rows = c.execute(
            text(
                "SELECT id, country, level, state_code, municipality, name_es, name_en "
                "FROM jurisdictions ORDER BY country, "
                "CASE level WHEN 'federal' THEN 0 WHEN 'state' THEN 1 ELSE 2 END, "
                "state_code NULLS FIRST, municipality NULLS FIRST"
            )
        ).mappings()
        return [dict(r) for r in rows]


@router.get("/catalog/personas")
def personas(_: PrincipalDep, st: StateDep) -> list[dict[str, Any]]:
    with st.engine.connect() as c:
        rows = c.execute(
            text("SELECT code, label_es, label_en FROM personas ORDER BY code")
        ).mappings()
        return [dict(r) for r in rows]


def _run_chat(st: AppState, p: Principal, body: ChatBody, on_stage: Any = None) -> ChatResult:
    st.limits.check(str(p.tenant_id), str(p.user_id))
    req = ChatRequest(query=body.query, persona=body.persona, workflow=body.workflow)
    with tenant_transaction(st.engine, p.tenant_id, user_id=p.user_id) as conn:
        try:
            ctx = load_session_context(
                conn, p.tenant_id, body.jurisdiction_id, language=body.language,
                include_historical=body.include_historical,
            )  # fmt: skip
        except UnknownJurisdictionError:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "unknown jurisdiction"
            ) from None
        return answer_query(conn, ctx, req, st.chat, on_stage)


@router.post("/chat", response_model=ChatResult)
def chat(body: ChatBody, p: PrincipalDep, st: StateDep) -> ChatResult:
    return _run_chat(st, p, body)


@router.post("/chat/stream")
def chat_stream(body: ChatBody, p: PrincipalDep, st: StateDep) -> StreamingResponse:
    """SSE. Stage events as the pipeline advances, then ONE `result` event. Model text is never
    streamed token by token: an answer is only shown after the validator accepted it."""
    st.limits.check(str(p.tenant_id), str(p.user_id))  # fail fast, before the stream starts
    events: queue.Queue[tuple[str, Any] | None] = queue.Queue()

    def work() -> None:
        try:
            req = ChatRequest(query=body.query, persona=body.persona, workflow=body.workflow)
            with tenant_transaction(st.engine, p.tenant_id, user_id=p.user_id) as conn:
                ctx = load_session_context(
                    conn, p.tenant_id, body.jurisdiction_id, language=body.language,
                    include_historical=body.include_historical,
                )  # fmt: skip
                result = answer_query(conn, ctx, req, st.chat, lambda s: events.put(("stage", s)))
            events.put(("result", result.model_dump(mode="json")))
        except UnknownJurisdictionError:
            events.put(("error", {"code": "unknown_jurisdiction"}))
        except Exception as exc:
            # Same public codes as the non-streaming handlers; never an internal class name.
            code = stream_error_code(exc)
            if code == "internal":
                log.exception("chat stream failed")
            events.put(("error", {"code": code}))
        finally:
            events.put(None)

    threading.Thread(target=work, daemon=True).start()

    def gen() -> Iterator[str]:
        while (item := events.get()) is not None:
            name, data = item
            yield f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        gen(), media_type="text/event-stream", headers={"Cache-Control": "no-store"}
    )


_SOURCE_SQL = text(
    "SELECT c.cite_id, c.citation_string, c.breadcrumb, c.text, c.char_start, c.char_end, "
    "c.is_current, d.title AS document_title, d.version_label, d.valid_to, "
    "coalesce(a.full_text, n.full_text) AS article_text, "
    "coalesce(a.label, n.label) AS article_label, "
    "c.tenant_id = public_corpus_id() AS is_public "
    "FROM chunks c JOIN documents d ON d.id = c.document_id "
    "JOIN doc_nodes n ON n.id = c.node_id "
    "LEFT JOIN LATERAL (SELECT x.full_text, x.label FROM doc_nodes x "
    "WHERE x.document_id = n.document_id AND x.path @> n.path "
    "AND x.node_type IN ('articulo', 'transitorio') "
    "ORDER BY nlevel(x.path) DESC LIMIT 1) a ON true "
    "WHERE c.cite_id = :c"
)


@router.get("/sources/{cite_id}")
def source(cite_id: str, p: PrincipalDep, st: StateDep) -> dict[str, Any]:
    """The exact cited text plus its whole article, for the viewer behind a citation chip. RLS
    decides visibility: another tenant's client file is simply not found."""
    if not (0 < len(cite_id) <= 200):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "bad cite_id")
    with tenant_transaction(st.engine, p.tenant_id, user_id=p.user_id) as conn:
        row = conn.execute(_SOURCE_SQL, {"c": cite_id}).mappings().one_or_none()
        succ = successions(conn, [cite_id]).get(cite_id) if row is not None else None
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "source not found")
    out = dict(row)
    # ADR-017: a reform changed this unit after it was cited (None: current or never reformed)
    out["reform"] = (
        succ.model_dump(mode="json") if succ is not None and succ.change != "unchanged" else None
    )
    return out


@router.get("/traces/{request_id}")
def trace(
    request_id: uuid.UUID, p: Annotated[Principal, Depends(require_admin)], st: StateDep
) -> TraceRecord:
    try:
        return st.chat.tracer.get(p.tenant_id, request_id)  # type: ignore[attr-defined,no-any-return]
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "trace not found") from None


def register_errors(app: Any) -> None:
    from fastapi.responses import JSONResponse

    from app.orchestration.aliases import UnpinnedAliasError
    from app.orchestration.resilient import LLMUnavailableError, RateLimitExceededError

    def rate_limited(_: Request, exc: Exception) -> JSONResponse:
        retry = int(getattr(exc, "retry_after", 1)) + 1
        return JSONResponse(
            {"detail": "rate limit exceeded", "code": "rate_limited"}, 429,
            headers={"Retry-After": str(retry)},
        )  # fmt: skip

    def llm_unavailable(_: Request, __: Exception) -> JSONResponse:
        return JSONResponse({"detail": "model service unavailable", "code": "llm_unavailable"}, 503)

    def unpinned(_: Request, __: Exception) -> JSONResponse:
        return JSONResponse({"detail": "model not configured", "code": "alias_unpinned"}, 503)

    def coded(_: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, CodedError)
        return JSONResponse({"detail": exc.code, "code": exc.code}, exc.status_code)

    app.add_exception_handler(CodedError, coded)
    app.add_exception_handler(RateLimitExceededError, rate_limited)
    app.add_exception_handler(LLMUnavailableError, llm_unavailable)
    app.add_exception_handler(UnpinnedAliasError, unpinned)
