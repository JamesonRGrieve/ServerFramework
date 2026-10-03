# SPDX-License-Identifier: AGPL-3.0-or-later
"""This server as an internal certificate authority: it issues TLS client
certificates to its users (and their devices) from the CSRs they submit,
revokes them, and publishes a CRL. The protocol and routes are described
in ``BLL_X509Provider``; the CA's key handling in
``PRV_X509CertificateAuthority``.

Setting it up: as root, ``POST /v1/x509_provider/ca/generate`` (or
``/ca/import`` an existing CA's certificate and key), or inject
``X509_PROVIDER_CA_CERTIFICATE`` / ``X509_PROVIDER_CA_KEY`` from the secret
store. Set ``X509_PROVIDER_BASE_URL`` (else ``SERVER_URI``) to the origin
relying parties reach the server at, so issued certificates carry their CRL
and CA URLs. ``FRAMEWORK_FERNET_KEY`` must be set: the CA key is stored
encrypted with it.

Abilities that touch a user's certificates act for ``requester_id`` under
that user's permissions.
"""

from typing import Any, ClassVar, Dict, List, Optional, Set

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from fastapi import HTTPException

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractStaticExtension,
    ability,
)
from zephyrex.extensions.ExternalErrors import InvalidInputExternalError
from zephyrex.extensions.x509_provider.BLL_X509Provider import (
    Authorities,
    X509IssuedCertificateManager,
    revocation_list_der,
)
from zephyrex.extensions.x509_provider.CertificateAuthority import (
    REVOCATION_REASONS,
)
from zephyrex.lib.Dependencies import Dependencies, PIP_Dependency
from zephyrex.lib.SecretEncryption import (
    MissingFernetKeyError,
    assert_encryption_available,
)
from zephyrex.pydantic2.registry import ModelRegistry


def _row(model: Any) -> Dict[str, Any]:
    dumped: Dict[str, Any] = model.model_dump(mode="json")
    return dumped


class EXT_X509Provider(AbstractStaticExtension):
    name: ClassVar[str] = "x509_provider"
    version: ClassVar[str] = "2.0.0"
    description: ClassVar[str] = (
        "Run this server as a certificate authority issuing TLS client "
        "certificates to its users from their CSRs, with revocation and a CRL."
    )

    _env: ClassVar[Dict[str, Any]] = {
        "X509_PROVIDER_BASE_URL": "",
        "X509_PROVIDER_CERT_LIFETIME_DAYS": "30",
        "X509_PROVIDER_MAX_ISSUANCES_PER_DAY": "10",
        "X509_PROVIDER_CRL_LIFETIME_HOURS": "24",
    }

    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            PIP_Dependency(
                name="cryptography",
                friendly_name="cryptography",
                semver=">=45.0.0",
                reason="CSR checks, certificate issuance and CRL signing",
            ),
        ]
    )

    _abilities: ClassVar[Set[str]] = {
        "x509_provider_issue_cert",
        "x509_provider_list_certs",
        "x509_provider_revoke_cert",
        "x509_provider_crl",
    }

    @classmethod
    def validate_config(cls) -> List[str]:
        try:
            assert_encryption_available()
        except MissingFernetKeyError:
            return [
                "FRAMEWORK_FERNET_KEY is unset: the CA key cannot be stored "
                "encrypted, so no CA can be generated or imported"
            ]
        return []

    @classmethod
    def certificates(cls, requester_id: str) -> X509IssuedCertificateManager:
        manager: X509IssuedCertificateManager = cls.as_requester(
            X509IssuedCertificateManager, requester_id
        )
        return manager

    @classmethod
    @ability("x509_provider_issue_cert")
    async def x509_provider_issue_cert(
        cls, requester_id: str, csr_pem: str, lifetime_days: Optional[int] = None
    ) -> Dict[str, Any]:
        """A client certificate for the user's CSR (PEM), naming the user's
        account, with the issuing CA's certificate as ``chain_pem``."""
        return _row(cls.certificates(requester_id).issue(csr_pem, lifetime_days))

    @classmethod
    @ability("x509_provider_list_certs")
    async def x509_provider_list_certs(cls, requester_id: str) -> List[Dict[str, Any]]:
        """The certificates issued to the user."""
        return [_row(record) for record in cls.certificates(requester_id).list()]

    @classmethod
    @ability("x509_provider_revoke_cert")
    async def x509_provider_revoke_cert(
        cls, requester_id: str, certificate_id: str, reason: str = "unspecified"
    ) -> Dict[str, Any]:
        """Revoke one of the user's certificates (root and system: any)."""
        if reason not in REVOCATION_REASONS:
            raise InvalidInputExternalError(
                f"reason is one of {', '.join(REVOCATION_REASONS)}"
            )
        return _row(cls.certificates(requester_id).revoke(certificate_id, reason))

    @classmethod
    @ability("x509_provider_crl")
    async def x509_provider_crl(
        cls, ca_fingerprint: Optional[str] = None
    ) -> Dict[str, str]:
        """The CRL (PEM) of the CA with ``ca_fingerprint``, else of the CA
        now issuing."""
        registry = ModelRegistry.attached()
        if registry is None:
            raise HTTPException(status_code=503, detail=f"{cls.name}: no running app")
        authorities = Authorities(registry)
        wanted = (
            ca_fingerprint.lower()
            if ca_fingerprint
            else authorities.active().fingerprint
        )
        der = revocation_list_der(registry, wanted)
        pem = x509.load_der_x509_crl(der).public_bytes(serialization.Encoding.PEM)
        return {"ca_fingerprint": wanted, "crl_pem": pem.decode("ascii")}
