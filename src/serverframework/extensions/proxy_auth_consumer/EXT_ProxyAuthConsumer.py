"""Trusted proxy header authentication consumer extension manifest.

Authenticate local users based on trusted proxy headers (X-Forwarded-User,
X-Remote-User, etc.) set by a reverse proxy that has already performed
authentication. Only trusts headers from configured proxy IP addresses.

The complementary ``proxy_auth_provider`` extension implements the *server*
side (this server sets proxy headers for downstream services).
"""

from typing import Any, ClassVar, Dict, List, Set

from serverframework.extensions.AbstractExtensionProvider import AbstractStaticExtension
from serverframework.lib.Dependencies import Dependencies
from serverframework.lib.Logging import logger


class EXT_ProxyAuthConsumer(AbstractStaticExtension):
    name: ClassVar[str] = "proxy_auth_consumer"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Authenticate users via trusted proxy headers (X-Forwarded-User)."
    )

    _env: ClassVar[Dict[str, Any]] = {
        "PROXY_AUTH_CONSUMER_HEADER": "X-Forwarded-User",
        "PROXY_AUTH_CONSUMER_EMAIL_HEADER": "X-Forwarded-Email",
        "PROXY_AUTH_CONSUMER_NAME_HEADER": "X-Forwarded-Name",
        "PROXY_AUTH_CONSUMER_GROUPS_HEADER": "X-Forwarded-Groups",
        "PROXY_AUTH_CONSUMER_TRUSTED_PROXIES": "",
        "PROXY_AUTH_CONSUMER_AUTO_CREATE_USERS": "false",
    }

    dependencies: ClassVar[Dependencies] = Dependencies([])

    _abilities: ClassVar[Set[str]] = {
        "proxy_auth_consumer_authenticate",
    }
    _providers: ClassVar[List] = []
    extension_dependencies: ClassVar[List[str]] = ["auth_session"]

    @classmethod
    def on_initialize(cls) -> bool:
        from serverframework.extensions.proxy_auth_consumer import (  # noqa: F401
            BLL_ProxyAuthConsumer,
        )

        logger.debug("proxy_auth_consumer initialized")
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
        if not _env("PROXY_AUTH_CONSUMER_TRUSTED_PROXIES"):
            issues.append(
                "PROXY_AUTH_CONSUMER_TRUSTED_PROXIES is unset; "
                "proxy auth headers will be rejected from all sources"
            )
        return issues

    @classmethod
    def get_abilities(cls) -> Set[str]:
        return cls._abilities.copy()

    def has_ability(self, ability: str) -> bool:
        return ability in self._abilities
