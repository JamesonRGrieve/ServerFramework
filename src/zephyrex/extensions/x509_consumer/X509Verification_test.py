# SPDX-License-Identifier: AGPL-3.0-or-later
"""How forwarded certificates are read and identities taken from them,
with real certificates."""

import base64
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from zephyrex.extensions.x509_consumer.X509Verification import (
    MAX_PRESENTED_CERTIFICATES,
    UPN_OID,
    CertificateEncodingError,
    CertificateRefusedError,
    email_of,
    identity_of,
    load_ca_certificate,
    parse_forwarded_chain,
    parse_tls_extension_chain,
    validate_identity_attribute,
)

NOW = datetime.now(timezone.utc)


def _certificate(
    subject: x509.Name,
    *,
    ca: bool = False,
    names: tuple = (),
) -> x509.Certificate:
    key = ec.generate_private_key(ec.SECP256R1())
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(NOW - timedelta(days=1))
        .not_valid_after(NOW + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), True)
    )
    if names:
        builder = builder.add_extension(x509.SubjectAlternativeName(list(names)), False)
    return builder.sign(key, hashes.SHA256())


def _cn(value: str, *extra: x509.NameAttribute) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, value), *extra])


def _pem(certificate: x509.Certificate) -> str:
    return certificate.public_bytes(serialization.Encoding.PEM).decode()


LEAF = _certificate(_cn("alice"))
SECOND = _certificate(_cn("issuer"), ca=True)


class TestForwardedChain:
    def test_a_url_encoded_pem_as_nginx_escapes_it(self) -> None:
        assert parse_forwarded_chain(quote(_pem(LEAF))) == [LEAF]

    def test_a_pem_whose_line_breaks_became_spaces_or_tabs(self) -> None:
        flattened = _pem(LEAF).replace("\n", " ")
        assert parse_forwarded_chain(flattened) == [LEAF]
        assert parse_forwarded_chain(_pem(LEAF).replace("\n", "\n\t")) == [LEAF]

    def test_several_pem_blocks_are_the_leaf_then_its_chain(self) -> None:
        assert parse_forwarded_chain(quote(_pem(LEAF) + _pem(SECOND))) == [
            LEAF,
            SECOND,
        ]

    def test_comma_separated_base64_der(self) -> None:
        ders = [
            base64.b64encode(c.public_bytes(serialization.Encoding.DER)).decode()
            for c in (LEAF, SECOND)
        ]
        assert parse_forwarded_chain(",".join(ders)) == [LEAF, SECOND]

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "not base64 !!",
            base64.b64encode(b"not a certificate").decode(),
            "-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----",
            "x" * (64 * 1024 + 1),
        ],
    )
    def test_what_is_not_a_certificate_is_refused(self, value: str) -> None:
        with pytest.raises(CertificateEncodingError):
            parse_forwarded_chain(value)

    def test_a_chain_too_long_is_refused(self) -> None:
        with pytest.raises(CertificateEncodingError):
            parse_forwarded_chain(_pem(LEAF) * (MAX_PRESENTED_CERTIFICATES + 1))

    def test_the_tls_extension_chain(self) -> None:
        assert parse_tls_extension_chain([_pem(LEAF), _pem(SECOND)]) == [LEAF, SECOND]
        with pytest.raises(CertificateEncodingError):
            parse_tls_extension_chain([])


class TestIdentity:
    def test_the_attributes_an_anchor_may_map(self) -> None:
        certificate = _certificate(
            _cn(
                "alice",
                x509.NameAttribute(NameOID.USER_ID, "a1001"),
                x509.NameAttribute(NameOID.EMAIL_ADDRESS, "Alice@Example.COM"),
            ),
            names=(
                x509.RFC822Name("alice@example.com"),
                x509.UniformResourceIdentifier("urn:example:alice"),
                x509.DNSName("alice.example.com"),
                x509.OtherName(UPN_OID, b"\x0c\x10alice@corp.local"),
            ),
        )
        assert identity_of(certificate, "subject:CN") == "alice"
        assert identity_of(certificate, "subject:UID") == "a1001"
        assert identity_of(certificate, "san:email") == "alice@example.com"
        assert identity_of(certificate, "san:uri") == "urn:example:alice"
        assert identity_of(certificate, "san:dns") == "alice.example.com"
        assert identity_of(certificate, "san:upn") == "alice@corp.local"
        assert identity_of(certificate, "subject").startswith("1.2.840.113549")
        # The subjectAltName email wins over the subject's, lower-cased.
        assert email_of(certificate) == "alice@example.com"

    def test_a_long_upn_uses_the_long_length_form(self) -> None:
        upn = ("u" * 200) + "@corp.local"
        encoded = upn.encode()
        der = b"\x0c\x81" + bytes([len(encoded)]) + encoded
        certificate = _certificate(_cn("long"), names=(x509.OtherName(UPN_OID, der),))
        assert identity_of(certificate, "san:upn") == upn

    def test_a_upn_that_is_not_a_utf8_string_is_refused(self) -> None:
        certificate = _certificate(
            _cn("bad"), names=(x509.OtherName(UPN_OID, b"\x04\x03abc"),)
        )
        with pytest.raises(CertificateRefusedError):
            identity_of(certificate, "san:upn")

    def test_a_missing_or_doubled_attribute_identifies_no_one(self) -> None:
        with pytest.raises(CertificateRefusedError) as missing:
            identity_of(LEAF, "san:email")
        assert missing.value.reason == "no_identity"
        doubled = _certificate(
            _cn("x"), names=(x509.RFC822Name("a@x.test"), x509.RFC822Name("b@x.test"))
        )
        with pytest.raises(CertificateRefusedError) as ambiguous:
            identity_of(doubled, "san:email")
        assert ambiguous.value.reason == "ambiguous_identity"
        assert email_of(doubled) is None

    def test_only_known_attributes_are_accepted(self) -> None:
        assert validate_identity_attribute("san:upn") == "san:upn"
        with pytest.raises(CertificateEncodingError):
            validate_identity_attribute("subject:OU")


class TestAnchorCertificate:
    def test_a_ca_certificate_is_accepted(self) -> None:
        assert load_ca_certificate(_pem(SECOND)) == SECOND

    @pytest.mark.parametrize(
        "pem",
        [_pem(LEAF), _pem(SECOND) + _pem(SECOND), "garbage"],
        ids=["not-a-ca", "two-certificates", "garbage"],
    )
    def test_anything_else_is_refused(self, pem: str) -> None:
        with pytest.raises(CertificateEncodingError):
            load_ca_certificate(pem)
