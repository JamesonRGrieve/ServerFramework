"""LDAP consumer extension manifest.

Authenticate local users against an external LDAP or Active Directory server.
Binds with the user's credentials (or a service account for search-then-bind)
and resolves group memberships for role mapping.

The complementary ``ldap_provider`` extension implements the *server* side
(this server exposes an LDAP-compatible interface for third-party consumers).
"""

from typing import Any, ClassVar, Dict, List, Set

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension
from zephyrex.lib.Dependencies import Dependencies, PIP_Dependency
from zephyrex.lib.Logging import logger


class EXT_LDAPConsumer(AbstractStaticExtension):
    name: ClassVar[str] = "ldap_consumer"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Authenticate users against an external LDAP/Active Directory server."
    )

    _env: ClassVar[Dict[str, Any]] = {
        "LDAP_CONSUMER_HOST": "",
        "LDAP_CONSUMER_PORT": "389",
        "LDAP_CONSUMER_USE_SSL": "false",
        "LDAP_CONSUMER_USE_STARTTLS": "false",
        "LDAP_CONSUMER_BIND_DN": "",
        "LDAP_CONSUMER_BIND_PASSWORD": "",
        "LDAP_CONSUMER_BASE_DN": "",
        "LDAP_CONSUMER_USER_SEARCH_FILTER": "(uid={username})",
        "LDAP_CONSUMER_GROUP_SEARCH_BASE": "",
        "LDAP_CONSUMER_GROUP_SEARCH_FILTER": "(member={dn})",
        "LDAP_CONSUMER_TIMEOUT_SECONDS": "10",
    }

    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            PIP_Dependency(
                name="ldap3",
                friendly_name="LDAP v3 client library",
                semver=">=2.9.0",
                reason="LDAP bind and search against external directory servers",
            ),
        ]
    )

    _abilities: ClassVar[Set[str]] = {
        "ldap_consumer_authenticate",
        "ldap_consumer_search",
    }
    _providers: ClassVar[List] = []
    extension_dependencies: ClassVar[List[str]] = ["auth_session"]

    @classmethod
    def on_initialize(cls) -> bool:
        from zephyrex.extensions.ldap_consumer import (  # noqa: F401
            BLL_LDAPConsumer,
        )

        logger.debug("ldap_consumer initialized")
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
        if not _env("LDAP_CONSUMER_HOST"):
            issues.append("LDAP_CONSUMER_HOST is unset; LDAP authentication will fail")
        if not _env("LDAP_CONSUMER_BASE_DN"):
            issues.append("LDAP_CONSUMER_BASE_DN is unset; user search will fail")
        return issues

    @classmethod
    def get_abilities(cls) -> Set[str]:
        return cls._abilities.copy()

    def has_ability(self, ability: str) -> bool:
        return ability in self._abilities
