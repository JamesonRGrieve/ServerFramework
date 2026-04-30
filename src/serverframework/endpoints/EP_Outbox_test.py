"""Item 48 — outbox tracking endpoint tests."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from serverframework.endpoints.EP_Outbox import create_outbox_router
from serverframework.logic.Outbox import InMemoryOutboxStore, OutboxEntry


def _make_client(store) -> TestClient:
    app = FastAPI()
    app.include_router(create_outbox_router(store_resolver=lambda: store, require_auth=False))
    return TestClient(app)


def _enqueue(store: InMemoryOutboxStore, *, idempotency_key: str) -> str:
    entry = OutboxEntry(
        operation_type="rotation_call",
        target_provider="p",
        target_ability="a",
        payload={"hello": "world"},
        idempotency_key=idempotency_key,
    )
    return store.enqueue(entry)


def test_pending_entry_returns_pending_status() -> None:
    store = InMemoryOutboxStore()
    tid = _enqueue(store, idempotency_key="k1")
    client = _make_client(store)

    response = client.get(f"/v1/outbox/{tid}")
    assert response.status_code == 200
    body = response.json()
    assert body["tracking_id"] == tid
    assert body["status"] == "pending"


def test_succeeded_entry_returns_result() -> None:
    store = InMemoryOutboxStore()
    tid = _enqueue(store, idempotency_key="k2")
    store.mark_complete(tid)
    client = _make_client(store)

    response = client.get(f"/v1/outbox/{tid}")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "complete"
    assert body["result"] == {"hello": "world"}


def test_missing_tracking_id_returns_404() -> None:
    store = InMemoryOutboxStore()
    client = _make_client(store)

    response = client.get("/v1/outbox/does-not-exist")
    assert response.status_code == 404


def test_failed_entry_carries_error_in_body() -> None:
    store = InMemoryOutboxStore()
    tid = _enqueue(store, idempotency_key="k3")
    # Push past max_retries so the entry moves to dlq state.
    store.mark_failed(tid, RuntimeError("boom"), max_retries=0)
    client = _make_client(store)

    response = client.get(f"/v1/outbox/{tid}")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "dlq"
    assert "boom" in body["error"]


def test_no_store_configured_returns_503() -> None:
    app = FastAPI()
    app.include_router(create_outbox_router(store_resolver=lambda: None, require_auth=False))
    client = TestClient(app)

    response = client.get("/v1/outbox/anything")
    assert response.status_code == 503
