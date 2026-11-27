"""ADR tripwires as ops alerts (WS7.T05, architecture.md: every ADR that names a threshold
registers it here). A test compares the thresholds the ADR log declares with this list, so a
tripwire written into an ADR and forgotten (Library-RAG passed its own "revisit at 200k chunks"
at about 697k without noticing) fails the build.

Metrics that span tenants are counts and rates read with a privileged ops engine; they carry no
tenant content.
"""

from __future__ import annotations

import operator
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Connection, text

_OPS: dict[str, Callable[[float, float], bool]] = {">": operator.gt, ">=": operator.ge}


@dataclass(frozen=True)
class Tripwire:
    id: str
    adr: str  # "ADR-001"; "ops" for operational alerts that no ADR names
    metric: str
    op: Literal[">", ">="]
    threshold: float
    description: str
    calibrated: bool = True  # False: a placeholder to be tuned with real traffic

    def breached(self, value: float) -> bool:
        return _OPS[self.op](value, self.threshold)


class Alert(BaseModel):
    model_config = ConfigDict(frozen=True)

    tripwire: str
    adr: str
    metric: str
    value: float
    threshold: float
    description: str
    calibrated: bool


TRIPWIRES: tuple[Tripwire, ...] = (
    Tripwire(
        id="chunks-total",
        adr="ADR-001",
        metric="chunks_total",
        op=">",
        threshold=50_000_000,
        description="Single Postgres perimeter: revisit it (a second store is a second boundary).",
    ),
    Tripwire(
        id="chunks-total-plan",
        adr="ADR-011",
        metric="chunks_total",
        op=">",
        threshold=50_000_000,
        description="Re-measure the retrieval plan shapes under RLS at corpus scale.",
    ),
    Tripwire(
        id="chunks-exceed-window",
        adr="ADR-012",
        metric="chunks_exceeding_window",
        op=">",
        threshold=0,
        description="A chunk over the embedder window is silently truncated by the vector arm.",
    ),
    Tripwire(
        id="mixed-generations",
        adr="ADR-006",
        metric="live_generations",
        op=">",
        threshold=1,
        description="More than one embedding/chunker generation is live: invariant 5 is broken.",
    ),
    Tripwire(
        id="regeneration-rate",
        adr="ops",
        metric="regeneration_rate_max_alias",
        op=">",
        threshold=0.20,
        description="An alias regenerates on over 20% of queries: it should lose drafting.",
        calibrated=False,
    ),
    Tripwire(
        id="refusal-rate",
        adr="ops",
        metric="refusal_rate_max_alias",
        op=">",
        threshold=0.30,
        description="Over 30% refusals: tau too high, sources missing, or the alias fabricates.",
        calibrated=False,
    ),
)

_REVISIT = re.compile(r"(?i)revisit at\s*>?\s*(?P<n>[\d.,]+)\s*(?P<unit>[KMB])?\s*(?P<what>\w+)")
_ADR = re.compile(r"^- (ADR-\d{3})")


def declared_thresholds(adr_log_markdown: str) -> list[tuple[str, float]]:
    """(adr id, numeric threshold) for every "Revisit at >N ..." clause in the ADR log."""
    out: list[tuple[str, float]] = []
    current = ""
    for line in adr_log_markdown.splitlines():
        if m := _ADR.match(line):
            current = m.group(1)
        for r in _REVISIT.finditer(line):
            n = float(r.group("n").replace(",", ""))
            n *= {"K": 1e3, "M": 1e6, "B": 1e9}.get((r.group("unit") or "").upper(), 1)
            out.append((current, n))
    return out


def evaluate(
    metrics: Mapping[str, float], tripwires: tuple[Tripwire, ...] = TRIPWIRES
) -> list[Alert]:
    """A missing metric is not silence: it raises, so a dead collector is noticed."""
    alerts: list[Alert] = []
    for t in tripwires:
        if t.metric not in metrics:
            raise KeyError(f"metric {t.metric!r} for tripwire {t.id!r} was not collected")
        v = float(metrics[t.metric])
        if t.breached(v):
            alerts.append(
                Alert(tripwire=t.id, adr=t.adr, metric=t.metric, value=v, threshold=t.threshold,
                      description=t.description, calibrated=t.calibrated)
            )  # fmt: skip
    return alerts


def collect_metrics(conn: Connection, *, window_days: int = 7) -> dict[str, float]:
    """Needs a privileged connection (cross-tenant aggregates; no tenant content is read)."""
    row = conn.execute(
        text(
            "SELECT count(*) AS total, "
            "count(*) FILTER (WHERE token_count > "
            "COALESCE((chunk_thresholds->>'embedder_window')::int, "
            "2147483647)) AS over_window, "
            "count(DISTINCT (embedding_model, embedding_revision, chunker_version, "
            "chunk_thresholds::text)) AS generations FROM chunks WHERE is_current "
            # ADR-006: a privileged engine bypasses RLS; count the ACTIVE generation only (a
            # generation being built aside is not live)
            "AND generation = app_generation()"
        )
    ).one()
    rates = conn.execute(
        text(
            "SELECT model_alias, count(*) AS n, "
            "count(*) FILTER (WHERE regenerations > 0) AS regen, "
            "count(*) FILTER (WHERE event_type = 'refusal') AS refused "
            "FROM audit_log WHERE model_alias IS NOT NULL "
            "AND occurred_at > now() - make_interval(days => :d) GROUP BY model_alias"
        ),
        {"d": window_days},
    ).all()
    # Rates need a minimum sample to mean anything; below it they report 0.0 (no signal).
    min_n = 20
    regen = max((r.regen / r.n for r in rates if r.n >= min_n), default=0.0)
    refused = max((r.refused / r.n for r in rates if r.n >= min_n), default=0.0)
    return {
        "chunks_total": float(row.total),
        "chunks_exceeding_window": float(row.over_window),
        "live_generations": float(row.generations),
        "regeneration_rate_max_alias": float(regen),
        "refusal_rate_max_alias": float(refused),
    }
