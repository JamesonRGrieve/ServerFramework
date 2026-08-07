"""RADIUS provider extension manifest.

This server acts as a RADIUS authentication and accounting server.
Third-party NAS devices send Access-Request packets which are validated
against the local user store.

The complementary ``radius_consumer`` extension implements the *client* side
(authenticate against an external RADIUS server).
"""

from typing import Any, ClassVar, Dict, List, Set

from serverframework.extensions.AbstractExtensionProvider import AbstractStaticExtension
from serverframework.lib.Dependencies import Dependencies, PIP_Dependency
from serverframework.lib.Logging import logger


class EXT_RADIUSProvider(AbstractStaticExtension):
    name: ClassVar[str] = "radius_provider"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Run this server as a RADIUS authenticator and accounting server."
    )

    _env: ClassVar[Dict[str, Any]] = {
        "RADIUS_PROVIDER_AUTH_PORT": "1812",
        "RADIUS_PROVIDER_ACCT_PORT": "1813",
        "RADIUS_PROVIDER_DEFAULT_SECRET": "",
        "RADIUS_PROVIDER_DICTIONARY_PATH": "",
        "RADIUS_PROVIDER_MAX_CLIENTS": "256",
    }

    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            PIP_Dependency(
                name="pyrad",
                friendly_name="RADIUS client/server library",
                semver=">=2.4",
                reason="RADIUS protocol server implementation",
            ),
        ]
    )

    _abilities: ClassVar[Set[str]] = {
        "radius_provider_authenticate",
        "radius_provider_accounting",
        "radius_provider_manage_clients",
    }
    _providers: ClassVar[List] = []
    extension_dependencies: ClassVar[List[str]] = ["auth_session"]

    @classmethod
    def on_initialize(cls) -> bool:
        from serverframework.extensions.radius_provider import (  # noqa: F401
            BLL_RADIUSProvider,
        )

        logger.debug("radius_provider initialized")
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
        if not _env("RADIUS_PROVIDER_DEFAULT_SECRET"):
            issues.append(
                "RADIUS_PROVIDER_DEFAULT_SECRET is unset; NAS clients will need per-client secrets"
            )
        return issues

    @classmethod
    def get_abilities(cls) -> Set[str]:
        return cls._abilities.copy()

    def has_ability(self, ability: str) -> bool:
        return ability in self._abilities
