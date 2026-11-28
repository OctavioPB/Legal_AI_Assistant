"""Exact small-sample statistics for the eval reports (ADR-032). Standard library only.

* `clopper_pearson`: the exact 95 % interval of a binomial rate. With 25 cases a point estimate
  hides how little it says: 5/5 correct refusals is 48-100 %.
* `mcnemar_exact`: the exact two-sided test for paired before/after outcomes on the same cases,
  computed from the flipped cases the runner already stores. It replaces the rule of thumb
  "1-2 cases is noise".
* `zero_failure_n`: how many cases with zero failures bound a rate below a target (one-sided).

Everything uses exact binomial sums (`math.comb`) and bisection, deterministic to 1e-12.
"""

from __future__ import annotations

import math
from collections.abc import Callable

ALPHA = 0.05  # 95 % intervals and a 5 % test level throughout the reports


def _binom_cdf(k: int, n: int, p: float) -> float:
    """P(X <= k) for X ~ Binomial(n, p)."""
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    return sum(math.comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(k + 1))


def _bisect(f: Callable[[float], float], target: float) -> float:
    """The p in [0, 1] where the decreasing function f(p) crosses `target`."""
    lo, hi = 0.0, 1.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if f(mid) > target:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-12:
            break
    return (lo + hi) / 2


def clopper_pearson(k: int, n: int, alpha: float = ALPHA) -> tuple[float, float]:
    """Exact (lower, upper) bounds of the rate k/n at confidence 1 - alpha."""
    if n <= 0 or not 0 <= k <= n:
        raise ValueError(f"need 0 <= k <= n and n > 0, got k={k}, n={n}")
    # lower: P(X >= k | p) = alpha/2, i.e. cdf(k-1) = 1 - alpha/2 (decreasing in p)
    lower = 0.0 if k == 0 else _bisect(lambda p: _binom_cdf(k - 1, n, p), 1 - alpha / 2)
    # upper: P(X <= k | p) = alpha/2 (decreasing in p)
    upper = 1.0 if k == n else _bisect(lambda p: _binom_cdf(k, n, p), alpha / 2)
    return round(lower, 4), round(upper, 4)


def mcnemar_exact(worse: int, better: int) -> float:
    """Two-sided exact McNemar p-value from the discordant pairs (cases that flipped)."""
    if worse < 0 or better < 0:
        raise ValueError("counts must be non-negative")
    n = worse + better
    if n == 0:
        return 1.0
    return round(min(1.0, 2 * _binom_cdf(min(worse, better), n, 0.5)), 4)


def zero_failure_n(rate: float, alpha: float = ALPHA) -> int:
    """Cases needed, with zero failures, for a one-sided upper bound at `rate`."""
    if not 0 < rate < 1:
        raise ValueError("rate must be in (0, 1)")
    return math.ceil(math.log(alpha) / math.log(1 - rate))


def interval(k: int, n: int) -> dict[str, float | int] | None:
    """{k, n, rate, lower, upper}, or None when there is nothing to measure."""
    if n <= 0:
        return None
    lo, hi = clopper_pearson(k, n)
    return {"k": k, "n": n, "rate": round(k / n, 4), "lower": lo, "upper": hi}
