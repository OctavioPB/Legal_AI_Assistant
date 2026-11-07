"""Rate limiting and provider resilience (WS7.T03).

`RateLimiter` is an in-process token bucket per key (tenant, user); the shared-state version for
several API workers belongs in Redis behind the same interface. `ResilientClient` wraps any
`LLMClient`: a provider failure or timeout on an alias walks that alias fallback chain
(`AliasSpec.fallbacks`); a bug in our own code is never swallowed as a "provider failure".
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

from app.orchestration.aliases import AliasName, AliasRegistry
from app.orchestration.client import LLMClient, LLMResult


class ProviderError(RuntimeError):
    """The model provider failed (5xx, connection reset, quota). The only error that falls back."""


class LLMUnavailableError(RuntimeError):
    """Every alias in the chain failed. The caller shows a service error, never a made-up answer."""

    def __init__(self, attempts: list[tuple[str, str]]) -> None:
        super().__init__(f"all aliases failed: {attempts}")
        self.attempts = attempts


class RateLimitExceededError(RuntimeError):
    def __init__(self, key: str, retry_after: float) -> None:
        super().__init__(f"rate limit exceeded for {key!r}; retry in {retry_after:.1f}s")
        self.key = key
        self.retry_after = retry_after


class RateLimiter:
    """Token bucket per key: `burst` tokens, refilled at `rate_per_s`."""

    def __init__(
        self, rate_per_s: float, burst: int, clock: Callable[[], float] = time.monotonic
    ) -> None:
        if rate_per_s <= 0 or burst < 1:
            raise ValueError("rate_per_s must be > 0 and burst >= 1")
        self._rate, self._burst, self._clock = rate_per_s, float(burst), clock
        self._state: dict[str, tuple[float, float]] = {}  # key -> (tokens, last_seen)
        self._lock = threading.Lock()

    def _tokens(self, key: str, now: float) -> float:
        tokens, last = self._state.get(key, (self._burst, now))
        return min(self._burst, tokens + (now - last) * self._rate)

    def retry_after(self, key: str, cost: float = 1.0) -> float:
        """0.0 when `cost` tokens are available now, else seconds until they are."""
        with self._lock:
            tokens = self._tokens(key, self._clock())
        return max(0.0, (cost - tokens) / self._rate)

    def acquire(self, key: str, cost: float = 1.0) -> None:
        with self._lock:
            now = self._clock()
            tokens = self._tokens(key, now)
            if tokens < cost:
                raise RateLimitExceededError(key, (cost - tokens) / self._rate)
            self._state[key] = (tokens - cost, now)


class TenantUserLimits:
    """Both budgets must have room, and neither is charged if the other refuses."""

    def __init__(self, per_tenant: RateLimiter, per_user: RateLimiter) -> None:
        self._tenant, self._user = per_tenant, per_user

    def check(self, tenant_id: str, user_id: str) -> None:
        wait = max(self._tenant.retry_after(tenant_id), self._user.retry_after(user_id))
        if wait > 0:
            key = tenant_id if self._tenant.retry_after(tenant_id) >= wait else user_id
            raise RateLimitExceededError(key, wait)
        self._tenant.acquire(tenant_id)
        self._user.acquire(user_id)


class ResilientClient:
    def __init__(
        self,
        inner: LLMClient,
        registry: AliasRegistry,
        *,
        timeout_s: float,
        max_workers: int = 8,
    ) -> None:
        if timeout_s <= 0:
            raise ValueError("timeout_s must be > 0")
        self._inner, self._registry, self._timeout = inner, registry, timeout_s
        # A timed-out call keeps running in its worker (threads cannot be cancelled); the pool
        # is bounded so a hung provider cannot exhaust the process.
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="llm")

    def _chain(self, alias: AliasName) -> list[AliasName]:
        out: list[AliasName] = []
        todo: list[AliasName] = [alias]
        while todo:
            cur = todo.pop(0)
            if cur not in out:
                out.append(cur)
                todo.extend(self._registry.spec(cur).fallbacks)
        return out

    def complete(
        self, alias: AliasName, *, system: str, user: str, max_tokens: int, tenant_id: str
    ) -> LLMResult:
        failures: list[tuple[str, str]] = []
        for candidate in self._chain(alias):
            future = self._pool.submit(
                self._inner.complete,
                candidate,
                system=system,
                user=user,
                max_tokens=max_tokens,
                tenant_id=tenant_id,
            )
            try:
                result = future.result(timeout=self._timeout)
            except FutureTimeout:
                failures.append((candidate, f"timeout after {self._timeout}s"))
                continue
            except ProviderError as exc:  # ONLY provider failures fall back
                failures.append((candidate, f"provider error: {exc}"))
                continue
            return result.model_copy(
                update={"requested_alias": alias, "attempts": tuple(c for c, _ in failures)}
            )
        raise LLMUnavailableError(failures)
