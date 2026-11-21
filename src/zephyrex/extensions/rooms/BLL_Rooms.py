# SPDX-License-Identifier: AGPL-3.0-or-later
"""Rooms: everyone who may see a record hears, live, when the records that
belong to it change.

A **room** is a record, named as ``lib.RecordTarget`` names one: its type
and id (``scene`` + a scene's id, ``conversation`` + a table's conversation).
Whoever may VIEW that record, by its own manager, may join the room.
Extensions say which room each of their records belongs to with
``lib.Rooms.room_of`` (a token to its scene, a seat to its table's
conversation); every create, update or delete through those managers then
lands here as a **change** ``{id, room, record {type, id}, op, etag,
changed_at}``. A change never carries the record's data: clients refetch it
through the API, under their own permissions (a hidden token's move shows as
"token X changed", never where it went).

Changes are kept for ``RETENTION`` in ``room_events`` so a client that
reconnects catches up (``Last-Event-ID`` or ``?since=``); one whose cursor is
older, or too far behind (``REPLAY_LIMIT``), gets a ``reset`` and refetches.
Delivery crosses server processes through the framework's event bus once a
broker-backed one is wired (``database_memory`` wires Valkey).

Routes on ``/v1/room``:

- ``GET /{room_type}/{room_id}/events?since=``: join the room and receive its
  changes as ``text/event-stream`` (``id:`` = the change id, ``event:
  change``; ``event: reset`` to refetch; ``event: revoked`` when the joiner
  can no longer see the room, rechecked every ``ACCESS_RECHECK_SECONDS``).
- ``GET /{room_type}/{room_id}/changes?after=&limit=``: the same changes as a
  JSON page, for polling; without ``after`` it returns no changes and the
  cursor to start from.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, ClassVar, Optional

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel as ResponseModel
from pydantic import Field

from zephyrex.lib.CustomRoute import ExposeIn, custom_route
from zephyrex.lib.Environment import env
from zephyrex.lib.EventStream import event_stream
from zephyrex.lib.Logging import logger
from zephyrex.lib.Preconditions import entity_etag
from zephyrex.lib.RecordTarget import TYPE_PATTERN, can_view, record_type_of
from zephyrex.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    ModelMeta,
    StringSearchModel,
    UpdateMixinModel,
    _cache_sync_run,
)
from zephyrex.logic.EventBus import get_event_bus
from zephyrex.pydantic2.fastapi import RouterMixin
from zephyrex.pydantic2.registry import BaseModel
from zephyrex.pydantic2.sqlalchemy.indexes import TableIndex

ROOMS_PREFIX = "/v1/room"
ROOM_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# How long changes are kept for clients catching up after a reconnect.
RETENTION = timedelta(hours=1)
# At most this many missed changes are replayed; further behind, a reset.
REPLAY_LIMIT = 500
CHANGES_LIMIT_DEFAULT = 100
CHANGES_LIMIT_MAX = 500
# An idle stream sends a comment this often, so proxies keep it open.
KEEPALIVE_SECONDS = 15.0
# A stream rechecks that its joiner may still see the room this often.
ACCESS_RECHECK_SECONDS = 60.0
# A stream that falls this many changes behind is closed (it reconnects).
STREAM_QUEUE_SIZE = 512
OPS = ("created", "updated", "deleted")
ROOM_INDEX = "ix_room_events_room"


class RoomEventModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    metaclass=ModelMeta,
):
    """One change in a room: which record, what happened, and its version."""

    Manager: ClassVar[type[RoomManager]]
    room_type: str = Field(..., description="The room record's type")
    room_id: str = Field(..., description="The room record's id")
    record_type: str = Field(..., description="The changed record's type")
    record_id: str = Field(..., description="The changed record's id")
    op: str = Field(..., description="created, updated or deleted")
    etag: Optional[str] = Field(None, description="The record's version after it")
    table_comment: ClassVar[str] = (
        "Room changes: which record in which room changed, kept briefly for catch-up"
    )
    table_indexes: ClassVar[tuple[TableIndex, ...]] = (
        TableIndex(name=ROOM_INDEX, columns=("room_type", "room_id", "created_at")),
    )

    class Create(BaseModel):
        room_type: str
        room_id: str
        record_type: str
        record_id: str
        op: str
        etag: Optional[str] = None

    class Update(BaseModel):
        pass

    class Search(ApplicationModel.Search):
        room_type: Optional[StringSearchModel] = None
        room_id: Optional[StringSearchModel] = None


class RoomRef(ResponseModel):
    type: str
    id: str


class RoomChange(ResponseModel):
    """A change as clients receive it (and as it crosses the event bus)."""

    id: str
    room: RoomRef
    record: RoomRef
    op: str
    etag: Optional[str] = None
    changed_at: str


class RoomChangePage(ResponseModel):
    changes: list[RoomChange]
    next: Optional[str] = Field(None, description="The cursor for the next page")
    reset: bool = Field(
        False, description="The cursor is too old or too far behind: refetch"
    )


def _change(row: Any) -> RoomChange:
    return RoomChange(
        id=str(row.id),
        room=RoomRef(type=row.room_type, id=row.room_id),
        record=RoomRef(type=row.record_type, id=row.record_id),
        op=row.op,
        etag=row.etag,
        changed_at=row.created_at.isoformat() if row.created_at else "",
    )


def _frame(change: RoomChange) -> str:
    return f"id: {change.id}\nevent: change\ndata: {change.model_dump_json()}\n\n"


def _control(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@dataclass
class _Stream:
    queue: asyncio.Queue[Optional[str]]
    loop: asyncio.AbstractEventLoop


@dataclass
class RoomHub:
    """The streams open in this process, by room. Changes reach it through the
    event bus; a stream that can't keep up is closed rather than grown."""

    _streams: dict[tuple[str, str], dict[str, _Stream]] = field(default_factory=dict)
    _subscribed_bus: Any = None

    def _ensure_bus_subscription(self) -> None:
        bus = get_event_bus()
        if self._subscribed_bus is not bus:
            bus.subscribe(RoomChange, self.deliver)
            self._subscribed_bus = bus

    def join(self, room: tuple[str, str]) -> tuple[str, asyncio.Queue[Optional[str]]]:
        self._ensure_bus_subscription()
        stream_id = uuid.uuid4().hex
        queue: asyncio.Queue[Optional[str]] = asyncio.Queue(maxsize=STREAM_QUEUE_SIZE)
        self._streams.setdefault(room, {})[stream_id] = _Stream(
            queue, asyncio.get_running_loop()
        )
        return stream_id, queue

    def leave(self, room: tuple[str, str], stream_id: str) -> None:
        streams = self._streams.get(room)
        if streams is None:
            return
        streams.pop(stream_id, None)
        if not streams:
            del self._streams[room]

    def listeners(self, room: tuple[str, str]) -> int:
        return len(self._streams.get(room, {}))

    def deliver(self, message: ResponseModel) -> None:
        """Hand a change from the bus to this process's streams in its room. A
        broker-backed bus may hand over its own model of the payload."""
        change = (
            message
            if isinstance(message, RoomChange)
            else RoomChange.model_validate(message.model_dump())
        )
        frame = _frame(change)
        room = (change.room.type, change.room.id)
        for stream_id, stream in list(self._streams.get(room, {}).items()):
            stream.loop.call_soon_threadsafe(
                self._offer, room, stream_id, stream, frame
            )

    def _offer(
        self, room: tuple[str, str], stream_id: str, stream: _Stream, frame: str
    ) -> None:
        try:
            stream.queue.put_nowait(frame)
        except asyncio.QueueFull:
            logger.warning(
                "rooms: closing a stream that fell %s changes behind", STREAM_QUEUE_SIZE
            )
            self.leave(room, stream_id)
            stream.queue.get_nowait()
            stream.queue.put_nowait(None)


HUB = RoomHub()


def record_change(manager: Any, room: tuple[str, str], record: Any, op: str) -> None:
    """Log one change in ``room`` (pruning what is past ``RETENTION``) and
    publish it to every process's streams. Called by ``lib.Rooms``'s hooks
    after the write has committed."""
    registry = manager.model_registry
    row = RoomManager(requester_id=env("SYSTEM_ID"), model_registry=registry).create(
        room_type=room[0],
        room_id=room[1],
        record_type=record_type_of(type(manager).Model),
        record_id=str(record.id),
        op=op,
        etag=entity_etag(record) if op != "deleted" else None,
    )
    _prune(registry, room, row.created_at)
    _cache_sync_run(get_event_bus().publish(_change(row)))


def _prune(registry: Any, room: tuple[str, str], now: Any) -> None:
    """Remove the room's changes older than ``RETENTION`` (a log, not
    records: removed outright, not soft-deleted)."""
    if now is None:
        return
    DB = RoomEventModel.DB(registry.DB.manager.Base)
    session = registry.DB.session()
    try:
        session.query(DB).filter(
            DB.room_type == room[0],
            DB.room_id == room[1],
            DB.created_at < now - RETENTION,
        ).delete(synchronize_session=False)
        session.commit()
    finally:
        session.close()


def _valid_room(room_type: str, room_id: str) -> tuple[str, str]:
    if not TYPE_PATTERN.match(room_type or ""):
        raise HTTPException(status_code=422, detail="room_type is not a record type")
    if not ROOM_ID_PATTERN.match(room_id or ""):
        raise HTTPException(status_code=422, detail="room_id is not a record id")
    return room_type, room_id


class RoomManager(AbstractBLLManager, RouterMixin):
    _model = RoomEventModel
    prefix: ClassVar[Optional[str]] = ROOMS_PREFIX
    tags: ClassVar[Optional[list[str]]] = ["Rooms"]
    routes_to_register: ClassVar[Optional[list[Any]]] = []

    def _may_view(self, room: tuple[str, str]) -> bool:
        return can_view(self.model_registry, str(self.requester.id), room[0], room[1])

    def _require_view(self, room: tuple[str, str]) -> None:
        """404 for a room the requester can't see, whether or not it exists."""
        if not self._may_view(room):
            raise HTTPException(status_code=404, detail="Room not found")

    def _rows(self, room: tuple[str, str]) -> Any:
        DB = RoomEventModel.DB(self.model_registry.DB.manager.Base)
        session = self.model_registry.DB.session()
        query = (
            session.query(DB)
            .filter(DB.room_type == room[0], DB.room_id == room[1])
            .order_by(DB.created_at, DB.id)
        )
        return session, DB, query

    def _after(
        self,
        room: tuple[str, str],
        cursor: Optional[str],
        limit: int,
        *,
        overflow_resets: bool,
    ) -> tuple[list[RoomChange], bool]:
        """The room's changes after ``cursor`` (oldest first, at most
        ``limit``), and whether the client must reset instead: its cursor is
        unknown here (expired, or another room's), or, with ``overflow_resets``
        (a stream's catch-up), more than ``limit`` changes followed it. A
        polling page just stops at ``limit``."""
        session, DB, query = self._rows(room)
        try:
            if cursor is None:
                return [], False
            anchor = query.filter(DB.id == cursor).first()
            if anchor is None:
                return [], True
            rows = (
                query.filter(
                    (DB.created_at > anchor.created_at)
                    | ((DB.created_at == anchor.created_at) & (DB.id > anchor.id))
                )
                .limit(limit + 1)
                .all()
            )
            if len(rows) > limit:
                if overflow_resets:
                    return [], True
                rows = rows[:limit]
            return [_change(r) for r in rows], False
        finally:
            session.close()

    def _latest(self, room: tuple[str, str]) -> Optional[str]:
        session, DB, query = self._rows(room)
        try:
            row = (
                query.order_by(None)
                .order_by(DB.created_at.desc(), DB.id.desc())
                .first()
            )
            return str(row.id) if row is not None else None
        finally:
            session.close()

    async def _stream(
        self,
        request: Request,
        room: tuple[str, str],
        stream_id: str,
        queue: asyncio.Queue[Optional[str]],
        replay: list[RoomChange],
        reset: bool,
    ) -> AsyncIterator[str]:
        try:
            yield ": connected\n\n"
            if reset:
                yield _control("reset", {"reason": "cursor expired or too far behind"})
            seen = {change.id for change in replay}
            for change in replay:
                yield _frame(change)
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
                    if not self._may_view(room):
                        yield _control(
                            "revoked", {"room": {"type": room[0], "id": room[1]}}
                        )
                        return
                if seen and frame.startswith("id: "):
                    change_id = frame[4 : frame.index("\n")]
                    if change_id in seen:
                        seen.discard(change_id)
                        continue
                yield frame
        finally:
            HUB.leave(room, stream_id)

    @custom_route(
        method="GET",
        path="/{room_type}/{room_id}/events",
        authentication_type="jwt",
        expose_in=(ExposeIn.REST,),
        response_class=StreamingResponse,
        openapi_tags=("Rooms",),
        summary="Join a record's room and receive its changes as server-sent events (Last-Event-ID or ?since= catches up)",
    )
    async def events_route(
        self,
        request: Request,
        room_type: str,
        room_id: str,
        since: Optional[str] = None,
    ) -> StreamingResponse:
        room = _valid_room(room_type, room_id)
        self._require_view(room)
        cursor = request.headers.get("last-event-id") or since
        # Join before reading the log, so nothing published in between is lost;
        # the stream drops a live change it has already replayed.
        stream_id, queue = HUB.join(room)
        try:
            replay, reset = self._after(
                room, cursor, REPLAY_LIMIT, overflow_resets=True
            )
        except Exception:
            HUB.leave(room, stream_id)
            raise
        return event_stream(
            self._stream(request, room, stream_id, queue, replay, reset)
        )

    @custom_route(
        method="GET",
        path="/{room_type}/{room_id}/changes",
        output_model=RoomChangePage,
        authentication_type="jwt",
        expose_in=(ExposeIn.REST,),
        openapi_tags=("Rooms",),
        summary="A record's room changes after a cursor, for polling (no cursor: where to start)",
    )
    def changes_route(
        self,
        room_type: str,
        room_id: str,
        after: Optional[str] = None,
        limit: int = CHANGES_LIMIT_DEFAULT,
    ) -> RoomChangePage:
        room = _valid_room(room_type, room_id)
        if not 1 <= limit <= CHANGES_LIMIT_MAX:
            raise HTTPException(
                status_code=422, detail=f"limit is 1 to {CHANGES_LIMIT_MAX}"
            )
        self._require_view(room)
        if after is None:
            return RoomChangePage(changes=[], next=self._latest(room))
        changes, reset = self._after(room, after, limit, overflow_resets=False)
        if reset:
            return RoomChangePage(changes=[], next=self._latest(room), reset=True)
        return RoomChangePage(
            changes=changes, next=changes[-1].id if changes else after
        )
