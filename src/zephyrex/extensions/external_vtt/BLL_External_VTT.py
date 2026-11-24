# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Foundry world's live events, pushed by its foundry-webhooks module:
a roll, a chat line, a combat's turn, a token moved, a scene changed, an
actor updated. The app reacts through the event bus (``VTTEvent``, which a
copilot or an AI GM subscribes to).

``POST /v1/external_vtt/{world_id}/events`` takes one event
``{world, type, document_type, id, data, at}`` or a list of them, signed as
the framework signs inbound requests (``lib.SignedRequests``):
``X-Zephyrex-Timestamp`` (Unix seconds) and ``X-Zephyrex-Signature``
(``sha256=`` and the hex HMAC-SHA256 of ``"<timestamp>\\n"`` and the raw
body), keyed by the world instance's ``webhook_secret``. A request is taken
only within the replay window, and each signature once; every refusal is
the same 401. The route is meant for the internal network only (the
operator's ruling: no public route), and the world instance must be the
operator's (root or system scope).

``GET /v1/external_vtt/{world_id}/stream`` gives the world's campaign
members those events live, as ``text/event-stream``: ``event: vtt`` with
``{type, document_type, document_id, at}``. Like a room's change, a notice
never carries the event's data (a whisper, a blind roll or a hidden token's
move is still only "this changed"); the client refetches through the
backend's reads, under its own access. Only a member of the Foundry campaign
on that world may join (the wiki's ``campaign_backend``, as the backend's
reads ask it); anyone else gets the same 404 whether the world exists or
not. Access is rechecked every ``ACCESS_RECHECK_SECONDS`` (``event:
revoked``, then the stream ends), a stream that falls ``STREAM_QUEUE_SIZE``
notices behind is closed, and nothing is replayed: a client that reconnects
refetches what it shows.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, List, Mapping, Optional

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel as RouteModel
from pydantic import Field

from zephyrex.extensions.ExternalErrors import InvalidInputExternalError
from zephyrex.lib.ContentNegotiation import skip_negotiation
from zephyrex.lib.CustomRoute import ExposeIn, custom_route
from zephyrex.lib.Environment import env
from zephyrex.lib.EventStream import event_stream
from zephyrex.lib.Logging import logger
from zephyrex.lib.RequestBody import capped_body
from zephyrex.lib.SessionCookies import accept_cross_site_writes
from zephyrex.lib.SignedRequests import SignedRequests, lower_case_headers, unsigned
from zephyrex.logic.AbstractLogicManager import AbstractBLLManager
from zephyrex.logic.BLL_Providers import OPERATOR_SCOPES, ProviderInstanceModel
from zephyrex.logic.EventBus import get_event_bus
from zephyrex.pydantic2.fastapi import AuthType, RouterMixin, RouteType

EVENTS_PREFIX = "/v1/external_vtt"
EVENTS_PATH = "/{world_id}/events"
MIN_WEBHOOK_SECRET_LENGTH = 32
MAX_EVENTS = 100
MAX_EVENT_BYTES = 1024 * 1024
STREAM_PATH = "/{world_id}/stream"
KEEPALIVE_SECONDS = 15.0
ACCESS_RECHECK_SECONDS = 60.0
STREAM_QUEUE_SIZE = 512


def _signed_content(timestamp: str, headers: Mapping[str, str], body: bytes) -> bytes:
    """What an event delivery signs: its timestamp, a line feed, the body."""
    return timestamp.encode() + b"\n" + body


EVENT_SIGNING = SignedRequests(
    replay_scope="external_vtt:events", signed_content=_signed_content
)


def event_signature(secret: str, timestamp: str, body: bytes) -> str:
    """The ``X-Zephyrex-Signature`` of a delivery signed with ``secret``."""
    return EVENT_SIGNING.signature(secret, timestamp, {}, body)


class VTTEvent(RouteModel):
    """Something that happened at a Foundry world's table, as the app hears
    it: every server process gets it (``broadcast``), since what reacts to
    it may run in any of them. Ids are the backend's ``fvtt:`` ids."""

    broadcast: ClassVar[bool] = True

    world_id: str
    type: str
    document_type: Optional[str] = None
    document_id: Optional[str] = None
    data: Dict[str, Any] = Field(default_factory=dict)
    at: Optional[str] = None


class EventsTaken(RouteModel):
    events: int


class VTTNotice(RouteModel):
    """What a campaign member's stream says of a ``VTTEvent``: what happened
    and to which document, never the event's data."""

    type: str
    document_type: Optional[str] = None
    document_id: Optional[str] = None
    at: Optional[str] = None


def _notice_frame(event: VTTEvent) -> str:
    notice = VTTNotice(
        type=event.type,
        document_type=event.document_type,
        document_id=event.document_id,
        at=event.at,
    )
    return f"event: vtt\ndata: {notice.model_dump_json()}\n\n"


def _control(event: str, data: Dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@dataclass
class _Stream:
    queue: asyncio.Queue[Optional[str]]
    loop: asyncio.AbstractEventLoop


@dataclass
class WorldHub:
    """The streams open in this process, by world. Events reach it through
    the event bus (``VTTEvent`` is broadcast, so every process hears each
    one); a stream that can't keep up is closed rather than grown."""

    _streams: Dict[str, Dict[str, _Stream]] = field(default_factory=dict)
    _subscribed_bus: Any = None

    def _ensure_bus_subscription(self) -> None:
        bus = get_event_bus()
        if self._subscribed_bus is not bus:
            bus.subscribe(VTTEvent, self.deliver)
            self._subscribed_bus = bus

    def join(self, world_id: str) -> tuple[str, asyncio.Queue[Optional[str]]]:
        self._ensure_bus_subscription()
        stream_id = uuid.uuid4().hex
        queue: asyncio.Queue[Optional[str]] = asyncio.Queue(maxsize=STREAM_QUEUE_SIZE)
        self._streams.setdefault(world_id, {})[stream_id] = _Stream(
            queue, asyncio.get_running_loop()
        )
        return stream_id, queue

    def leave(self, world_id: str, stream_id: str) -> None:
        streams = self._streams.get(world_id)
        if streams is None:
            return
        streams.pop(stream_id, None)
        if not streams:
            del self._streams[world_id]

    def listeners(self, world_id: str) -> int:
        return len(self._streams.get(world_id, {}))

    def deliver(self, message: RouteModel) -> None:
        """Hand an event from the bus to this process's streams on its world.
        A broker-backed bus may hand over its own model of the payload."""
        event = (
            message
            if isinstance(message, VTTEvent)
            else VTTEvent.model_validate(message.model_dump())
        )
        frame = _notice_frame(event)
        for stream_id, stream in list(self._streams.get(event.world_id, {}).items()):
            stream.loop.call_soon_threadsafe(
                self._offer, event.world_id, stream_id, stream, frame
            )

    def _offer(
        self, world_id: str, stream_id: str, stream: _Stream, frame: str
    ) -> None:
        try:
            stream.queue.put_nowait(frame)
        except asyncio.QueueFull:
            logger.warning(
                "external_vtt: closing a stream that fell %s events behind",
                STREAM_QUEUE_SIZE,
            )
            self.leave(world_id, stream_id)
            stream.queue.get_nowait()
            stream.queue.put_nowait(None)


HUB = WorldHub()


def world_instance(
    model_registry: Any, world_id: str
) -> Optional[ProviderInstanceModel]:
    """The live, enabled, operator-scoped instance ``world_id`` of the
    Foundry provider, else None. Read as ROOT: a delivery has no session."""
    instance_db = ProviderInstanceModel.DB(model_registry.DB.manager.Base)
    rows: List[ProviderInstanceModel] = instance_db.list(
        requester_id=env("ROOT_ID"),
        model_registry=model_registry,
        return_type="dto",
        override_dto=ProviderInstanceModel,
        filters=[instance_db.id == world_id, instance_db.deleted_at.is_(None)],
    )
    if not rows:
        return None
    instance = rows[0]
    if instance.enabled is False or instance.scope not in OPERATOR_SCOPES:
        return None
    from zephyrex.extensions.external_vtt.EXT_External_VTT import EXT_External_VTT

    try:
        provider = EXT_External_VTT.provider_class_for(instance)
    except LookupError:
        return None
    return instance if provider.name == "foundry" else None


def webhook_secret(instance: ProviderInstanceModel) -> Optional[str]:
    from zephyrex.extensions.external_vtt.PRV_Foundry import PRV_Foundry

    secret = PRV_Foundry.setting(instance, "webhook_secret")
    if not secret:
        return None
    if len(secret) < MIN_WEBHOOK_SECRET_LENGTH:
        logger.warning(
            "external_vtt: world %s has a webhook_secret under %s characters; "
            "its events are refused",
            instance.id,
            MIN_WEBHOOK_SECRET_LENGTH,
        )
        return None
    return str(secret)


def events_of(world_id: str, body: bytes) -> List[VTTEvent]:
    """The delivery's events, their document ids as the backend's own."""
    from zephyrex.extensions.external_vtt.FoundryBackend import fvtt_id

    try:
        payload = json.loads(body)
    except ValueError:
        raise HTTPException(status_code=422, detail="The body is not JSON") from None
    items = payload if isinstance(payload, list) else [payload]
    if not items or len(items) > MAX_EVENTS:
        raise HTTPException(
            status_code=422, detail=f"A delivery holds 1-{MAX_EVENTS} events"
        )
    events: List[VTTEvent] = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("type"), str):
            raise HTTPException(status_code=422, detail="Each event names its type")
        uuid = item.get("id")
        data = item.get("data")
        events.append(
            VTTEvent(
                world_id=world_id,
                type=item["type"],
                document_type=item.get("document_type"),
                document_id=(
                    fvtt_id(world_id, uuid) if isinstance(uuid, str) and uuid else None
                ),
                data=data if isinstance(data, dict) else {},
                at=str(item["at"]) if item.get("at") is not None else None,
            )
        )
    return events


async def receive_events(
    model_registry: Any, world_id: str, request: Request
) -> EventsTaken:
    instance = world_instance(model_registry, world_id)
    secret = webhook_secret(instance) if instance is not None else None
    body = await capped_body(
        request,
        MAX_EVENT_BYTES,
        f"An event delivery is at most {MAX_EVENT_BYTES} bytes",
    )
    if secret is None:
        raise unsigned()
    EVENT_SIGNING.accept(
        secret, world_id, lower_case_headers(request.headers.items()), body
    )
    events = events_of(world_id, body)
    bus = get_event_bus()
    for event in events:
        await bus.publish(event)
    return EventsTaken(events=len(events))


class VTTEventsManager(AbstractBLLManager, RouterMixin):
    """The signed events endpoint: no table of its own."""

    prefix: ClassVar[Optional[str]] = EVENTS_PREFIX
    tags: ClassVar[Optional[List[str]]] = ["External VTT"]
    auth_type: ClassVar[AuthType] = AuthType.NONE
    routes_to_register: ClassVar[Optional[List[RouteType]]] = []

    @custom_route(
        method="POST",
        path=EVENTS_PATH,
        output_model=EventsTaken,
        authentication_type="none",
        raw_body=True,
        openapi_tags=("External VTT",),
        summary="Take a Foundry world's live events, signed with its webhook secret",
        description=(
            "One event {world, type, document_type, id, data, at} or a list "
            f"of at most {MAX_EVENTS}. X-Zephyrex-Timestamp is Unix seconds; "
            "X-Zephyrex-Signature is sha256=<hex HMAC-SHA256 of "
            "'<timestamp>\\n' + the raw body>, keyed by the world instance's "
            f"webhook_secret (at least {MIN_WEBHOOK_SECRET_LENGTH} characters, "
            "on a root- or system-scoped instance). 401 for a bad, stale or "
            "replayed signature."
        ),
        expose_in=(ExposeIn.REST,),
    )
    async def events_route(self, world_id: str, request: Request) -> EventsTaken:
        return await receive_events(self.model_registry, world_id, request)


class VTTStreamManager(AbstractBLLManager, RouterMixin):
    """A world's live events for its campaign's members: no table of its own."""

    prefix: ClassVar[Optional[str]] = EVENTS_PREFIX
    tags: ClassVar[Optional[List[str]]] = ["External VTT"]
    routes_to_register: ClassVar[Optional[List[RouteType]]] = []

    async def _is_member(self, world_id: str) -> bool:
        from zephyrex.extensions.external_vtt.FoundryBackend import campaign_world

        try:
            await campaign_world(str(self.requester.id), world_id=world_id)
        except InvalidInputExternalError:
            return False
        return True

    async def _stream(
        self,
        request: Request,
        world_id: str,
        stream_id: str,
        queue: asyncio.Queue[Optional[str]],
    ) -> AsyncIterator[str]:
        try:
            yield ": connected\n\n"
            checked = time.monotonic()
            while True:
                try:
                    frame = await asyncio.wait_for(
                        queue.get(), timeout=KEEPALIVE_SECONDS
                    )
                except TimeoutError:
                    if await request.is_disconnected():
                        return
                    frame = ": keepalive\n\n"
                if frame is None:
                    return
                if time.monotonic() - checked >= ACCESS_RECHECK_SECONDS:
                    checked = time.monotonic()
                    if not await self._is_member(world_id):
                        yield _control("revoked", {"world_id": world_id})
                        return
                yield frame
        finally:
            HUB.leave(world_id, stream_id)

    @custom_route(
        method="GET",
        path=STREAM_PATH,
        authentication_type="jwt",
        expose_in=(ExposeIn.REST,),
        response_class=StreamingResponse,
        openapi_tags=("External VTT",),
        summary="A Foundry world's live events, for its campaign's members, as server-sent events (what changed, never its data)",
    )
    async def stream_route(self, request: Request, world_id: str) -> StreamingResponse:
        if not await self._is_member(world_id):
            raise HTTPException(status_code=404, detail="World not found")
        stream_id, queue = HUB.join(world_id)
        return event_stream(self._stream(request, world_id, stream_id, queue))


# A world has no session here: a delivery is authenticated by its signature
# alone, never a cookie.
_EVENTS_ROUTE = re.escape(EVENTS_PREFIX) + EVENTS_PATH.replace("{world_id}", "[^/]+")
accept_cross_site_writes(_EVENTS_ROUTE)
skip_negotiation(_EVENTS_ROUTE)
