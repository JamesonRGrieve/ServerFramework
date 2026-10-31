# SPDX-License-Identifier: AGPL-3.0-or-later
"""Live sync: a signed-in user's devices exchanging messages in real time.

A user's open windows and devices join a named **channel** (a teleprompter's
``prompter``, a slideshow's ``deck``, ...) and every message one device posts
reaches the user's other devices on that channel, as server-sent events. One
device per channel is the **main** device: the first to join, or whichever
last claimed control, so one window can drive the others and a phone can take
over from a laptop. Channels are private to their user; no one else's device
can join or post to them.

Routes on ``/v1/live``:

- ``GET /{channel}/events?client_id=&label=``: join the channel and receive
  its messages as ``text/event-stream``. Each event is a JSON message
  ``{"type", "sender", "data"}``; the server's own ``presence`` messages
  carry ``{"devices": [...]}`` whenever someone joins, leaves or takes control.
- ``POST /{channel}`` ``{client_id, type, data}``: send a message to the
  channel's other devices. The ``claim`` type makes the sender the main device.
- ``GET /{channel}/devices``: the devices on the channel now, and which is main.

The device registry is a table, so which device is main survives reconnects;
delivery goes through the framework's event bus, so messages cross server
processes once a broker-backed bus is wired (``database_memory`` wires Valkey).
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, ClassVar

import anyio
from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel as ResponseModel
from pydantic import Field, field_validator

from zephyrex.lib.CustomRoute import ExposeIn, custom_route
from zephyrex.lib.Environment import env
from zephyrex.lib.Logging import logger
from zephyrex.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    ModelMeta,
    StringSearchModel,
    UpdateMixinModel,
)
from zephyrex.logic.EventBus import get_event_bus
from zephyrex.pydantic2.fastapi import RouterMixin
from zephyrex.pydantic2.registry import BaseModel

LIVE_PREFIX = "/v1/live"
# Channel and client ids are short, URL-safe names chosen by the client.
NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_LABEL_LENGTH = 120
# A message's JSON-encoded data may be at most this many bytes.
MAX_MESSAGE_BYTES = 64 * 1024
# Message types a device may send; the server's own are reserved.
MESSAGE_TYPE_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
CLAIM_TYPE = "claim"
PRESENCE_TYPE = "presence"
RESERVED_TYPES = frozenset({PRESENCE_TYPE})
# How often an idle stream sends a comment, so proxies keep it open and a
# vanished client is noticed.
KEEPALIVE_SECONDS = 15.0
# How many undelivered messages one device may queue before it is dropped
# as too slow to keep up (it reconnects and resynchronises).
DEVICE_QUEUE_SIZE = 256


def _valid_name(value: str, what: str) -> str:
    if not NAME_PATTERN.match(value):
        raise HTTPException(
            status_code=422,
            detail=f"{what} must be 1-64 letters, digits, '-' or '_'",
        )
    return value


class LiveDeviceModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    metaclass=ModelMeta,
):
    """A device (browser window, phone, display) of a user on a channel."""

    Manager: ClassVar[type[LiveSyncManager]]
    channel: str = Field(..., description="The channel the device joined")
    client_id: str = Field(..., description="The device's own id, chosen by the client")
    label: str | None = Field(None, description="What the user calls the device")
    is_main: bool = Field(False, description="Whether this device drives the channel")
    last_seen_at: datetime | None = Field(
        None, description="When the device last joined"
    )
    table_comment: ClassVar[str] = (
        "Live sync devices: each user's windows and devices per channel"
    )

    class Create(BaseModel):
        channel: str
        client_id: str
        label: str | None = None
        is_main: bool = False
        last_seen_at: datetime | None = None

    class Update(BaseModel):
        label: str | None = None
        is_main: bool | None = None
        last_seen_at: datetime | None = None

    class Search(ApplicationModel.Search):
        channel: StringSearchModel | None = None
        client_id: StringSearchModel | None = None


class LiveDeviceEntry(ResponseModel):
    client_id: str
    label: str | None = None
    main: bool
    connected: bool


class LiveDeviceList(ResponseModel):
    devices: list[LiveDeviceEntry]


class LivePost(ResponseModel):
    client_id: str
    type: str
    data: Any = None

    @field_validator("client_id")
    @classmethod
    def _client_id(cls, value: str) -> str:
        if not NAME_PATTERN.match(value):
            raise ValueError("client_id must be 1-64 letters, digits, '-' or '_'")
        return value

    @field_validator("type")
    @classmethod
    def _type(cls, value: str) -> str:
        if not MESSAGE_TYPE_PATTERN.match(value):
            raise ValueError("type must be a lowercase name of at most 64 characters")
        if value in RESERVED_TYPES:
            raise ValueError(f"{value!r} is a server message type")
        return value


class LivePosted(ResponseModel):
    delivered: bool


class LiveMessageEvent(ResponseModel):
    """A message on a user's channel, as it crosses the event bus."""

    user_id: str
    channel: str
    sender: str
    type: str
    data: Any = None


@dataclass
class _Subscriber:
    client_id: str
    queue: asyncio.Queue[str | None]
    loop: asyncio.AbstractEventLoop


@dataclass
class LiveHub:
    """The streams open in this process, keyed by (user, channel).

    Every message reaches the hub through the event bus, which hands it to
    the matching local streams; a stream that cannot keep up is closed rather
    than allowed to grow without bound.
    """

    _streams: dict[tuple[str, str], dict[str, _Subscriber]] = field(
        default_factory=dict
    )
    _subscribed_bus: Any = None

    def _ensure_bus_subscription(self) -> None:
        bus = get_event_bus()
        if self._subscribed_bus is not bus:
            bus.subscribe(LiveMessageEvent, self.deliver)
            self._subscribed_bus = bus

    def connected(self, user_id: str, channel: str) -> set[str]:
        return set(self._streams.get((user_id, channel), {}))

    def join(
        self, user_id: str, channel: str, client_id: str
    ) -> asyncio.Queue[str | None]:
        self._ensure_bus_subscription()
        streams = self._streams.setdefault((user_id, channel), {})
        if client_id in streams:
            raise HTTPException(
                status_code=409,
                detail="That device is already connected to the channel",
            )
        queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=DEVICE_QUEUE_SIZE)
        streams[client_id] = _Subscriber(client_id, queue, asyncio.get_running_loop())
        return queue

    def leave(self, user_id: str, channel: str, client_id: str) -> None:
        streams = self._streams.get((user_id, channel))
        if streams is None:
            return
        streams.pop(client_id, None)
        if not streams:
            del self._streams[(user_id, channel)]

    def deliver(self, message: ResponseModel) -> None:
        """Hand a bus message to this process's streams on its channel, except the
        sender's. A broker-backed bus may hand over its own model of the payload."""
        event = (
            message
            if isinstance(message, LiveMessageEvent)
            else LiveMessageEvent.model_validate(message.model_dump())
        )
        frame = f"data: {json.dumps({'type': event.type, 'sender': event.sender, 'data': event.data})}\n\n"
        for subscriber in list(
            self._streams.get((event.user_id, event.channel), {}).values()
        ):
            if subscriber.client_id == event.sender:
                continue
            subscriber.loop.call_soon_threadsafe(
                self._offer, event.user_id, event.channel, subscriber, frame
            )

    def _offer(
        self, user_id: str, channel: str, subscriber: _Subscriber, frame: str
    ) -> None:
        try:
            subscriber.queue.put_nowait(frame)
        except asyncio.QueueFull:
            logger.warning(
                "live_sync: dropping a device that fell %s messages behind",
                DEVICE_QUEUE_SIZE,
            )
            self.leave(user_id, channel, subscriber.client_id)
            # Drain one frame so the close marker fits; the stream ends anyway.
            subscriber.queue.get_nowait()
            subscriber.queue.put_nowait(None)


HUB = LiveHub()


class LiveSyncManager(AbstractBLLManager, RouterMixin):
    _model = LiveDeviceModel
    prefix: ClassVar[str | None] = LIVE_PREFIX
    tags: ClassVar[list[str] | None] = ["Live Sync"]
    routes_to_register: ClassVar[list[Any] | None] = []

    @property
    def _user_id(self) -> str:
        return str(self.requester.id)

    def _devices(self, channel: str) -> list[Any]:
        DB = LiveDeviceModel.DB(self.model_registry.DB.manager.Base)
        rows = DB.list(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            filters=[
                DB.created_by_user_id == self._user_id,
                DB.channel == channel,
                DB.deleted_at.is_(None),
            ],
            return_type="dto",
            override_dto=LiveDeviceModel,
        )
        return list(rows or [])

    def _as_root(self) -> LiveSyncManager:
        return LiveSyncManager(
            requester_id=env("ROOT_ID"), model_registry=self.model_registry
        )

    def device_list(self, channel: str) -> LiveDeviceList:
        connected = HUB.connected(self._user_id, channel)
        return LiveDeviceList(
            devices=[
                LiveDeviceEntry(
                    client_id=device.client_id,
                    label=device.label,
                    main=bool(device.is_main),
                    connected=device.client_id in connected,
                )
                for device in self._devices(channel)
                if device.client_id in connected
            ]
        )

    def _register(self, channel: str, client_id: str, label: str | None) -> None:
        """Record the device as seen now; it becomes main when no connected device is."""
        devices = self._devices(channel)
        connected = HUB.connected(self._user_id, channel) - {client_id}
        has_main = any(d.is_main and d.client_id in connected for d in devices)
        mine = next((d for d in devices if d.client_id == client_id), None)
        now = datetime.now(UTC)
        if mine is None:
            self.create(
                channel=channel,
                client_id=client_id,
                label=label,
                is_main=not has_main,
                last_seen_at=now,
            )
            return
        changes: dict[str, Any] = {
            "last_seen_at": now,
            "is_main": bool(mine.is_main) or not has_main,
        }
        if label:
            changes["label"] = label
        self._as_root().update(id=mine.id, **changes)

    def _set_main(self, channel: str, client_id: str | None) -> None:
        """Make ``client_id`` the channel's only main device (``None``: the
        longest-connected device left, if any)."""
        devices = self._devices(channel)
        connected = HUB.connected(self._user_id, channel)
        if client_id is None:
            live = sorted(
                (d for d in devices if d.client_id in connected),
                key=lambda d: d.last_seen_at or datetime.min.replace(tzinfo=UTC),
            )
            client_id = live[0].client_id if live else None
        root = self._as_root()
        for device in devices:
            should = device.client_id == client_id
            if bool(device.is_main) != should:
                root.update(id=device.id, is_main=should)

    async def _publish(
        self, channel: str, sender: str, message_type: str, data: Any
    ) -> None:
        await get_event_bus().publish(
            LiveMessageEvent(
                user_id=self._user_id,
                channel=channel,
                sender=sender,
                type=message_type,
                data=data,
            )
        )

    async def _announce(self, channel: str) -> None:
        devices = self.device_list(channel).model_dump()["devices"]
        await self._publish(channel, "", PRESENCE_TYPE, {"devices": devices})

    async def _stream(
        self,
        request: Request,
        channel: str,
        client_id: str,
        queue: asyncio.Queue[str | None],
    ) -> AsyncIterator[str]:
        try:
            yield ": connected\n\n"
            await self._announce(channel)
            while True:
                try:
                    frame = await asyncio.wait_for(
                        queue.get(), timeout=KEEPALIVE_SECONDS
                    )
                except TimeoutError:
                    if await request.is_disconnected():
                        return
                    yield ": keepalive\n\n"
                    continue
                if frame is None:
                    return
                yield frame
        finally:
            HUB.leave(self._user_id, channel, client_id)
            # A disconnect cancels the stream, and anyio re-raises that at every
            # await in a cancelled scope: shield the hand-over announcement.
            with anyio.CancelScope(shield=True):
                was_main = any(
                    d.client_id == client_id and d.is_main
                    for d in self._devices(channel)
                )
                if was_main:
                    self._set_main(channel, None)
                await self._announce(channel)

    @custom_route(
        method="GET",
        path="/{channel}/events",
        authentication_type="jwt",
        expose_in=(ExposeIn.REST,),
        response_class=StreamingResponse,
        openapi_tags=("Live Sync",),
        summary="Join a channel and receive its messages as server-sent events",
    )
    async def events_route(
        self, request: Request, channel: str, client_id: str, label: str | None = None
    ) -> StreamingResponse:
        _valid_name(channel, "channel")
        _valid_name(client_id, "client_id")
        if label is not None and len(label) > MAX_LABEL_LENGTH:
            raise HTTPException(
                status_code=422,
                detail=f"label may be at most {MAX_LABEL_LENGTH} characters",
            )
        queue = HUB.join(self._user_id, channel, client_id)
        try:
            self._register(channel, client_id, label)
        except Exception:
            HUB.leave(self._user_id, channel, client_id)
            raise
        return StreamingResponse(
            self._stream(request, channel, client_id, queue),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @custom_route(
        method="POST",
        path="/{channel}",
        input_model=LivePost,
        output_model=LivePosted,
        authentication_type="jwt",
        expose_in=(ExposeIn.REST,),
        openapi_tags=("Live Sync",),
        summary="Send a message to your other devices on a channel ('claim' takes control)",
    )
    async def post_route(self, channel: str, body: LivePost) -> LivePosted:
        _valid_name(channel, "channel")
        if len(json.dumps(body.data)) > MAX_MESSAGE_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"A message's data may be at most {MAX_MESSAGE_BYTES} bytes",
            )
        if body.client_id not in HUB.connected(self._user_id, channel) and not any(
            d.client_id == body.client_id for d in self._devices(channel)
        ):
            raise HTTPException(
                status_code=409, detail="Join the channel before sending to it"
            )
        if body.type == CLAIM_TYPE:
            self._set_main(channel, body.client_id)
            await self._announce(channel)
            return LivePosted(delivered=True)
        await self._publish(channel, body.client_id, body.type, body.data)
        return LivePosted(delivered=True)

    @custom_route(
        method="GET",
        path="/{channel}/devices",
        output_model=LiveDeviceList,
        authentication_type="jwt",
        expose_in=(ExposeIn.REST,),
        openapi_tags=("Live Sync",),
        summary="Your devices connected to a channel, and which one is main",
    )
    def devices_route(self, channel: str) -> LiveDeviceList:
        return self.device_list(_valid_name(channel, "channel"))
