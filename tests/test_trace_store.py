"""The in-process trace store is bounded (audit 2026-11-17).

Every answered query records its full prompt, context and retrieved texts. The store lived as long as
the API process and never dropped anything, so a long-running server grew without limit. It now keeps
the newest N traces per tenant; one busy firm never evicts another firm's traces.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.observability.trace import InMemoryTracer, TraceRecord


def trace(tenant: uuid.UUID) -> TraceRecord:
    return TraceRecord(
        request_id=uuid.uuid4(), tenant_id=tenant, occurred_at=datetime.now(UTC), query="q",
        session={}, filters_sql="", filters_params={}, semantic_ids=(), lexical_ids=(), hits=(),
        retrieved_texts={}, gate={}, outcome="refused", versions={},
    )  # fmt: skip


def test_the_store_keeps_the_newest_traces_of_each_tenant() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    store = InMemoryTracer(max_per_tenant=2)
    a1, a2, a3, b1 = trace(a), trace(a), trace(a), trace(b)
    for t in (a1, b1, a2, a3):
        store.record(t)
    with pytest.raises(KeyError):
        store.get(a, a1.request_id)  # the oldest of the busy tenant went
    assert store.get(a, a3.request_id) is a3
    assert store.get(b, b1.request_id) is b1  # the other tenant is untouched
    assert store.list_keys(a) == [str(a2.request_id), str(a3.request_id)]


def test_reading_a_trace_does_not_create_an_empty_tenant_entry() -> None:
    store = InMemoryTracer(max_per_tenant=2)
    with pytest.raises(KeyError):
        store.get(uuid.uuid4(), uuid.uuid4())
    assert store.traces == {}


def test_the_bound_must_be_positive() -> None:
    with pytest.raises(ValueError):
        InMemoryTracer(max_per_tenant=0)
