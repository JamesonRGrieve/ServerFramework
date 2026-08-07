"""Extension manifest for fileio.

Owns the ``AbstractFileIOProvider`` interface and the bundled ``Local``
provider that exposes the host filesystem under a configured base
directory. Path containment is enforced by ``Path.relative_to`` at every
permission check (rejects sibling-prefix attacks and post-resolution
symlinks).
"""

from typing import Any, ClassVar, Dict, List, Set

from serverframework.extensions.AbstractExtensionProvider import AbstractStaticExtension
from serverframework.lib.Dependencies import Dependencies
from serverframework.lib.Logging import logger


class EXT_FileIO(AbstractStaticExtension):
    name: ClassVar[str] = "fileio"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "File I/O extension for interacting with local files and directories"
    )

    _env: ClassVar[Dict[str, Any]] = {
        "FILEIO_BASE_DIRECTORY": "",
    }

    dependencies: ClassVar[Dependencies] = Dependencies([])
    _abilities: ClassVar[Set[str]] = {
        "fileio_read",
        "fileio_write",
        "fileio_list",
        "fileio_delete",
    }
    _providers: ClassVar[List] = []
    extension_dependencies: ClassVar[List[str]] = []

    @classmethod
    def on_initialize(cls) -> bool:
        from serverframework.extensions.fileio import (  # noqa: F401
            Local,
            PRV_FileIO,
        )

        logger.debug("fileio initialized")
        return True

    @classmethod
    def on_start(cls) -> bool:
        return True

    @classmethod
    def on_stop(cls) -> bool:
        return True

    @classmethod
    def validate_config(cls) -> List[str]:
        from serverframework.lib.Environment import env as _env

        issues: List[str] = []
        if not _env("FILEIO_BASE_DIRECTORY"):
            issues.append(
                "FILEIO_BASE_DIRECTORY is unset; the Local provider will not be usable"
            )
        return issues

    @classmethod
    def get_abilities(cls) -> Set[str]:
        return cls._abilities.copy()

    def has_ability(self, ability: str) -> bool:
        return ability in self._abilities
