# SPDX-License-Identifier: AGPL-3.0-or-later
"""A software WebAuthn authenticator and attestation CA for the tests.

Real keys (``cryptography``), real CBOR (``cbor2``), and spec-shaped
authenticator data, client data and attestation statements (none, packed
self and full, fido-u2f, tpm, apple), so the Relying Party verifies genuine
signatures exactly as it would from hardware. Knobs let a test produce
each refused response: another origin or rpId, no user verification, a
replayed or foreign challenge, a broken signature, a stale counter.

The tests at the bottom check the authenticator against py_webauthn's own
parsers, so a failing ceremony test points at the server, not at this
fixture."""

import base64
import hashlib
import json
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Literal, Optional, Tuple

import cbor2
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from webauthn.helpers import (
    decode_credential_public_key,
    parse_attestation_object,
    parse_authenticator_data,
    parse_client_data_json,
)

Algorithm = Literal["es256", "rs256", "ed25519"]
Format = Literal["none", "packed-self", "packed", "fido-u2f", "tpm", "apple"]

FLAG_UP = 0x01
FLAG_UV = 0x04
FLAG_BE = 0x08
FLAG_BS = 0x10
FLAG_AT = 0x40

COSE_ES256 = -7
COSE_RS256 = -257
COSE_EDDSA = -8
_RSA_KEY_BITS = 2048
_RSA_EXPONENT = 65537
_CERT_LIFETIME = timedelta(days=30)
_APPLE_NONCE_OID = x509.ObjectIdentifier("1.2.840.113635.100.8.2")
_TPM_AIK_EKU = x509.ObjectIdentifier("2.23.133.8.3")
_TPM_MANUFACTURER = x509.ObjectIdentifier("2.23.133.2.1")
_TPM_MODEL = x509.ObjectIdentifier("2.23.133.2.2")
_TPM_VERSION = x509.ObjectIdentifier("2.23.133.2.3")
# TPM 2.0 constants (TPM 2.0 Part 2): TPM_GENERATED_VALUE, TPM_ST_ATTEST_CERTIFY,
# TPM_ALG_RSA / _ECC / _SHA256 / _NULL, TPM_ECC_NIST_P256.
_TPM_GENERATED = b"\xff\x54\x43\x47"
_TPM_ST_ATTEST_CERTIFY = b"\x80\x17"
_TPM_ALG_RSA = b"\x00\x01"
_TPM_ALG_SHA256 = b"\x00\x0b"
_TPM_ALG_NULL = b"\x00\x10"
_TPM_OBJECT_ATTRIBUTES = (0x00060472).to_bytes(4, "big")


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def from_b64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _u16(data: bytes) -> bytes:
    return len(data).to_bytes(2, "big") + data


def _ec_point(key: ec.EllipticCurvePublicKey) -> Tuple[bytes, bytes]:
    numbers = key.public_numbers()
    return numbers.x.to_bytes(32, "big"), numbers.y.to_bytes(32, "big")


class CertificateAuthority:
    """An attestation root that issues leaf certificates."""

    def __init__(self, name: str = "Software Attestation Root") -> None:
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
        now = datetime.now(timezone.utc)
        self.certificate = (
            x509.CertificateBuilder()
            .subject_name(self.name)
            .issuer_name(self.name)
            .public_key(self.key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + _CERT_LIFETIME)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
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
                True,
            )
            .sign(self.key, hashes.SHA256())
        )

    @property
    def pem(self) -> bytes:
        return self.certificate.public_bytes(serialization.Encoding.PEM)

    def issue(
        self,
        public_key: Any,
        subject: x509.Name,
        extensions: List[Tuple[x509.ExtensionType, bool]],
    ) -> bytes:
        now = datetime.now(timezone.utc)
        builder = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(self.name)
            .public_key(public_key)
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + _CERT_LIFETIME)
        )
        for extension, critical in extensions:
            builder = builder.add_extension(extension, critical)
        certificate = builder.sign(self.key, hashes.SHA256())
        return certificate.public_bytes(serialization.Encoding.DER)


def _leaf_name() -> x509.Name:
    return x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "CA"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Software Authenticators"),
            x509.NameAttribute(
                NameOID.ORGANIZATIONAL_UNIT_NAME, "Authenticator Attestation"
            ),
            x509.NameAttribute(NameOID.COMMON_NAME, "Software Key"),
        ]
    )


def _not_ca() -> Tuple[x509.ExtensionType, bool]:
    return x509.BasicConstraints(ca=False, path_length=None), True


@dataclass
class SoftwareAuthenticator:
    """One credential on a software authenticator."""

    algorithm: Algorithm = "es256"
    aaguid: bytes = field(default_factory=lambda: secrets.token_bytes(16))
    backup_eligible: bool = False
    transports: Tuple[str, ...] = ("usb",)
    credential_id: bytes = field(default_factory=lambda: secrets.token_bytes(32))
    sign_count: int = 0
    user_handle: Optional[bytes] = None

    def __post_init__(self) -> None:
        if self.algorithm == "es256":
            self.private_key: Any = ec.generate_private_key(ec.SECP256R1())
        elif self.algorithm == "rs256":
            self.private_key = rsa.generate_private_key(
                public_exponent=_RSA_EXPONENT, key_size=_RSA_KEY_BITS
            )
        else:
            self.private_key = ed25519.Ed25519PrivateKey.generate()

    # -- Keys ---------------------------------------------------------------

    @property
    def cose_algorithm(self) -> int:
        return {"es256": COSE_ES256, "rs256": COSE_RS256, "ed25519": COSE_EDDSA}[
            self.algorithm
        ]

    def cose_public_key(self) -> bytes:
        public = self.private_key.public_key()
        if self.algorithm == "es256":
            x, y = _ec_point(public)
            return cbor2.dumps({1: 2, 3: COSE_ES256, -1: 1, -2: x, -3: y})
        if self.algorithm == "rs256":
            numbers = public.public_numbers()
            n = numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")
            e = numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big")
            return cbor2.dumps({1: 3, 3: COSE_RS256, -1: n, -2: e})
        raw = public.public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        return cbor2.dumps({1: 1, 3: COSE_EDDSA, -1: 6, -2: raw})

    def sign(self, data: bytes) -> bytes:
        if self.algorithm == "es256":
            signature: bytes = self.private_key.sign(data, ec.ECDSA(hashes.SHA256()))
        elif self.algorithm == "rs256":
            signature = self.private_key.sign(data, padding.PKCS1v15(), hashes.SHA256())
        else:
            signature = self.private_key.sign(data)
        return signature

    # -- Data ---------------------------------------------------------------

    @staticmethod
    def client_data(
        kind: str, challenge: str, origin: str, cross_origin: bool = False
    ) -> bytes:
        return json.dumps(
            {
                "type": kind,
                "challenge": challenge,
                "origin": origin,
                "crossOrigin": cross_origin,
            }
        ).encode()

    def _flags(self, user_present: bool, user_verified: bool, attested: bool) -> int:
        flags = 0
        if user_present:
            flags |= FLAG_UP
        if user_verified:
            flags |= FLAG_UV
        if self.backup_eligible:
            flags |= FLAG_BE | FLAG_BS
        if attested:
            flags |= FLAG_AT
        return flags

    def authenticator_data(
        self,
        rp_id: str,
        *,
        user_present: bool = True,
        user_verified: bool = True,
        attested: bool = False,
        aaguid: Optional[bytes] = None,
    ) -> bytes:
        data = (
            hashlib.sha256(rp_id.encode()).digest()
            + bytes([self._flags(user_present, user_verified, attested)])
            + self.sign_count.to_bytes(4, "big")
        )
        if attested:
            data += (
                (self.aaguid if aaguid is None else aaguid)
                + _u16(self.credential_id)
                + self.cose_public_key()
            )
        return data

    # -- Ceremonies ---------------------------------------------------------

    def create(
        self,
        options: Dict[str, Any],
        origin: str,
        *,
        fmt: Format = "none",
        ca: Optional[CertificateAuthority] = None,
        rp_id: Optional[str] = None,
        challenge: Optional[str] = None,
        user_verified: bool = True,
        user_present: bool = True,
        cross_origin: bool = False,
        discoverable: bool = True,
    ) -> Dict[str, Any]:
        """``navigator.credentials.create()`` answering ``options`` (the
        server's publicKey JSON), as ``PublicKeyCredential.toJSON()``."""
        self.user_handle = from_b64url(options["user"]["id"])
        client_data = self.client_data(
            "webauthn.create",
            challenge or options["challenge"],
            origin,
            cross_origin,
        )
        rp = rp_id or options["rp"]["id"]
        auth_data = self.authenticator_data(
            rp,
            user_present=user_present,
            user_verified=user_verified,
            attested=True,
            aaguid=bytes(16) if fmt == "fido-u2f" else None,
        )
        statement = self._attestation_statement(fmt, ca, rp, auth_data, client_data)
        attestation_object = cbor2.dumps(
            {
                "fmt": "packed" if fmt == "packed-self" else fmt,
                "attStmt": statement,
                "authData": auth_data,
            }
        )
        return {
            "id": b64url(self.credential_id),
            "rawId": b64url(self.credential_id),
            "type": "public-key",
            "authenticatorAttachment": "cross-platform",
            "response": {
                "clientDataJSON": b64url(client_data),
                "attestationObject": b64url(attestation_object),
                "transports": list(self.transports),
            },
            "clientExtensionResults": {"credProps": {"rk": discoverable}},
        }

    def get(
        self,
        options: Dict[str, Any],
        origin: str,
        *,
        rp_id: Optional[str] = None,
        challenge: Optional[str] = None,
        user_verified: bool = True,
        cross_origin: bool = False,
        advance_counter: bool = True,
        user_handle: Optional[bytes] = None,
        corrupt_signature: bool = False,
    ) -> Dict[str, Any]:
        """``navigator.credentials.get()`` answering ``options``."""
        if advance_counter:
            self.sign_count += 1
        client_data = self.client_data(
            "webauthn.get", challenge or options["challenge"], origin, cross_origin
        )
        auth_data = self.authenticator_data(
            rp_id or options["rpId"], user_verified=user_verified
        )
        signature = self.sign(auth_data + hashlib.sha256(client_data).digest())
        if corrupt_signature:
            signature = signature[:-1] + bytes([signature[-1] ^ 0x01])
        handle = user_handle if user_handle is not None else self.user_handle
        return {
            "id": b64url(self.credential_id),
            "rawId": b64url(self.credential_id),
            "type": "public-key",
            "authenticatorAttachment": "cross-platform",
            "response": {
                "clientDataJSON": b64url(client_data),
                "authenticatorData": b64url(auth_data),
                "signature": b64url(signature),
                "userHandle": b64url(handle) if handle else None,
            },
            "clientExtensionResults": {},
        }

    # -- Attestation statements ---------------------------------------------

    def _attestation_statement(
        self,
        fmt: Format,
        ca: Optional[CertificateAuthority],
        rp_id: str,
        auth_data: bytes,
        client_data: bytes,
    ) -> Dict[str, Any]:
        client_data_hash = hashlib.sha256(client_data).digest()
        if fmt == "none":
            return {}
        if fmt == "packed-self":
            return {
                "alg": self.cose_algorithm,
                "sig": self.sign(auth_data + client_data_hash),
            }
        if ca is None:
            raise ValueError(f"{fmt} attestation needs a certificate authority")
        if fmt == "packed":
            return self._packed(ca, auth_data + client_data_hash)
        if fmt == "fido-u2f":
            return self._fido_u2f(ca, rp_id, client_data_hash)
        if fmt == "tpm":
            return self._tpm(ca, auth_data + client_data_hash)
        return self._apple(ca, auth_data + client_data_hash)

    def _packed(self, ca: CertificateAuthority, signed: bytes) -> Dict[str, Any]:
        attestation_key = ec.generate_private_key(ec.SECP256R1())
        certificate = ca.issue(attestation_key.public_key(), _leaf_name(), [_not_ca()])
        return {
            "alg": COSE_ES256,
            "sig": attestation_key.sign(signed, ec.ECDSA(hashes.SHA256())),
            "x5c": [certificate],
        }

    def _fido_u2f(
        self, ca: CertificateAuthority, rp_id: str, client_data_hash: bytes
    ) -> Dict[str, Any]:
        if self.algorithm != "es256":
            raise ValueError("fido-u2f credentials are P-256 keys")
        attestation_key = ec.generate_private_key(ec.SECP256R1())
        certificate = ca.issue(attestation_key.public_key(), _leaf_name(), [_not_ca()])
        x, y = _ec_point(self.private_key.public_key())
        signed = (
            b"\x00"
            + hashlib.sha256(rp_id.encode()).digest()
            + client_data_hash
            + self.credential_id
            + b"\x04"
            + x
            + y
        )
        return {
            "sig": attestation_key.sign(signed, ec.ECDSA(hashes.SHA256())),
            "x5c": [certificate],
        }

    def _tpm(self, ca: CertificateAuthority, att_to_be_signed: bytes) -> Dict[str, Any]:
        """A TPM 2.0 attestation of an RSA credential key, certified by an
        attestation identity key whose certificate ``ca`` issues."""
        if self.algorithm != "rs256":
            raise ValueError("this software TPM certifies RSA credentials")
        numbers = self.private_key.public_key().public_numbers()
        modulus = numbers.n.to_bytes(_RSA_KEY_BITS // 8, "big")
        pub_area = (
            _TPM_ALG_RSA
            + _TPM_ALG_SHA256
            + _TPM_OBJECT_ATTRIBUTES
            + _u16(b"")
            + _TPM_ALG_NULL
            + _TPM_ALG_NULL
            + _RSA_KEY_BITS.to_bytes(2, "big")
            + (0).to_bytes(4, "big")
            + _u16(modulus)
        )
        name = _TPM_ALG_SHA256 + hashlib.sha256(pub_area).digest()
        cert_info = (
            _TPM_GENERATED
            + _TPM_ST_ATTEST_CERTIFY
            + _u16(_TPM_ALG_SHA256 + secrets.token_bytes(32))
            + _u16(hashlib.sha256(att_to_be_signed).digest())
            + secrets.token_bytes(8)
            + (1).to_bytes(4, "big")
            + (1).to_bytes(4, "big")
            + b"\x01"
            + secrets.token_bytes(8)
            + _u16(name)
            + _u16(_TPM_ALG_SHA256 + secrets.token_bytes(32))
        )
        aik = rsa.generate_private_key(
            public_exponent=_RSA_EXPONENT, key_size=_RSA_KEY_BITS
        )
        tpm_name = x509.Name(
            [
                x509.NameAttribute(_TPM_MANUFACTURER, "id:414D4400"),
                x509.NameAttribute(_TPM_MODEL, "SoftTPM"),
                x509.NameAttribute(_TPM_VERSION, "id:00020000"),
            ]
        )
        certificate = ca.issue(
            aik.public_key(),
            x509.Name([]),
            [
                (x509.SubjectAlternativeName([x509.DirectoryName(tpm_name)]), True),
                (x509.ExtendedKeyUsage([_TPM_AIK_EKU]), False),
                _not_ca(),
            ],
        )
        return {
            "ver": "2.0",
            "alg": COSE_RS256,
            "x5c": [certificate],
            "sig": aik.sign(cert_info, padding.PKCS1v15(), hashes.SHA256()),
            "certInfo": cert_info,
            "pubArea": pub_area,
        }

    def _apple(self, ca: CertificateAuthority, nonce_to_hash: bytes) -> Dict[str, Any]:
        """Apple anonymous attestation: a certificate for the credential key
        itself, carrying the nonce."""
        if self.algorithm != "es256":
            raise ValueError("apple credentials are P-256 keys")
        nonce = hashlib.sha256(nonce_to_hash).digest()
        # SEQUENCE { [1] EXPLICIT OCTET STRING nonce }
        nonce_extension = b"\x30\x24\xa1\x22\x04\x20" + nonce
        certificate = ca.issue(
            self.private_key.public_key(),
            _leaf_name(),
            [
                (x509.UnrecognizedExtension(_APPLE_NONCE_OID, nonce_extension), False),
                _not_ca(),
            ],
        )
        return {"x5c": [certificate]}


def _options(challenge: bytes, rp_id: str = "example.test") -> Dict[str, Any]:
    return {
        "challenge": b64url(challenge),
        "rp": {"id": rp_id, "name": "Example"},
        "rpId": rp_id,
        "user": {"id": b64url(b"user-1"), "name": "u", "displayName": "U"},
    }


class TestSoftwareAuthenticator:
    """The fixture produces what py_webauthn parses as the spec defines it."""

    def test_attestation_parses_with_the_attested_key(self) -> None:
        for algorithm in ("es256", "rs256", "ed25519"):
            authenticator = SoftwareAuthenticator(algorithm=algorithm)
            created = authenticator.create(
                _options(b"c" * 32), "https://app.example.test"
            )
            parsed = parse_attestation_object(
                from_b64url(created["response"]["attestationObject"])
            )
            attested = parsed.auth_data.attested_credential_data
            assert attested is not None
            assert attested.credential_id == authenticator.credential_id
            assert (
                decode_credential_public_key(attested.credential_public_key).alg
                == authenticator.cose_algorithm
            )
            assert parsed.auth_data.flags.up and parsed.auth_data.flags.uv
            assert (
                parsed.auth_data.rp_id_hash == hashlib.sha256(b"example.test").digest()
            )

    def test_client_data_carries_the_challenge_and_origin(self) -> None:
        created = SoftwareAuthenticator().create(
            _options(b"\x01" * 32), "https://app.example.test"
        )
        client = parse_client_data_json(
            from_b64url(created["response"]["clientDataJSON"])
        )
        assert client.challenge == b"\x01" * 32
        assert client.origin == "https://app.example.test"
        assert client.type == "webauthn.create"

    def test_assertions_count_up_and_sign_what_the_spec_says(self) -> None:
        authenticator = SoftwareAuthenticator()
        first = authenticator.get(_options(b"a" * 32), "https://app.example.test")
        second = authenticator.get(_options(b"b" * 32), "https://app.example.test")
        counts = [
            parse_authenticator_data(
                from_b64url(a["response"]["authenticatorData"])
            ).sign_count
            for a in (first, second)
        ]
        assert counts == [1, 2]
        response = second["response"]
        authenticator.private_key.public_key().verify(
            from_b64url(response["signature"]),
            from_b64url(response["authenticatorData"])
            + hashlib.sha256(from_b64url(response["clientDataJSON"])).digest(),
            ec.ECDSA(hashes.SHA256()),
        )
