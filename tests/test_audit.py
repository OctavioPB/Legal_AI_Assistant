"""Every answer and refusal leaves a complete, immutable, tenant-private audit row (WS5.T03)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from app.db.session import tenant_transaction
from app.validation.audit import write_audit
from app.validation.loop import generate_validated
from app.validation.types import RetrievedSet, ValidationConfig
from tests.fakes import FakeLLM

ART47 = "MX-FED-LFT-ART47-FRACX-v2025.06"
RS = RetrievedSet.build([(ART47, ["X. Tener el trabajador más de tres faltas de asistencia."])])
GOOD = f"La rescisión procede por más de tres faltas en treinta días [cite: {ART47}]."
BAD = "La rescisión procede por más de tres faltas en treinta días [cite: MX-NOPE-v1]."


def _answer(replies: list[str]):  # type: ignore[no-untyped-def]
    return generate_validated(
        FakeLLM(replies), "drafting-primary", system="S", user="u", retrieved=RS,
        workflow="dossier_builder", tenant_id="t", max_tokens=10, regen_max_tokens=10,
        config=ValidationConfig(),
    )  # fmt: skip


def test_answer_row_is_complete(app_engine: Engine, tenant_a: uuid.UUID) -> None:
    res = _answer([BAD, GOOD])
    with tenant_transaction(app_engine, tenant_a) as c:
        rid = write_audit(
            c, tenant_id=tenant_a, query_text="¿Causales?", filters={"state": "NL", "current": True},
            retrieved_cite_ids=[ART47], result=res, output_text=res.answer, event_type="query",
            model_alias="drafting-primary",
        )  # fmt: skip
    with tenant_transaction(app_engine, tenant_a) as c:
        row = (
            c.execute(text("SELECT * FROM audit_log WHERE request_id = :r"), {"r": rid})
            .mappings()
            .one()
        )
    assert row["query_text"] == "¿Causales?" and row["output_text"] == GOOD
    assert row["retrieved_cite_ids"] == [ART47] and row["filters"] == {
        "state": "NL",
        "current": True,
    }
    assert row["model_alias"] == "drafting-primary" and row["resolved_model_id"] == "fake-model-1"
    assert row["regenerations"] == 1 and row["verdict"]["grounded"] is True
    assert len(row["verdict"]["attempts"]) == 2 and row["verdict"]["attempts"][0]["invalid"]


def test_refusals_are_logged_with_their_reason(app_engine: Engine, tenant_a: uuid.UUID) -> None:
    res = _answer([BAD, BAD])
    with tenant_transaction(app_engine, tenant_a) as c:
        write_audit(
            c, tenant_id=tenant_a, query_text="q", filters={}, retrieved_cite_ids=[ART47],
            result=res, output_text=res.refusal.block if res.refusal else None, event_type="refusal",
        )  # fmt: skip
        write_audit(  # the pre-LLM tau refusal: there is no model result
            c, tenant_id=tenant_a, query_text="q2", filters={}, retrieved_cite_ids=[], result=None,
            output_text="<insuficiente>retrieval.below_threshold</insuficiente>",
            event_type="refusal", extra_verdict={"best_score": 0.1, "tau": 0.5},
        )  # fmt: skip
    with tenant_transaction(app_engine, tenant_a) as c:
        rows = c.execute(text("SELECT verdict, resolved_model_id FROM audit_log ORDER BY id")).all()
    assert rows[0].verdict["refusal_reason"] == "validation.unsupported"
    assert rows[1].verdict == {"best_score": 0.1, "tau": 0.5} and rows[1].resolved_model_id is None


def test_audit_rows_are_tenant_private_and_immutable(
    app_engine: Engine, tenant_a: uuid.UUID, tenant_b: uuid.UUID
) -> None:
    res = _answer([GOOD])
    with tenant_transaction(app_engine, tenant_a) as c:
        write_audit(c, tenant_id=tenant_a, query_text="secreto", filters={}, retrieved_cite_ids=[ART47],
                    result=res, output_text=res.answer, event_type="query")  # fmt: skip
    with tenant_transaction(app_engine, tenant_b) as c:
        assert c.execute(text("SELECT count(*) FROM audit_log")).scalar_one() == 0
        with pytest.raises(DBAPIError):  # B cannot write a row claiming to be A
            write_audit(c, tenant_id=tenant_a, query_text="x", filters={}, retrieved_cite_ids=[],
                        result=None, output_text=None, event_type="query")  # fmt: skip
    with tenant_transaction(app_engine, tenant_a) as c, pytest.raises(DBAPIError):
        c.execute(
            text("UPDATE audit_log SET output_text = 'editado'")
        )  # privilege-level append-only
