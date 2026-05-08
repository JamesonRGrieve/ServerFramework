"""
Tests for IMPROVEMENTS Items 93 (federation translators), 94 (inbound
webhooks), and 95 (capability ladder) on the SendGrid provider.

Pure helper unit tests (signature verification, field-mapping transforms,
query translation, cursor round-trip) use `@pytest.mark.unit` and may use
mocks. Tests that would touch the SendGrid sandbox carry
`@pytest.mark.external_api(provider="sendgrid")` so they auto-xfail
without credentials per Item 15.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from serverframework.extensions.webhooks import (
    WEBHOOK_REGISTRY,
    create_webhook_router,
)
from serverframework.extensions.webhooks.BLL_Webhooks import _PROVIDER_CLASSES
from serverframework.extensions.email.AbstractEmailProviderInstance import (
    AbstractEmailProviderInstance,
    BulkSendResult,
    EmailValidationResult,
    EmailStats,
    MessageListPage,
    SuppressionListPage,
)
from serverframework.extensions.email.EmailErrors import NotSupportedError
from serverframework.extensions.email.EXT_EMail import (
    Capability,
    EmailAddress,
    EmailDeliveryEvent,
    EmailMessage,
    Importance,
    dispatch_email_delivery_event,
    list_email_delivery_subscribers,
    subscribe_email_delivery,
    unsubscribe_email_delivery,
)
from serverframework.extensions.email.PRV_SendGrid_EMail import (
    SendgridEmailInstance,
    SendgridProvider,
    _coerce_sendgrid_event,
    _dispatch_sendgrid_events,
    _sendgrid_message_to_sg_payload,
    _sendgrid_payload_to_message_kwargs,
    _register_sendgrid_webhook_handlers,
)
from serverframework.extensions.FieldMappings import apply_from_external, apply_to_external
from serverframework.extensions.Paginators import (
    decode_token,
    encode_token,
    query_hash,
)
from serverframework.extensions.QueryTranslators import KeyValueTranslator


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Item 95 — capability ladder + NotSupportedError defaults.
# ---------------------------------------------------------------------------


class _BareInstance(AbstractEmailProviderInstance):
    """Concrete instance that declares NO ladder capabilities so we can
    exercise the abstract-base `NotSupportedError` defaults in isolation.
    """

    capabilities = frozenset()

    async def send(self, message): raise NotImplementedError
    async def send_bulk(self, messages): raise NotImplementedError
    async def list_emails(self, *, folder=None, query=None, limit=10, cursor=None): return []
    async def get_email(self, message_id): return {}
    async def update_email(self, message_id, *, read=None, flagged=None, folder=None, deleted=False): return {}
    async def reply(self, message_id, body, attachments=None): raise NotImplementedError
    async def download_attachment(self, message_id, attachment_id): return b""
    async def list_threads(self, folder=None, limit=10): return []


def _run(coro):
    return asyncio.run(coro)


def test_capability_ladder_default_raises_not_supported():
    inst = _BareInstance()

    with pytest.raises(NotSupportedError) as exc:
        _run(inst.validate_address("a@b.c"))
    assert exc.value.capability == "validate_address"

    with pytest.raises(NotSupportedError):
        _run(inst.send_with_template("tpl", ["a@b.c"], {}))
    with pytest.raises(NotSupportedError):
        _run(inst.list_suppressions("bounce", limit=10))
    with pytest.raises(NotSupportedError):
        _run(inst.add_suppression("a@b.c", "bounce"))
    with pytest.raises(NotSupportedError):
        _run(inst.remove_suppression("a@b.c", "bounce"))
    with pytest.raises(NotSupportedError):
        _run(inst.get_stats())
    with pytest.raises(NotSupportedError):
        _run(inst.list_messages())


def test_sendgrid_capability_declarations_include_ladder_subset():
    caps = SendgridProvider.capabilities
    assert Capability.SEND in caps
    assert Capability.VALIDATE_ADDRESS in caps
    assert Capability.TEMPLATES in caps
    assert Capability.SUPPRESSIONS in caps
    assert Capability.STATS in caps
    assert Capability.MESSAGES in caps
    assert Capability.INBOUND_WEBHOOK in caps


def test_sendgrid_instance_inherits_capabilities():
    inst = SendgridEmailInstance(api_key="sk_test", from_email="x@y.z")
    assert Capability.VALIDATE_ADDRESS in inst.capabilities
    assert Capability.STATS in inst.capabilities


# ---------------------------------------------------------------------------
# Item 95 — SendGrid ladder methods round-tripped via a fake HTTP client.
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload) if not isinstance(payload, str) else payload

    def json(self):
        return self._payload


class _FakeAsyncClient:
    """Minimal `httpx.AsyncClient`-compatible stub used by SendGrid ladder
    tests. Records every request and returns canned responses keyed by
    `(method, path)`.
    """

    def __init__(self, routes: Dict[tuple, _FakeResponse]) -> None:
        self.routes = routes
        self.calls: List[tuple] = []

    async def request(self, method, url, *, params=None, json=None, headers=None):
        path = url.replace("https://api.sendgrid.com", "")
        self.calls.append((method, path, params, json))
        for (m, p), resp in self.routes.items():
            if m == method and p == path:
                return resp
        return _FakeResponse(404, {"errors": [f"{method} {path} not stubbed"]})


def test_validate_address_returns_typed_result():
    client = _FakeAsyncClient(
        {
            ("POST", "/v3/validations/email"): _FakeResponse(
                200,
                {
                    "result": {
                        "email": "a@b.c",
                        "verdict": "Valid",
                        "score": 0.9,
                        "checks": {"domain": {"has_valid_address_syntax": True}},
                    }
                },
            )
        }
    )
    inst = SendgridEmailInstance(api_key="sk_test", from_email="x@y.z", http_client=client)
    result = _run(inst.validate_address("a@b.c"))
    assert isinstance(result, EmailValidationResult)
    assert result.verdict == "valid"
    assert result.score == 0.9


def test_get_stats_aggregates_counters():
    client = _FakeAsyncClient(
        {
            ("GET", "/v3/stats"): _FakeResponse(
                200,
                [
                    {
                        "date": "2026-04-01",
                        "stats": [
                            {
                                "metrics": {
                                    "delivered": 10,
                                    "opens": 4,
                                    "clicks": 1,
                                    "bounces": 2,
                                    "spam_reports": 0,
                                    "unsubscribes": 1,
                                    "requests": 13,
                                }
                            }
                        ],
                    },
                    {
                        "date": "2026-04-02",
                        "stats": [
                            {
                                "metrics": {
                                    "delivered": 5,
                                    "opens": 1,
                                    "clicks": 0,
                                    "bounces": 0,
                                    "spam_reports": 1,
                                    "unsubscribes": 0,
                                    "requests": 6,
                                }
                            }
                        ],
                    },
                ],
            )
        }
    )
    inst = SendgridEmailInstance(api_key="sk_test", from_email="x@y.z", http_client=client)
    stats = _run(inst.get_stats())
    assert isinstance(stats, EmailStats)
    assert stats.delivered == 15
    assert stats.opens == 5
    assert stats.spam_reports == 1
    assert stats.requests == 19


def test_list_messages_round_trips_cursor_through_next_token():
    """Item 93 acceptance: cursor round-trips through the framework's
    `next_token` envelope across providers."""
    page1 = _FakeResponse(
        200,
        {
            "messages": [
                {"msg_id": "m1", "to_email": "a@b.c", "subject": "hi", "status": "delivered"},
            ],
            "next_page_token": "PROVIDER_TOKEN_42",
        },
    )
    page2 = _FakeResponse(200, {"messages": [], "next_page_token": None})
    client = _FakeAsyncClient(
        {
            ("GET", "/v3/messages"): page1,
        }
    )
    inst = SendgridEmailInstance(api_key="sk_test", from_email="x@y.z", http_client=client)
    page = _run(inst.list_messages(limit=1))
    assert isinstance(page, MessageListPage)
    assert page.next_token is not None
    # The next_token is an opaque envelope; decoding round-trips the
    # provider cursor.
    envelope = decode_token(page.next_token, query_hash({"limit": 1}))
    assert envelope["provider_cursor"] == "PROVIDER_TOKEN_42"
    assert envelope["page_size"] == 1

    # Now call with the cursor and verify the upstream call carries
    # `page_token=PROVIDER_TOKEN_42`.
    client.routes[("GET", "/v3/messages")] = page2
    _run(inst.list_messages(limit=1, cursor=page.next_token))
    last_call = client.calls[-1]
    assert last_call[2]["page_token"] == "PROVIDER_TOKEN_42"


def test_list_suppressions_known_type():
    client = _FakeAsyncClient(
        {
            ("GET", "/v3/suppression/bounces"): _FakeResponse(
                200,
                [
                    {"email": "a@b.c", "reason": "bounced", "created": 1700000000},
                    {"email": "d@e.f", "reason": "soft", "created": 1700000001},
                ],
            )
        }
    )
    inst = SendgridEmailInstance(api_key="sk_test", from_email="x@y.z", http_client=client)
    page = _run(inst.list_suppressions("bounce", limit=2))
    assert isinstance(page, SuppressionListPage)
    assert len(page.items) == 2
    assert page.items[0].email == "a@b.c"
    assert page.items[0].suppression_type == "bounce"


# ---------------------------------------------------------------------------
# Item 93 — field-mapping round-trip + query translator.
# ---------------------------------------------------------------------------


def test_field_mapping_round_trip_preserves_email_message():
    msg = EmailMessage(
        to=[EmailAddress(address="a@b.c", name="Alice")],
        subject="Round trip",
        body_text="hello",
        body_html=None,
        importance=Importance.HIGH,
        from_=EmailAddress(address="from@example.com", name="From Name"),
        tags=["welcome"],
    )
    payload = _sendgrid_message_to_sg_payload(msg)
    assert payload["subject"] == "Round trip"
    assert payload["priority"] == "high"
    assert payload["from"] == {"email": "from@example.com", "name": "From Name"}
    assert payload["personalizations"][0]["to"] == [{"email": "a@b.c", "name": "Alice"}]
    assert any(c["type"] == "text/plain" and c["value"] == "hello" for c in payload["content"])

    # Inverse: external -> internal kwargs reproduces the originating shape.
    kwargs = _sendgrid_payload_to_message_kwargs(payload)
    rebuilt = EmailMessage(**kwargs)
    assert rebuilt.subject == msg.subject
    assert rebuilt.body_text == msg.body_text
    assert rebuilt.importance == Importance.HIGH
    assert rebuilt.from_.address == "from@example.com"
    assert rebuilt.to[0].address == "a@b.c"


def test_field_mappings_enum_remap_importance():
    flat = {"importance": Importance.LOW.value}
    out = apply_to_external(SendgridProvider.field_mappings, flat)
    assert out["priority"] == "low"
    inv = apply_from_external(SendgridProvider.field_mappings, out)
    assert inv["importance"] == Importance.LOW.value


def test_query_translator_emits_flat_keyvalue():
    tr = KeyValueTranslator()
    out = tr.translate({"email": "a@b.c", "status": "delivered"})
    assert out == {"email": "a@b.c", "status": "delivered"}


def test_paginator_classvar_declared_on_sendgrid():
    """Item 93 acceptance: `paginator` and `query_translator` declared
    on the provider rather than reconstructed inside list bodies."""
    from serverframework.extensions.Paginators import PageTokenPaginator

    assert SendgridProvider.paginator is PageTokenPaginator
    assert SendgridProvider.query_translator is KeyValueTranslator


# ---------------------------------------------------------------------------
# Item 94 — webhook signature verification (HMAC fallback path).
# ---------------------------------------------------------------------------


def _hmac_signature(secret: str, timestamp: str, body: bytes) -> str:
    digest = hmac.new(
        secret.encode("utf-8"), timestamp.encode("utf-8") + body, hashlib.sha256
    ).digest()
    return base64.b64encode(digest).decode("ascii")


def _fresh_timestamp() -> str:
    """Return a current epoch timestamp; required since the SendGrid
    ``verify_signature`` enforces a 300-second freshness window (H-1).
    """
    import time

    return str(int(time.time()))


def test_verify_signature_hmac_fallback_known_good(monkeypatch):
    monkeypatch.setenv("SENDGRID_WEBHOOK_PUBLIC_KEY", "")
    monkeypatch.setenv("SENDGRID_WEBHOOK_SECRET", "shh-shared")
    body = b'{"event":"delivered"}'
    timestamp = _fresh_timestamp()
    sig = _hmac_signature("shh-shared", timestamp, body)
    headers = {
        SendgridProvider.SENDGRID_SIGNATURE_HEADER: sig,
        SendgridProvider.SENDGRID_TIMESTAMP_HEADER: timestamp,
    }
    assert SendgridProvider.verify_signature(headers, body) is True


def test_verify_signature_hmac_fallback_known_bad(monkeypatch):
    monkeypatch.setenv("SENDGRID_WEBHOOK_PUBLIC_KEY", "")
    monkeypatch.setenv("SENDGRID_WEBHOOK_SECRET", "shh-shared")
    body = b'{"event":"delivered"}'
    headers = {
        SendgridProvider.SENDGRID_SIGNATURE_HEADER: base64.b64encode(b"x" * 32).decode(),
        SendgridProvider.SENDGRID_TIMESTAMP_HEADER: _fresh_timestamp(),
    }
    assert SendgridProvider.verify_signature(headers, body) is False


def test_verify_signature_missing_key_and_secret_returns_false(monkeypatch):
    monkeypatch.setenv("SENDGRID_WEBHOOK_PUBLIC_KEY", "")
    monkeypatch.setenv("SENDGRID_WEBHOOK_SECRET", "")
    assert (
        SendgridProvider.verify_signature(
            {
                SendgridProvider.SENDGRID_SIGNATURE_HEADER: "AAAA",
                SendgridProvider.SENDGRID_TIMESTAMP_HEADER: _fresh_timestamp(),
            },
            b"{}",
        )
        is False
    )


def test_verify_signature_stale_timestamp_rejected(monkeypatch):
    """H-1 — a captured webhook with an old timestamp must not replay."""
    monkeypatch.setenv("SENDGRID_WEBHOOK_PUBLIC_KEY", "")
    monkeypatch.setenv("SENDGRID_WEBHOOK_SECRET", "shh-shared")
    body = b'{"event":"delivered"}'
    # Outside the 300s window — even a correctly-HMAC'd payload must be
    # refused on the freshness gate alone.
    timestamp = "1700000000"
    sig = _hmac_signature("shh-shared", timestamp, body)
    headers = {
        SendgridProvider.SENDGRID_SIGNATURE_HEADER: sig,
        SendgridProvider.SENDGRID_TIMESTAMP_HEADER: timestamp,
    }
    assert SendgridProvider.verify_signature(headers, body) is False


def test_verify_signature_non_numeric_timestamp_rejected(monkeypatch):
    monkeypatch.setenv("SENDGRID_WEBHOOK_PUBLIC_KEY", "")
    monkeypatch.setenv("SENDGRID_WEBHOOK_SECRET", "shh-shared")
    headers = {
        SendgridProvider.SENDGRID_SIGNATURE_HEADER: "AAAA",
        SendgridProvider.SENDGRID_TIMESTAMP_HEADER: "not-a-number",
    }
    assert SendgridProvider.verify_signature(headers, b"{}") is False


def test_extract_replay_keys_returns_timestamp_and_nonce():
    """H-1 — the framework's ``check_replay`` plumbing requires the
    provider to expose ``extract_replay_keys``."""
    body = b'{"event":"delivered"}'
    timestamp = _fresh_timestamp()
    headers = {
        SendgridProvider.SENDGRID_SIGNATURE_HEADER: "ignored",
        SendgridProvider.SENDGRID_TIMESTAMP_HEADER: timestamp,
    }
    ts, nonce = SendgridProvider.extract_replay_keys(headers, body)
    assert ts == int(timestamp)
    assert nonce is not None and len(nonce) == 64  # sha256 hex


def test_extract_replay_keys_missing_timestamp_returns_none():
    ts, nonce = SendgridProvider.extract_replay_keys({}, b"{}")
    assert ts is None and nonce is None


def test_replay_window_seconds_class_attr_present():
    assert isinstance(SendgridProvider.replay_window_seconds, int)
    assert SendgridProvider.replay_window_seconds > 0


def test_verify_signature_missing_headers_returns_false(monkeypatch):
    monkeypatch.setenv("SENDGRID_WEBHOOK_SECRET", "shh-shared")
    assert SendgridProvider.verify_signature({}, b"{}") is False


# ---------------------------------------------------------------------------
# Item 94 — webhook dispatch fans into the canonical hook bus.
# ---------------------------------------------------------------------------


def test_email_delivery_event_subscribe_unsubscribe():
    captured = []

    def cb(evt):
        captured.append(evt)

    subscribe_email_delivery(cb)
    try:
        evt = EmailDeliveryEvent(provider="sendgrid", event_type="bounce")
        _run(dispatch_email_delivery_event(evt))
        assert len(captured) == 1
        assert captured[0].event_type == "bounce"
    finally:
        unsubscribe_email_delivery(cb)
    assert cb not in list_email_delivery_subscribers()


def test_dispatch_sendgrid_events_normalises_payload():
    captured = []

    async def cb(evt):
        captured.append(evt)

    subscribe_email_delivery(cb)
    try:
        payload = [
            {
                "event": "bounce",
                "email": "a@b.c",
                "sg_message_id": "msg-42",
                "timestamp": 1700000000,
            },
            {
                "event": "delivered",
                "email": "d@e.f",
                "sg_message_id": "msg-43",
                "timestamp": 1700000001,
            },
        ]
        _run(_dispatch_sendgrid_events(payload, "bounce"))
        assert len(captured) == 2
        assert captured[0].event_type == "bounce"
        assert captured[0].recipient == "a@b.c"
        assert captured[0].message_id == "msg-42"
        assert captured[1].event_type == "delivered"
    finally:
        unsubscribe_email_delivery(cb)


def test_coerce_sendgrid_event_handles_string_timestamp():
    evt = _coerce_sendgrid_event({"email": "x@y.z", "timestamp": "1700000000.5"}, "open")
    assert evt.event_type == "open"
    assert evt.timestamp == 1700000000.5
    assert evt.provider == "sendgrid"


def test_webhook_handlers_registered_for_each_event_type():
    """Item 94 acceptance: every documented Event Webhook event type is
    routable through the framework's webhook registry."""
    expected = {
        "bounce",
        "delivered",
        "open",
        "click",
        "spam_report",
        "unsubscribe",
        "dropped",
        "processed",
    }
    for event in expected:
        assert ("email", "sendgrid", event) in WEBHOOK_REGISTRY


def test_webhook_e2e_signature_failure_returns_401(monkeypatch):
    """Posting an invalid signature against the registered SendGrid
    handler returns 401 without invoking any subscriber."""
    monkeypatch.setenv("SENDGRID_WEBHOOK_PUBLIC_KEY", "")
    monkeypatch.setenv("SENDGRID_WEBHOOK_SECRET", "shh-shared")
    # Make sure provider class is registered (re-register is idempotent).
    _register_sendgrid_webhook_handlers()

    captured = []

    def cb(evt):
        captured.append(evt)

    subscribe_email_delivery(cb)
    try:
        app = FastAPI()
        app.include_router(create_webhook_router())
        client = TestClient(app)
        r = client.post(
            "/webhook/email/sendgrid/delivered",
            json=[{"event": "delivered", "email": "a@b.c"}],
            headers={
                SendgridProvider.SENDGRID_SIGNATURE_HEADER: base64.b64encode(b"x" * 32).decode(),
                SendgridProvider.SENDGRID_TIMESTAMP_HEADER: _fresh_timestamp(),
            },
        )
        assert r.status_code == 401
        assert captured == []
    finally:
        unsubscribe_email_delivery(cb)


def test_webhook_e2e_valid_signature_dispatches_event(monkeypatch):
    """Posting a valid HMAC signature dispatches to the canonical hook
    bus with normalised `EmailDeliveryEvent` payload."""
    secret = "shh-shared"
    monkeypatch.setenv("SENDGRID_WEBHOOK_PUBLIC_KEY", "")
    monkeypatch.setenv("SENDGRID_WEBHOOK_SECRET", secret)
    _register_sendgrid_webhook_handlers()

    captured = []

    async def cb(evt):
        captured.append(evt)

    subscribe_email_delivery(cb)
    try:
        body_obj = [
            {"event": "bounce", "email": "a@b.c", "sg_message_id": "msg-1", "timestamp": 1700000000}
        ]
        body = json.dumps(body_obj).encode("utf-8")
        timestamp = _fresh_timestamp()
        sig = _hmac_signature(secret, timestamp, body)

        app = FastAPI()
        app.include_router(create_webhook_router())
        client = TestClient(app)
        r = client.post(
            "/webhook/email/sendgrid/bounce",
            content=body,
            headers={
                SendgridProvider.SENDGRID_SIGNATURE_HEADER: sig,
                SendgridProvider.SENDGRID_TIMESTAMP_HEADER: timestamp,
                "content-type": "application/json",
            },
        )
        assert r.status_code == 200
        assert len(captured) == 1
        assert captured[0].event_type == "bounce"
        assert captured[0].recipient == "a@b.c"
    finally:
        unsubscribe_email_delivery(cb)


# ---------------------------------------------------------------------------
# Item 95 — external API smoke tests, auto-xfail without sandbox creds.
# ---------------------------------------------------------------------------


@pytest.mark.external_api(provider="sendgrid")
def test_validate_address_against_sendgrid_sandbox():
    """Hits SendGrid's `/v3/validations/email`. Auto-xfails without a
    sandbox API key per Item 15."""
    from serverframework.lib.Environment import env

    if not env("SENDGRID_API_KEY"):
        pytest.xfail("SENDGRID_API_KEY not set")

    inst = SendgridEmailInstance(
        api_key=env("SENDGRID_API_KEY"), from_email=env("SENDGRID_FROM_EMAIL")
    )
    result = _run(inst.validate_address("contact@sendgrid.com"))
    assert isinstance(result, EmailValidationResult)
