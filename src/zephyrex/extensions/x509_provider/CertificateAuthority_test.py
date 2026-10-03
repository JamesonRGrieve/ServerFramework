# SPDX-License-Identifier: AGPL-3.0-or-later
"""The CA's cryptography with real keys: CSRs made with ``cryptography``,
issued certificates path-validated as a TLS server validates a client
certificate, and CRLs checked against the CA's key."""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import dsa, ec, ed448, ed25519, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from cryptography.x509.verification import PolicyBuilder, Store

from zephyrex.extensions.x509_provider.CertificateAuthority import (
    KEY_TYPES,
    Authority,
    CertificateRefused,
    Identity,
    Publication,
    Revocation,
    checked_csr,
    fingerprint,
)

NOW = datetime.now(timezone.utc).replace(microsecond=0)
LIFETIME = timedelta(days=30)
USER_ID = str(uuid.uuid4())
ALICE = Identity(user_id=USER_ID, username="alice", email="alice@example.test")


def csr_for(
    key: Any,
    *,
    common_name: str = "root",
    email: Optional[str] = "root@evil.test",
    algorithm: Optional[hashes.SHA256] = hashes.SHA256(),
) -> str:
    """A CSR for ``key`` claiming an identity it has no right to."""
    builder = x509.CertificateSigningRequestBuilder().subject_name(
        x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    )
    if email:
        builder = builder.add_extension(
            x509.SubjectAlternativeName([x509.RFC822Name(email)]), critical=False
        )
    if isinstance(key, (ed25519.Ed25519PrivateKey, ed448.Ed448PrivateKey)):
        algorithm = None
    return (
        builder.sign(key, algorithm).public_bytes(serialization.Encoding.PEM).decode()
    )


def rsa_key(bits: int = 2048, exponent: int = 65537) -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=exponent, key_size=bits)


@pytest.fixture(scope="module")
def authority() -> Authority:
    return Authority.generated("Test Client CA", "ec-p384", timedelta(days=365), NOW)


def verified_client(authority: Authority, leaf: x509.Certificate) -> Any:
    """Path-validate ``leaf`` as a TLS client certificate (EKU clientAuth)
    against the CA alone."""
    verifier = (
        PolicyBuilder()
        .store(Store([authority.certificate]))
        .time(NOW + timedelta(minutes=1))
        .build_client_verifier()
    )
    return verifier.verify(leaf, [])


# -- the CA itself -------------------------------------------------------------


@pytest.mark.parametrize("key_type", KEY_TYPES)
def test_a_generated_ca_is_a_self_signed_ca_for_end_entities(key_type):
    ca = Authority.generated("Generated CA", key_type, timedelta(days=10), NOW)
    certificate = ca.certificate
    certificate.verify_directly_issued_by(certificate)
    constraints = certificate.extensions.get_extension_for_class(x509.BasicConstraints)
    assert constraints.critical and constraints.value.ca
    assert constraints.value.path_length == 0
    usage = certificate.extensions.get_extension_for_class(x509.KeyUsage).value
    assert usage.key_cert_sign and usage.crl_sign
    assert certificate.not_valid_after_utc == NOW + timedelta(days=10)


@pytest.mark.parametrize("key_type", KEY_TYPES)
def test_every_ca_key_type_issues_certificates_verifiers_accept(key_type):
    """Ed25519 was offered for CAs until its chains were found to be refused
    by the Web PKI path validator (``Forbidden public key algorithm``)."""
    ca = Authority.generated("Generated CA", key_type, timedelta(days=10), NOW)
    key = ec.generate_private_key(ec.SECP256R1())
    leaf = ca.issue(key.public_key(), ALICE, LIFETIME, NOW, Publication())
    assert verified_client(ca, leaf).chain[-1] == ca.certificate
    crl = ca.revocation_list([], NOW, timedelta(hours=1))
    assert crl.is_signature_valid(ca.key.public_key())


def test_an_ed25519_ca_cannot_be_imported():
    key = ed25519.Ed25519PrivateKey.generate()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Ed25519 CA")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(NOW)
        .not_valid_after(NOW + LIFETIME)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(key, None)
    )
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    pem = certificate.public_bytes(serialization.Encoding.PEM).decode()
    with pytest.raises(CertificateRefused, match="Ed25519 CA"):
        Authority.loaded(pem, key_pem)


def test_a_ca_round_trips_through_its_pems(authority):
    again = Authority.loaded(authority.certificate_pem, authority.key_pem())
    assert (
        again.fingerprint == authority.fingerprint == fingerprint(authority.certificate)
    )


def test_an_encrypted_ca_key_needs_its_passphrase(authority):
    encrypted = authority.key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.BestAvailableEncryption(b"correct horse"),
    ).decode()
    assert Authority.loaded(authority.certificate_pem, encrypted, "correct horse")
    with pytest.raises(CertificateRefused, match="passphrase"):
        Authority.loaded(authority.certificate_pem, encrypted)
    with pytest.raises(CertificateRefused, match="passphrase"):
        Authority.loaded(authority.certificate_pem, encrypted, "wrong")


def test_a_key_that_is_not_the_certificates_is_refused(authority):
    other = Authority.generated("Other CA", "ec-p384", timedelta(days=10), NOW)
    with pytest.raises(CertificateRefused, match="not the CA certificate's key"):
        Authority.loaded(authority.certificate_pem, other.key_pem())


def test_a_certificate_that_is_not_a_cas_cannot_be_imported(authority):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "leaf")])
    leaf = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(NOW)
        .not_valid_after(NOW + LIFETIME)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(True, False, False, False, False, False, False, False, False),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    pem = leaf.public_bytes(serialization.Encoding.PEM).decode()
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    with pytest.raises(CertificateRefused, match="not a CA's"):
        Authority.loaded(pem, key_pem)


def test_a_weak_ca_key_cannot_be_imported():
    key = rsa_key(1024)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "weak CA")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(NOW)
        .not_valid_after(NOW + LIFETIME)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    with pytest.raises(CertificateRefused, match="RSA-1024"):
        Authority.loaded(
            certificate.public_bytes(serialization.Encoding.PEM).decode(), key_pem
        )


# -- CSRs ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "make_key",
    [
        lambda: rsa_key(2048),
        lambda: rsa_key(3072),
        lambda: ec.generate_private_key(ec.SECP256R1()),
        lambda: ec.generate_private_key(ec.SECP384R1()),
        ed25519.Ed25519PrivateKey.generate,
    ],
    ids=["rsa-2048", "rsa-3072", "p-256", "p-384", "ed25519"],
)
def test_strong_keys_are_accepted_and_chain_to_the_ca(authority, make_key):
    key = make_key()
    csr = checked_csr(csr_for(key))
    leaf = authority.issue(csr.public_key(), ALICE, LIFETIME, NOW, Publication())
    assert leaf.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    ) == key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    client = verified_client(authority, leaf)
    assert client.chain[-1] == authority.certificate


@pytest.mark.parametrize(
    "make_key, why",
    [
        (lambda: rsa_key(1024), "RSA-1024"),
        (lambda: rsa_key(2048, exponent=3), "exponent"),
        (lambda: ec.generate_private_key(ec.SECP521R1()), "secp521r1"),
        (lambda: ec.generate_private_key(ec.SECP256K1()), "secp256k1"),
        (ed448.Ed448PrivateKey.generate, "not RSA"),
        (lambda: dsa.generate_private_key(2048), "not RSA"),
    ],
    ids=["rsa-1024", "rsa-e3", "p-521", "secp256k1", "ed448", "dsa"],
)
def test_weak_or_unsupported_keys_are_refused(make_key, why):
    with pytest.raises(CertificateRefused, match=why):
        checked_csr(csr_for(make_key()))


def test_a_csr_whose_signature_does_not_verify_is_refused():
    key = ec.generate_private_key(ec.SECP256R1())
    csr = x509.load_pem_x509_csr(csr_for(key).encode())
    der = bytearray(csr.public_bytes(serialization.Encoding.DER))
    der[-1] ^= 0x01
    forged = x509.load_der_x509_csr(bytes(der)).public_bytes(serialization.Encoding.PEM)
    with pytest.raises(CertificateRefused, match="signature does not verify"):
        checked_csr(forged.decode())


def test_a_csr_signed_with_someone_elses_key_is_refused():
    """The CSR names one key but is signed by another: no proof of
    possession of the key it would certify."""
    victim = ec.generate_private_key(ec.SECP256R1())
    attacker = ec.generate_private_key(ec.SECP256R1())
    victim_csr = x509.load_pem_x509_csr(csr_for(victim).encode())
    attacker_csr = x509.load_pem_x509_csr(csr_for(attacker).encode())
    # The victim's request body under the attacker's signature: a CSR is
    # SEQUENCE { body, signatureAlgorithm, signature }.
    attacker_der = attacker_csr.public_bytes(serialization.Encoding.DER)
    attacker_body = attacker_csr.tbs_certrequest_bytes
    signature = attacker_der[attacker_der.index(attacker_body) + len(attacker_body) :]
    forged = _der_sequence(victim_csr.tbs_certrequest_bytes + signature)
    with pytest.raises(CertificateRefused, match="signature does not verify"):
        checked_csr(
            x509.load_der_x509_csr(forged)
            .public_bytes(serialization.Encoding.PEM)
            .decode()
        )


def _der_sequence(content: bytes) -> bytes:
    length = len(content)
    if length < 0x80:
        encoded = bytes([length])
    else:
        octets = length.to_bytes((length.bit_length() + 7) // 8, "big")
        encoded = bytes([0x80 | len(octets)]) + octets
    return b"\x30" + encoded + content


SHA1_WITH_RSA = bytes.fromhex("300d06092a864886f70d0101050500")


def _der_bit_string(content: bytes) -> bytes:
    return b"\x03" + _der_sequence(b"\x00" + content)[1:]


def test_a_sha1_signed_csr_is_refused():
    """``cryptography`` no longer signs CSRs with SHA-1, so the request is
    assembled by hand: a real RSA signature over the body, made with SHA-1."""
    from cryptography.hazmat.primitives.asymmetric import padding

    key = rsa_key(2048)
    body = x509.load_pem_x509_csr(csr_for(key).encode()).tbs_certrequest_bytes
    signature = key.sign(body, padding.PKCS1v15(), hashes.SHA1())
    der = _der_sequence(body + SHA1_WITH_RSA + _der_bit_string(signature))
    pem = x509.load_der_x509_csr(der).public_bytes(serialization.Encoding.PEM)
    with pytest.raises(CertificateRefused, match="sha1"):
        checked_csr(pem.decode())


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "not a csr",
        "-----BEGIN CERTIFICATE REQUEST-----\nAAAA\n-----END CERTIFICATE REQUEST-----",
    ],
)
def test_what_is_not_a_csr_is_refused(text):
    with pytest.raises(CertificateRefused):
        checked_csr(text)


def test_an_oversized_csr_is_refused():
    with pytest.raises(CertificateRefused, match="too large"):
        checked_csr("A" * 70000)


# -- issued certificates ------------------------------------------------------


def test_the_certificate_names_the_server_identity_not_the_csrs_claims(authority):
    key = ec.generate_private_key(ec.SECP256R1())
    csr = checked_csr(csr_for(key, common_name="root", email="root@evil.test"))
    leaf = authority.issue(csr.public_key(), ALICE, LIFETIME, NOW, Publication())
    assert leaf.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == "alice"
    assert leaf.subject.get_attributes_for_oid(NameOID.USER_ID)[0].value == USER_ID
    san = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert san.get_values_for_type(x509.RFC822Name) == ["alice@example.test"]
    assert san.get_values_for_type(x509.UniformResourceIdentifier) == [
        f"urn:uuid:{USER_ID}"
    ]
    assert "root" not in leaf.subject.rfc4514_string()
    assert leaf.issuer == authority.certificate.subject


def test_the_certificate_is_for_client_authentication_only(authority):
    key = ec.generate_private_key(ec.SECP256R1())
    leaf = authority.issue(key.public_key(), ALICE, LIFETIME, NOW, Publication())
    eku = leaf.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    assert list(eku) == [ExtendedKeyUsageOID.CLIENT_AUTH]
    constraints = leaf.extensions.get_extension_for_class(x509.BasicConstraints)
    assert constraints.critical and not constraints.value.ca
    usage = leaf.extensions.get_extension_for_class(x509.KeyUsage).value
    assert usage.digital_signature and not usage.key_cert_sign


def test_a_server_verifier_refuses_it_for_server_authentication(authority):
    """EKU clientAuth only: it cannot pose as a TLS server."""
    from cryptography.x509.verification import VerificationError

    key = ec.generate_private_key(ec.SECP256R1())
    leaf = authority.issue(key.public_key(), ALICE, LIFETIME, NOW, Publication())
    verifier = (
        PolicyBuilder()
        .store(Store([authority.certificate]))
        .time(NOW + timedelta(minutes=1))
        .build_server_verifier(x509.DNSName("alice.example.test"))
    )
    with pytest.raises(VerificationError):
        verifier.verify(leaf, [])


def test_validity_is_the_lifetime_asked_and_never_beyond_the_cas(authority):
    key = ec.generate_private_key(ec.SECP256R1())
    leaf = authority.issue(key.public_key(), ALICE, LIFETIME, NOW, Publication())
    assert leaf.not_valid_after_utc == NOW + LIFETIME
    assert NOW - timedelta(minutes=10) < leaf.not_valid_before_utc <= NOW
    short = Authority.generated("Short CA", "ec-p256", timedelta(days=2), NOW)
    capped = short.issue(key.public_key(), ALICE, LIFETIME, NOW, Publication())
    assert capped.not_valid_after_utc == short.certificate.not_valid_after_utc


def test_an_expired_ca_issues_nothing():
    expired = Authority.generated(
        "Old CA", "ec-p256", timedelta(days=1), NOW - timedelta(days=5)
    )
    key = ec.generate_private_key(ec.SECP256R1())
    with pytest.raises(CertificateRefused, match="not valid now"):
        expired.issue(key.public_key(), ALICE, LIFETIME, NOW, Publication())


def test_the_cas_own_key_is_never_certified(authority):
    with pytest.raises(CertificateRefused, match="CA's own key"):
        authority.issue(authority.key.public_key(), ALICE, LIFETIME, NOW, Publication())


def test_serials_are_large_random_and_distinct(authority):
    key = ec.generate_private_key(ec.SECP256R1())
    serials = [
        authority.issue(
            key.public_key(), ALICE, LIFETIME, NOW, Publication()
        ).serial_number
        for _ in range(40)
    ]
    assert len(set(serials)) == len(serials)
    assert all(0 < serial < 2**159 for serial in serials)
    assert all(serial > 2**64 for serial in serials)


def test_publication_urls_are_embedded(authority):
    key = ec.generate_private_key(ec.SECP256R1())
    leaf = authority.issue(
        key.public_key(),
        ALICE,
        LIFETIME,
        NOW,
        Publication(
            crl_url="https://ca.example.test/crl",
            ca_issuers_url="https://ca.example.test/ca",
        ),
    )
    points = leaf.extensions.get_extension_for_class(x509.CRLDistributionPoints).value
    assert [n.value for n in points[0].full_name] == ["https://ca.example.test/crl"]
    access = leaf.extensions.get_extension_for_class(
        x509.AuthorityInformationAccess
    ).value
    assert [d.access_location.value for d in access] == ["https://ca.example.test/ca"]
    aki = leaf.extensions.get_extension_for_class(x509.AuthorityKeyIdentifier).value
    ski = authority.certificate.extensions.get_extension_for_class(
        x509.SubjectKeyIdentifier
    ).value
    assert aki.key_identifier == ski.digest


def test_a_long_or_missing_username_falls_back(authority):
    long_name = Identity(user_id=USER_ID, username="u" * 80, email="bob@example.test")
    assert long_name.common_name == "bob@example.test"
    bare = Identity(user_id="not-a-uuid")
    assert bare.common_name == "not-a-uuid"
    assert bare.alternative_names() == []
    unicode_mail = Identity(user_id=USER_ID, email="jösé@exämple.test")
    assert [type(n) for n in unicode_mail.alternative_names()] == [
        x509.UniformResourceIdentifier
    ]


# -- CRLs ---------------------------------------------------------------------


def test_the_crl_lists_revoked_serials_signed_by_the_ca(authority):
    revoked: List[Revocation] = [
        Revocation(serial_number=0x1234, revoked_at=NOW, reason="key_compromise"),
        Revocation(serial_number=0xABCDEF, revoked_at=NOW, reason="unspecified"),
    ]
    crl = authority.revocation_list(revoked, NOW, timedelta(hours=24))
    assert crl.is_signature_valid(authority.key.public_key())
    assert crl.issuer == authority.certificate.subject
    assert crl.next_update_utc == NOW + timedelta(hours=24)
    entry = crl.get_revoked_certificate_by_serial_number(0x1234)
    assert entry is not None
    assert entry.extensions.get_extension_for_class(x509.CRLReason).value.reason == (
        x509.ReasonFlags.key_compromise
    )
    plain = crl.get_revoked_certificate_by_serial_number(0xABCDEF)
    assert plain is not None and len(plain.extensions) == 0
    assert crl.get_revoked_certificate_by_serial_number(0x9999) is None


def _crl_number(crl: x509.CertificateRevocationList) -> int:
    return int(crl.extensions.get_extension_for_class(x509.CRLNumber).value.crl_number)


def test_crl_numbers_increase(authority):
    first = authority.revocation_list([], NOW, timedelta(hours=1))
    later = authority.revocation_list(
        [], NOW + timedelta(seconds=1), timedelta(hours=1)
    )
    assert _crl_number(later) > _crl_number(first)
