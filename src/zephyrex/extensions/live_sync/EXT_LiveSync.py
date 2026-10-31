# SPDX-License-Identifier: AGPL-3.0-or-later
"""Live sync: real-time messages between a signed-in user's own devices.

See ``BLL_LiveSync`` for the channel model and the routes on ``/v1/live``.
"""

from typing import Any, ClassVar

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension
from zephyrex.lib.Dependencies import Dependencies


class EXT_LiveSync(AbstractStaticExtension):
    """A user's windows and devices exchanging messages on named channels
    (one window driving the others, a phone remote-controlling a display)."""

    name: ClassVar[str] = "live_sync"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Real-time messages between a user's own devices on named channels, with one main device"
    )

    _env: ClassVar[dict[str, Any]] = {}
    dependencies: ClassVar[Dependencies] = Dependencies([])
    _abilities: ClassVar[set[str]] = set()
    _providers: ClassVar[list[Any]] = []
