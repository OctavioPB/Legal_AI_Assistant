"""Every threshold declared in the ADR log has an alert; alerts fire and cannot go silent."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import Engine, text

from app.db.session import tenant_transaction
from app.ops.tripwires import TRIPWIRES, Tripwire, collect_metrics, declared_thresholds, evaluate
from app.validation.audit import write_audit
from tests import helpers

ADR_LOG = Path(__file__).parents[1] / ".claude" / "rules" / "architecture.md"
ZERO = {
    "chunks_total": 0, "chunks_exceeding_window": 0, "live_generations": 1,
    "regeneration_rate_max_alias": 0.0, "refusal_rate_max_alias": 0.0,
}  # fmt: skip


def test_every_threshold_declared_in_the_adr_log_has_an_alert() -> None:
    declared = declared_thresholds(ADR_LOG.read_text(encoding="utf-8"))
    assert declared, "the parser found no 'Revisit at' clause: the ADR log format changed?"
    for adr, threshold in declared:
        assert any(t.adr == adr and t.threshold == threshold for t in TRIPWIRES), (adr, threshold)


def test_parser_reads_units_and_the_owning_adr() -> None:
    md = "- ADR-001: Postgres. Revisit at >50M chunks.\n- ADR-002: x. Revisit at > 200k docs\n"
    assert declared_thresholds(md) == [("ADR-001", 50_000_000.0), ("ADR-002", 200_000.0)]


def test_adrs_that_name_a_measurable_limit_are_covered() -> None:
    adrs = {t.adr for t in TRIPWIRES}
    assert {"ADR-001", "ADR-006", "ADR-011", "ADR-012"} <= adrs  # window, generations, scale


def test_ids_are_unique_and_placeholders_are_flagged() -> None:
    assert len({t.id for t in TRIPWIRES}) == len(TRIPWIRES)
    assert all(not t.calibrated for t in TRIPWIRES if t.adr == "ops")  # no invented certainty


def test_alerts_fire_only_past_the_threshold() -> None:
    assert evaluate(ZERO) == []
    over = evaluate({**ZERO, "chunks_total": 50_000_001, "chunks_exceeding_window": 1})
    assert {a.tripwire for a in over} == {
        "chunks-total",
        "chunks-total-plan",
        "chunks-exceed-window",
    }
    assert evaluate({**ZERO, "chunks_total": 50_000_000}) == []  # strictly greater than


def test_a_metric_that_stops_being_collected_raises_instead_of_going_quiet() -> None:
    partial = {k: v for k, v in ZERO.items() if k != "live_generations"}
    with pytest.raises(KeyError, match="live_generations"):
        evaluate(partial)
    with pytest.raises(KeyError):
        evaluate(ZERO, (*TRIPWIRES, Tripwire("new", "ADR-099", "never_collected", ">", 1, "d")))


def test_metrics_come_from_the_database(
    app_engine: Engine,
    ingestor_engine: Engine,
    admin_engine: Engine,
    tenant_a: uuid.UUID,
    tenant_b: uuid.UUID,
) -> None:
    helpers.seed_overlapping_tenants(app_engine, ingestor_engine, tenant_a, tenant_b)
    with admin_engine.connect() as c:
        m = collect_metrics(c)
    assert m["chunks_total"] > 0 and m["live_generations"] == 1
    assert m["chunks_exceeding_window"] == 0 and evaluate(m) == []
    # break invariant 5 on purpose: a second generation is live -> the ADR-006 alert fires
    with admin_engine.begin() as c:
        c.execute(
            text("UPDATE chunks SET embedding_revision = 'r-other' WHERE cite_id LIKE '%ART47%'")
        )
    with admin_engine.connect() as c:
        m2 = collect_metrics(c)
    assert m2["live_generations"] == 2
    assert [a.tripwire for a in evaluate(m2)] == ["mixed-generations"]


def test_rates_need_a_minimum_sample_and_use_the_worst_alias(
    app_engine: Engine, admin_engine: Engine, tenant_a: uuid.UUID
) -> None:
    def log(n: int, alias: str, event: str, regen: int) -> None:
        with admin_engine.begin() as c:
            c.execute(
                text(
                    "INSERT INTO audit_log (tenant_id, event_type, model_alias, regenerations) "
                    "SELECT :t, :e, :a, :r FROM generate_series(1, :n)"
                ),
                {"t": tenant_a, "e": event, "a": alias, "r": regen, "n": n},
            )

    log(10, "cheap-utility", "refusal", 1)  # 100% but only 10 samples: no signal
    with admin_engine.connect() as c:
        assert collect_metrics(c)["refusal_rate_max_alias"] == 0.0
    log(20, "drafting-primary", "query", 0)
    log(10, "drafting-primary", "query", 1)  # 10/30 regenerated = 33% > 20%
    with admin_engine.connect() as c:
        m = collect_metrics(c)
    assert round(m["regeneration_rate_max_alias"], 2) == 0.33
    assert any(
        a.tripwire == "regeneration-rate" and not a.calibrated for a in evaluate({**ZERO, **m})
    )
    _ = tenant_transaction, write_audit  # imported for symmetry with the other audit tests
