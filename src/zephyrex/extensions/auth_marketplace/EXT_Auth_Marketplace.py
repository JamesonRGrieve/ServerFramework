"""Extension manifest for auth_marketplace.

Owns ``UserPaymentPortalModel`` and ``TeamPaymentPortalModel`` — the
linkage rows that map a tenant onto a payment-provider customer record.
"""

from typing import Any, ClassVar, Dict, List, Set

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension
from zephyrex.lib.Dependencies import Dependencies
from zephyrex.lib.Logging import logger


class EXT_Auth_Marketplace(AbstractStaticExtension):
    name: ClassVar[str] = "auth_marketplace"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Per-user and per-team links to payment-portal providers."
    )

    _env: ClassVar[Dict[str, Any]] = {}
    dependencies: ClassVar[Dependencies] = Dependencies([])

    _abilities: ClassVar[Set[str]] = {
        "marketplace_link_user_portal",
        "marketplace_link_team_portal",
    }
    _providers: ClassVar[List] = []
    extension_dependencies: ClassVar[List[str]] = ["payment"]

    @classmethod
    def on_initialize(cls) -> bool:
        from zephyrex.extensions.auth_marketplace import (  # noqa: F401
            BLL_Auth_Marketplace,
        )

        logger.debug("auth_marketplace initialized")
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
