"""Local model clients against a simulated Ollama (no network) and, under `-m live`, the real one."""

from __future__ import annotations

import json
from datetime import date

import httpx
import pytest

from app.ingestion.embedders import OllamaEmbedder, cosine
from app.orchestration.aliases import AliasRegistry, AliasSpec, UnpinnedAliasError, default_aliases
from app.orchestration.ollama import OllamaClient, pin_from_runtime
from app.orchestration.resilient import ProviderError, ResilientClient
from app.rag.rerank import DenseReranker
from tests.fakes import FakeEmbedder

TAGS = {
    "models": [
        {"name": "bge-m3:latest", "digest": "790764642607aaaa"},
        {"name": "gemma4:26b", "digest": "2dd70431afedbbbb"},
    ]
}
SHOW = {"model_info": {"gemma4.context_length": 131072, "general.architecture": "gemma4"}}


def fake_ollama(log: list[dict[str, object]], *, chat_status: int = 200, embed_failures: int = 0):  # type: ignore[no-untyped-def]
    state = {"embed_calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        log.append({"path": request.url.path, "body": body})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json=TAGS)
        if request.url.path == "/api/show":
            return httpx.Response(200, json=SHOW)
        if request.url.path == "/api/embed":
            state["embed_calls"] += 1
            if state["embed_calls"] <= embed_failures:
                return httpx.Response(503, text="loading model")
            return httpx.Response(200, json={"embeddings": [[0.5, 0.5] for _ in body["input"]]})
        if request.url.path == "/api/chat":
            if chat_status != 200:
                return httpx.Response(chat_status, text="boom")
            return httpx.Response(
                200,
                json={
                    "message": {"content": "respuesta"},
                    "prompt_eval_count": 11,
                    "eval_count": 7,
                },
            )
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def pinned_registry() -> AliasRegistry:
    specs = default_aliases()
    specs["drafting-primary"] = AliasSpec(
        alias="drafting-primary", model_id="gemma4:26b@2dd70431afed", verified_on=date(2026, 11, 7),
        source_url="http://localhost:11434/api/show", context_window=131072, max_answer_tokens=4096,
        fallbacks=("fallback-general",),
    )  # fmt: skip
    return AliasRegistry(specs)


# ── embedder ──────────────────────────────────────────────────────────────────────────────────
def test_embedder_pins_the_digest_batches_and_never_truncates() -> None:
    log: list[dict[str, object]] = []
    emb = OllamaEmbedder("bge-m3", transport=fake_ollama(log))
    assert emb.model == "bge-m3" and emb.revision == "790764642607"  # content digest, not a tag
    vectors = emb.embed([f"t{i}" for i in range(70)])
    assert len(vectors) == 70
    posts = [e for e in log if e["path"] == "/api/embed"]
    assert [len(p["body"]["input"]) for p in posts] == [32, 32, 6]  # type: ignore[index]
    assert all(p["body"]["truncate"] is False for p in posts)  # type: ignore[index]


def test_embedder_retries_a_loading_model_then_succeeds() -> None:
    sleeps: list[float] = []
    log: list[dict[str, object]] = []
    emb = OllamaEmbedder(transport=fake_ollama(log, embed_failures=2), sleep=sleeps.append)
    assert emb.embed(["x"]) == [[0.5, 0.5]] and sleeps == [2.0, 4.0]


def test_embedder_gives_up_with_a_provider_error() -> None:
    emb = OllamaEmbedder(transport=fake_ollama([], embed_failures=99), sleep=lambda _s: None)
    with pytest.raises(ProviderError, match="3 attempts"):
        emb.embed(["x"])


def test_an_unknown_model_is_a_bug_not_an_outage() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json=TAGS)
        return httpx.Response(404, text="model not found")

    emb = OllamaEmbedder(transport=httpx.MockTransport(handler))
    with pytest.raises(ValueError, match="rejected"):
        emb.embed(["x"])
    with pytest.raises(ValueError, match="not available"):
        OllamaEmbedder("nope", transport=httpx.MockTransport(handler))


# ── chat client ───────────────────────────────────────────────────────────────────────────────
def test_chat_sends_a_deterministic_request_with_its_own_context_window() -> None:
    log: list[dict[str, object]] = []
    client = OllamaClient(pinned_registry(), num_ctx=20_480, seed=7, transport=fake_ollama(log))
    out = client.complete(
        "drafting-primary", system="SYS", user="USER", max_tokens=321, tenant_id="t"
    )
    assert out.text == "respuesta" and out.resolved_model_id == "gemma4:26b@2dd70431afed"
    assert (out.prompt_tokens, out.completion_tokens) == (11, 7)
    body = next(e["body"] for e in log if e["path"] == "/api/chat")
    assert body["model"] == "gemma4:26b"  # the digest is for the trace, not for the call  # type: ignore[index]
    assert body["options"] == {"temperature": 0, "seed": 7, "num_predict": 321, "num_ctx": 20480}  # type: ignore[index]
    assert body["think"] is False and body["stream"] is False  # type: ignore[index]
    assert [m["role"] for m in body["messages"]] == ["system", "user"]  # type: ignore[index]


def test_an_unpinned_alias_cannot_call_anything() -> None:
    client = OllamaClient(AliasRegistry(), transport=fake_ollama([]))
    with pytest.raises(UnpinnedAliasError):
        client.complete("cheap-utility", system="s", user="u", max_tokens=5, tenant_id="t")


def test_runtime_failures_are_provider_errors_but_bad_requests_are_not() -> None:
    down = OllamaClient(pinned_registry(), transport=fake_ollama([], chat_status=503))
    with pytest.raises(ProviderError):
        down.complete("drafting-primary", system="s", user="u", max_tokens=5, tenant_id="t")

    def refuse(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(ProviderError):
        OllamaClient(pinned_registry(), transport=httpx.MockTransport(refuse)).complete(
            "drafting-primary", system="s", user="u", max_tokens=5, tenant_id="t"
        )
    bad = OllamaClient(pinned_registry(), transport=fake_ollama([], chat_status=400))
    with pytest.raises(ValueError, match="rejected"):
        bad.complete("drafting-primary", system="s", user="u", max_tokens=5, tenant_id="t")


def test_a_dead_local_runtime_falls_back_through_the_resilient_client() -> None:
    specs = default_aliases()
    for a, m in (("drafting-primary", "m1"), ("fallback-general", "m2")):
        specs[a] = AliasSpec(  # type: ignore[index]
            alias=a, model_id=f"{m}@abc", verified_on=date(2026, 11, 7), source_url="http://x",  # type: ignore[arg-type]
            context_window=32000, max_answer_tokens=100,
            fallbacks=("fallback-general",) if a == "drafting-primary" else (),
        )  # fmt: skip
    reg = AliasRegistry(specs)

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["model"] == "m1":
            return httpx.Response(503, text="model crashed")
        return httpx.Response(200, json={"message": {"content": f"from {body['model']}"}})

    inner = OllamaClient(reg, transport=httpx.MockTransport(handler))
    out = ResilientClient(inner, reg, timeout_s=5).complete(
        "drafting-primary", system="s", user="u", max_tokens=5, tenant_id="t"
    )
    assert (
        out.text == "from m2"
        and out.requested_alias == "drafting-primary"
        and out.alias == "fallback-general"
    )


# ── pinning from the runtime ─────────────────────────────────────────────────────────────────
def test_pinning_reads_the_digest_and_context_from_the_runtime() -> None:
    spec = pin_from_runtime(
        "drafting-primary", "gemma4:26b", max_answer_tokens=4096, fallbacks=("fallback-general",),
        transport=fake_ollama([]), today=date(2026, 11, 7),
    )  # fmt: skip
    assert spec.model_id == "gemma4:26b@2dd70431afed" and spec.context_window == 131072
    assert spec.verified_on == date(2026, 11, 7) and spec.source_url.endswith("/api/show")  # type: ignore[union-attr]
    AliasRegistry({**default_aliases(), "drafting-primary": spec}).validate_budget(
        "drafting-primary", 12_000
    )


# ── dense reranker ────────────────────────────────────────────────────────────────────────────
def test_dense_reranker_scores_identical_text_highest_and_handles_empty() -> None:
    r = DenseReranker(FakeEmbedder())
    scores = r.score(
        "rescisión del contrato laboral", ["rescisión del contrato laboral", "pastel de chocolate"]
    )
    assert scores[0] > 0.99 and scores[0] > scores[1] and r.score("q", []) == []
    assert cosine([1.0, 0.0], [0.0, 1.0]) == 0.0 and cosine([0.0, 0.0], [1.0, 0.0]) == 0.0


# ── live (real Ollama on this machine): `uv run pytest -m live` ───────────────────────────────
def _ollama_up() -> bool:
    try:
        return httpx.get("http://localhost:11434/api/version", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


@pytest.mark.live
def test_live_bge_m3_is_cross_lingual_and_normalized() -> None:
    if not _ollama_up():
        pytest.fail("Ollama is not running: start it (ollama serve) to run the live suite")
    emb = OllamaEmbedder("bge-m3")
    q, art47, ruido = emb.embed(
        [
            "wrongful termination without cause",
            "Son causas de rescisión de la relación de trabajo, sin responsabilidad para el patrón",
            "Receta de pastel de chocolate con harina",
        ]
    )
    assert len(q) == 1024 and abs(sum(x * x for x in q) - 1.0) < 1e-3
    assert cosine(q, art47) > cosine(q, ruido) + 0.15  # English query finds the Spanish article
    assert len(emb.revision) == 12


@pytest.mark.live
def test_live_chat_with_the_small_model_answers_and_is_deterministic() -> None:
    if not _ollama_up():
        pytest.fail("Ollama is not running: start it (ollama serve) to run the live suite")
    spec = pin_from_runtime("cheap-utility", "qwen3:4b-instruct-2507-q4_K_M", max_answer_tokens=64)
    reg = AliasRegistry({**default_aliases(), "cheap-utility": spec})
    client = OllamaClient(reg, timeout_s=300)
    ask = lambda: client.complete(  # noqa: E731
        "cheap-utility",
        system="Responde solo con una palabra.",
        user="¿Capital de México?",
        max_tokens=16,
        tenant_id="t",
    )
    a, b = ask(), ask()
    assert any(
        w in a.text.lower() for w in ("méxico", "mexico", "cdmx", "ciudad")
    )  # any right spelling
    assert a.text == b.text  # temperature 0 + fixed seed
    assert a.resolved_model_id.startswith("qwen3:4b-instruct-2507-q4_K_M@")
