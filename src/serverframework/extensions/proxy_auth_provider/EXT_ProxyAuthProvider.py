"""Trusted proxy header authentication provider extension manifest.

This server acts as an authenticating reverse proxy, setting trusted
headers (X-Forwarded-User, X-Forwarded-Email, X-Forwarded-Groups) for
downstream services after validating the user's session.

The complementary ``proxy_auth_consumer`` extension implements the
*client* side (trust headers from an upstream proxy).
"""

from typing import Any, ClassVar, Dict, List, Set

from serverframework.extensions.AbstractExtensionProvider import AbstractStaticExtension
from serverframework.lib.Dependencies import Dependencies
from serverframework.lib.Logging import logger


class EXT_ProxyAuthProvider(AbstractStaticExtension):
    name: ClassVar[str] = "proxy_auth_provider"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Set trusted proxy auth headers for downstream services."
    )

    _env: ClassVar[Dict[str, Any]] = {
        "PROXY_AUTH_PROVIDER_USER_HEADER": "X-Forwarded-User",
        "PROXY_AUTH_PROVIDER_EMAIL_HEADER": "X-Forwarded-Email",
        "PROXY_AUTH_PROVIDER_NAME_HEADER": "X-Forwarded-Name",
        "PROXY_AUTH_PROVIDER_GROUPS_HEADER": "X-Forwarded-Groups",
        "PROXY_AUTH_PROVIDER_STRIP_INCOMING": "true",
    }

    dependencies: ClassVar[Dependencies] = Dependencies([])

    _abilities: ClassVar[Set[str]] = {
        "proxy_auth_provider_inject_headers",
        "proxy_auth_provider_manage_targets",
    }
    _providers: ClassVar[List] = []
    extension_dependencies: ClassVar[List[str]] = ["auth_session"]

    @classmethod
    def on_initialize(cls) -> bool:
        from serverframework.extensions.proxy_auth_provider import (  # noqa: F401
            BLL_ProxyAuthProvider,
        )

        logger.debug("proxy_auth_provider initialized")
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
