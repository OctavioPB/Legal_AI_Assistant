"""Basic chaos: kill the primary provider and the fallback must answer (WS7.T03)."""

from __future__ import annotations

import time

import pytest

from app.orchestration.aliases import AliasName, AliasRegistry
from app.orchestration.client import LLMResult
from app.orchestration.resilient import (
    LLMUnavailableError,
    ProviderError,
    RateLimiter,
    RateLimitExceededError,
    ResilientClient,
    TenantUserLimits,
)


class Provider:
    """Scripted per-alias behaviour: 'ok', 'error', 'hang', or 'bug'."""

    def __init__(self, behaviour: dict[str, str]) -> None:
        self.behaviour, self.calls = behaviour, []  # type: ignore[var-annotated]

    def complete(
        self, alias: AliasName, *, system: str, user: str, max_tokens: int, tenant_id: str
    ) -> LLMResult:
        self.calls.append(alias)
        mode = self.behaviour.get(alias, "ok")
        if mode == "error":
            raise ProviderError("503 from provider")
        if mode == "hang":
            time.sleep(0.5)
        if mode == "bug":
            raise ValueError("our own bug")
        return LLMResult(text=f"answer from {alias}", alias=alias, resolved_model_id=f"id-{alias}")


def client(behaviour: dict[str, str], timeout: float = 0.1) -> tuple[ResilientClient, Provider]:
    p = Provider(behaviour)
    return ResilientClient(p, AliasRegistry(), timeout_s=timeout), p


def ask(c: ResilientClient, alias: AliasName = "drafting-primary") -> LLMResult:
    return c.complete(alias, system="S", user="U", max_tokens=10, tenant_id="t")


def test_healthy_primary_answers_with_no_fallback() -> None:
    c, p = client({})
    out = ask(c)
    assert (
        out.alias == "drafting-primary" and out.attempts == () and p.calls == ["drafting-primary"]
    )


def test_primary_provider_down_falls_back_and_says_so() -> None:
    c, p = client({"drafting-primary": "error"})
    out = ask(c)
    assert out.alias == "fallback-general" and out.requested_alias == "drafting-primary"
    assert out.attempts == ("drafting-primary",) and out.resolved_model_id == "id-fallback-general"
    assert p.calls == ["drafting-primary", "fallback-general"]


def test_a_hung_primary_times_out_and_falls_back() -> None:
    c, _ = client({"drafting-primary": "hang"}, timeout=0.05)
    t0 = time.monotonic()
    out = ask(c)
    assert out.alias == "fallback-general" and time.monotonic() - t0 < 0.4


def test_chain_walks_more_than_one_level() -> None:
    c, p = client({"adversarial-primary": "error", "drafting-primary": "error"})
    assert ask(c, "adversarial-primary").alias == "fallback-general"
    assert p.calls == ["adversarial-primary", "drafting-primary", "fallback-general"]


def test_everything_down_raises_a_service_error_never_an_answer() -> None:
    c, _ = client({a: "error" for a in ("drafting-primary", "fallback-general")})
    with pytest.raises(LLMUnavailableError) as exc:
        ask(c)
    assert [a for a, _ in exc.value.attempts] == ["drafting-primary", "fallback-general"]


def test_our_own_bugs_are_not_masked_as_provider_failures() -> None:
    c, p = client({"drafting-primary": "bug"})
    with pytest.raises(ValueError, match="our own bug"):
        ask(c)
    assert p.calls == ["drafting-primary"]  # no fallback attempted


def test_an_alias_without_fallbacks_fails_alone() -> None:
    c, _ = client({"cheap-utility": "error"})
    with pytest.raises(LLMUnavailableError):
        ask(c, "cheap-utility")


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def test_token_bucket_burst_refill_and_retry_after() -> None:
    clock = Clock()
    rl = RateLimiter(rate_per_s=1.0, burst=3, clock=clock)
    for _ in range(3):
        rl.acquire("tenant-a")
    with pytest.raises(RateLimitExceededError) as exc:
        rl.acquire("tenant-a")
    assert exc.value.retry_after == pytest.approx(1.0)
    clock.t += 0.5
    with pytest.raises(RateLimitExceededError) as exc:
        rl.acquire("tenant-a")
    assert exc.value.retry_after == pytest.approx(0.5)
    clock.t += 0.5
    rl.acquire("tenant-a")  # one token refilled
    clock.t += 100
    for _ in range(3):  # refill is capped at the burst
        rl.acquire("tenant-a")
    with pytest.raises(RateLimitExceededError):
        rl.acquire("tenant-a")


def test_one_tenant_cannot_exhaust_another() -> None:
    clock = Clock()
    rl = RateLimiter(rate_per_s=1.0, burst=1, clock=clock)
    rl.acquire("tenant-a")
    with pytest.raises(RateLimitExceededError):
        rl.acquire("tenant-a")
    rl.acquire("tenant-b")  # untouched


def test_tenant_and_user_budgets_both_apply_and_refusal_charges_nothing() -> None:
    clock = Clock()
    limits = TenantUserLimits(RateLimiter(10, 10, clock), RateLimiter(1, 1, clock))
    limits.check("t1", "u1")
    with pytest.raises(RateLimitExceededError) as exc:
        limits.check("t1", "u1")  # the user bucket is empty
    assert exc.value.key == "u1"
    limits.check("t1", "u2")  # another user of the same tenant is fine
    # two accepted calls consumed 2 of 10 tenant tokens; the refused one consumed nothing
    assert limits._tenant.retry_after("t1", cost=8) == 0.0
    assert limits._tenant.retry_after("t1", cost=9) > 0.0
    with pytest.raises(ValueError):
        RateLimiter(0, 1)


def test_concurrent_requests_never_charge_the_tenant_for_a_refused_call() -> None:
    """Regression (audit 2026-11-17): `check` read both buckets and then charged them in separate
    steps, so two requests racing for a user's last token could both pass the read, and the loser
    paid the tenant's token before the user bucket refused it."""
    import threading

    class SlowRead(RateLimiter):
        """Widens the gap between reading a bucket and charging it, so the race shows every time."""

        def retry_after(self, key: str, cost: float = 1.0) -> float:
            out = super().retry_after(key, cost)
            time.sleep(0.005)
            return out

    clock = Clock()  # frozen: no refill while the threads run
    limits = TenantUserLimits(SlowRead(1, 100, clock), SlowRead(1, 3, clock))
    start = threading.Barrier(48)
    accepted: list[int] = []

    def call() -> None:
        start.wait()
        try:
            limits.check("t1", "u1")
            accepted.append(1)
        except RateLimitExceededError:
            pass

    threads = [threading.Thread(target=call) for _ in range(48)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(accepted) == 3  # the user's burst
    assert limits._tenant.retry_after("t1", cost=97) == 0.0  # exactly 3 tenant tokens spent
    assert limits._tenant.retry_after("t1", cost=98) > 0.0
