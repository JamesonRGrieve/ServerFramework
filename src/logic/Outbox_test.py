"""Tests for ``logic.Outbox``."""

from __future__ import annotations

import pytest

from logic.Outbox import (
    DLQEntry,
    InMemoryOutboxStore,
    OutboxEntry,
    OutboxStore,
)


def _entry(idem: str = "k-1", **overrides) -> OutboxEntry:
    base = dict(
        operation_type="user.create",
        target_provider="stripe",
        target_ability="customers.create",
        payload={"name": "test"},
        idempotency_key=idem,
    )
    base.update(overrides)
    return OutboxEntry(**base)


@pytest.mark.unit
def test_inmemory_store_implements_protocol():
    store = InMemoryOutboxStore()
    assert isinstance(store, OutboxStore)


@pytest.mark.unit
def test_enqueue_then_find_by_key():
    store = InMemoryOutboxStore()
    entry = _entry("idem-A")
    eid = store.enqueue(entry)
    assert eid == entry.id
    found = store.find_by_idempotency_key("idem-A")
    assert found is not None and found.id == entry.id


@pytest.mark.unit
def test_enqueue_is_idempotent_on_key():
    store = InMemoryOutboxStore()
    a = _entry("idem-X")
    b = _entry("idem-X")
    id_a = store.enqueue(a)
    id_b = store.enqueue(b)
    assert id_a == id_b  # idempotency key collapses duplicates.


@pytest.mark.unit
def test_claim_pending_transitions_to_in_flight():
    store = InMemoryOutboxStore()
    store.enqueue(_entry("idem-1"))
    store.enqueue(_entry("idem-2"))
    claimed = store.claim_pending(limit=10)
    assert len(claimed) == 2
    for c in claimed:
        assert c.status == "in_flight"
    # A second claim returns nothing.
    assert store.claim_pending(limit=10) == []


@pytest.mark.unit
def test_mark_complete_finishes_entry():
    store = InMemoryOutboxStore()
    e = _entry("idem-c")
    store.enqueue(e)
    store.claim_pending()
    store.mark_complete(e.id)
    assert store.find_by_idempotency_key("idem-c").status == "complete"


@pytest.mark.unit
def test_mark_failed_retries_then_dlqs():
    store = InMemoryOutboxStore()
    e = _entry("idem-f")
    store.enqueue(e)
    store.claim_pending()
    for _ in range(2):
        store.mark_failed(e.id, RuntimeError("boom"), max_retries=2)
        # Returned to pending so the drain can pick it up again.
        assert store.find_by_idempotency_key("idem-f").status == "pending"
        store.claim_pending()
    # Third failure pushes past max_retries=2.
    store.mark_failed(e.id, RuntimeError("boom"), max_retries=2)
    assert store.find_by_idempotency_key("idem-f").status == "dlq"
    dlq = store.list_dlq()
    assert len(dlq) == 1
    assert isinstance(dlq[0], DLQEntry)
    assert dlq[0].idempotency_key == "idem-f"
