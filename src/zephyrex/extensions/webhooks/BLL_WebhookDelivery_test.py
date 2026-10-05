# SPDX-License-Identifier: AGPL-3.0-or-later
"""Outbound webhook delivery (#203), against the database and a real HTTP
endpoint: subscriptions are validated and their secrets encrypted; an event
reaches only owners who can see its record; deliveries are signed, claimed
once, retried with backoff and dead-lettered; the SSRF guard holds; and
deliveries are read by their owner and written only by the server."""

import hashlib
import hmac
import json
import secrets
import threading
import uuid
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Iterator, List

import pytest
from fastapi import HTTPException

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.webhooks.BLL_WebhookDelivery import (
    BASE_DELAY_SECONDS,
    DEAD,
    DELIVERED,
    DELIVERY_ID_HEADER,
    EVENT_HEADER,
    MAX_ATTEMPTS,
    PENDING,
    SIGNATURE_HEADER,
    WebhookDeliveryManager,
    WebhookSubscriptionModel,
    _claim,
    deliver_due,
    dispatch_webhook_event,
)
from zephyrex.extensions.webhooks.EXT_Webhooks import EXT_Webhooks
from zephyrex.lib.Environment import env
from zephyrex.lib.SecretEncryption import decrypt_secret
from zephyrex.logic.BLL_Auth import TeamModel
from zephyrex.testing.factories import current_if_match

SUBSCRIPTIONS = "/v1/webhook-subscription"
DELIVERIES = "/v1/webhook-delivery"


class Receiver:
    """A real HTTP endpoint on the loopback that records what it is sent
    and answers with ``status``."""

    def __init__(self) -> None:
        self.status = 200
        self.requests: List[Dict[str, Any]] = []
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                receiver.requests.append(
                    {"headers": dict(self.headers), "body": self.rfile.read(length)}
                )
                self.send_response(receiver.status)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args: Any) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}/hook"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def receiver() -> Iterator[Receiver]:
    endpoint = Receiver()
    yield endpoint
    endpoint.close()


@pytest.fixture
def allowed(receiver: Receiver, monkeypatch: pytest.MonkeyPatch) -> Receiver:
    """The receiver, with the SSRF guard told to let it through."""
    monkeypatch.setenv("EGRESS_ALLOWED_HOSTS", f"127.0.0.1:{receiver.port}")
    return receiver


def bearer(user: Any) -> Dict[str, str]:
    return {"Authorization": f"Bearer {user.jwt}"}


def subscribe(server: Any, user: Any, url: str, **fields: Any) -> Dict[str, Any]:
    secret = fields.pop("secret", secrets.token_hex(16))
    response = server.post(
        SUBSCRIPTIONS,
        json={"webhook_subscription": {"target_url": url, "secret": secret, **fields}},
        headers=bearer(user),
    )
    assert response.status_code == 201, response.text
    return {**response.json()["webhook_subscription"], "plain_secret": secret}


def as_root(server: Any) -> WebhookDeliveryManager:
    return WebhookDeliveryManager(
        requester_id=env("ROOT_ID"), model_registry=server.app.state.model_registry
    )


def dispatch(server: Any, event_type: str, team: Any) -> List[str]:
    return dispatch_webhook_event(
        server.app.state.model_registry,
        event_type,
        {"team_id": str(team.id), "nonce": uuid.uuid4().hex},
        about_model=TeamModel,
        about_id=str(team.id),
    )


def make_due(server: Any, delivery_id: str) -> None:
    WebhookDeliveryManager(
        requester_id=env("SYSTEM_ID"), model_registry=server.app.state.model_registry
    ).update(
        delivery_id, next_attempt_at=datetime.now(timezone.utc) - timedelta(seconds=1)
    )


class TestSubscriptions(ExtensionServerMixin):
    extension_class = EXT_Webhooks

    @pytest.mark.parametrize(
        "fields",
        [
            {"target_url": "ftp://hooks.example.com/x"},
            {"target_url": "https://user:pass@hooks.example.com/x"},
            {"target_url": "/relative"},
            {"secret": "short"},
            {"event_types": "not a valid!"},
            {"event_types": ""},
        ],
    )
    def test_a_subscription_needs_an_http_url_a_real_secret_and_event_types(
        self, server, admin_a, fields
    ):
        body = {
            "target_url": "https://hooks.example.com/x",
            "secret": secrets.token_hex(16),
            **fields,
        }
        response = server.post(
            SUBSCRIPTIONS, json={"webhook_subscription": body}, headers=bearer(admin_a)
        )
        assert response.status_code == 422, response.text

    def test_the_secret_is_stored_encrypted_and_never_returned(self, server, admin_a):
        made = subscribe(server, admin_a, "https://hooks.example.com/enc")
        assert "secret" not in made
        read = server.get(f"{SUBSCRIPTIONS}/{made['id']}", headers=bearer(admin_a))
        assert read.status_code == 200 and "secret" not in read.text
        registry = server.app.state.model_registry
        row = WebhookSubscriptionModel.DB(registry.DB.manager.Base).get(
            requester_id=env("ROOT_ID"),
            model_registry=registry,
            id=made["id"],
            return_type="dto",
            override_dto=WebhookSubscriptionModel,
        )
        assert row.secret != made["plain_secret"]
        assert decrypt_secret(row.secret) == made["plain_secret"]

    def test_event_types_are_normalised(self, server, admin_a):
        made = subscribe(
            server, admin_a, "https://hooks.example.com/n", event_types="a.b, c.d a.b"
        )
        assert made["event_types"] == "a.b c.d"

    def test_dispatch_names_one_real_event_type(self, server, team_a):
        for bad in ("*", "not valid!"):
            with pytest.raises(ValueError):
                dispatch(server, bad, team_a)

    def test_an_event_names_its_record_or_who_may_see_it_but_not_both(
        self, server, team_a
    ):
        registry = server.app.state.model_registry
        with pytest.raises(ValueError):
            dispatch_webhook_event(registry, "team.audited", {})
        with pytest.raises(ValueError):
            dispatch_webhook_event(
                registry,
                "team.audited",
                {},
                about_model=TeamModel,
                about_id=str(team_a.id),
                may_see=lambda user_id: True,
            )

    def test_an_event_about_no_local_record_reaches_only_whom_may_see_admits(
        self, server, admin_a, admin_b
    ):
        """An upstream system's event (an ERP document) has no local record
        to check: the predicate decides, and a subscriber it refuses gets
        nothing although their subscription wants every event."""
        event = f"upstream.changed.{uuid.uuid4().hex[:8]}"
        admitted = subscribe(
            server, admin_a, "https://hooks.example.com/u", event_types=event
        )
        subscribe(server, admin_b, "https://hooks.example.com/u", event_types="*")
        asked: List[str] = []

        def may_see(user_id: str) -> bool:
            asked.append(user_id)
            return user_id == str(admin_a.id)

        delivery_ids = dispatch_webhook_event(
            server.app.state.model_registry, event, {"n": 1}, may_see=may_see
        )
        registry = server.app.state.model_registry
        owners = {
            WebhookSubscriptionModel.DB(registry.DB.manager.Base)
            .get(
                requester_id=env("ROOT_ID"),
                model_registry=registry,
                id=as_root(server).get(id=delivery_id).webhook_subscription_id,
                return_type="dto",
                override_dto=WebhookSubscriptionModel,
            )
            .user_id
            for delivery_id in delivery_ids
        }
        reached = {
            as_root(server).get(id=delivery_id).webhook_subscription_id
            for delivery_id in delivery_ids
        }
        assert admitted["id"] in reached
        assert owners == {str(admin_a.id)}
        assert str(admin_b.id) in asked


class TestDelivery(ExtensionServerMixin):
    extension_class = EXT_Webhooks

    async def test_an_event_reaches_only_owners_who_can_see_its_record(
        self, server, admin_a, admin_b, team_a, allowed
    ):
        event = f"team.audited.{uuid.uuid4().hex[:8]}"
        wanted = subscribe(server, admin_a, allowed.url, event_types=event)
        subscribe(server, admin_a, allowed.url, event_types=event, active=False)
        subscribe(server, admin_a, allowed.url, event_types="other.event")
        subscribe(server, admin_b, allowed.url, event_types="*")

        [delivery_id] = dispatch(server, event, team_a)
        assert as_root(server).get(id=delivery_id).webhook_subscription_id == (
            wanted["id"]
        )
        await deliver_due(server.app.state.model_registry)

        [request] = allowed.requests
        headers = {k.lower(): v for k, v in request["headers"].items()}
        expected = hmac.new(
            wanted["plain_secret"].encode(), request["body"], hashlib.sha256
        ).hexdigest()
        assert headers[SIGNATURE_HEADER.lower()] == f"sha256={expected}"
        assert headers[EVENT_HEADER.lower()] == event
        assert headers[DELIVERY_ID_HEADER.lower()] == delivery_id
        assert json.loads(request["body"])["team_id"] == str(team_a.id)
        delivered = as_root(server).get(id=delivery_id)
        assert delivered.status == DELIVERED and delivered.attempts == 1
        assert delivered.delivered_at is not None

    async def test_a_failing_endpoint_is_retried_with_backoff_then_dead_lettered(
        self, server, admin_a, team_a, allowed
    ):
        allowed.status = 500
        event = f"team.failing.{uuid.uuid4().hex[:8]}"
        subscribe(server, admin_a, allowed.url, event_types=event)
        [delivery_id] = dispatch(server, event, team_a)
        registry = server.app.state.model_registry

        before = datetime.now(timezone.utc)
        await deliver_due(registry)
        first = as_root(server).get(id=delivery_id)
        assert first.status == PENDING and first.attempts == 1
        assert "500" in (first.last_error or "")
        waited = first.next_attempt_at.replace(tzinfo=timezone.utc) - before
        assert waited >= timedelta(seconds=BASE_DELAY_SECONDS)

        for _ in range(MAX_ATTEMPTS - 1):
            make_due(server, delivery_id)
            await deliver_due(registry)
        dead = as_root(server).get(id=delivery_id)
        assert dead.status == DEAD and dead.attempts == MAX_ATTEMPTS
        assert len(allowed.requests) == MAX_ATTEMPTS
        make_due(server, delivery_id)
        await deliver_due(registry)
        assert len(allowed.requests) == MAX_ATTEMPTS

    async def test_a_delivery_is_claimed_by_one_worker(
        self, server, admin_a, team_a, allowed
    ):
        event = f"team.claimed.{uuid.uuid4().hex[:8]}"
        subscribe(server, admin_a, allowed.url, event_types=event)
        [delivery_id] = dispatch(server, event, team_a)
        seen = as_root(server).get(id=delivery_id)
        now = datetime.now(timezone.utc)
        registry = server.app.state.model_registry
        assert _claim(registry, seen, now) is True
        assert _claim(registry, seen, now) is False
        assert as_root(server).get(id=delivery_id).attempts == 1

    async def test_the_ssrf_guard_holds_without_an_allowance(
        self, server, admin_a, team_a, receiver
    ):
        event = f"team.guarded.{uuid.uuid4().hex[:8]}"
        subscribe(server, admin_a, receiver.url, event_types=event)
        [delivery_id] = dispatch(server, event, team_a)
        await deliver_due(server.app.state.model_registry)
        refused = as_root(server).get(id=delivery_id)
        assert refused.status == PENDING and refused.attempts == 1
        assert refused.last_error
        assert receiver.requests == []

    async def test_a_removed_subscription_dead_letters_what_it_had_queued(
        self, server, admin_a, team_a, allowed
    ):
        event = f"team.removed.{uuid.uuid4().hex[:8]}"
        made = subscribe(server, admin_a, allowed.url, event_types=event)
        [delivery_id] = dispatch(server, event, team_a)
        url = f"{SUBSCRIPTIONS}/{made['id']}"
        version = current_if_match(server, url, bearer(admin_a))
        removed = server.delete(url, headers={**bearer(admin_a), **version})
        assert removed.status_code == 204, removed.text
        await deliver_due(server.app.state.model_registry)
        assert as_root(server).get(id=delivery_id).status == DEAD
        assert dispatch(server, event, team_a) == []
        assert allowed.requests == []

    def test_deliveries_are_read_by_their_owner_and_written_by_the_server(
        self, server, admin_a, admin_b, team_a
    ):
        event = f"team.read.{uuid.uuid4().hex[:8]}"
        made = subscribe(
            server, admin_a, "https://hooks.example.com/r", event_types=event
        )
        [delivery_id] = dispatch(server, event, team_a)
        mine = server.get(f"{DELIVERIES}/{delivery_id}", headers=bearer(admin_a))
        assert mine.status_code == 200, mine.text
        theirs = server.get(f"{DELIVERIES}/{delivery_id}", headers=bearer(admin_b))
        assert theirs.status_code == 404, theirs.text
        posted = server.post(
            DELIVERIES,
            json={
                "webhook_delivery": {
                    "webhook_subscription_id": made["id"],
                    "event_type": event,
                    "payload": "{}",
                    "next_attempt_at": datetime.now(timezone.utc).isoformat(),
                }
            },
            headers=bearer(admin_a),
        )
        assert posted.status_code in (404, 405), posted.text
        manager = WebhookDeliveryManager(
            requester_id=admin_a.id, model_registry=server.app.state.model_registry
        )
        with pytest.raises(HTTPException) as refused:
            manager.create(
                webhook_subscription_id=made["id"],
                event_type=event,
                payload="{}",
                next_attempt_at=datetime.now(timezone.utc),
            )
        assert refused.value.status_code == 403
