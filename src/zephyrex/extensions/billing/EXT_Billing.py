"""Billing extension — cost tracking, summaries, audit emission."""

from typing import Any, ClassVar, Dict, List, Set

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension
from zephyrex.lib.Logging import logger


class EXT_Billing(AbstractStaticExtension):
    """Cost observability: model registry, daily summaries, audit emitter.

    Cost tracking is business-domain (chargeback, billing, finance
    reconstruction). A generic server framework should not assume that
    every deployment is monetized; this extension is the opt-in path
    for cost-aware deployments.

    The ``quota`` extension lists this as an optional dependency for
    USD-denominated caps; billing itself stands alone.
    """

    name: ClassVar[str] = "billing"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Cost model registry, daily cost summaries, per-tenant cost audit "
        "emission for finance reconstruction"
    )

    _env: ClassVar[Dict[str, Any]] = {}
    _abilities: ClassVar[Set[str]] = {
        "cost_model",
        "cost_summary",
        "cost_audit_emit",
    }
    _providers: ClassVar[List] = []

    @classmethod
    def on_initialize(cls) -> bool:
        logger.debug("Initializing billing extension")
        return True

    @classmethod
    def on_start(cls) -> bool:
        logger.debug("billing extension started")
        return True

    @classmethod
    def on_stop(cls) -> bool:
        logger.debug("billing extension stopped")
        return True

    @classmethod
    def validate_config(cls) -> List[str]:
        return []

    @classmethod
    def get_abilities(cls) -> Set[str]:
        return cls._abilities.copy()

    def has_ability(self, ability: str) -> bool:
        return ability in self._abilities
