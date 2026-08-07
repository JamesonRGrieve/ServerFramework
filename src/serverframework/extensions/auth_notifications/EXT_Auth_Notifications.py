"""Extension manifest for auth_notifications.

Owns ``NotificationModel`` (broadcast) and ``UserNotificationModel``
(per-user delivery state).
"""

from typing import Any, ClassVar, Dict, List, Set

from serverframework.extensions.AbstractExtensionProvider import AbstractStaticExtension
from serverframework.lib.Dependencies import Dependencies
from serverframework.lib.Logging import logger


class EXT_Auth_Notifications(AbstractStaticExtension):
    name: ClassVar[str] = "auth_notifications"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "User-facing notifications with per-user read/acknowledged state."
    )

    _env: ClassVar[Dict[str, Any]] = {}
    dependencies: ClassVar[Dependencies] = Dependencies([])

    _abilities: ClassVar[Set[str]] = {
        "notifications_create",
        "notifications_acknowledge",
    }
    _providers: ClassVar[List] = []
    extension_dependencies: ClassVar[List[str]] = []

    @classmethod
    def on_initialize(cls) -> bool:
        from serverframework.extensions.auth_notifications import (  # noqa: F401
            BLL_Auth_Notifications,
        )
        from serverframework.extensions.auth_notifications.BLL_Auth_Notifications import (
            register_merge_participation,
        )

        register_merge_participation()
        logger.debug("auth_notifications initialized")
        return True

    @classmethod
    def on_start(cls) -> bool:
        return True

    @classmethod
    def on_stop(cls) -> bool:
        return True

    @classmethod
    def validate_config(cls) -> List[str]:
        return []

    @classmethod
    def get_abilities(cls) -> Set[str]:
        return cls._abilities.copy()

    def has_ability(self, ability: str) -> bool:
        return ability in self._abilities
