# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Foundry world's live events, signed by its foundry-webhooks module,
reach the app's event bus as ``VTTEvent``s with the backend's own ids; an
unsigned, stale, replayed or mis-keyed delivery, or one to a world that
isn't the operator's, is the same 401 and publishes nothing. A world's
campaign members hear those events live on its stream, as notices that never
carry the event's data; anyone else is a 404, and a member who leaves is
revoked."""

from __future__ import annotations

import asyncio
import hmac
import json
import time
from collections.abc import AsyncIterator
from typing import Any, Dict, Iterator, List, Optional, Set

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.external_vtt import BLL_External_VTT
from zephyrex.extensions.external_vtt import FoundryBackend as backend
from zephyrex.extensions.external_vtt.BLL_External_VTT import (
    EVENTS_PREFIX,
    HUB,
    VTTEvent,
    VTTStreamManager,
    event_signature,
)
from zephyrex.extensions.external_vtt.EXT_External_VTT import EXT_External_VTT
from zephyrex.lib.SignedRequests import SIGNATURE_HEADER, TIMESTAMP_HEADER
from zephyrex.logic.EventBus import get_event_bus
from zephyrex.testing.factories import provider_instance_as

SECRET = "s" * 40


def test_the_signature_is_the_documented_hmac():
    body = b'{"type": "roll"}'
    expected = hmac.new(SECRET.encode(), b"1700000000\n" + body, "sha256").hexdigest()
    assert event_signature(SECRET, "1700000000", body) == f"sha256={expected}"


def _headers(
    body: bytes, secret: str = SECRET, at: Optional[int] = None
) -> Dict[str, str]:
    moment = str(int(time.time()) if at is None else at)
    return {
        "Content-Type": "application/json",
        TIMESTAMP_HEADER: moment,
        SIGNATURE_HEADER: event_signature(secret, moment, body),
    }


@pytest.fixture
def heard() -> Iterator[List[VTTEvent]]:
    seen: List[VTTEvent] = []

    async def listen(event: Any) -> None:
        seen.append(event)

    get_event_bus().subscribe(VTTEvent, listen)
    yield seen


class TestEvents(ExtensionServerMixin):
    extension_class = EXT_External_VTT

    def _world(self, server: Any, scope: str = "root", **settings: str) -> Any:
        registry = server.app.state.model_registry
        return provider_instance_as(
            registry,
            "foundry",
            {"mcp_server": "foundry-solenne", "webhook_secret": SECRET, **settings},
            scope=scope,
        )

    def _deliver(
        self, server: Any, world_id: str, body: bytes, headers: Dict[str, str]
    ) -> Any:
        return server.post(
            f"{EVENTS_PREFIX}/{world_id}/events", content=body, headers=headers
        )

    def test_a_signed_delivery_reaches_the_bus(self, server, heard):
        world = self._world(server)
        body = json.dumps(
            [
                {
                    "world": "solenne",
                    "type": "combat.turn",
                    "document_type": "Combat",
                    "id": "Combat.k1",
                    "data": {"round": 2, "turn": 1},
                    "at": "2026-11-24T05:00:00Z",
                },
                {
                    "type": "chat.roll",
                    "document_type": "ChatMessage",
                    "id": "ChatMessage.m1",
                },
            ]
        ).encode()
        answered = self._deliver(server, world.id, body, _headers(body))
        assert answered.status_code == 200, answered.text
        assert answered.json() == {"events": 2}
        mine = [e for e in heard if e.world_id == world.id]
        assert [(e.type, e.document_id) for e in mine] == [
            ("combat.turn", f"fvtt:{world.id}:Combat.k1"),
            ("chat.roll", f"fvtt:{world.id}:ChatMessage.m1"),
        ]
        assert mine[0].data == {"round": 2, "turn": 1}

    def test_a_replayed_delivery_is_refused(self, server, heard):
        world = self._world(server)
        body = b'{"type": "scene.change"}'
        headers = _headers(body)
        assert self._deliver(server, world.id, body, headers).status_code == 200
        assert self._deliver(server, world.id, body, headers).status_code == 401
        assert len([e for e in heard if e.world_id == world.id]) == 1

    @pytest.mark.parametrize(
        "case",
        ["wrong-secret", "stale", "unsigned", "tampered"],
    )
    def test_an_unsigned_delivery_is_refused(self, server, heard, case):
        world = self._world(server)
        body = b'{"type": "token.move"}'
        headers = {
            "wrong-secret": _headers(body, secret="x" * 40),
            "stale": _headers(body, at=int(time.time()) - 3600),
            "unsigned": {"Content-Type": "application/json"},
            "tampered": _headers(b'{"type": "other"}'),
        }[case]
        assert self._deliver(server, world.id, body, headers).status_code == 401
        assert [e for e in heard if e.world_id == world.id] == []

    def test_a_users_own_world_takes_no_deliveries(self, server, heard):
        """Only the operator's worlds (root or system scope) take events."""
        from zephyrex.testing.factories import make_admin_a

        admin = make_admin_a(server)
        registry = server.app.state.model_registry
        world = provider_instance_as(
            registry,
            "foundry",
            {"mcp_server": "x", "webhook_secret": SECRET},
            requester_id=str(admin.id),
            scope="user",
        )
        body = b'{"type": "roll"}'
        assert self._deliver(server, world.id, body, _headers(body)).status_code == 401

    def test_a_short_secret_takes_no_deliveries(self, server, heard):
        world = self._world(server, webhook_secret="short")
        body = b'{"type": "roll"}'
        assert (
            self._deliver(
                server, world.id, body, _headers(body, secret="short")
            ).status_code
            == 401
        )

    def test_an_unknown_world_is_the_same_refusal(self, server, heard):
        body = b'{"type": "roll"}'
        assert (
            self._deliver(server, "no-such-world", body, _headers(body)).status_code
            == 401
        )

    def test_a_malformed_delivery_is_422_after_its_signature(self, server, heard):
        world = self._world(server)
        for body in (b"not json", b"[]", b'[{"no": "type"}]'):
            assert (
                self._deliver(server, world.id, body, _headers(body)).status_code == 422
            )


STREAM_WORLD = "solenne"
FRAME_TIMEOUT_SECONDS = 5.0


def _request() -> Request:
    """A request whose client stays connected (the test closes the stream)."""

    async def receive() -> Dict[str, Any]:
        await asyncio.Event().wait()
        return {"type": "http.disconnect"}

    return Request({"type": "http", "method": "GET", "headers": []}, receive=receive)


class Stream:
    """One open world stream, read frame by frame."""

    def __init__(self, body: AsyncIterator[Any]) -> None:
        self._body = body

    async def frame(self) -> str:
        frame = await asyncio.wait_for(
            self._body.__anext__(), timeout=FRAME_TIMEOUT_SECONDS
        )
        return str(frame)

    async def event(self) -> tuple[str, Dict[str, Any]]:
        """The next (event, data), skipping comments."""
        while True:
            frame = await self.frame()
            if frame.startswith(":"):
                continue
            fields = dict(
                line.split(": ", 1)
                for line in frame.strip().splitlines()
                if ": " in line
            )
            return fields["event"], json.loads(fields["data"])

    async def close(self) -> None:
        await self._body.aclose()  # type: ignore[attr-defined]


@pytest.fixture
def members(monkeypatch) -> Set[str]:
    """The wiki's answer for the stream's world: the requester's own campaign
    there while they are in it (``members``), none otherwise."""
    inside: Set[str] = set()

    async def campaign_backend(
        model_registry: Any,
        requester_id: str,
        campaign_id: Optional[str] = None,
        person_id: Optional[str] = None,
        world_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if world_id == STREAM_WORLD and requester_id in inside:
            return {"backend": "foundry", "world_id": STREAM_WORLD, "campaign_id": "c1"}
        return {"backend": "native", "world_id": None, "campaign_id": None}

    monkeypatch.setattr(backend, "_registry", lambda: None)
    monkeypatch.setattr(
        backend,
        "loaded_ability",
        lambda registry, ext, name: (
            campaign_backend if name == "campaign_backend" else None
        ),
    )
    return inside


class TestStream(ExtensionServerMixin):
    extension_class = EXT_External_VTT

    async def _join(self, model_registry: Any, user: Any) -> Stream:
        manager = VTTStreamManager(requester_id=user.id, model_registry=model_registry)
        response = await manager.stream_route(request=_request(), world_id=STREAM_WORLD)
        stream = Stream(response.body_iterator)
        assert await stream.frame() == ": connected\n\n"
        return stream

    async def test_only_the_campaigns_members_may_join(
        self, model_registry, admin_a, user_b, members
    ):
        members.add(str(admin_a.id))
        manager = VTTStreamManager(
            requester_id=user_b.id, model_registry=model_registry
        )
        with pytest.raises(HTTPException) as refused:
            await manager.stream_route(request=_request(), world_id=STREAM_WORLD)
        assert refused.value.status_code == 404
        with pytest.raises(HTTPException) as unknown:
            await manager.stream_route(request=_request(), world_id="no-such-world")
        assert unknown.value.status_code == 404

    async def test_an_event_reaches_the_member_without_its_data(
        self, model_registry, admin_a, members
    ):
        members.add(str(admin_a.id))
        stream = await self._join(model_registry, admin_a)
        bus = get_event_bus()
        await bus.publish(VTTEvent(world_id="elsewhere", type="roll"))
        await bus.publish(
            VTTEvent(
                world_id=STREAM_WORLD,
                type="chat",
                document_type="ChatMessage",
                document_id=f"fvtt:{STREAM_WORLD}:ChatMessage.m1",
                data={"content": "a whisper to the GM", "whisper": ["gm"]},
                at="2026-11-24T15:00:00Z",
            )
        )
        event, data = await stream.event()
        await stream.close()
        assert event == "vtt"
        assert data == {
            "type": "chat",
            "document_type": "ChatMessage",
            "document_id": f"fvtt:{STREAM_WORLD}:ChatMessage.m1",
            "at": "2026-11-24T15:00:00Z",
        }

    async def test_a_member_who_leaves_is_revoked(
        self, model_registry, admin_a, members, monkeypatch
    ):
        monkeypatch.setattr(BLL_External_VTT, "ACCESS_RECHECK_SECONDS", 0.0)
        members.add(str(admin_a.id))
        stream = await self._join(model_registry, admin_a)
        members.discard(str(admin_a.id))
        await get_event_bus().publish(VTTEvent(world_id=STREAM_WORLD, type="roll"))
        event, data = await stream.event()
        assert event == "revoked"
        assert data == {"world_id": STREAM_WORLD}
        with pytest.raises(StopAsyncIteration):
            await stream.frame()
        assert HUB.listeners(STREAM_WORLD) == 0

    async def test_a_closed_stream_leaves_the_world(
        self, model_registry, admin_a, members
    ):
        members.add(str(admin_a.id))
        stream = await self._join(model_registry, admin_a)
        assert HUB.listeners(STREAM_WORLD) == 1
        await stream.close()
        assert HUB.listeners(STREAM_WORLD) == 0
