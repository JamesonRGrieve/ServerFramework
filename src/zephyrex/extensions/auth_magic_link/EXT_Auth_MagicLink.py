"""Magic-link authentication extension manifest (Item 58)."""

from typing import Any, ClassVar, Dict, List, Set

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension
from zephyrex.lib.Dependencies import Dependencies
from zephyrex.lib.Logging import logger


class EXT_Auth_MagicLink(AbstractStaticExtension):
    """Passwordless email-link sign-in extension.

    Issues a one-time, hashed-at-rest token, emails it through ``EXT_Email``
    as a deep link, and exchanges the redeemed token for a normal session
    via ``UserManager.login_via_grant("magic_link", ...)``.
    """

    name: ClassVar[str] = "auth_magic_link"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Passwordless email magic-link authentication (Item 58)"
    )

    _env: ClassVar[Dict[str, Any]] = {
        "MAGIC_LINK_TTL_MINUTES": "15",
        "MAGIC_LINK_BASE_URL": "",
    }

    dependencies: ClassVar[Dependencies] = Dependencies([])

    _abilities: ClassVar[Set[str]] = {"magic_link_request", "magic_link_verify"}
    _providers: ClassVar[List] = []

    extension_dependencies: ClassVar[List[str]] = ["email"]

    @classmethod
    def on_initialize(cls) -> bool:
        logger.debug("Initializing magic-link extension")
        return True

    @classmethod
    def on_start(cls) -> bool:
        logger.debug("Magic-link extension started")
        return True

    @classmethod
    def on_stop(cls) -> bool:
        logger.debug("Magic-link extension stopped")
        return True

    @classmethod
    def validate_config(cls) -> List[str]:
        from zephyrex.lib.Environment import env as _env

        issues: List[str] = []
        if not _env("MAGIC_LINK_BASE_URL"):
            issues.append(
                "MAGIC_LINK_BASE_URL not configured; redirect target will be empty"
            )
        return issues

    @classmethod
    def get_abilities(cls) -> Set[str]:
        return cls._abilities.copy()

    def has_ability(self, ability: str) -> bool:
        return ability in self._abilities
