"""RADIUS consumer extension manifest.

Authenticate local users against an external RADIUS server supporting
PAP, CHAP, and EAP authentication methods. Carries RADIUS attributes
(Reply-Message, Session-Timeout, vendor-specific) back into the session.

The complementary ``radius_provider`` extension implements the *server* side
(this server acts as a RADIUS authenticator/accounting server).
"""

from typing import Any, ClassVar, Dict, List, Set

from serverframework.extensions.AbstractExtensionProvider import AbstractStaticExtension
from serverframework.lib.Dependencies import Dependencies, PIP_Dependency
from serverframework.lib.Logging import logger


class EXT_RADIUSConsumer(AbstractStaticExtension):
    name: ClassVar[str] = "radius_consumer"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Authenticate users against an external RADIUS server (PAP/CHAP/EAP)."
    )

    _env: ClassVar[Dict[str, Any]] = {
        "RADIUS_CONSUMER_HOST": "",
        "RADIUS_CONSUMER_AUTH_PORT": "1812",
        "RADIUS_CONSUMER_ACCT_PORT": "1813",
        "RADIUS_CONSUMER_SECRET": "",
        "RADIUS_CONSUMER_TIMEOUT_SECONDS": "5",
        "RADIUS_CONSUMER_RETRIES": "3",
        "RADIUS_CONSUMER_NAS_IDENTIFIER": "",
        "RADIUS_CONSUMER_AUTH_METHOD": "PAP",
    }

    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            PIP_Dependency(
                name="pyrad",
                friendly_name="RADIUS client/server library",
                semver=">=2.4",
                reason="RADIUS Access-Request/Accept/Reject against external servers",
            ),
        ]
    )

    _abilities: ClassVar[Set[str]] = {
        "radius_consumer_authenticate",
        "radius_consumer_accounting",
    }
    _providers: ClassVar[List] = []
    extension_dependencies: ClassVar[List[str]] = ["auth_session"]

    @classmethod
    def on_initialize(cls) -> bool:
        from serverframework.extensions.radius_consumer import (  # noqa: F401
            BLL_RADIUSConsumer,
        )

        logger.debug("radius_consumer initialized")
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
        if not _env("RADIUS_CONSUMER_HOST"):
            issues.append("RADIUS_CONSUMER_HOST is unset; RADIUS authentication will fail")
        if not _env("RADIUS_CONSUMER_SECRET"):
            issues.append("RADIUS_CONSUMER_SECRET is unset; shared secret required")
        return issues

    @classmethod
    def get_abilities(cls) -> Set[str]:
        return cls._abilities.copy()

    def has_ability(self, ability: str) -> bool:
        return ability in self._abilities
