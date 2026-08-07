"""Forward auth provider extension manifest.

This server acts as a forward auth endpoint for reverse proxies
(Traefik ForwardAuth, nginx auth_request, Caddy forward_auth). The
proxy sends a subrequest; this server validates the user's session
and returns 2xx (with identity headers) or 401/403.

The complementary ``forward_auth_consumer`` extension implements the
*client* side (make subrequests to an external forward auth service).
"""

from typing import Any, ClassVar, Dict, List, Set

from serverframework.extensions.AbstractExtensionProvider import AbstractStaticExtension
from serverframework.lib.Dependencies import Dependencies
from serverframework.lib.Logging import logger


class EXT_ForwardAuthProvider(AbstractStaticExtension):
    name: ClassVar[str] = "forward_auth_provider"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Serve as a forward auth endpoint for Traefik/nginx/Caddy."
    )

    _env: ClassVar[Dict[str, Any]] = {
        "FORWARD_AUTH_PROVIDER_PATH": "/api/verify",
        "FORWARD_AUTH_PROVIDER_USER_HEADER": "X-Forwarded-User",
        "FORWARD_AUTH_PROVIDER_EMAIL_HEADER": "X-Forwarded-Email",
        "FORWARD_AUTH_PROVIDER_NAME_HEADER": "X-Forwarded-Name",
        "FORWARD_AUTH_PROVIDER_GROUPS_HEADER": "X-Forwarded-Groups",
        "FORWARD_AUTH_PROVIDER_COOKIE_NAME": "",
        "FORWARD_AUTH_PROVIDER_DEFAULT_REDIRECT_URL": "",
    }

    dependencies: ClassVar[Dependencies] = Dependencies([])

    _abilities: ClassVar[Set[str]] = {
        "forward_auth_provider_verify",
        "forward_auth_provider_manage_rules",
    }
    _providers: ClassVar[List] = []
    extension_dependencies: ClassVar[List[str]] = ["auth_session"]

    @classmethod
    def on_initialize(cls) -> bool:
        from serverframework.extensions.forward_auth_provider import (  # noqa: F401
            BLL_ForwardAuthProvider,
        )

        logger.debug("forward_auth_provider initialized")
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
