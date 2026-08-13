"""Kerberos consumer extension manifest.

Authenticate local users via Kerberos/SPNEGO/GSSAPI against an external
Key Distribution Center (KDC). Supports both password-based (kinit-style)
and ticket-based (SPNEGO Negotiate header) authentication flows.

The complementary ``kerberos_provider`` extension implements the *server* side
(this server acts as a Kerberos KDC/application service).
"""

from typing import Any, ClassVar, Dict, List, Set

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension
from zephyrex.lib.Dependencies import Dependencies, PIP_Dependency
from zephyrex.lib.Logging import logger


class EXT_KerberosConsumer(AbstractStaticExtension):
    name: ClassVar[str] = "kerberos_consumer"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Authenticate users via Kerberos/SPNEGO/GSSAPI against an external KDC."
    )

    _env: ClassVar[Dict[str, Any]] = {
        "KERBEROS_CONSUMER_REALM": "",
        "KERBEROS_CONSUMER_KDC_HOST": "",
        "KERBEROS_CONSUMER_KDC_PORT": "88",
        "KERBEROS_CONSUMER_SERVICE_PRINCIPAL": "",
        "KERBEROS_CONSUMER_KEYTAB_PATH": "",
        "KERBEROS_CONSUMER_ALLOW_PASSWORD_AUTH": "true",
        "KERBEROS_CONSUMER_ALLOW_SPNEGO": "true",
    }

    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            PIP_Dependency(
                name="gssapi",
                friendly_name="GSSAPI bindings for Kerberos",
                semver=">=1.8.0",
                reason="Kerberos/SPNEGO authentication via GSSAPI",
            ),
        ]
    )

    _abilities: ClassVar[Set[str]] = {
        "kerberos_consumer_authenticate",
        "kerberos_consumer_negotiate",
    }
    _providers: ClassVar[List] = []
    extension_dependencies: ClassVar[List[str]] = ["auth_session"]

    @classmethod
    def on_initialize(cls) -> bool:
        from zephyrex.extensions.kerberos_consumer import (  # noqa: F401
            BLL_KerberosConsumer,
        )

        logger.debug("kerberos_consumer initialized")
        return True

    @classmethod
    def validate_config(cls) -> List[str]:
        from zephyrex.lib.Environment import env as _env

        issues: List[str] = []
        if not _env("KERBEROS_CONSUMER_REALM"):
            issues.append("KERBEROS_CONSUMER_REALM is unset; Kerberos auth will fail")
        if not _env("KERBEROS_CONSUMER_KDC_HOST"):
            issues.append("KERBEROS_CONSUMER_KDC_HOST is unset; cannot reach KDC")
        return issues
