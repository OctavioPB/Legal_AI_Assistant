"""ADR-024: model profiles per conversation. Unit tests for the catalog and the Claude client (fake
SDK, no network), and the real routes over Postgres+RLS for the firm opt-in and the per-conversation
choice."""

from __future__ import annotations

import dataclasses
from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import build_profiles, create_app
from app.orchestration import anthropic_client
from app.orchestration.aliases import ALL_ALIASES, AliasRegistry
from app.orchestration.anthropic_client import (
    AnthropicClient,
    MissingSDKError,
    ModelDeclinedError,
)
from app.orchestration.pins import load_registry
from app.orchestration.profiles import (
    ANTHROPIC_PINS,
    ModelProfile,
    ProfileCatalog,
    ProfileUnavailableError,
    UnknownProfileError,
    load_measurements,
    load_pins,
    qwen_registry,
)
from app.orchestration.resilient import LLMUnavailableError, ProviderError, ResilientClient
from app.settings import Settings
from tests.test_api import auth, make_state, world  # noqa: F401  (fixture)
from tests.test_chat_service import ScriptedLLM
from tests.test_conversations import create, send
from tests.test_retrieval import corpus  # noqa: F401  (fixture)


# ── catalog ──────────────────────────────────────────────────────────────────────────────────────
def test_qwen_profile_serves_every_alias_with_the_small_model_and_no_fallbacks() -> None:
    local = load_registry()
    qwen = qwen_registry(local)
    small = local.spec("fallback-general").model_id
    for a in ALL_ALIASES:
        s = qwen.spec(a)
        assert s.model_id == small and s.fallbacks == ()
        assert s.max_answer_tokens == local.spec(a).max_answer_tokens


def test_claude_pins_come_from_the_documentation_with_a_date_and_no_fallbacks() -> None:
    reg = load_pins(ANTHROPIC_PINS)
    for a in ALL_ALIASES:
        s = reg.require_pinned(a)
        assert s.model_id == "claude-opus-5-5"
        assert s.verified_on == date(2026, 11, 9)
        assert s.source_url and s.source_url.startswith("https://platform.claude.com/docs/")
        assert s.fallbacks == ()  # never answered by another model mid-conversation
    reg.validate_budget("drafting-primary", Settings().retrieval_context_budget_tokens)


def test_the_default_profile_must_be_local_and_unknown_keys_are_refused() -> None:
    reg = AliasRegistry()
    llm = ScriptedLLM("FAITHFUL")
    with pytest.raises(ValueError, match="local"):
        ProfileCatalog({"claude": ModelProfile("claude", reg, llm)}, default="claude")  # type: ignore[arg-type]
    cat = ProfileCatalog(
        {
            "gemma": ModelProfile("gemma", reg, llm),
            "claude": ModelProfile("claude", reg, None, "no_api_key"),
        }  # type: ignore[arg-type]
    )
    with pytest.raises(UnknownProfileError):
        cat.get("gpt")
    with pytest.raises(ProfileUnavailableError, match="no_api_key"):
        cat.serving("claude")


def test_measurements_label_only_what_a_run_measured() -> None:
    m = load_measurements()
    assert m["gemma"] and str(m["gemma"]["run"]).startswith("golden-")
    assert m.get("claude") is None  # never labeled "measured" without a run


def test_without_a_key_claude_is_listed_but_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    cat = build_profiles(Settings(anthropic_api_key=None), load_registry())
    assert cat.profiles["claude"].llm is None
    assert cat.profiles["claude"].unavailable == "no_api_key"
    assert cat.profiles["gemma"].llm is not None and cat.profiles["qwen"].llm is not None

    def no_sdk(*_a: object) -> object:
        raise MissingSDKError("x")

    monkeypatch.setattr(anthropic_client, "_sdk_client", no_sdk)
    cat = build_profiles(Settings(anthropic_api_key="sk-test"), load_registry())  # type: ignore[arg-type]
    assert cat.profiles["claude"].unavailable == "sdk_missing"


def test_for_profile_never_substitutes_another_profile() -> None:
    a, b = ScriptedLLM("FAITHFUL"), ScriptedLLM("FAITHFUL")
    reg = AliasRegistry()
    cat = ProfileCatalog(
        {
            "gemma": ModelProfile("gemma", reg, a),
            "qwen": ModelProfile("qwen", reg, b),  # type: ignore[arg-type]
            "claude": ModelProfile("claude", reg, None, "no_api_key"),
        }  # fmt: skip
    )
    deps = make_state(None, a).chat  # type: ignore[arg-type]
    deps = dataclasses.replace(deps, profiles=cat)
    assert deps.for_profile("qwen").llm is b and deps.for_profile("qwen").model_profile == "qwen"
    assert deps.for_profile("gemma") is deps
    with pytest.raises(ProfileUnavailableError):
        deps.for_profile("claude")


# ── Claude client (fake SDK: no network) ─────────────────────────────────────────────────────────
class FakeMessages:
    def __init__(self, reply: Any = None, exc: Exception | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.reply, self.exc = reply, exc

    def create(self, **kw: Any) -> Any:
        self.calls.append(kw)
        if self.exc:
            raise self.exc
        return self.reply


def reply(text: str = "Texto [cite: X]", stop: str = "end_turn") -> Any:
    return SimpleNamespace(
        content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
        stop_reason=stop, model="claude-opus-5-5",
        usage=SimpleNamespace(input_tokens=100, output_tokens=20),
    )  # fmt: skip


def client_with(messages: FakeMessages, **kw: Any) -> AnthropicClient:
    return AnthropicClient(
        load_pins(ANTHROPIC_PINS), api_key="", sdk=SimpleNamespace(messages=messages), **kw
    )


def test_claude_request_shape_and_result() -> None:
    m = FakeMessages(reply())
    res = client_with(m, effort="high", thinking_headroom_tokens=8000).complete(
        "drafting-primary", system="SYS", user="pregunta", max_tokens=4096, tenant_id="TENANT-XYZ"
    )
    call = m.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["max_tokens"] == 4096 + 8000  # thinking counts toward max_tokens
    assert call["system"] == "SYS" and call["messages"] == [{"role": "user", "content": "pregunta"}]
    assert call["output_config"] == {"effort": "high"}
    assert not {"temperature", "top_p", "top_k", "thinking", "metadata"} & call.keys()
    assert "TENANT-XYZ" not in str(call)  # the tenant id is never sent to the provider
    assert res.text == "Texto [cite: X]" and res.resolved_model_id == "claude-opus-5-5"
    assert (res.prompt_tokens, res.completion_tokens) == (100, 20)


def test_a_provider_decline_is_not_retried_on_another_model() -> None:
    m = FakeMessages(reply(stop="refusal"))
    c = client_with(m)
    with pytest.raises(ModelDeclinedError):
        ResilientClient(c, load_pins(ANTHROPIC_PINS), timeout_s=5).complete(
            "drafting-primary", system="s", user="u", max_tokens=10, tenant_id="t"
        )
    assert len(m.calls) == 1


class StatusError(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(status)
        self.status_code = status


class APIConnectionError(Exception):
    pass


@pytest.mark.parametrize(
    ("exc", "expected"),
    [(StatusError(529), ProviderError), (StatusError(429), ProviderError),
     (StatusError(500), ProviderError), (APIConnectionError(), ProviderError),
     (StatusError(401), ValueError), (StatusError(400), ValueError)],
)  # fmt: skip
def test_errors_map_to_the_app_contract(exc: Exception, expected: type) -> None:
    with pytest.raises(expected):
        client_with(FakeMessages(exc=exc)).complete(
            "drafting-primary", system="s", user="u", max_tokens=10, tenant_id="t"
        )


def test_an_outage_ends_as_unavailable_not_as_another_model() -> None:
    reg = load_pins(ANTHROPIC_PINS)
    m = FakeMessages(exc=StatusError(529))
    with pytest.raises(LLMUnavailableError):
        ResilientClient(client_with(m), reg, timeout_s=5).complete(
            "drafting-primary", system="s", user="u", max_tokens=10, tenant_id="t"
        )
    assert len(m.calls) == 1


# ── routes: per-conversation choice and the firm's opt-in ───────────────────────────────────────
@pytest.fixture
def profiled(world):  # type: ignore[no-untyped-def]
    client, state, llm, corpus, ta, tb = world
    reg = AliasRegistry()
    qwen, claude = ScriptedLLM("FAITHFUL"), ScriptedLLM("FAITHFUL")
    cat = ProfileCatalog(
        {
            "gemma": ModelProfile("gemma", reg, llm),
            "qwen": ModelProfile("qwen", reg, qwen),  # type: ignore[arg-type]
            "claude": ModelProfile("claude", reg, claude),
        },  # type: ignore[arg-type]  # fmt: skip
        measurements={"gemma": {"run": "golden-x", "recall_at_8": 1.0}},
    )
    state.chat = dataclasses.replace(state.chat, profiles=cat)
    with TestClient(create_app(state)) as c:
        yield c, llm, qwen, claude, corpus


def test_models_lists_the_catalog_with_measurement_and_policy(profiled) -> None:  # type: ignore[no-untyped-def]
    client, *_ = profiled
    out = client.get("/models", headers=auth(client)).json()
    by = {o["key"]: o for o in out["options"]}
    assert list(by) == ["gemma", "qwen", "claude"]
    assert by["gemma"]["default"] and by["gemma"]["measurement"]["run"] == "golden-x"
    assert by["qwen"]["measurement"] is None
    assert by["claude"]["locality"] == "remote" and not by["claude"]["allowed"]
    assert out["allow_remote"] is False and out["can_manage"] is True


def test_a_conversation_answers_with_its_own_profile(profiled) -> None:  # type: ignore[no-untyped-def]
    client, gemma, qwen, _, corpus = profiled
    h = auth(client)
    conv = create(client, h, corpus, model_profile="qwen")
    assert conv["model_profile"] == "qwen"
    n = len(gemma.calls)
    send(client, h, conv["id"], "artículo 47 rescisión probidad")
    assert qwen.calls and len(gemma.calls) == n
    assert create(client, h, corpus)["model_profile"] == "gemma"  # default stays local


def test_remote_profile_needs_the_admin_opt_in_and_stops_when_withdrawn(profiled) -> None:  # type: ignore[no-untyped-def]
    client, _, _, claude, corpus = profiled
    admin, member = auth(client), auth(client, email="mario@a.mx")
    body = {"kind": "chat", "title": "x", "jurisdiction_id": corpus["fed"], "persona": "labor",
            "model_profile": "claude"}  # fmt: skip
    denied = client.post("/conversations", json=body, headers=admin)
    assert denied.status_code == 403 and denied.json()["code"] == "model_not_allowed"
    assert (
        client.post("/models/policy", json={"allow_remote": True}, headers=member).status_code
        == 403
    )
    assert client.post("/models/policy", json={"allow_remote": True}, headers=admin).json()[
        "allow_remote"
    ]
    conv = create(client, member, corpus, model_profile="claude")
    send(client, member, conv["id"], "artículo 47 rescisión probidad")
    assert claude.calls
    client.post("/models/policy", json={"allow_remote": False}, headers=admin)
    r = client.post(f"/conversations/{conv['id']}/messages/stream",
                    json={"text": "otra", "action": "ask"}, headers=member)  # fmt: skip
    assert r.status_code == 403  # withdrawn: the remote conversation cannot continue
    assert len(claude.calls) == 1


def test_the_opt_in_of_one_firm_does_not_reach_another(profiled) -> None:  # type: ignore[no-untyped-def]
    client, *_ = profiled
    client.post("/models/policy", json={"allow_remote": True}, headers=auth(client))
    other = client.get("/models", headers=auth(client, "firm-b", "bruno@b.mx")).json()
    assert other["allow_remote"] is False and other["can_manage"] is False


def test_unknown_or_unavailable_profiles_are_refused(profiled) -> None:  # type: ignore[no-untyped-def]
    client, *_rest, corpus = profiled
    h = auth(client)
    base = {"kind": "chat", "title": "x", "jurisdiction_id": corpus["fed"], "persona": "labor"}
    assert (
        client.post("/conversations", json={**base, "model_profile": "gpt"}, headers=h).status_code
        == 422
    )
