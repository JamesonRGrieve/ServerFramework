"""Forward auth consumer extension manifest.

Authenticate requests by making a subrequest to an external forward auth
service (Traefik ForwardAuth, nginx auth_request, Caddy forward_auth).
The external service returns 2xx to allow or 401/403 to deny, optionally
setting response headers with user identity.

The complementary ``forward_auth_provider`` extension implements the
*server* side (this server acts as the forward auth endpoint).
"""

from typing import Any, ClassVar, Dict, List, Set

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension
from zephyrex.lib.Dependencies import Dependencies, PIP_Dependency
from zephyrex.lib.Logging import logger


class EXT_ForwardAuthConsumer(AbstractStaticExtension):
    name: ClassVar[str] = "forward_auth_consumer"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Authenticate via forward auth subrequest (Traefik/nginx auth_request)."
    )

    _env: ClassVar[Dict[str, Any]] = {
        "FORWARD_AUTH_CONSUMER_URL": "",
        "FORWARD_AUTH_CONSUMER_USER_HEADER": "X-Forwarded-User",
        "FORWARD_AUTH_CONSUMER_EMAIL_HEADER": "X-Forwarded-Email",
        "FORWARD_AUTH_CONSUMER_TIMEOUT_SECONDS": "5",
        "FORWARD_AUTH_CONSUMER_PASS_COOKIES": "true",
        "FORWARD_AUTH_CONSUMER_PASS_AUTHORIZATION": "true",
    }

    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            PIP_Dependency(
                name="requests",
                friendly_name="HTTP requests library",
                semver=">=2.31.0",
                reason="Subrequest to external forward auth endpoint",
            ),
        ]
    )

    _abilities: ClassVar[Set[str]] = {
        "forward_auth_consumer_verify",
    }
    _providers: ClassVar[List] = []
    extension_dependencies: ClassVar[List[str]] = ["auth_session"]

    @classmethod
    def on_initialize(cls) -> bool:
        from zephyrex.extensions.forward_auth_consumer import (  # noqa: F401
            BLL_ForwardAuthConsumer,
        )

        logger.debug("forward_auth_consumer initialized")
        return True

    @classmethod
    def on_start(cls) -> bool:
        return True

    @classmethod
    def on_stop(cls) -> bool:
        return True

    @classmethod
    def validate_config(cls) -> List[str]:
        from zephyrex.lib.Environment import env as _env

        issues: List[str] = []
        if not _env("FORWARD_AUTH_CONSUMER_URL"):
            issues.append("FORWARD_AUTH_CONSUMER_URL is unset; subrequests will fail")
        return issues

    @classmethod
    def get_abilities(cls) -> Set[str]:
        return cls._abilities.copy()

    def has_ability(self, ability: str) -> bool:
        return ability in self._abilities
