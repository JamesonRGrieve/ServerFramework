"""Quota extension — per-user / per-team / system usage caps.

The quota model is opt-in: deployments that don't enforce quotas (per-call
billing, no caps) shouldn't carry the model. The cost-tied integration
points in ``BLL_Providers`` resolve quota lazily and degrade gracefully if
the extension is not loaded.

The ``billing`` extension (formerly ``CostModel``/``CostSummary``) is
listed as an *optional* dependency: cost-aware quotas (USD caps) require
billing's cost model, but a plain call-count quota does not.
"""

from typing import Any, ClassVar, Dict, List, Set

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension
from zephyrex.lib.Logging import logger


class EXT_Quota(AbstractStaticExtension):
    name: ClassVar[str] = "quota"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Per-user / per-team / system quota model with atomic decrement and "
        "period-key derivation. Optional billing dependency for USD caps."
    )

    _env: ClassVar[Dict[str, Any]] = {}
    _abilities: ClassVar[Set[str]] = {
        "quota_decrement",
        "quota_resolve",
    }
    _providers: ClassVar[List] = []

    @classmethod
    def on_initialize(cls) -> bool:
        logger.debug("Initializing quota extension")
        return True
