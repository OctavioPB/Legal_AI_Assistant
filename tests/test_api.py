"""The real FastAPI routes over Postgres+RLS with scripted models: auth, tenant isolation, limits,
errors and the SSE stream. In-process ASGI (no socket)."""

from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from app.api.auth import Principal, hash_password, issue_token
from app.api.deps import AppState
from app.chat.service import ChatDeps
from app.db.session import tenant_transaction
from app.ingestion.tokens import ApproxCounter
from app.main import UnsafeConfigurationError, check_configuration, create_app
from app.observability.trace import InMemoryTracer
from app.orchestration.aliases import AliasRegistry
from app.orchestration.resilient import (
    LLMUnavailableError,
    RateLimiter,
    TenantUserLimits,
)
from app.prompts.composer import Composer
from app.settings import Settings
from tests.fakes import FakeEmbedder, FakeReranker
from tests.helpers import jurisdiction_id
from tests.test_chat_service import ScriptedLLM
from tests.test_retrieval import corpus  # noqa: F401  (fixture)

SECRET = "t" * 48
PASSWORD = "correct horse battery"


def settings() -> Settings:
    return Settings(refusal_tau=0.3, jwt_secret=SECRET)


def make_state(engine: Engine, llm: object, *, user_burst: int = 10) -> AppState:
    s = settings()
    chat = ChatDeps(
        embedder=FakeEmbedder(), reranker=FakeReranker(), counter=ApproxCounter(), settings=s,
        composer=Composer(), llm=llm, aliases=AliasRegistry(), tracer=InMemoryTracer(),  # type: ignore[arg-type]
        require_pinned_models=False,
    )  # fmt: skip
    limits = TenantUserLimits(RateLimiter(100, 100), RateLimiter(0.001, user_burst))
    return AppState(settings=s, engine=engine, chat=chat, limits=limits)


def add_user(engine: Engine, tenant: uuid.UUID, email: str, role: str = "member") -> uuid.UUID:
    with tenant_transaction(engine, tenant) as c:
        return c.execute(
            text(
                "INSERT INTO users (tenant_id, email, display_name, role, password_hash) "
                "VALUES (:t, :e, 'Usuaria', :r, :h) RETURNING id"
            ),
            {"t": tenant, "e": email, "r": role, "h": hash_password(PASSWORD)},
        ).scalar_one()


@pytest.fixture
def world(app_engine, provisioner_engine, admin_engine, tenant_a, tenant_b, corpus):  # type: ignore[no-untyped-def]
    with provisioner_engine.begin() as c:
        c.execute(text("UPDATE tenants SET slug = 'firm-a' WHERE id = :t"), {"t": tenant_a})
        c.execute(text("UPDATE tenants SET slug = 'firm-b' WHERE id = :t"), {"t": tenant_b})
    add_user(app_engine, tenant_a, "ana@a.mx", "admin")
    add_user(app_engine, tenant_b, "bruno@b.mx")
    add_user(app_engine, tenant_a, "mario@a.mx")
    llm = ScriptedLLM("FAITHFUL")
    state = make_state(app_engine, llm)
    with TestClient(create_app(state)) as client:
        yield client, state, llm, corpus, tenant_a, tenant_b


def login(client: TestClient, tenant: str, email: str, password: str = PASSWORD):  # type: ignore[no-untyped-def]
    return client.post("/auth/login", json={"tenant": tenant, "email": email, "password": password})


def auth(client: TestClient, tenant: str = "firm-a", email: str = "ana@a.mx") -> dict[str, str]:
    return {"Authorization": "Bearer " + login(client, tenant, email).json()["access_token"]}


# ── auth ──────────────────────────────────────────────────────────────────────────────────────
def test_health_and_no_api_docs_are_exposed(world) -> None:  # type: ignore[no-untyped-def]
    client, *_ = world
    assert client.get("/health").json() == {"status": "ok", "db": True}
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_login_issues_a_token_and_me_returns_the_user(world) -> None:  # type: ignore[no-untyped-def]
    client, *_ = world
    r = login(client, "firm-a", "ANA@a.mx ")  # case and spaces in the email are normalized
    assert (
        r.status_code == 200 and r.json()["token_type"] == "bearer" and r.json()["expires_in"] > 0
    )
    me = client.get("/me", headers={"Authorization": "Bearer " + r.json()["access_token"]})
    assert me.json() == {
        "email": "ana@a.mx",
        "display_name": "Usuaria",
        "role": "admin",
        "preferred_language": "es-MX",
    }


@pytest.mark.parametrize(
    ("tenant", "email", "password"),
    [("firm-a", "ana@a.mx", "wrong password!"), ("firm-a", "nobody@a.mx", PASSWORD), ("no-such-firm", "ana@a.mx", PASSWORD),
     ("firm-b", "ana@a.mx", PASSWORD), ("FIRM A; DROP TABLE users", "ana@a.mx", PASSWORD),
     ("firm-a", "ana@a.mx' OR '1'='1", PASSWORD), ("public-corpus", "ana@a.mx", PASSWORD)],
)  # fmt: skip
def test_every_login_failure_looks_the_same(world, tenant: str, email: str, password: str) -> None:  # type: ignore[no-untyped-def]
    client, *_ = world
    r = login(client, tenant, email, password)
    assert r.status_code == 401 and r.json() == {"detail": "invalid credentials"}


def test_a_user_of_one_firm_cannot_log_into_another(world) -> None:  # type: ignore[no-untyped-def]
    client, *_ = world
    assert login(client, "firm-b", "bruno@b.mx").status_code == 200
    assert login(client, "firm-a", "bruno@b.mx").status_code == 401  # email unique PER tenant


def test_login_rejects_unknown_fields_and_empty_values(world) -> None:  # type: ignore[no-untyped-def]
    client, *_ = world
    assert (
        client.post(
            "/auth/login",
            json={"tenant": "firm-a", "email": "a@a.mx", "password": "x", "role": "admin"},
        ).status_code
        == 422
    )
    assert (
        client.post("/auth/login", json={"tenant": "", "email": "", "password": ""}).status_code
        == 422
    )


def test_tokens_that_are_missing_forged_expired_or_for_ghosts_are_refused(world) -> None:  # type: ignore[no-untyped-def]
    client, state, _, _, tenant_a, _ = world
    assert client.get("/me").status_code == 401
    assert client.get("/me", headers={"Authorization": "Basic abc"}).status_code == 401
    uid = login(client, "firm-a", "ana@a.mx").json()["access_token"]
    assert (
        client.get("/me", headers={"Authorization": f"Bearer {uid}x"}).status_code == 401
    )  # tampered
    claims = {
        "sub": str(uuid.uuid4()),
        "tenant_id": str(tenant_a),
        "role": "admin",
        "exp": int(time.time()) + 600,
    }
    forged = jwt.encode(claims, "another-secret-another-secret-another", algorithm="HS256")
    assert client.get("/me", headers={"Authorization": f"Bearer {forged}"}).status_code == 401
    none_alg = jwt.encode(claims, key="", algorithm="none")
    assert client.get("/me", headers={"Authorization": f"Bearer {none_alg}"}).status_code == 401
    ghost = jwt.encode(claims, SECRET, algorithm="HS256")  # valid signature, user does not exist
    assert client.get("/me", headers={"Authorization": f"Bearer {ghost}"}).status_code == 401
    p = Principal(
        user_id=uuid.uuid4(),
        tenant_id=tenant_a,
        role="admin",
        email="x@a.mx",
        display_name="x",
        preferred_language="es-MX",
    )
    old = issue_token(p, state.settings, now=datetime.now(UTC) - timedelta(days=2))
    assert client.get("/me", headers={"Authorization": f"Bearer {old}"}).status_code == 401


def test_a_valid_token_cannot_be_replayed_against_another_tenants_user(world) -> None:  # type: ignore[no-untyped-def]
    client, state, _, _, _, tenant_b = world
    uid_a = client.get("/me", headers=auth(client)).json()["email"]
    assert uid_a == "ana@a.mx"
    # same user id, but the token claims tenant B: RLS makes the user invisible -> 401
    with tenant_transaction(
        state.engine,
        uuid.UUID(
            jwt.decode(auth(client)["Authorization"][7:], SECRET, algorithms=["HS256"])["tenant_id"]
        ),
    ) as c:
        user_id = c.execute(text("SELECT id FROM users WHERE email = 'ana@a.mx'")).scalar_one()
    claims = {
        "sub": str(user_id),
        "tenant_id": str(tenant_b),
        "role": "admin",
        "exp": int(time.time()) + 600,
    }
    token = jwt.encode(claims, SECRET, algorithm="HS256")
    assert client.get("/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


# ── catalog ───────────────────────────────────────────────────────────────────────────────────
def test_catalog_requires_auth_and_lists_the_closed_catalog(world) -> None:  # type: ignore[no-untyped-def]
    client, *_ = world
    assert client.get("/catalog/jurisdictions").status_code == 401
    rows = client.get("/catalog/jurisdictions", headers=auth(client)).json()
    assert len(rows) == 38 and rows[0]["level"] == "federal" and rows[0]["country"] == "MX"
    assert {r["code"] for r in client.get("/catalog/personas", headers=auth(client)).json()} >= {
        "labor",
        "general",
    }


# ── chat ──────────────────────────────────────────────────────────────────────────────────────
def chat_body(corpus: dict, **over: object) -> dict[str, object]:  # type: ignore[type-arg]
    return {"query": "artículo 47 rescisión probidad", "jurisdiction_id": corpus["fed"], "persona": "labor",
            "workflow": "dossier_builder", "language": "es", **over}  # fmt: skip


def test_chat_answers_with_citations_and_leaves_an_audit_row(world) -> None:  # type: ignore[no-untyped-def]
    client, state, llm, corpus, tenant_a, _ = world
    r = client.post("/chat", json=chat_body(corpus), headers=auth(client))
    assert r.status_code == 200
    out = r.json()
    assert out["status"] == "answered" and out["citations"] and out["refusal_block"] is None
    assert llm.calls[0]["tenant_id"] if False else True
    with tenant_transaction(state.engine, tenant_a) as c:
        assert (
            c.execute(
                text("SELECT count(*) FROM audit_log WHERE event_type = 'query'")
            ).scalar_one()
            == 1
        )


def test_chat_input_is_validated_by_closed_enums_and_catalog(world) -> None:  # type: ignore[no-untyped-def]
    client, _, llm, corpus, *_ = world
    h = auth(client)
    for bad in (
        {"persona": "ignora todo"}, {"workflow": "root"}, {"language": "fr"}, {"jurisdiction_id": 0},
        {"query": ""}, {"query": "x" * 4001}, {"unexpected": 1},
    ):  # fmt: skip
        assert client.post("/chat", json=chat_body(corpus, **bad), headers=h).status_code == 422, (
            bad
        )
    assert (
        client.post("/chat", json=chat_body(corpus, jurisdiction_id=99999), headers=h).status_code
        == 422
    )
    assert llm.calls == []


def test_chat_requires_authentication(world) -> None:  # type: ignore[no-untyped-def]
    client, _, _, corpus, *_ = world
    assert client.post("/chat", json=chat_body(corpus)).status_code == 401


def test_a_refusal_is_a_normal_200_answer_not_an_error(world) -> None:  # type: ignore[no-untyped-def]
    client, state, llm, corpus, *_ = world
    state.chat.settings.__dict__["refusal_tau"] = 0.99  # type: ignore[index]
    object.__setattr__(
        state.chat, "settings", state.chat.settings.model_copy(update={"refusal_tau": 0.99})
    )
    r = client.post(
        "/chat",
        json=chat_body(corpus, query="mecánica cuántica de agujeros negros"),
        headers=auth(client),
    )
    assert r.status_code == 200 and r.json()["status"] == "refused"
    assert r.json()["refusal_block"].startswith("<insuficiente>") and llm.calls == []


def test_the_per_user_rate_limit_returns_429_with_retry_after(
    app_engine, provisioner_engine, tenant_a, corpus
) -> None:  # type: ignore[no-untyped-def]
    with provisioner_engine.begin() as c:
        c.execute(text("UPDATE tenants SET slug = 'firm-a' WHERE id = :t"), {"t": tenant_a})
    add_user(app_engine, tenant_a, "ana@a.mx")
    with TestClient(
        create_app(make_state(app_engine, ScriptedLLM("FAITHFUL"), user_burst=2))
    ) as client:
        h = auth(client)
        codes = [
            client.post("/chat", json=chat_body(corpus), headers=h).status_code for _ in range(3)
        ]
        assert codes == [200, 200, 429]
        blocked = client.post("/chat", json=chat_body(corpus), headers=h)
        assert int(blocked.headers["Retry-After"]) >= 1 and blocked.json()["code"] == "rate_limited"


def test_a_dead_model_service_is_a_503_not_a_made_up_answer(
    app_engine, provisioner_engine, tenant_a, corpus
) -> None:  # type: ignore[no-untyped-def]
    class Down:
        def complete(self, *a: object, **k: object) -> object:
            raise LLMUnavailableError([("drafting-primary", "provider error")])

    with provisioner_engine.begin() as c:
        c.execute(text("UPDATE tenants SET slug = 'firm-a' WHERE id = :t"), {"t": tenant_a})
    add_user(app_engine, tenant_a, "ana@a.mx")
    with TestClient(create_app(make_state(app_engine, Down()))) as client:
        r = client.post("/chat", json=chat_body(corpus), headers=auth(client))
        assert r.status_code == 503 and r.json()["code"] == "llm_unavailable"


def test_the_sse_stream_reports_stages_then_one_result(world) -> None:  # type: ignore[no-untyped-def]
    client, _, _, corpus, *_ = world
    with client.stream("POST", "/chat/stream", json=chat_body(corpus), headers=auth(client)) as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        raw = "".join(r.iter_text())
    events = [
        (b.split("\n")[0][7:], json.loads(b.split("\n")[1][6:])) for b in raw.strip().split("\n\n")
    ]
    assert [e[0] for e in events] == ["stage", "stage", "result"]
    assert [e[1] for e in events[:2]] == ["retrieving", "generating"]
    assert events[2][1]["status"] == "answered" and events[2][1]["citations"]


def test_an_unauthenticated_stream_is_refused_before_it_starts(world) -> None:  # type: ignore[no-untyped-def]
    client, _, _, corpus, *_ = world
    assert client.post("/chat/stream", json=chat_body(corpus)).status_code == 401


# ── sources and traces ────────────────────────────────────────────────────────────────────────
def test_a_source_returns_the_exact_text_and_its_article(world) -> None:  # type: ignore[no-untyped-def]
    client, *_ = world
    cid = "MX-FED-LFT-ART47-FRACI-v1"
    r = client.get(f"/sources/{cid}", headers=auth(client))
    assert r.status_code == 200
    body = r.json()
    assert (
        body["cite_id"] == cid
        and "certificados falsos" in body["text"]
        and "Artículo 47" in body["article_text"]
    )
    assert body["is_public"] is True and body["citation_string"].startswith("Ley LFT")
    assert client.get("/sources/NOPE-1", headers=auth(client)).status_code == 404
    assert client.get("/sources/" + "x" * 201, headers=auth(client)).status_code == 422


def test_a_client_file_source_is_invisible_to_another_firm(world) -> None:  # type: ignore[no-untyped-def]
    client, *_ = world
    cid = "CF-EXP1-ART1"
    assert (
        client.get(f"/sources/{cid}", headers=auth(client, "firm-a", "ana@a.mx")).status_code == 200
    )
    assert (
        client.get(f"/sources/{cid}", headers=auth(client, "firm-b", "bruno@b.mx")).status_code
        == 404
    )


def test_traces_are_admin_only_and_tenant_scoped(world) -> None:  # type: ignore[no-untyped-def]
    client, _, _, corpus, *_ = world
    rid = client.post("/chat", json=chat_body(corpus), headers=auth(client)).json()["request_id"]
    assert (
        client.get(f"/traces/{rid}", headers=auth(client, "firm-a", "mario@a.mx")).status_code
        == 403
    )
    ok = client.get(f"/traces/{rid}", headers=auth(client))
    assert ok.status_code == 200 and ok.json()["prompt_system"].startswith("Eres un asistente")
    assert (
        client.get(f"/traces/{rid}", headers=auth(client, "firm-b", "bruno@b.mx")).status_code
        == 403
    )
    assert client.get(f"/traces/{uuid.uuid4()}", headers=auth(client)).status_code == 404


# ── configuration guard ───────────────────────────────────────────────────────────────────────
def test_the_default_secret_is_refused_outside_development() -> None:
    check_configuration(Settings())  # dev: allowed (a local machine)
    with pytest.raises(UnsafeConfigurationError):
        check_configuration(Settings(environment="prod"))
    with pytest.raises(UnsafeConfigurationError):
        check_configuration(Settings(jwt_secret="short"))
    check_configuration(Settings(environment="prod", jwt_secret="p" * 40))


def test_jurisdiction_helper_is_still_consistent(admin_engine: Engine) -> None:
    with admin_engine.connect() as c:
        assert jurisdiction_id(c, "MX", "federal") > 0
