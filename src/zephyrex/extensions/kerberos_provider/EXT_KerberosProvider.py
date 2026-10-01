"""Kerberos provider extension manifest.

This server acts as a Kerberos application service (SPNEGO acceptor)
and optionally as a KDC for issuing tickets. Third-party clients
authenticate via GSSAPI/SPNEGO Negotiate headers.

The complementary ``kerberos_consumer`` extension implements the *client* side
(authenticate against an external KDC).
"""

from typing import Any, ClassVar, Dict, List, Set

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension
from zephyrex.lib.Dependencies import Dependencies, PIP_Dependency
from zephyrex.lib.Logging import logger


class EXT_KerberosProvider(AbstractStaticExtension):
    name: ClassVar[str] = "kerberos_provider"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Run this server as a Kerberos KDC / SPNEGO application service."
    )

    _env: ClassVar[Dict[str, Any]] = {
        "KERBEROS_PROVIDER_REALM": "",
        "KERBEROS_PROVIDER_KDC_PORT": "88",
        "KERBEROS_PROVIDER_SERVICE_PRINCIPAL": "",
        "KERBEROS_PROVIDER_KEYTAB_PATH": "",
        "KERBEROS_PROVIDER_TICKET_LIFETIME_HOURS": "10",
        "KERBEROS_PROVIDER_RENEW_LIFETIME_DAYS": "7",
    }

    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            PIP_Dependency(
                name="gssapi",
                friendly_name="GSSAPI bindings for Kerberos",
                semver=">=1.8.0",
                reason="Kerberos/SPNEGO ticket issuance and validation",
            ),
        ]
    )

    _abilities: ClassVar[Set[str]] = {
        "kerberos_provider_issue_ticket",
        "kerberos_provider_validate_ticket",
        "kerberos_provider_manage_principals",
    }
    _providers: ClassVar[List] = []
    extension_dependencies: ClassVar[List[str]] = ["auth_session"]

    @classmethod
    def on_initialize(cls) -> bool:
        from zephyrex.extensions.kerberos_provider import (  # noqa: F401
            BLL_KerberosProvider,
        )

        logger.debug("kerberos_provider initialized")
        return True

    @classmethod
    def validate_config(cls) -> List[str]:
        from zephyrex.lib.Environment import env as _env

        issues: List[str] = []
        if not _env("KERBEROS_PROVIDER_REALM"):
            issues.append("KERBEROS_PROVIDER_REALM is unset; cannot serve tickets")
        if not _env("KERBEROS_PROVIDER_KEYTAB_PATH"):
            issues.append("KERBEROS_PROVIDER_KEYTAB_PATH is unset; SPNEGO will fail")
        return issues
