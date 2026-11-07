"""Audit log writer (WS5.T03). One immutable row per answered or refused query; the table is
append-only for every role (privileges + trigger), tenant-private under RLS."""

from __future__ import annotations

import json
import uuid

from sqlalchemy import Connection, text

from app.validation.loop import ValidatedAnswer


def write_audit(
    conn: Connection,
    *,
    tenant_id: uuid.UUID,
    query_text: str,
    filters: dict[str, object],
    retrieved_cite_ids: list[str],
    result: ValidatedAnswer | None,
    output_text: str | None,
    event_type: str,
    model_alias: str | None = None,
    user_id: uuid.UUID | None = None,
    session_id: uuid.UUID | None = None,
    extra_verdict: dict[str, object] | None = None,
) -> uuid.UUID:
    """`conn` must be inside `tenant_transaction` for `tenant_id` (the INSERT is checked by RLS).

    `result=None` is the pre-LLM refusal (tau gate): there is no model, only the retrieval verdict
    in `extra_verdict`. Otherwise every attempt's verdict is stored, not just the last.
    """
    verdict: dict[str, object] = dict(extra_verdict or {})
    if result is not None:
        verdict["attempts"] = [v.model_dump(mode="json") for v in result.verdicts]
        verdict["grounded"] = result.verdicts[-1].grounded
        if result.refusal is not None:
            verdict["refusal_reason"] = result.refusal.reason_key
    request_id: uuid.UUID = conn.execute(
        text(
            "INSERT INTO audit_log (tenant_id, user_id, session_id, event_type, query_text, "
            "filters, retrieved_cite_ids, output_text, verdict, model_alias, resolved_model_id, "
            "regenerations) VALUES (:t, :u, :s, :e, :q, CAST(:f AS jsonb), :ids, :o, "
            "CAST(:v AS jsonb), :a, :m, :r) RETURNING request_id"
        ),
        {
            "t": tenant_id,
            "u": user_id,
            "s": session_id,
            "e": event_type,
            "q": query_text,
            "f": json.dumps(filters, sort_keys=True),
            "ids": retrieved_cite_ids,
            "o": output_text,
            "v": json.dumps(verdict, sort_keys=True, default=str),
            "a": model_alias,
            "m": result.resolved_model_id if result else None,
            "r": result.regenerations if result else 0,
        },
    ).scalar_one()
    return request_id
