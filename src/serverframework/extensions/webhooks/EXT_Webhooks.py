"""Webhooks extension — inbound webhook router and handler registry."""

from typing import Any, ClassVar, Dict, List, Set

from serverframework.extensions.AbstractExtensionProvider import AbstractStaticExtension
from serverframework.lib.Logging import logger


class EXT_Webhooks(AbstractStaticExtension):
    """Inbound webhook router and handler registry.

    Other extensions register handlers via
    ``serverframework.extensions.webhooks.BLL_Webhooks.webhook_handler``.
    The router is exposed via
    ``serverframework.extensions.webhooks.EP_Webhooks.create_webhook_router``
    and is mounted by the host application at ``/webhook``.

    The framework's mission is a generic server scaffold; inbound webhooks
    are an integration shape that not every deployment needs (some emit
    only outbound events). This extension makes them opt-in.
    """

    name: ClassVar[str] = "webhooks"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Inbound webhook handler registry and router with mandatory "
        "signature verification and optional replay protection"
    )

    _env: ClassVar[Dict[str, Any]] = {}
    _abilities: ClassVar[Set[str]] = {
        "webhook_register",
        "webhook_dispatch",
    }
    _providers: ClassVar[List] = []

    @classmethod
    def on_initialize(cls) -> bool:
        logger.debug("Initializing webhooks extension")
        return True

    @classmethod
    def on_start(cls) -> bool:
        logger.debug("webhooks extension started")
        return True

    @classmethod
    def on_stop(cls) -> bool:
        logger.debug("webhooks extension stopped")
        return True

    @classmethod
    def validate_config(cls) -> List[str]:
        return []

    @classmethod
    def get_abilities(cls) -> Set[str]:
        return cls._abilities.copy()

    def has_ability(self, ability: str) -> bool:
        return ability in self._abilities
