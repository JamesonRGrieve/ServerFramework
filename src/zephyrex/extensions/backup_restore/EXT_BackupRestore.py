"""Backup-restore extension — backup commands, targets, restore drills."""

from typing import Any, ClassVar, Dict, List, Set

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension
from zephyrex.lib.Logging import logger


class EXT_BackupRestore(AbstractStaticExtension):
    """Backup commands, archival targets (S3/GCS/local), restore drills.

    Backup is operational, not framework-primitive. Storage choice and
    RTO/RPO are deployment-specific. The framework keeps the pure
    primitives (database session management) in core; backup belongs
    in an extension that deployments opt into.
    """

    name: ClassVar[str] = "backup_restore"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Backup commands (sqlite3 / pg_dump), archival targets "
        "(local / S3 / GCS), and the restore-drill runner"
    )

    _env: ClassVar[Dict[str, Any]] = {}
    _abilities: ClassVar[Set[str]] = {
        "backup_run",
        "backup_restore",
        "restore_drill",
    }
    _providers: ClassVar[List] = []

    @classmethod
    def on_initialize(cls) -> bool:
        logger.debug("Initializing backup_restore extension")
        return True
