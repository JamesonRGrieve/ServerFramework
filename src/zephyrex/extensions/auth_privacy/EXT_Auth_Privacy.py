"""Extension manifest for auth_privacy.

Owns the data-residency tables (``DataRegionModel``,
``DataResidencyPreferenceModel``).

Note on scope: the original commit drafted a much wider GDPR/consent/
anonymisation surface here. That work overlaps with ``privacy/BLL_PII``
(redaction primitives) and ``audit_retention/BLL_RetentionService``
(purge windows). The audit recommends consolidating consent and
right-to-be-forgotten flows in ``privacy/`` rather than splitting them
across extensions; this manifest is intentionally small until that
consolidation lands.
"""

from typing import Any, ClassVar, Dict, List, Set

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension
from zephyrex.lib.Dependencies import Dependencies
from zephyrex.lib.Logging import logger


class EXT_Auth_Privacy(AbstractStaticExtension):
    name: ClassVar[str] = "auth_privacy"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Data-residency state (regions and per-tenant pinning) for "
        "compliance with regional data-handling rules."
    )

    _env: ClassVar[Dict[str, Any]] = {}
    dependencies: ClassVar[Dependencies] = Dependencies([])

    _abilities: ClassVar[Set[str]] = {
        "data_residency_register_region",
        "data_residency_pin_tenant",
    }
    _providers: ClassVar[List] = []
    extension_dependencies: ClassVar[List[str]] = []

    @classmethod
    def on_initialize(cls) -> bool:
        from zephyrex.extensions.auth_privacy import (  # noqa: F401
            BLL_Auth_Privacy,
        )

        logger.debug("auth_privacy initialized")
        return True
