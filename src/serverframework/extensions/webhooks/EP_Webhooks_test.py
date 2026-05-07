"""Tests for the webhooks extension router and registry."""

from __future__ import annotations

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from serverframework.extensions.webhooks import (
    WEBHOOK_REGISTRY,
    WebhookContext,
    create_webhook_router,
    reset_replay_cache_for_test,
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
    reset_replay_cache_for_test()
    _FakeProvider.accept_signatures = True
    yield
    WEBHOOK_REGISTRY.clear()
    reset_replay_cache_for_test()


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
    @webhook_handler(_FakeExtension, provider="stripe", event="customer.deleted")
    def handle(ctx: WebhookContext) -> None:
        raise AssertionError("must not be called for unrecognized event")

    client = _client()
    r = client.post(
        "/webhook/fakepay/stripe/customer.created",
        json={"id": "cus_3"},
    )
    assert r.status_code == 200


@pytest.mark.unit
def test_missing_verify_signature_returns_503():
    """A registered provider that doesn't expose verify_signature is a
    misconfiguration, not a signature mismatch — surface it as 503 so
    operators can distinguish "provider unsafe" from "request invalid"."""

    class _ExtensionWithoutVerify:
        extension_name = "noverify"

        class PRV_Stripe:
            pass

    @webhook_handler(_ExtensionWithoutVerify, provider="stripe", event="customer.updated")
    def handle(ctx: WebhookContext) -> None:
        raise AssertionError("must not be called when signature isn't configured")

    client = _client()
    r = client.post(
        "/webhook/noverify/stripe/customer.updated",
        json={"id": "cus_no_sig"},
    )
    assert r.status_code == 503


@pytest.mark.unit
def test_unknown_extension_returns_404():
    """An unknown (extension, provider) is rejected with 404, not 200.
    The previous behaviour turned the endpoint into an extension-discovery
    oracle: an attacker could probe the deployment without crossing a
    signature check."""
    client = _client()
    r = client.post(
        "/webhook/unknown_extension/some_provider/some_event",
        json={"id": "cus_unknown"},
    )
    assert r.status_code == 404


@pytest.mark.unit
def test_replay_protection_rejects_stale_timestamp():
    class _ReplayProvider:
        replay_window_seconds = 60
        accept_signatures = True

        @staticmethod
        def verify_signature(headers, body) -> bool:
            return True

        @staticmethod
        def extract_replay_keys(headers, body):
            return (int(headers.get("x-ts", "0")), headers.get("x-nonce", ""))

    class _ReplayExtension:
        extension_name = "replay_ext"
        PRV_Stripe = _ReplayProvider

    @webhook_handler(_ReplayExtension, provider="stripe", event="evt")
    def handle(ctx: WebhookContext) -> None:
        return None

    client = _client()
    stale_ts = int(time.time()) - 3600
    r = client.post(
        "/webhook/replay_ext/stripe/evt",
        json={"id": "x"},
        headers={"X-Ts": str(stale_ts), "X-Nonce": "n1"},
    )
    assert r.status_code == 401
    assert "Replay" in r.text


@pytest.mark.unit
def test_replay_protection_rejects_nonce_reuse():
    class _ReplayProvider:
        replay_window_seconds = 60

        @staticmethod
        def verify_signature(headers, body) -> bool:
            return True

        @staticmethod
        def extract_replay_keys(headers, body):
            return (int(headers.get("x-ts", "0")), headers.get("x-nonce", ""))

    class _ReplayExtension:
        extension_name = "replay_ext2"
        PRV_Stripe = _ReplayProvider

    @webhook_handler(_ReplayExtension, provider="stripe", event="evt")
    def handle(ctx: WebhookContext) -> None:
        return None

    client = _client()
    fresh_ts = int(time.time())
    headers = {"X-Ts": str(fresh_ts), "X-Nonce": "same-nonce"}
    r1 = client.post("/webhook/replay_ext2/stripe/evt", json={"id": "x"}, headers=headers)
    assert r1.status_code == 200
    r2 = client.post("/webhook/replay_ext2/stripe/evt", json={"id": "x"}, headers=headers)
    assert r2.status_code == 401
