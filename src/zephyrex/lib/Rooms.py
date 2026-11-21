# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which room a record's changes go to, for the ``rooms`` extension's live
streams.

An extension maps a manager's records to their room(s) once, at import:

    room_of(SceneTokenManager, lambda token: ("scene", token.scene_id))

A room is itself a record, named as ``lib.RecordTarget`` names one (its
type and id); whoever may VIEW that record may join the room. After each
create, update or delete through the manager, every room the record names
gets a change: ``{record type, id, op, etag}``, never the record's data, so
clients refetch it through the API under their own permissions. A deleted
record's rooms are read before it goes.

Nothing happens unless the app loaded the ``rooms`` extension (it holds the
change log and the streams), so an extension may declare its rooms whether
or not the app uses them. Dry runs skip AFTER hooks, so they publish nothing.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any, Union

from zephyrex.lib.Logging import logger

ROOMS_EXTENSION = "rooms"
Room = tuple[str, str]
RoomsOf = Callable[[Any], Union[None, Room, Iterable[Room]]]
_DELETED_KEY = "rooms.deleted"
# The ops a change carries.
CREATED, UPDATED, DELETED = "created", "updated", "deleted"


def room_of(manager_class: type, rooms: RoomsOf) -> None:
    """Publish the changes ``manager_class`` makes to the room(s) ``rooms``
    names for each record: ``None``, one ``(type, id)``, or several."""
    registry = manager_class._hook_registry  # type: ignore[attr-defined]
    registry.register_hook(
        manager_class, "create", "after", _after_write(CREATED, rooms)
    )
    registry.register_hook(
        manager_class, "update", "after", _after_write(UPDATED, rooms)
    )
    registry.register_hook(manager_class, "delete", "before", _before_delete(rooms))
    registry.register_hook(manager_class, "delete", "after", _after_delete)


def _rooms(rooms: RoomsOf, record: Any) -> list[Room]:
    named = rooms(record)
    if named is None:
        return []
    if isinstance(named, tuple) and len(named) == 2 and isinstance(named[0], str):
        named = [named]
    return [(str(t), str(i)) for t, i in named if t and i]


def _active(manager: Any) -> bool:
    extensions = getattr(manager.model_registry, "extension_registry", None)
    csv = getattr(extensions, "csv", "") or ""
    return ROOMS_EXTENSION in csv.split(",")


def _after_write(op: str, rooms: RoomsOf) -> Callable[[Any], None]:
    def publish_change(context: Any) -> None:
        if not _active(context.manager):
            return
        results = (
            context.result if isinstance(context.result, list) else [context.result]
        )
        for record in results:
            if record is None:
                continue
            for room in _rooms(rooms, record):
                publish(context.manager, room, record, op)

    publish_change.__name__ = publish_change.__qualname__ = f"rooms_{op}"
    return publish_change


def _before_delete(rooms: RoomsOf) -> Callable[[Any], None]:
    def note_rooms(context: Any) -> None:
        if not _active(context.manager):
            return
        record_id = context.kwargs.get("id") or (
            context.args[0] if context.args else None
        )
        if record_id is None:
            return
        try:
            record = context.manager.get(id=record_id)
        except Exception as err:  # the delete itself reports a missing record
            logger.debug(
                "rooms: no record %s to note before delete: %s", record_id, err
            )
            return
        context.condition_data[_DELETED_KEY] = (record, _rooms(rooms, record))

    return note_rooms


def _after_delete(context: Any) -> None:
    noted = context.condition_data.get(_DELETED_KEY)
    if noted is None:
        return
    record, rooms = noted
    for room in rooms:
        publish(context.manager, room, record, DELETED)


def publish(manager: Any, room: Room, record: Any, op: str) -> None:
    """Record the change in the room's log and hand it to every process's
    streams (``rooms`` extension)."""
    from zephyrex.extensions.rooms.BLL_Rooms import record_change

    record_change(manager, room, record, op)
