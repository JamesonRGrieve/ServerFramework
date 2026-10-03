# SPDX-License-Identifier: AGPL-3.0-or-later
"""The certificate authority's cryptography, with no database: checking a
CA's key and certificate, generating one, checking a certificate signing
request, issuing a client certificate for a verified identity, and signing
a certificate revocation list.

Policy, all of it enforced here:

- A CSR's key: RSA of at least 2048 bits with a public exponent of at
  least 65537, ECDSA on P-256 or P-384, or Ed25519. Anything else is
  refused, as is a CSR signed with SHA-1 or MD5 or whose signature does not
  verify (proof the requester holds the private key).
- A CA's key: the same RSA and ECDSA, but not Ed25519. RFC 5280 path
  validators on the Web PKI profile (``cryptography``'s, which
  x509_consumer uses, and most TLS stacks) refuse an Ed25519 signature on
  a certificate, so an Ed25519 CA's certificates would be refused.
- An issued certificate names the identity the server verified, never what
  the CSR claims: the CSR contributes its public key and nothing else. The
  subject is CN (username, else email, else the user id) and UID (the user
  id); the subjectAltName carries the email and, for a UUID user id, the
  ``urn:uuid:`` URI.
- It is an end-entity certificate for TLS client authentication only
  (basicConstraints CA:false, keyUsage digitalSignature, extendedKeyUsage
  clientAuth), with a serial of 159 random bits from the OS CSPRNG, and no
  validity beyond the CA's own.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, List, Literal, Optional, Union, get_args

from cryptography import x509
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa
from cryptography.x509.oid import (
    AuthorityInformationAccessOID,
    ExtendedKeyUsageOID,
    NameOID,
)

# The keys a CA signs with, and those it accepts in a CSR.
PrivateKey = Union[rsa.RSAPrivateKey, ec.EllipticCurvePrivateKey]
PublicKey = Union[rsa.RSAPublicKey, ec.EllipticCurvePublicKey, ed25519.Ed25519PublicKey]
KeyType = Literal["ec-p256", "ec-p384", "rsa-3072", "rsa-4096"]
KEY_TYPES: tuple[str, ...] = get_args(KeyType)
DEFAULT_CA_KEY_TYPE: KeyType = "ec-p384"

MIN_RSA_BITS = 2048
MIN_RSA_PUBLIC_EXPONENT = 65537
RSA_PUBLIC_EXPONENT = 65537
ALLOWED_CURVES = frozenset({ec.SECP256R1.name, ec.SECP384R1.name})
WEAK_SIGNATURE_HASHES = frozenset({hashes.SHA1.name, hashes.MD5.name})
# A CSR (or a CA's PEM) longer than this is not one.
MAX_PEM_CHARS = 64 * 1024
# The longest CN RFC 5280 allows (ub-common-name).
MAX_COMMON_NAME = 64
# Backdating not_before tolerates a relying party's clock running behind.
CLOCK_SKEW = timedelta(minutes=5)

RevocationReason = Literal[
    "unspecified",
    "key_compromise",
    "affiliation_changed",
    "superseded",
    "cessation_of_operation",
    "privilege_withdrawn",
]
REVOCATION_REASONS: tuple[str, ...] = get_args(RevocationReason)
_REASON_FLAGS = {
    "key_compromise": x509.ReasonFlags.key_compromise,
    "affiliation_changed": x509.ReasonFlags.affiliation_changed,
    "superseded": x509.ReasonFlags.superseded,
    "cessation_of_operation": x509.ReasonFlags.cessation_of_operation,
    "privilege_withdrawn": x509.ReasonFlags.privilege_withdrawn,
}


class CertificateRefused(ValueError):
    """A CSR, key or CA the policy refuses. The text says why and is safe
    to show the requester (it never carries key material)."""


def _pem_bytes(pem: str, what: str) -> bytes:
    if not pem or not pem.strip():
        raise CertificateRefused(f"{what} is empty")
    if len(pem) > MAX_PEM_CHARS:
        raise CertificateRefused(f"{what} is too large")
    try:
        return pem.strip().encode("ascii")
    except UnicodeEncodeError:
        raise CertificateRefused(f"{what} is not PEM") from None


def checked_public_key(key: object, what: str = "the key") -> PublicKey:
    """``key`` if the policy accepts it (see the module text), else
    :class:`CertificateRefused`."""
    if isinstance(key, rsa.RSAPublicKey):
        if key.key_size < MIN_RSA_BITS:
            raise CertificateRefused(
                f"{what} is RSA-{key.key_size}; at least RSA-{MIN_RSA_BITS} is required"
            )
        if key.public_numbers().e < MIN_RSA_PUBLIC_EXPONENT:
            raise CertificateRefused(
                f"{what} has an RSA public exponent below {MIN_RSA_PUBLIC_EXPONENT}"
            )
        return key
    if isinstance(key, ec.EllipticCurvePublicKey):
        if key.curve.name not in ALLOWED_CURVES:
            raise CertificateRefused(
                f"{what} is on curve {key.curve.name}; P-256 or P-384 is required"
            )
        return key
    if isinstance(key, ed25519.Ed25519PublicKey):
        return key
    raise CertificateRefused(f"{what} is not RSA, ECDSA (P-256/P-384) or Ed25519")


def checked_csr(pem: str) -> x509.CertificateSigningRequest:
    """The CSR in ``pem`` if its signature verifies (the requester holds
    the private key), it is not signed with a weak hash, and its key meets
    the policy. Its subject and extensions are ignored, never refused."""
    raw = _pem_bytes(pem, "the CSR")
    try:
        csr = x509.load_pem_x509_csr(raw)
    except (ValueError, UnsupportedAlgorithm):
        raise CertificateRefused(
            "the CSR is not a PEM certificate signing request"
        ) from None
    try:
        signature_hash = csr.signature_hash_algorithm
        valid = csr.is_signature_valid
    except (ValueError, UnsupportedAlgorithm):
        raise CertificateRefused(
            "the CSR's signature algorithm is not supported"
        ) from None
    if signature_hash is not None and signature_hash.name in WEAK_SIGNATURE_HASHES:
        raise CertificateRefused(
            f"the CSR is signed with {signature_hash.name}, which is too weak"
        )
    if not valid:
        raise CertificateRefused("the CSR's signature does not verify")
    try:
        public_key = csr.public_key()
    except (ValueError, UnsupportedAlgorithm):
        raise CertificateRefused("the CSR's key is not supported") from None
    checked_public_key(public_key, "the CSR's key")
    return csr


def _signing_hash(key: PrivateKey) -> Union[hashes.SHA256, hashes.SHA384]:
    """The hash a signature by ``key`` uses."""
    if (
        isinstance(key, ec.EllipticCurvePrivateKey)
        and key.curve.name == ec.SECP384R1.name
    ):
        return hashes.SHA384()
    return hashes.SHA256()


def _spki(key: PublicKey) -> bytes:
    return key.public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )


def fingerprint(certificate: x509.Certificate) -> str:
    """The certificate's SHA-256 fingerprint, lowercase hex."""
    return certificate.fingerprint(hashes.SHA256()).hex()


def certificate_pem(certificate: x509.Certificate) -> str:
    return certificate.public_bytes(serialization.Encoding.PEM).decode("ascii")


def load_certificate(pem: str) -> x509.Certificate:
    try:
        return x509.load_pem_x509_certificate(_pem_bytes(pem, "the certificate"))
    except ValueError:
        raise CertificateRefused(
            "the certificate is not a PEM X.509 certificate"
        ) from None


def generate_key(key_type: KeyType) -> PrivateKey:
    if key_type == "ec-p256":
        return ec.generate_private_key(ec.SECP256R1())
    if key_type == "ec-p384":
        return ec.generate_private_key(ec.SECP384R1())
    if key_type == "rsa-3072":
        return rsa.generate_private_key(
            public_exponent=RSA_PUBLIC_EXPONENT, key_size=3072
        )
    if key_type == "rsa-4096":
        return rsa.generate_private_key(
            public_exponent=RSA_PUBLIC_EXPONENT, key_size=4096
        )
    raise CertificateRefused(f"key_type is one of {', '.join(KEY_TYPES)}")


@dataclass(frozen=True)
class Identity:
    """Who a certificate is issued to, as the server knows them."""

    user_id: str
    username: Optional[str] = None
    email: Optional[str] = None

    @property
    def common_name(self) -> str:
        for candidate in (self.username, self.email):
            if candidate and len(candidate) <= MAX_COMMON_NAME:
                return candidate
        return self.user_id[:MAX_COMMON_NAME]

    def subject(self) -> x509.Name:
        return x509.Name(
            [
                x509.NameAttribute(NameOID.COMMON_NAME, self.common_name),
                x509.NameAttribute(NameOID.USER_ID, self.user_id),
            ]
        )

    def alternative_names(self) -> List[x509.GeneralName]:
        names: List[x509.GeneralName] = []
        if self.email and self.email.isascii() and "@" in self.email:
            names.append(x509.RFC822Name(self.email))
        try:
            names.append(x509.UniformResourceIdentifier(uuid.UUID(self.user_id).urn))
        except ValueError:
            pass
        return names


@dataclass(frozen=True)
class Publication:
    """Where relying parties fetch this CA's CRL and certificate (embedded
    in what it issues); None leaves the extension out."""

    crl_url: Optional[str] = None
    ca_issuers_url: Optional[str] = None


@dataclass(frozen=True)
class Revocation:
    serial_number: int
    revoked_at: datetime
    reason: str


@dataclass(frozen=True)
class Authority:
    """A CA: its certificate and the private key it signs with."""

    certificate: x509.Certificate
    key: PrivateKey

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.certificate)

    @property
    def certificate_pem(self) -> str:
        return certificate_pem(self.certificate)

    def key_pem(self) -> str:
        """The private key as unencrypted PKCS#8 PEM: only ever handed to
        the framework's secret storage, which encrypts it at rest."""
        return self.key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode("ascii")

    @classmethod
    def loaded(
        cls,
        certificate_pem_text: str,
        key_pem_text: str,
        passphrase: Optional[str] = None,
    ) -> "Authority":
        """The CA in the PEMs given, checked: the certificate is a CA's
        (basicConstraints CA:true, keyUsage keyCertSign and cRLSign), the key
        meets the policy and is the certificate's."""
        certificate = load_certificate(certificate_pem_text)
        try:
            key = serialization.load_pem_private_key(
                _pem_bytes(key_pem_text, "the CA key"),
                password=passphrase.encode("utf-8") if passphrase else None,
            )
        except TypeError:
            raise CertificateRefused(
                "the CA key is encrypted: give its passphrase"
            ) from None
        except (ValueError, UnsupportedAlgorithm):
            raise CertificateRefused(
                "the CA key is not a PEM private key, or the passphrase is wrong"
            ) from None
        if not isinstance(key, (rsa.RSAPrivateKey, ec.EllipticCurvePrivateKey)):
            raise CertificateRefused(
                "the CA key is not RSA or ECDSA (P-256/P-384); an Ed25519 CA's "
                "certificates are refused by Web PKI path validators"
            )
        checked_public_key(key.public_key(), "the CA key")
        if _spki(key.public_key()) != _spki(
            checked_public_key(certificate.public_key(), "the CA certificate's key")
        ):
            raise CertificateRefused("the CA key is not the CA certificate's key")
        try:
            constraints = certificate.extensions.get_extension_for_class(
                x509.BasicConstraints
            ).value
            usage = certificate.extensions.get_extension_for_class(x509.KeyUsage).value
        except x509.ExtensionNotFound:
            raise CertificateRefused(
                "the certificate is not a CA's (basicConstraints and keyUsage are required)"
            ) from None
        if not constraints.ca:
            raise CertificateRefused(
                "the certificate is not a CA's (basicConstraints CA:false)"
            )
        if not (usage.key_cert_sign and usage.crl_sign):
            raise CertificateRefused(
                "the CA certificate's keyUsage must allow keyCertSign and cRLSign"
            )
        return cls(certificate=certificate, key=key)

    @classmethod
    def generated(
        cls, common_name: str, key_type: KeyType, lifetime: timedelta, now: datetime
    ) -> "Authority":
        """A new self-signed CA that issues end-entity certificates only."""
        if (
            not common_name
            or not common_name.strip()
            or len(common_name) > MAX_COMMON_NAME
        ):
            raise CertificateRefused(
                f"the CA's common name is 1-{MAX_COMMON_NAME} characters"
            )
        key = generate_key(key_type)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name.strip())])
        public_key = key.public_key()
        certificate = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(public_key)
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - CLOCK_SKEW)
            .not_valid_after(now + lifetime)
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=False,
                    content_commitment=False,
                    key_encipherment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=True,
                    crl_sign=True,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(
                x509.SubjectKeyIdentifier.from_public_key(public_key), critical=False
            )
            .sign(key, _signing_hash(key))
        )
        return cls(certificate=certificate, key=key)

    def _authority_key_identifier(self) -> x509.AuthorityKeyIdentifier:
        try:
            ski = self.certificate.extensions.get_extension_for_class(
                x509.SubjectKeyIdentifier
            ).value
            return x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(ski)
        except x509.ExtensionNotFound:
            return x509.AuthorityKeyIdentifier.from_issuer_public_key(
                self.key.public_key()
            )

    def issue(
        self,
        csr_key: object,
        identity: Identity,
        lifetime: timedelta,
        now: datetime,
        publication: Publication,
    ) -> x509.Certificate:
        """A TLS client certificate binding ``csr_key`` (a CSR's public
        key) to ``identity``."""
        public_key = checked_public_key(csr_key, "the CSR's key")
        if _spki(public_key) == _spki(self.key.public_key()):
            raise CertificateRefused("the CSR carries the CA's own key")
        ca_from = self.certificate.not_valid_before_utc
        ca_until = self.certificate.not_valid_after_utc
        if not ca_from <= now < ca_until:
            raise CertificateRefused("the CA certificate is not valid now")
        not_before = max(now - CLOCK_SKEW, ca_from)
        not_after = min(now + lifetime, ca_until)
        builder = (
            x509.CertificateBuilder()
            .subject_name(identity.subject())
            .issuer_name(self.certificate.subject)
            .public_key(public_key)
            .serial_number(x509.random_serial_number())
            .not_valid_before(not_before)
            .not_valid_after(not_after)
            .add_extension(
                x509.BasicConstraints(ca=False, path_length=None), critical=True
            )
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    content_commitment=False,
                    key_encipherment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=False,
                    crl_sign=False,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(
                x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False
            )
            .add_extension(
                x509.SubjectKeyIdentifier.from_public_key(public_key), critical=False
            )
            .add_extension(self._authority_key_identifier(), critical=False)
        )
        alternative_names = identity.alternative_names()
        if alternative_names:
            builder = builder.add_extension(
                x509.SubjectAlternativeName(alternative_names), critical=False
            )
        if publication.crl_url:
            builder = builder.add_extension(
                x509.CRLDistributionPoints(
                    [
                        x509.DistributionPoint(
                            full_name=[
                                x509.UniformResourceIdentifier(publication.crl_url)
                            ],
                            relative_name=None,
                            reasons=None,
                            crl_issuer=None,
                        )
                    ]
                ),
                critical=False,
            )
        if publication.ca_issuers_url:
            builder = builder.add_extension(
                x509.AuthorityInformationAccess(
                    [
                        x509.AccessDescription(
                            AuthorityInformationAccessOID.CA_ISSUERS,
                            x509.UniformResourceIdentifier(publication.ca_issuers_url),
                        )
                    ]
                ),
                critical=False,
            )
        return builder.sign(self.key, _signing_hash(self.key))

    def revocation_list(
        self, revoked: Iterable[Revocation], now: datetime, lifetime: timedelta
    ) -> x509.CertificateRevocationList:
        """A CRL listing ``revoked``, valid for ``lifetime``. Its number
        is the issue time in milliseconds, so it only ever increases."""
        builder = (
            x509.CertificateRevocationListBuilder()
            .issuer_name(self.certificate.subject)
            .last_update(now)
            .next_update(now + lifetime)
            .add_extension(self._authority_key_identifier(), critical=False)
            .add_extension(x509.CRLNumber(int(now.timestamp() * 1000)), critical=False)
        )
        for entry in revoked:
            revoked_builder = (
                x509.RevokedCertificateBuilder()
                .serial_number(entry.serial_number)
                .revocation_date(entry.revoked_at)
            )
            flag = _REASON_FLAGS.get(entry.reason)
            if flag is not None:
                revoked_builder = revoked_builder.add_extension(
                    x509.CRLReason(flag), critical=False
                )
            builder = builder.add_revoked_certificate(revoked_builder.build())
        return builder.sign(self.key, _signing_hash(self.key))
