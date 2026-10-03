# SPDX-License-Identifier: AGPL-3.0-or-later
"""The kerberos_consumer extension's declarations and pure helpers."""

import base64
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Union

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from cryptography.x509.oid import NameOID
from fastapi import HTTPException

from zephyrex.extensions.ExternalErrors import (
    InvalidInputExternalError,
    PermanentExternalError,
)
from zephyrex.extensions.kerberos_consumer.BLL_KerberosConsumer import (
    KerberosPrincipalManager,
    KerberosPrincipalModel,
    negotiate_token,
)
from zephyrex.extensions.kerberos_consumer.EXT_KerberosConsumer import (
    EXT_KerberosConsumer,
)
from zephyrex.extensions.kerberos_consumer.PRV_KerberosKeytab import (
    TLS_SERVER_END_POINT_PREFIX,
    Acceptor,
    PRV_KerberosKeytab,
    decode_keytab,
    principal_realm,
    realm_list,
    tls_server_end_point,
)


class TestDeclarations:
    def test_metadata(self):
        assert EXT_KerberosConsumer.name == "kerberos_consumer"
        assert EXT_KerberosConsumer.version == "2.0.0"
        assert EXT_KerberosConsumer.get_abilities() == {"kerberos_linked_principals"}

    def test_its_provider_and_requirements(self):
        assert EXT_KerberosConsumer.providers == [PRV_KerberosKeytab]
        assert EXT_KerberosConsumer.pip_requirements() == ["gssapi>=1.12.0"]

    def test_the_keytab_is_a_write_only_multiline_secret(self):
        keytab = PRV_KerberosKeytab.instance_setting("keytab")
        assert keytab.secret and keytab.multiline

    def test_the_link_model_and_its_routes(self):
        fields = set(KerberosPrincipalModel.model_fields)
        assert {"principal", "realm", "user_id", "last_login_at"} <= fields
        # Links are never created or edited over the API.
        assert {r.value for r in KerberosPrincipalManager.routes_to_register} == {
            "get",
            "list",
            "search",
            "delete",
        }


class TestPrincipalRealm:
    @pytest.mark.parametrize(
        "principal, realm",
        [
            ("alice@EXAMPLE.COM", "EXAMPLE.COM"),
            ("HTTP/sso.example.com@CORP.EXAMPLE.COM", "CORP.EXAMPLE.COM"),
            ("alice@example.com@AD.EXAMPLE.COM", "AD.EXAMPLE.COM"),
            ("odd\\@name@REALM", "REALM"),
        ],
    )
    def test_the_realm_follows_the_last_unescaped_at(self, principal, realm):
        assert principal_realm(principal) == realm

    @pytest.mark.parametrize("principal", ["alice", "alice@", "@REALM", "a\\@REALM"])
    def test_a_principal_without_a_realm_is_refused(self, principal):
        with pytest.raises(InvalidInputExternalError):
            principal_realm(principal)

    def test_realm_lists_are_split_and_case_kept(self):
        assert realm_list(" EXAMPLE.COM, corp.example.com\nAD.TEST ") == {
            "EXAMPLE.COM",
            "corp.example.com",
            "AD.TEST",
        }
        assert realm_list(None) == frozenset()


class TestKeytabSetting:
    def test_base64_with_line_breaks_decodes(self):
        raw = bytes(range(200))
        encoded = base64.b64encode(raw).decode()
        wrapped = "\n".join(encoded[i : i + 64] for i in range(0, len(encoded), 64))
        assert decode_keytab(wrapped) == raw

    @pytest.mark.parametrize("value", ["not base64!", "", "   "])
    def test_anything_else_is_a_configuration_error(self, value):
        with pytest.raises(PermanentExternalError):
            decode_keytab(value)

    def test_the_keys_never_appear_in_a_repr(self):
        """Tracebacks and log lines render the acceptor; its keytab must not
        be in them."""
        secret = b"\x05\x02SECRET-KEY-MATERIAL"
        acceptor = Acceptor(
            keytab=secret, service=None, allowed_realms=frozenset(), bindings=None
        )
        assert "SECRET-KEY-MATERIAL" not in repr(acceptor)


class TestNegotiateHeader:
    def test_the_token_is_decoded(self):
        assert negotiate_token("Negotiate " + base64.b64encode(b"tok").decode()) == (
            b"tok"
        )
        assert negotiate_token("negotiate " + base64.b64encode(b"tok").decode()) == (
            b"tok"
        )

    @pytest.mark.parametrize(
        "header", [None, "", "Negotiate", "Negotiate   ", "Basic YTpi", "Bearer x"]
    )
    def test_no_negotiate_token(self, header):
        assert negotiate_token(header) is None

    def test_a_token_that_is_not_base64_is_a_bad_request(self):
        with pytest.raises(HTTPException) as refused:
            negotiate_token("Negotiate @@@")
        assert refused.value.status_code == 400


SigningHash = Union[hashes.SHA256, hashes.SHA384, hashes.SHA512]


def _certificate_pem(algorithm: Optional[SigningHash]) -> str:
    """A self-signed certificate; with no hash, an Ed25519 one (whose
    signature names none)."""
    key: Any = (
        ed25519.Ed25519PrivateKey.generate()
        if algorithm is None
        else ec.generate_private_key(ec.SECP256R1())
    )
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "sso.test")])
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=1))
        .sign(key, algorithm)
    )
    return certificate.public_bytes(serialization.Encoding.PEM).decode()


class TestChannelBinding:
    """RFC 5929 tls-server-end-point: the certificate's signature hash,
    or SHA-256 when it names none. (Its SHA-1 and MD5 upgrade can't be
    exercised: this OpenSSL refuses to sign with either.)"""

    @pytest.mark.parametrize(
        "signed_with, expected",
        [
            (hashes.SHA256(), hashes.SHA256()),
            (hashes.SHA384(), hashes.SHA384()),
            (hashes.SHA512(), hashes.SHA512()),
            (None, hashes.SHA256()),
        ],
    )
    def test_the_binding_hashes_the_der_certificate(self, signed_with, expected):
        pem = _certificate_pem(signed_with)
        der = x509.load_pem_x509_certificate(pem.encode()).public_bytes(
            serialization.Encoding.DER
        )
        digest = hashes.Hash(expected)
        digest.update(der)
        assert tls_server_end_point(pem) == (
            TLS_SERVER_END_POINT_PREFIX + digest.finalize()
        )

    def test_a_non_certificate_is_a_configuration_error(self):
        with pytest.raises(PermanentExternalError):
            tls_server_end_point("-----BEGIN CERTIFICATE-----\nAAAA\n")
