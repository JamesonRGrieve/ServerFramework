"""Audit-retention extension — retention policies, archival, legal hold."""

from typing import Any, ClassVar, Dict, List, Set

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension
from zephyrex.lib.Logging import logger


class EXT_AuditRetention(AbstractStaticExtension):
    """Retention policy management, archival, and legal-hold workflows.

    Audit-log retention is a compliance/operational concern. Generic
    deployments do not need legal hold or jurisdictional retention
    windows; this extension is the opt-in path for them.
    """

    name: ClassVar[str] = "audit_retention"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Retention policies, archival to S3/GCS/local, and legal-hold "
        "workflows for the framework's audit log"
    )

    _env: ClassVar[Dict[str, Any]] = {}
    _abilities: ClassVar[Set[str]] = {
        "retention_policy",
        "retention_archive",
        "legal_hold",
    }
    _providers: ClassVar[List] = []

    @classmethod
    def on_initialize(cls) -> bool:
        logger.debug("Initializing audit_retention extension")
        return True

    @classmethod
    def on_start(cls) -> bool:
        logger.debug("audit_retention extension started")
        return True

    @classmethod
    def on_stop(cls) -> bool:
        logger.debug("audit_retention extension stopped")
        return True

    @classmethod
    def validate_config(cls) -> List[str]:
        return []

    @classmethod
    def get_abilities(cls) -> Set[str]:
        return cls._abilities.copy()

    def has_ability(self, ability: str) -> bool:
        return ability in self._abilities
