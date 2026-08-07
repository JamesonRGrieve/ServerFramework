"""Privacy extension — PII classification, redaction, right-to-erasure."""

from typing import Any, ClassVar, Dict, List, Set

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension
from zephyrex.lib.Logging import logger


class EXT_Privacy(AbstractStaticExtension):
    """PII classification, log redaction, GDPR right-to-erasure orchestration.

    Privacy/PII handling is a compliance concern, not a framework
    primitive. Deployments without PII obligations don't carry the
    classification metadata; this extension makes it opt-in.
    """

    name: ClassVar[str] = "privacy"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "PII classification metadata, redaction helpers, right-to-erasure "
        "and data-export orchestration for GDPR/CCPA compliance"
    )

    _env: ClassVar[Dict[str, Any]] = {}
    _abilities: ClassVar[Set[str]] = {
        "pii_classify",
        "pii_redact",
        "right_to_erasure",
        "data_export",
    }
    _providers: ClassVar[List] = []

    @classmethod
    def on_initialize(cls) -> bool:
        logger.debug("Initializing privacy extension")
        return True

    @classmethod
    def on_start(cls) -> bool:
        logger.debug("privacy extension started")
        return True

    @classmethod
    def on_stop(cls) -> bool:
        logger.debug("privacy extension stopped")
        return True

    @classmethod
    def validate_config(cls) -> List[str]:
        return []

    @classmethod
    def get_abilities(cls) -> Set[str]:
        return cls._abilities.copy()

    def has_ability(self, ability: str) -> bool:
        return ability in self._abilities
