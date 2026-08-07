"""X.509 certificate provider extension manifest.

This server acts as a Certificate Authority (CA) for issuing client
certificates. Users request certificates, the CA signs them, and the
certificates can be used for mutual TLS authentication.

The complementary ``x509_consumer`` extension implements the *client* side
(authenticate users via certificates issued by an external CA).
"""

from typing import Any, ClassVar, Dict, List, Set

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension
from zephyrex.lib.Dependencies import Dependencies, PIP_Dependency
from zephyrex.lib.Logging import logger


class EXT_X509Provider(AbstractStaticExtension):
    name: ClassVar[str] = "x509_provider"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Run this server as a Certificate Authority for client certificates."
    )

    _env: ClassVar[Dict[str, Any]] = {
        "X509_PROVIDER_CA_CERT_PATH": "",
        "X509_PROVIDER_CA_KEY_PATH": "",
        "X509_PROVIDER_CRL_DISTRIBUTION_URL": "",
        "X509_PROVIDER_CERT_LIFETIME_DAYS": "365",
        "X509_PROVIDER_KEY_SIZE": "4096",
        "X509_PROVIDER_SIGNATURE_ALGORITHM": "SHA256",
    }

    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            PIP_Dependency(
                name="cryptography",
                friendly_name="Cryptographic library",
                semver=">=41.0.0",
                reason="X.509 certificate generation, signing, and CRL management",
            ),
        ]
    )

    _abilities: ClassVar[Set[str]] = {
        "x509_provider_issue_cert",
        "x509_provider_revoke_cert",
        "x509_provider_crl",
    }
    _providers: ClassVar[List] = []
    extension_dependencies: ClassVar[List[str]] = ["auth_session"]

    @classmethod
    def on_initialize(cls) -> bool:
        from zephyrex.extensions.x509_provider import (  # noqa: F401
            BLL_X509Provider,
        )

        logger.debug("x509_provider initialized")
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
        if not _env("X509_PROVIDER_CA_CERT_PATH"):
            issues.append("X509_PROVIDER_CA_CERT_PATH is unset; cannot sign certificates")
        if not _env("X509_PROVIDER_CA_KEY_PATH"):
            issues.append("X509_PROVIDER_CA_KEY_PATH is unset; cannot sign certificates")
        return issues

    @classmethod
    def get_abilities(cls) -> Set[str]:
        return cls._abilities.copy()

    def has_ability(self, ability: str) -> bool:
        return ability in self._abilities
