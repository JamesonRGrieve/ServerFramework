# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for outbound webhook delivery (#203).

Subscription filtering, HMAC signing, dispatch fan-out, delivery success, and
exponential-backoff -> dead-letter, plus the management/delivery-log router.
Delivery uses an injected clock + injected HTTP so backoff is deterministic.
"""

import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone

import pytest

from zephyrex.extensions.webhooks.BLL_WebhookDelivery import (
    SIGNATURE_HEADER,
    WebhookDeliveryService,
    WebhookSubscription,
    sign_payload,
)


def _fake_clock(start):
    state = {"now": start}

    def now():
        return state["now"]

    def advance(seconds):
        state["now"] = state["now"] + timedelta(seconds=seconds)

    return now, advance


class _Resp:
    def __init__(self, status):
        self.status_code = status


def _post(status, calls):
    async def post(url, *, content, headers):
        calls.append({"url": url, "content": content, "headers": headers})
        return _Resp(status)

    return post


def _service(**kw):
    return WebhookDeliveryService(base_delay_seconds=2.0, max_attempts=3, **kw)


class TestSubscriptions:
    def test_matches_filter_and_active(self):
        s = WebhookSubscription(
            target_url="http://x", secret="k", event_types=["email.bounce"]
        )
        assert s.matches("email.bounce")
        assert not s.matches("email.delivered")
        star = WebhookSubscription(target_url="http://x", secret="k", event_types=["*"])
        assert star.matches("anything")
        star.active = False
        assert not star.matches("anything")

    def test_register_list_unregister(self):
        svc = _service()
        sub = svc.register(WebhookSubscription(target_url="http://x", secret="k"))
        assert [s.id for s in svc.list_subscriptions()] == [sub.id]
        assert svc.unregister(sub.id) is True
        assert svc.list_subscriptions() == []
        assert svc.unregister("nope") is False


class TestSign:
    def test_sign_payload(self):
        expected = "sha256=" + hmac.new(b"k", b"body", hashlib.sha256).hexdigest()
        assert sign_payload("k", b"body") == expected


class TestDispatch:
    def test_enqueues_for_matching_active_only(self):
        svc = _service()
        a = svc.register(
            WebhookSubscription(target_url="http://a", secret="k", event_types=["e1"])
        )
        svc.register(
            WebhookSubscription(target_url="http://b", secret="k", event_types=["e2"])
        )
        svc.register(
            WebhookSubscription(
                target_url="http://c", secret="k", event_types=["e1"], active=False
            )
        )
        ids = svc.dispatch("e1", {"x": 1})
        assert len(ids) == 1
        d = svc.list_deliveries()[0]
        assert d.subscription_id == a.id and d.target_url == "http://a"


class TestDelivery:
    @pytest.mark.asyncio
    async def test_success_marks_delivered_and_signs(self):
        svc = _service()
        svc.register(
            WebhookSubscription(
                target_url="http://a", secret="topsecret", event_types=["*"]
            )
        )
        svc.dispatch("email.bounce", {"b": 2})
        calls: list = []
        assert await svc.run_once(_post(202, calls)) == 1
        d = svc.list_deliveries()[0]
        assert d.status == "delivered" and d.attempts == 1
        body = json.dumps({"b": 2}, separators=(",", ":"), sort_keys=True).encode()
        assert calls[0]["headers"][SIGNATURE_HEADER] == sign_payload("topsecret", body)
        assert calls[0]["headers"]["X-Webhook-Event"] == "email.bounce"

    @pytest.mark.asyncio
    async def test_failure_backs_off_then_dead_letters(self):
        now, advance = _fake_clock(datetime(2026, 1, 1, tzinfo=timezone.utc))
        svc = _service(now=now)  # base_delay=2, max_attempts=3
        svc.register(
            WebhookSubscription(target_url="http://a", secret="k", event_types=["*"])
        )
        svc.dispatch("e", {})
        post = _post(500, [])

        # attempt 1 fails -> pending, next_attempt = now + 2
        assert await svc.run_once(post) == 1
        d = svc.list_deliveries()[0]
        assert d.status == "pending" and d.attempts == 1
        assert d.next_attempt_at == now() + timedelta(seconds=2)

        # not yet due -> nothing attempted
        assert await svc.run_once(post) == 0

        # advance past backoff -> attempt 2, next = now + 4 (exponential)
        advance(2)
        assert await svc.run_once(post) == 1
        d = svc.list_deliveries()[0]
        assert d.attempts == 2 and d.status == "pending"
        assert d.next_attempt_at == now() + timedelta(seconds=4)

        # attempt 3 reaches max_attempts -> dead-letter
        advance(4)
        assert await svc.run_once(post) == 1
        d = svc.list_deliveries()[0]
        assert d.attempts == 3 and d.status == "dead"

        # dead deliveries are never re-attempted
        advance(10_000)
        assert await svc.run_once(post) == 0


class TestRouter:
    def test_subscription_crud_and_delivery_log(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from zephyrex.extensions.webhooks.BLL_WebhookDelivery import (
            reset_delivery_service_for_test,
        )
        from zephyrex.extensions.webhooks.EP_WebhookDelivery import (
            create_webhook_delivery_router,
        )

        reset_delivery_service_for_test()
        app = FastAPI()
        app.include_router(create_webhook_delivery_router())
        client = TestClient(app)

        resp = client.post(
            "/webhook/subscriptions",
            json={"target_url": "http://a", "event_types": ["e1"], "secret": "s"},
        )
        assert resp.status_code == 201
        assert "secret" not in resp.json()  # never echo the HMAC secret
        sub_id = resp.json()["id"]

        assert client.get("/webhook/subscriptions").json()[0]["id"] == sub_id
        assert client.get("/webhook/deliveries").json() == []

        assert client.delete(f"/webhook/subscriptions/{sub_id}").status_code == 204
        assert client.delete(f"/webhook/subscriptions/{sub_id}").status_code == 404
        reset_delivery_service_for_test()
