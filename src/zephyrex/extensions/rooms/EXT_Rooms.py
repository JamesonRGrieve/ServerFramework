# SPDX-License-Identifier: AGPL-3.0-or-later
"""Rooms: live change streams for everyone who may see a record.

See ``BLL_Rooms`` for rooms, changes and the routes on ``/v1/room``, and
``lib.Rooms.room_of`` for how an extension maps its records to rooms.
"""

from typing import Any, ClassVar

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension
from zephyrex.lib.Dependencies import Dependencies


class EXT_Rooms(AbstractStaticExtension):
    """Several users watching one record (a scene, a table's conversation)
    hear, as it happens, when the records that belong to it change."""

    name: ClassVar[str] = "rooms"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Live change streams for a record's viewers: who may see a record hears when the records in its room change"
    )

    _env: ClassVar[dict[str, Any]] = {}
    dependencies: ClassVar[Dependencies] = Dependencies([])
    _abilities: ClassVar[set[str]] = set()
    _providers: ClassVar[list[Any]] = []
