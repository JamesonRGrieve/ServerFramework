"""Extension manifest for auth_merge.

Owns the ``UserMergeModel`` audit table and exposes ``UserMergeManager``
on ``/v1/auth/user-merge``.
"""

from typing import Any, ClassVar, Dict, List, Set

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension
from zephyrex.lib.Dependencies import Dependencies
from zephyrex.lib.Logging import logger


class EXT_Auth_Merge(AbstractStaticExtension):
    name: ClassVar[str] = "auth_merge"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Audit-record extension for consolidating user accounts."
    )

    _env: ClassVar[Dict[str, Any]] = {}
    dependencies: ClassVar[Dependencies] = Dependencies([])

    _abilities: ClassVar[Set[str]] = {"user_merge"}
    _providers: ClassVar[List] = []
    extension_dependencies: ClassVar[List[str]] = ["auth_session"]

    @classmethod
    def on_initialize(cls) -> bool:
        from zephyrex.extensions.auth_merge import (  # noqa: F401
            BLL_Auth_Merge,
        )

        logger.debug("auth_merge initialized")
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
