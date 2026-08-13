"""LDAP provider extension manifest.

This server acts as an LDAP directory, exposing local users and groups
to third-party LDAP consumers. Supports bind, search, and compare
operations against the local user store.

The complementary ``ldap_consumer`` extension implements the *client* side
(authenticate local users against an external LDAP/AD server).
"""

from typing import Any, ClassVar, Dict, List, Set

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension
from zephyrex.lib.Dependencies import Dependencies, PIP_Dependency
from zephyrex.lib.Logging import logger


class EXT_LDAPProvider(AbstractStaticExtension):
    name: ClassVar[str] = "ldap_provider"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Run this server as an LDAP directory for third-party consumers."
    )

    _env: ClassVar[Dict[str, Any]] = {
        "LDAP_PROVIDER_LISTEN_PORT": "3389",
        "LDAP_PROVIDER_BASE_DN": "",
        "LDAP_PROVIDER_TLS_CERT_PATH": "",
        "LDAP_PROVIDER_TLS_KEY_PATH": "",
        "LDAP_PROVIDER_REALM": "",
        "LDAP_PROVIDER_MAX_CONNECTIONS": "100",
    }

    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            PIP_Dependency(
                name="ldap3",
                friendly_name="LDAP v3 library",
                semver=">=2.9.0",
                reason="LDAP protocol server implementation",
            ),
        ]
    )

    _abilities: ClassVar[Set[str]] = {
        "ldap_provider_bind",
        "ldap_provider_search",
        "ldap_provider_compare",
    }
    _providers: ClassVar[List] = []
    extension_dependencies: ClassVar[List[str]] = ["auth_session"]

    @classmethod
    def on_initialize(cls) -> bool:
        from zephyrex.extensions.ldap_provider import (  # noqa: F401
            BLL_LDAPProvider,
        )

        logger.debug("ldap_provider initialized")
        return True

    @classmethod
    def validate_config(cls) -> List[str]:
        from zephyrex.lib.Environment import env as _env

        issues: List[str] = []
        if not _env("LDAP_PROVIDER_BASE_DN"):
            issues.append("LDAP_PROVIDER_BASE_DN is unset; directory root undefined")
        return issues
