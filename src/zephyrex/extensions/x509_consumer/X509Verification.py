# SPDX-License-Identifier: AGPL-3.0-or-later
"""Client certificate verification: what a mutual-TLS sign-in proves.

Pure certificate logic, no database: parsing the chain a TLS terminator
forwards (or an ASGI server hands over), verifying it against one trust
anchor with ``cryptography``'s X.509 path validation, checking revocation by
CRL and OCSP, and reading the identity attribute an anchor maps to a user.

Every refusal raises :class:`CertificateRefusedError` with a stable
``reason`` code; a value that is not a certificate at all raises
:class:`CertificateEncodingError`.
"""

import base64
import binascii
import enum
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, List, Literal, Optional, Sequence, Tuple, get_args
from urllib.parse import unquote

from cryptography import x509
from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import (
    dsa,
    ec,
    ed448,
    ed25519,
    padding,
    rsa,
)
from cryptography.x509 import ocsp
from cryptography.x509.oid import (
    AuthorityInformationAccessOID,
    ExtendedKeyUsageOID,
    NameOID,
    ObjectIdentifier,
)
from cryptography.x509.verification import (
    Criticality,
    ExtensionPolicy,
    Policy,
    PolicyBuilder,
    Store,
    VerificationError,
)

from zephyrex.extensions.ExternalErrors import BaseExternalError
from zephyrex.lib.Logging import logger
from zephyrex.lib.ProviderHTTPClient import ProviderHTTPClient

HTTP_SOURCE = "x509_consumer"
# A forwarded chain larger than this is not a client certificate chain.
MAX_FORWARDED_CHARS = 64 * 1024
MAX_PRESENTED_CERTIFICATES = 8
MAX_CRL_BYTES = 10 * 1024 * 1024
MAX_OCSP_RESPONSE_BYTES = 64 * 1024
# Tolerated disagreement between this server's clock and a CRL or OCSP
# responder's, and how old an OCSP answer without nextUpdate may be.
CLOCK_SKEW = timedelta(minutes=5)
OCSP_MAX_AGE_WITHOUT_NEXT_UPDATE = timedelta(hours=1)
OCSP_REQUEST_TYPE = "application/ocsp-request"
OCSP_RESPONSE_TYPE = "application/ocsp-response"
UPN_OID = ObjectIdentifier("1.3.6.1.4.1.311.20.2.3")
_DER_UTF8_STRING = 0x0C
_DER_SHORT_LENGTH_LIMIT = 0x80
_PEM_CERTIFICATE = re.compile(
    r"-----BEGIN CERTIFICATE-----(.*?)-----END CERTIFICATE-----", re.DOTALL
)
_HTTP_SCHEMES = ("http://", "https://")
# The keys a CA can sign a CRL with (X25519/X448 only agree keys).
_SIGNING_KEYS = (
    dsa.DSAPublicKey,
    rsa.RSAPublicKey,
    ec.EllipticCurvePublicKey,
    ed25519.Ed25519PublicKey,
    ed448.Ed448PublicKey,
)

RevocationCheck = Literal["none", "crl", "ocsp", "ocsp_then_crl"]
REVOCATION_CHECKS: Tuple[str, ...] = get_args(RevocationCheck)

# ``subject:<name>`` reads that subject attribute; ``san:<kind>`` that
# subjectAltName entry; ``subject`` the whole RFC 4514 subject DN.
SUBJECT_ATTRIBUTES = {
    "CN": NameOID.COMMON_NAME,
    "UID": NameOID.USER_ID,
    "emailAddress": NameOID.EMAIL_ADDRESS,
    "serialNumber": NameOID.SERIAL_NUMBER,
}
SAN_KINDS = ("email", "upn", "uri", "dns")
WHOLE_SUBJECT = "subject"
IDENTITY_ATTRIBUTES: Tuple[str, ...] = (
    WHOLE_SUBJECT,
    *(f"subject:{name}" for name in SUBJECT_ATTRIBUTES),
    *(f"san:{kind}" for kind in SAN_KINDS),
)
DEFAULT_IDENTITY_ATTRIBUTE = "subject:CN"


class CertificateEncodingError(ValueError):
    """The presented value is not a certificate (chain): the client's error."""


class CertificateRefusedError(Exception):
    """The certificate does not sign anyone in. ``reason`` is a stable code."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class RevocationStatus(enum.Enum):
    GOOD = "good"
    REVOKED = "revoked"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class TrustAnchor:
    """One configured trust anchor and how certificates under it are read."""

    id: str
    certificate: x509.Certificate
    identity_attribute: str = DEFAULT_IDENTITY_ATTRIBUTE
    trusted_for_email: bool = False
    revocation_check: str = "crl"
    revocation_required: bool = True
    crl_url: Optional[str] = None
    ocsp_url: Optional[str] = None


# -- parsing ------------------------------------------------------------------


def load_ca_certificate(pem: str) -> x509.Certificate:
    """The one CA certificate of ``pem``; refused when it is not exactly one
    certificate, or not a CA that may sign certificates."""
    certificates = _pem_certificates(pem)
    if len(certificates) != 1:
        raise CertificateEncodingError("Give exactly one PEM CA certificate")
    [certificate] = certificates
    try:
        constraints = certificate.extensions.get_extension_for_class(
            x509.BasicConstraints
        ).value
    except x509.ExtensionNotFound:
        raise CertificateEncodingError(
            "The certificate has no basicConstraints: it is not a CA"
        ) from None
    if not constraints.ca:
        raise CertificateEncodingError("The certificate is not a CA certificate")
    usage = _key_usage(certificate)
    if usage is not None and not usage.key_cert_sign:
        raise CertificateEncodingError("The CA's key usage does not allow keyCertSign")
    return certificate


def pem_of(certificate: x509.Certificate) -> str:
    return certificate.public_bytes(serialization.Encoding.PEM).decode()


def fingerprint_sha256(certificate: x509.Certificate) -> str:
    return certificate.fingerprint(hashes.SHA256()).hex()


def parse_forwarded_chain(value: str) -> List[x509.Certificate]:
    """The chain a TLS terminator forwarded in a header, leaf first.

    Accepts a URL-encoded PEM (nginx ``$ssl_client_escaped_cert``), a PEM
    whose line breaks became spaces or tabs, several PEM blocks (leaf, then
    intermediates), or comma-separated base64 DER (Traefik)."""
    if len(value) > MAX_FORWARDED_CHARS:
        raise CertificateEncodingError("The forwarded certificate is too large")
    text = unquote(value.strip())
    if "-----BEGIN" in text:
        certificates = _pem_certificates(text)
    else:
        certificates = [
            _der_certificate(_base64(part)) for part in text.split(",") if part.strip()
        ]
    return _bounded(certificates)


def parse_tls_extension_chain(chain: Iterable[str]) -> List[x509.Certificate]:
    """The ASGI TLS extension's ``client_cert_chain``: PEM strings, leaf first."""
    certificates: List[x509.Certificate] = []
    for pem in chain:
        certificates.extend(_pem_certificates(pem))
    return _bounded(certificates)


def _bounded(certificates: List[x509.Certificate]) -> List[x509.Certificate]:
    if not certificates:
        raise CertificateEncodingError("No certificate was presented")
    if len(certificates) > MAX_PRESENTED_CERTIFICATES:
        raise CertificateEncodingError("The presented chain is too long")
    return certificates


def _pem_certificates(text: str) -> List[x509.Certificate]:
    blocks = _PEM_CERTIFICATE.findall(text)
    if not blocks:
        raise CertificateEncodingError("No PEM certificate found")
    return [_der_certificate(_base64(block)) for block in blocks]


def _base64(text: str) -> bytes:
    try:
        return base64.b64decode("".join(text.split()), validate=True)
    except (binascii.Error, ValueError):
        raise CertificateEncodingError("The certificate is not valid base64") from None


def _der_certificate(der: bytes) -> x509.Certificate:
    try:
        return x509.load_der_x509_certificate(der)
    except ValueError:
        raise CertificateEncodingError(
            "The value is not an X.509 certificate"
        ) from None


# -- path validation -----------------------------------------------------------


def _key_usage(certificate: x509.Certificate) -> Optional[x509.KeyUsage]:
    try:
        return certificate.extensions.get_extension_for_class(x509.KeyUsage).value
    except x509.ExtensionNotFound:
        return None


def _extended_key_usage(
    certificate: x509.Certificate,
) -> Optional[x509.ExtendedKeyUsage]:
    try:
        return certificate.extensions.get_extension_for_class(
            x509.ExtendedKeyUsage
        ).value
    except x509.ExtensionNotFound:
        return None


def _require_client_auth(
    policy: Policy, certificate: x509.Certificate, usage: x509.ExtendedKeyUsage
) -> None:
    if ExtendedKeyUsageOID.CLIENT_AUTH not in usage:
        raise ValueError("extendedKeyUsage does not include clientAuth")


def _require_digital_signature(
    policy: Policy, certificate: x509.Certificate, usage: Optional[x509.KeyUsage]
) -> None:
    if usage is not None and not usage.digital_signature:
        raise ValueError("keyUsage does not include digitalSignature")


def _client_ee_policy() -> ExtensionPolicy:
    """The web PKI's end-entity rules, but: an extendedKeyUsage naming
    clientAuth is required (the default lets a certificate without one
    act for any purpose); a keyUsage, when present, must allow signing; and
    a subjectAltName is optional, since many client certificates identify
    their holder by subject alone."""
    return (
        ExtensionPolicy.webpki_defaults_ee()
        .require_present(
            x509.ExtendedKeyUsage, Criticality.AGNOSTIC, _require_client_auth
        )
        .may_be_present(x509.KeyUsage, Criticality.AGNOSTIC, _require_digital_signature)
        .may_be_present(x509.SubjectAlternativeName, Criticality.AGNOSTIC, None)
    )


def check_leaf(leaf: x509.Certificate, now: datetime) -> None:
    """What the leaf alone must satisfy, refused with a reason of its own
    before any path is built (the path validation enforces it again)."""
    if now < leaf.not_valid_before_utc:
        raise CertificateRefusedError(
            "not_yet_valid", "The client certificate is not yet valid"
        )
    if now > leaf.not_valid_after_utc:
        raise CertificateRefusedError("expired", "The client certificate has expired")
    usage = _extended_key_usage(leaf)
    if usage is None or ExtendedKeyUsageOID.CLIENT_AUTH not in usage:
        raise CertificateRefusedError(
            "not_for_client_auth",
            "The certificate is not issued for client authentication",
        )
    key_usage = _key_usage(leaf)
    if key_usage is not None and not key_usage.digital_signature:
        raise CertificateRefusedError(
            "not_for_client_auth", "The certificate's key usage does not allow signing"
        )


def verify_chain(
    leaf: x509.Certificate,
    intermediates: Sequence[x509.Certificate],
    anchor: x509.Certificate,
    now: datetime,
) -> Optional[List[x509.Certificate]]:
    """The validated path from ``leaf`` to ``anchor`` (leaf first, anchor
    last), or None when no path to this anchor is valid at ``now``."""
    verifier = (
        PolicyBuilder()
        .store(Store([anchor]))
        .time(now)
        .extension_policies(
            ca_policy=ExtensionPolicy.webpki_defaults_ca(),
            ee_policy=_client_ee_policy(),
        )
        .build_client_verifier()
    )
    try:
        return list(verifier.verify(leaf, list(intermediates)).chain)
    except VerificationError as exc:
        logger.debug("x509_consumer: no path to an anchor: %s", exc)
        return None


# -- identity -----------------------------------------------------------------


def _san(certificate: x509.Certificate) -> Optional[x509.SubjectAlternativeName]:
    try:
        return certificate.extensions.get_extension_for_class(
            x509.SubjectAlternativeName
        ).value
    except x509.ExtensionNotFound:
        return None


def _der_utf8_string(der: bytes) -> str:
    """The text of a DER UTF8String (how a UPN otherName is encoded)."""
    if len(der) < 2 or der[0] != _DER_UTF8_STRING:
        raise CertificateRefusedError("no_identity", "The UPN is not a UTF8String")
    length, offset = der[1], 2
    if length >= _DER_SHORT_LENGTH_LIMIT:
        width = length - _DER_SHORT_LENGTH_LIMIT
        length, offset = int.from_bytes(der[2 : 2 + width], "big"), 2 + width
    if offset + length != len(der):
        raise CertificateRefusedError("no_identity", "The UPN is malformed")
    try:
        return der[offset:].decode("utf-8")
    except UnicodeDecodeError:
        raise CertificateRefusedError("no_identity", "The UPN is not UTF-8") from None


def _san_values(certificate: x509.Certificate, kind: str) -> List[str]:
    san = _san(certificate)
    if san is None:
        return []
    if kind == "email":
        return list(san.get_values_for_type(x509.RFC822Name))
    if kind == "uri":
        return list(san.get_values_for_type(x509.UniformResourceIdentifier))
    if kind == "dns":
        return list(san.get_values_for_type(x509.DNSName))
    return [
        _der_utf8_string(other.value)
        for other in san.get_values_for_type(x509.OtherName)
        if other.type_id == UPN_OID
    ]


def _subject_values(certificate: x509.Certificate, name: str) -> List[str]:
    return [
        str(attribute.value)
        for attribute in certificate.subject.get_attributes_for_oid(
            SUBJECT_ATTRIBUTES[name]
        )
    ]


def validate_identity_attribute(attribute: str) -> str:
    if attribute not in IDENTITY_ATTRIBUTES:
        raise CertificateEncodingError(
            f"identity_attribute must be one of {', '.join(IDENTITY_ATTRIBUTES)}"
        )
    return attribute


def identity_of(certificate: x509.Certificate, attribute: str) -> str:
    """The one value of ``attribute`` in ``certificate``: refused when it is
    absent, or present more than once (which of them would be the user?)."""
    if attribute == WHOLE_SUBJECT:
        subject = certificate.subject.rfc4514_string()
        if not subject:
            raise CertificateRefusedError(
                "no_identity", "The certificate has no subject"
            )
        return subject
    source, _, name = attribute.partition(":")
    values = (
        _subject_values(certificate, name)
        if source == "subject"
        else _san_values(certificate, name)
    )
    distinct = sorted({value.strip() for value in values if value.strip()})
    if not distinct:
        raise CertificateRefusedError(
            "no_identity", f"The certificate carries no {attribute}"
        )
    if len(distinct) > 1:
        raise CertificateRefusedError(
            "ambiguous_identity", f"The certificate carries more than one {attribute}"
        )
    return distinct[0]


def email_of(certificate: x509.Certificate) -> Optional[str]:
    """The certificate's one email (subjectAltName, else the subject's
    emailAddress), lower-cased; None when it has none or several."""
    values = _san_values(certificate, "email") or _subject_values(
        certificate, "emailAddress"
    )
    distinct = {value.strip().lower() for value in values if value.strip()}
    return distinct.pop() if len(distinct) == 1 else None


# -- revocation ---------------------------------------------------------------


def _http_urls(urls: Iterable[str]) -> List[str]:
    return [url for url in urls if url.lower().startswith(_HTTP_SCHEMES)]


def crl_urls(
    certificate: x509.Certificate, issuer: x509.Certificate, anchor: TrustAnchor
) -> List[str]:
    """Where ``certificate``'s CRL is: the anchor's configured CRL for the
    certificates it issued itself, then the certificate's own distribution
    points (HTTP ones; LDAP is not fetched)."""
    urls: List[str] = []
    if anchor.crl_url and issuer == anchor.certificate:
        urls.append(anchor.crl_url)
    try:
        points = certificate.extensions.get_extension_for_class(
            x509.CRLDistributionPoints
        ).value
    except x509.ExtensionNotFound:
        points = x509.CRLDistributionPoints([])
    for point in points:
        for name in point.full_name or []:
            if isinstance(name, x509.UniformResourceIdentifier):
                urls.append(name.value)
    return _http_urls(urls)


def ocsp_urls(
    certificate: x509.Certificate, issuer: x509.Certificate, anchor: TrustAnchor
) -> List[str]:
    """Where to ask about ``certificate``: the anchor's configured responder
    for the certificates it issued itself, then the certificate's AIA."""
    urls: List[str] = []
    if anchor.ocsp_url and issuer == anchor.certificate:
        urls.append(anchor.ocsp_url)
    try:
        access = certificate.extensions.get_extension_for_class(
            x509.AuthorityInformationAccess
        ).value
    except x509.ExtensionNotFound:
        access = x509.AuthorityInformationAccess([])
    for description in access:
        if description.access_method == AuthorityInformationAccessOID.OCSP and (
            isinstance(description.access_location, x509.UniformResourceIdentifier)
        ):
            urls.append(description.access_location.value)
    return _http_urls(urls)


def _load_crl(body: bytes) -> Optional[x509.CertificateRevocationList]:
    for loader in (x509.load_der_x509_crl, x509.load_pem_x509_crl):
        try:
            return loader(body)
        except ValueError:
            continue
    return None


def _crl_covers(
    crl: x509.CertificateRevocationList, certificate: x509.Certificate
) -> bool:
    """A complete CRL whose scope includes ``certificate``: no delta CRL,
    no indirect or reason-partitioned one, and no CA-only CRL for a leaf
    (or user-only CRL for a CA)."""
    try:
        crl.extensions.get_extension_for_class(x509.DeltaCRLIndicator)
        return False
    except x509.ExtensionNotFound:
        pass
    try:
        point = crl.extensions.get_extension_for_class(
            x509.IssuingDistributionPoint
        ).value
    except x509.ExtensionNotFound:
        return True
    try:
        is_ca = certificate.extensions.get_extension_for_class(
            x509.BasicConstraints
        ).value.ca
    except x509.ExtensionNotFound:
        is_ca = False
    return not (
        point.indirect_crl
        or point.only_some_reasons
        or point.only_contains_attribute_certs
        or (point.only_contains_ca_certs and not is_ca)
        or (point.only_contains_user_certs and is_ca)
    )


def crl_status(
    crl: x509.CertificateRevocationList,
    certificate: x509.Certificate,
    issuer: x509.Certificate,
    now: datetime,
) -> RevocationStatus:
    """``certificate``'s status by ``crl``, UNKNOWN unless the CRL is the
    issuer's, signed by it, current at ``now`` and covers the certificate."""
    usage = _key_usage(issuer)
    if (
        crl.issuer != issuer.subject
        or (usage is not None and not usage.crl_sign)
        or crl.next_update_utc is None
        or now > crl.next_update_utc + CLOCK_SKEW
        or now + CLOCK_SKEW < crl.last_update_utc
        or not _crl_covers(crl, certificate)
    ):
        return RevocationStatus.UNKNOWN
    key = issuer.public_key()
    if not isinstance(key, _SIGNING_KEYS):
        return RevocationStatus.UNKNOWN
    try:
        if not crl.is_signature_valid(key):
            return RevocationStatus.UNKNOWN
    except (TypeError, UnsupportedAlgorithm):
        return RevocationStatus.UNKNOWN
    if crl.get_revoked_certificate_by_serial_number(certificate.serial_number):
        return RevocationStatus.REVOKED
    return RevocationStatus.GOOD


def _signature_valid(
    certificate: x509.Certificate,
    signature: bytes,
    data: bytes,
    algorithm: Optional[hashes.HashAlgorithm],
) -> bool:
    key = certificate.public_key()
    try:
        if isinstance(key, rsa.RSAPublicKey) and algorithm is not None:
            key.verify(signature, data, padding.PKCS1v15(), algorithm)
        elif isinstance(key, ec.EllipticCurvePublicKey) and algorithm is not None:
            key.verify(signature, data, ec.ECDSA(algorithm))
        elif isinstance(key, (ed25519.Ed25519PublicKey, ed448.Ed448PublicKey)):
            key.verify(signature, data)
        else:
            return False
    except (InvalidSignature, UnsupportedAlgorithm):
        return False
    return True


def _key_hash(certificate: x509.Certificate) -> bytes:
    """SHA-1 of the subjectPublicKey bits (an OCSP ResponderID byKey)."""
    return x509.SubjectKeyIdentifier.from_public_key(certificate.public_key()).digest


def _ocsp_signer(
    response: ocsp.OCSPResponse, issuer: x509.Certificate, now: datetime
) -> Optional[x509.Certificate]:
    """Who may have signed ``response``: the issuer itself, or a responder
    the issuer delegated to (issued by it, for OCSPSigning, valid now)."""

    def named(certificate: x509.Certificate) -> bool:
        if response.responder_key_hash is not None:
            return _key_hash(certificate) == response.responder_key_hash
        return certificate.subject == response.responder_name

    if named(issuer):
        return issuer
    for candidate in response.certificates:
        if not named(candidate):
            continue
        usage = _extended_key_usage(candidate)
        if (
            usage is None
            or ExtendedKeyUsageOID.OCSP_SIGNING not in usage
            or not candidate.not_valid_before_utc
            <= now
            <= candidate.not_valid_after_utc
        ):
            continue
        try:
            candidate.verify_directly_issued_by(issuer)
        except (ValueError, TypeError, InvalidSignature, UnsupportedAlgorithm):
            continue
        return candidate
    return None


def ocsp_status(
    body: bytes,
    request: ocsp.OCSPRequest,
    issuer: x509.Certificate,
    now: datetime,
) -> RevocationStatus:
    """The status an OCSP response ``body`` gives for ``request``'s
    certificate; UNKNOWN unless it is a successful, authorized, current
    answer about that very certificate."""
    try:
        response = ocsp.load_der_ocsp_response(body)
    except ValueError:
        return RevocationStatus.UNKNOWN
    if response.response_status != ocsp.OCSPResponseStatus.SUCCESSFUL:
        return RevocationStatus.UNKNOWN
    signer = _ocsp_signer(response, issuer, now)
    if signer is None or not _signature_valid(
        signer,
        response.signature,
        response.tbs_response_bytes,
        response.signature_hash_algorithm,
    ):
        return RevocationStatus.UNKNOWN
    for single in response.responses:
        if (
            single.serial_number != request.serial_number
            or single.issuer_key_hash != request.issuer_key_hash
            or single.issuer_name_hash != request.issuer_name_hash
            or not isinstance(single.hash_algorithm, type(request.hash_algorithm))
        ):
            continue
        if now + CLOCK_SKEW < single.this_update_utc:
            return RevocationStatus.UNKNOWN
        if single.next_update_utc is None:
            if now - single.this_update_utc > OCSP_MAX_AGE_WITHOUT_NEXT_UPDATE:
                return RevocationStatus.UNKNOWN
        elif now > single.next_update_utc + CLOCK_SKEW:
            return RevocationStatus.UNKNOWN
        if single.certificate_status == ocsp.OCSPCertStatus.GOOD:
            return RevocationStatus.GOOD
        if single.certificate_status == ocsp.OCSPCertStatus.REVOKED:
            return RevocationStatus.REVOKED
        return RevocationStatus.UNKNOWN
    return RevocationStatus.UNKNOWN


class RevocationChecker:
    """Fetches CRLs and asks OCSP responders, over the framework's guarded
    HTTP client (SSRF guard: an internal CA's endpoints must be listed in
    ``EGRESS_ALLOWED_HOSTS``)."""

    def __init__(self) -> None:
        self.http = ProviderHTTPClient(provider_name=HTTP_SOURCE)

    async def by_crl(
        self,
        certificate: x509.Certificate,
        issuer: x509.Certificate,
        anchor: TrustAnchor,
        now: datetime,
    ) -> RevocationStatus:
        for url in crl_urls(certificate, issuer, anchor):
            try:
                fetched = await self.http.fetch(url, max_bytes=MAX_CRL_BYTES)
            except BaseExternalError as exc:
                logger.warning("x509_consumer: CRL %s unavailable: %s", url, exc)
                continue
            crl = None if fetched.truncated else _load_crl(fetched.body)
            if crl is None:
                logger.warning("x509_consumer: %s is not a CRL", url)
                continue
            status = crl_status(crl, certificate, issuer, now)
            if status is not RevocationStatus.UNKNOWN:
                return status
            logger.warning("x509_consumer: CRL %s is not usable", url)
        return RevocationStatus.UNKNOWN

    async def by_ocsp(
        self,
        certificate: x509.Certificate,
        issuer: x509.Certificate,
        anchor: TrustAnchor,
        now: datetime,
    ) -> RevocationStatus:
        request = (
            ocsp.OCSPRequestBuilder()
            # SHA-1 CertIDs are what responders universally accept; the hash
            # only names the certificate, it protects nothing.
            .add_certificate(certificate, issuer, hashes.SHA1()).build()
        )
        der = request.public_bytes(serialization.Encoding.DER)
        for url in ocsp_urls(certificate, issuer, anchor):
            try:
                answer = await self.http.post(
                    url,
                    raw=True,
                    content=der,
                    headers={
                        "Content-Type": OCSP_REQUEST_TYPE,
                        "Accept": OCSP_RESPONSE_TYPE,
                    },
                )
            except BaseExternalError as exc:
                logger.warning("x509_consumer: OCSP %s unavailable: %s", url, exc)
                continue
            body = bytes(answer.content)
            if len(body) > MAX_OCSP_RESPONSE_BYTES:
                continue
            status = ocsp_status(body, request, issuer, now)
            if status is not RevocationStatus.UNKNOWN:
                return status
            logger.warning("x509_consumer: OCSP %s gave no usable answer", url)
        return RevocationStatus.UNKNOWN

    async def status(
        self,
        certificate: x509.Certificate,
        issuer: x509.Certificate,
        anchor: TrustAnchor,
        now: datetime,
    ) -> RevocationStatus:
        mode = anchor.revocation_check
        if mode == "none":
            return RevocationStatus.GOOD
        if mode in ("ocsp", "ocsp_then_crl"):
            found = await self.by_ocsp(certificate, issuer, anchor, now)
            if found is not RevocationStatus.UNKNOWN or mode == "ocsp":
                return found
        return await self.by_crl(certificate, issuer, anchor, now)

    async def check_chain(
        self, chain: Sequence[x509.Certificate], anchor: TrustAnchor, now: datetime
    ) -> None:
        """Refuse when any certificate of the validated ``chain`` below the
        anchor is revoked, or when its status is unknown and the anchor
        requires one."""
        for certificate, issuer in zip(chain, chain[1:]):
            status = await self.status(certificate, issuer, anchor, now)
            if status is RevocationStatus.REVOKED:
                raise CertificateRefusedError(
                    "revoked",
                    (
                        "The client certificate has been revoked"
                        if certificate is chain[0]
                        else "A certificate of the client's chain has been revoked"
                    ),
                )
            if status is RevocationStatus.UNKNOWN:
                if anchor.revocation_required:
                    raise CertificateRefusedError(
                        "revocation_unknown",
                        "The certificate's revocation status could not be established",
                    )
                logger.warning(
                    "x509_consumer: revocation status of %s unknown; allowed by "
                    "anchor %s",
                    certificate.subject.rfc4514_string(),
                    anchor.id,
                )
