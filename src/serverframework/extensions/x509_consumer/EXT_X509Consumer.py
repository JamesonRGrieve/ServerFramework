"""X.509 client certificate consumer extension manifest.

Authenticate local users via X.509 client certificates (mutual TLS).
The TLS terminator (nginx, Traefik, etc.) validates the client certificate
and passes the certificate subject/fingerprint in request headers. This
extension maps certificate identities to local users.

The complementary ``x509_provider`` extension implements the *server* side
(this server acts as a certificate authority for client certificates).
"""

from typing import Any, ClassVar, Dict, List, Set

from serverframework.extensions.AbstractExtensionProvider import AbstractStaticExtension
from serverframework.lib.Dependencies import Dependencies, PIP_Dependency
from serverframework.lib.Logging import logger


class EXT_X509Consumer(AbstractStaticExtension):
    name: ClassVar[str] = "x509_consumer"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Authenticate users via X.509 client certificates (mutual TLS)."
    )

    _env: ClassVar[Dict[str, Any]] = {
        "X509_CONSUMER_CERT_HEADER": "X-Client-Cert",
        "X509_CONSUMER_SUBJECT_HEADER": "X-Client-Cert-Subject",
        "X509_CONSUMER_FINGERPRINT_HEADER": "X-Client-Cert-Fingerprint",
        "X509_CONSUMER_VERIFY_HEADER": "X-Client-Cert-Verify",
        "X509_CONSUMER_CA_CERT_PATH": "",
        "X509_CONSUMER_CRL_PATH": "",
        "X509_CONSUMER_SUBJECT_MATCH_FIELD": "CN",
    }

    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            PIP_Dependency(
                name="cryptography",
                friendly_name="Cryptographic library",
                semver=">=41.0.0",
                reason="X.509 certificate parsing and validation",
            ),
        ]
    )

    _abilities: ClassVar[Set[str]] = {
        "x509_consumer_authenticate",
        "x509_consumer_verify",
    }
    _providers: ClassVar[List] = []
    extension_dependencies: ClassVar[List[str]] = ["auth_session"]

    @classmethod
    def on_initialize(cls) -> bool:
        from serverframework.extensions.x509_consumer import (  # noqa: F401
            BLL_X509Consumer,
        )

        logger.debug("x509_consumer initialized")
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
        if not _env("X509_CONSUMER_CA_CERT_PATH"):
            issues.append("X509_CONSUMER_CA_CERT_PATH is unset; cannot validate client certificates")
        return issues

    @classmethod
    def get_abilities(cls) -> Set[str]:
        return cls._abilities.copy()

    def has_ability(self, ability: str) -> bool:
        return ability in self._abilities
