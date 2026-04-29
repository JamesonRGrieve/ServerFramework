"""Tests for ``endpoints.Webhook``."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from endpoints.Webhook import (
    WEBHOOK_REGISTRY,
    WebhookContext,
    create_webhook_router,
    webhook_handler,
)


class _FakeProvider:
    accept_signatures: bool = True

    @staticmethod
    def verify_signature(headers, body) -> bool:
        return _FakeProvider.accept_signatures


class _FakeExtension:
    extension_name = "fakepay"
    PRV_Stripe = _FakeProvider


@pytest.fixture(autouse=True)
def _reset_registry():
    WEBHOOK_REGISTRY.clear()
    _FakeProvider.accept_signatures = True
    yield
    WEBHOOK_REGISTRY.clear()


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(create_webhook_router())
    return TestClient(app)


@pytest.mark.unit
def test_handler_registers_in_registry():
    @webhook_handler(_FakeExtension, provider="stripe", event="customer.updated")
    def handle(ctx: WebhookContext) -> None:
        return None

    assert ("fakepay", "stripe", "customer.updated") in WEBHOOK_REGISTRY


@pytest.mark.unit
def test_dispatch_invokes_handler_and_passes_context():
    seen: dict = {}

    @webhook_handler(_FakeExtension, provider="stripe", event="customer.updated")
    def handle(ctx: WebhookContext) -> None:
        seen["payload"] = ctx.payload
        seen["event"] = ctx.event_name

    client = _client()
    r = client.post(
        "/webhook/fakepay/stripe/customer.updated",
        json={"id": "cus_1"},
    )
    assert r.status_code == 200
    assert seen["payload"] == {"id": "cus_1"}
    assert seen["event"] == "customer.updated"


@pytest.mark.unit
def test_signature_failure_returns_401():
    @webhook_handler(_FakeExtension, provider="stripe", event="customer.updated")
    def handle(ctx: WebhookContext) -> None:
        raise AssertionError("must not be called on bad signature")

    _FakeProvider.accept_signatures = False
    client = _client()
    r = client.post(
        "/webhook/fakepay/stripe/customer.updated",
        json={"id": "cus_2"},
    )
    assert r.status_code == 401


@pytest.mark.unit
def test_unrecognized_event_returns_200_without_handler():
    # Register only a different event.
    @webhook_handler(_FakeExtension, provider="stripe", event="customer.deleted")
    def handle(ctx: WebhookContext) -> None:
        raise AssertionError("must not be called for unrecognized event")

    client = _client()
    r = client.post(
        "/webhook/fakepay/stripe/customer.created",
        json={"id": "cus_3"},
    )
    assert r.status_code == 200
