"""QR-code device-pairing authentication extension manifest (Item 59)."""

from typing import Any, ClassVar, Dict, List, Set

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension
from zephyrex.lib.Dependencies import Dependencies
from zephyrex.lib.Logging import logger


class EXT_Auth_DevicePairing(AbstractStaticExtension):
    """QR-code cross-device pairing authentication extension.

    Implements the Steam Guard / Discord-style flow: an unauthenticated
    new-device requests a pairing token, displays it as a QR code, and is
    paired when an already-authenticated device approves the token.
    """

    name: ClassVar[str] = "auth_device_pairing"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "QR-code cross-device pairing authentication (Item 59)"
    )

    _env: ClassVar[Dict[str, Any]] = {
        "PAIRING_TTL_SECONDS": "300",
        "PAIRING_BASE_URL": "",
    }

    dependencies: ClassVar[Dependencies] = Dependencies([])

    _abilities: ClassVar[Set[str]] = {
        "device_pairing_request",
        "device_pairing_approve",
        "device_pairing_deny",
        "device_pairing_status",
    }
    _providers: ClassVar[List] = []

    @classmethod
    def on_initialize(cls) -> bool:
        logger.debug("Initializing device-pairing extension")
        return True

    @classmethod
    def on_start(cls) -> bool:
        logger.debug("Device-pairing extension started")
        return True

    @classmethod
    def on_stop(cls) -> bool:
        logger.debug("Device-pairing extension stopped")
        return True

    @classmethod
    def validate_config(cls) -> List[str]:
        return []

    @classmethod
    def get_abilities(cls) -> Set[str]:
        return cls._abilities.copy()

    def has_ability(self, ability: str) -> bool:
        return ability in self._abilities
