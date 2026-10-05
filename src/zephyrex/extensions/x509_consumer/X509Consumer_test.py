# SPDX-License-Identifier: AGPL-3.0-or-later
"""Certificate sign-in end to end, against a real test PKI.

Each test builds its own PKI with ``cryptography``: a root CA, an issuing
CA under it, client certificates (good, expired, not yet valid, without
clientAuth, revoked, renewed), and a CA nobody trusts. Its CRLs and OCSP
responses are real, signed DER, served by a real local HTTP server the
certificates' distribution points and AIA name. The real login route is
driven through clients connecting from an allowed proxy address, from a
disallowed one, and through an ASGI server that terminates TLS itself.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Tuple
from urllib.parse import quote

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509 import ocsp
from cryptography.x509.oid import (
    AuthorityInformationAccessOID,
    ExtendedKeyUsageOID,
    NameOID,
)
from fastapi.testclient import TestClient

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.x509_consumer.BLL_X509Consumer import (
    UserX509LinkManager,
    UserX509LinkModel,
    X509TrustAnchorManager,
)
from zephyrex.extensions.x509_consumer.EXT_X509Consumer import EXT_X509Consumer
from zephyrex.extensions.x509_consumer.X509Verification import UPN_OID
from zephyrex.lib.Environment import env
from zephyrex.logic.BLL_Auth import UserModel
from zephyrex.logic.BLL_Auth.user_team import UserTeamModel
from zephyrex.testing.factories import (
    INTERNAL_ACCOUNTS,
    create_user,
    generate_test_email,
    if_match_of,
    internal_account_email,
)

LOGIN = "/v1/auth/x509/login"
HEADER = "X-SSL-Client-Cert"
PROXY = "10.20.30.40"
PROXY_NETWORK = "10.20.30.0/24"
STRANGER = "198.51.100.7"
NOW = datetime.now(timezone.utc)
DAY = timedelta(days=1)

Route = Tuple[int, Dict[str, str], bytes]


@dataclass
class Issued:
    certificate: x509.Certificate
    key: ec.EllipticCurvePrivateKey

    @property
    def pem(self) -> str:
        return self.certificate.public_bytes(serialization.Encoding.PEM).decode()


def _name(common_name: str, *extra: x509.NameAttribute) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name), *extra])


def _ski(key: ec.EllipticCurvePrivateKey) -> x509.SubjectKeyIdentifier:
    return x509.SubjectKeyIdentifier.from_public_key(key.public_key())


def _aki(issuer: Issued) -> x509.AuthorityKeyIdentifier:
    return x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer.key.public_key())


@dataclass
class TestPKI:
    """A root, an issuing CA under it, and what they publish at ``base``."""

    base: str
    routes: Dict[str, Route]
    root: Issued = field(init=False)
    issuing: Issued = field(init=False)

    __test__ = False  # not a test class

    def __post_init__(self) -> None:
        self.root = self.ca("Test Root CA")
        self.issuing = self.ca("Test Issuing CA", issuer=self.root)
        self.publish_crl(self.root)
        self.publish_crl(self.issuing)

    def crl_path(self, issuer: Issued) -> str:
        return f"/crl/{issuer.certificate.serial_number:x}.crl"

    def ocsp_path(self, certificate: x509.Certificate) -> str:
        return f"/ocsp/{certificate.serial_number:x}"

    def ca(self, common_name: str, issuer: Optional[Issued] = None) -> Issued:
        key = ec.generate_private_key(ec.SECP256R1())
        signing_key = issuer.key if issuer else key
        builder = (
            x509.CertificateBuilder()
            .subject_name(_name(common_name))
            .issuer_name(issuer.certificate.subject if issuer else _name(common_name))
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(NOW - DAY)
            .not_valid_after(NOW + 365 * DAY)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
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
            .add_extension(_ski(key), False)
        )
        if issuer is not None:
            builder = builder.add_extension(_aki(issuer), False).add_extension(
                x509.CRLDistributionPoints(
                    [
                        x509.DistributionPoint(
                            [
                                x509.UniformResourceIdentifier(
                                    self.base + self.crl_path(issuer)
                                )
                            ],
                            None,
                            None,
                            None,
                        )
                    ]
                ),
                False,
            )
        return Issued(builder.sign(signing_key, hashes.SHA256()), key)

    def client(
        self,
        common_name: str,
        issuer: Optional[Issued] = None,
        *,
        usages: Optional[Sequence[x509.ObjectIdentifier]] = (
            ExtendedKeyUsageOID.CLIENT_AUTH,
        ),
        not_before: datetime = NOW - DAY,
        not_after: datetime = NOW + 30 * DAY,
        emails: Sequence[str] = (),
        upn: Optional[str] = None,
        uid: Optional[str] = None,
        with_crl: bool = True,
        with_ocsp: bool = False,
        key_usage_signs: bool = True,
    ) -> Issued:
        issuer = issuer or self.issuing
        key = ec.generate_private_key(ec.SECP256R1())
        extra = [x509.NameAttribute(NameOID.USER_ID, uid)] if uid else []
        serial = x509.random_serial_number()
        builder = (
            x509.CertificateBuilder()
            .subject_name(_name(common_name, *extra))
            .issuer_name(issuer.certificate.subject)
            .public_key(key.public_key())
            .serial_number(serial)
            .not_valid_before(not_before)
            .not_valid_after(not_after)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
            .add_extension(_aki(issuer), False)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=key_usage_signs,
                    content_commitment=False,
                    key_encipherment=not key_usage_signs,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=False,
                    crl_sign=False,
                    encipher_only=False,
                    decipher_only=False,
                ),
                True,
            )
        )
        if usages is not None:
            builder = builder.add_extension(x509.ExtendedKeyUsage(list(usages)), False)
        names: List[x509.GeneralName] = [x509.RFC822Name(e) for e in emails]
        if upn:
            encoded = upn.encode()
            names.append(x509.OtherName(UPN_OID, bytes([0x0C, len(encoded)]) + encoded))
        if names:
            builder = builder.add_extension(x509.SubjectAlternativeName(names), False)
        if with_crl:
            builder = builder.add_extension(
                x509.CRLDistributionPoints(
                    [
                        x509.DistributionPoint(
                            [
                                x509.UniformResourceIdentifier(
                                    self.base + self.crl_path(issuer)
                                )
                            ],
                            None,
                            None,
                            None,
                        )
                    ]
                ),
                False,
            )
        if with_ocsp:
            builder = builder.add_extension(
                x509.AuthorityInformationAccess(
                    [
                        x509.AccessDescription(
                            AuthorityInformationAccessOID.OCSP,
                            x509.UniformResourceIdentifier(
                                f"{self.base}/ocsp/{serial:x}"
                            ),
                        )
                    ]
                ),
                False,
            )
        return Issued(builder.sign(issuer.key, hashes.SHA256()), key)

    def publish_crl(
        self,
        issuer: Issued,
        revoked: Sequence[x509.Certificate] = (),
        *,
        signer: Optional[Issued] = None,
        last_update: datetime = NOW - DAY,
        next_update: datetime = NOW + 7 * DAY,
    ) -> None:
        builder = (
            x509.CertificateRevocationListBuilder()
            .issuer_name(issuer.certificate.subject)
            .last_update(last_update)
            .next_update(next_update)
            .add_extension(_aki(issuer), False)
            .add_extension(x509.CRLNumber(1), False)
        )
        for certificate in revoked:
            builder = builder.add_revoked_certificate(
                x509.RevokedCertificateBuilder()
                .serial_number(certificate.serial_number)
                .revocation_date(NOW - DAY)
                .build()
            )
        crl = builder.sign((signer or issuer).key, hashes.SHA256())
        self.routes[self.crl_path(issuer)] = (
            200,
            {"Content-Type": "application/pkix-crl"},
            crl.public_bytes(serialization.Encoding.DER),
        )

    def publish_ocsp(
        self,
        subject: Issued,
        issuer: Optional[Issued] = None,
        *,
        status: ocsp.OCSPCertStatus = ocsp.OCSPCertStatus.GOOD,
        responder: Optional[Issued] = None,
    ) -> None:
        """The responder's answer about ``subject``, signed by the issuer or
        by a delegated ``responder``."""
        issuer = issuer or self.issuing
        signer = responder or issuer
        builder = (
            ocsp.OCSPResponseBuilder()
            .add_response(
                cert=subject.certificate,
                issuer=issuer.certificate,
                algorithm=hashes.SHA1(),
                cert_status=status,
                this_update=NOW - timedelta(minutes=1),
                next_update=NOW + DAY,
                revocation_time=(
                    NOW - DAY if status == ocsp.OCSPCertStatus.REVOKED else None
                ),
                revocation_reason=None,
            )
            .responder_id(ocsp.OCSPResponderEncoding.HASH, signer.certificate)
        )
        if responder is not None:
            builder = builder.certificates([responder.certificate])
        response = builder.sign(signer.key, hashes.SHA256())
        self.routes[self.ocsp_path(subject.certificate)] = (
            200,
            {"Content-Type": "application/ocsp-response"},
            response.public_bytes(serialization.Encoding.DER),
        )

    def ocsp_responder(self, issuer: Optional[Issued] = None) -> Issued:
        """A delegated OCSP signer the issuer certified."""
        issuer = issuer or self.issuing
        key = ec.generate_private_key(ec.SECP256R1())
        certificate = (
            x509.CertificateBuilder()
            .subject_name(_name("Test OCSP Responder"))
            .issuer_name(issuer.certificate.subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(NOW - DAY)
            .not_valid_after(NOW + 30 * DAY)
            .add_extension(
                x509.ExtendedKeyUsage([ExtendedKeyUsageOID.OCSP_SIGNING]), False
            )
            .sign(issuer.key, hashes.SHA256())
        )
        return Issued(certificate, key)


def forwarded(*chain: Issued) -> Dict[str, str]:
    """The header nginx sets from ``$ssl_client_escaped_cert``."""
    return {HEADER: quote("".join(issued.pem for issued in chain))}


class TLSTerminatingServer:
    """An ASGI server terminating TLS itself: it hands the app the client's
    verified chain through the ASGI TLS extension."""

    def __init__(
        self, app: Any, chain: Sequence[Issued], error: Optional[str] = None
    ) -> None:
        self.app = app
        self.tls = {
            "server_cert": None,
            "client_cert_chain": [issued.pem for issued in chain],
            "client_cert_name": None,
            "client_cert_error": error,
            "tls_version": 0x0304,
            "cipher_suite": 0x1301,
        }

    async def __call__(self, scope: Dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] in ("http", "websocket"):
            scope = {
                **scope,
                "scheme": "https",
                "extensions": {**(scope.get("extensions") or {}), "tls": self.tls},
            }
        await self.app(scope, receive, send)


class TestCertificateSignIn(ExtensionServerMixin):
    extension_class = EXT_X509Consumer

    # -- fixtures ---------------------------------------------------------

    @pytest.fixture
    def pki(self, local_http_server: Callable[..., Any]) -> TestPKI:
        # The server answers from this dict; the PKI fills it once it knows
        # the server's address, which its certificates name.
        routes: Dict[str, Route] = {}
        started = local_http_server(routes)
        return TestPKI(base=started.base_url, routes=routes)

    @pytest.fixture
    def anchor(self, server: Any) -> Iterator[Callable[..., Any]]:
        """Add trust anchors as ROOT; each is removed after the test."""
        manager = X509TrustAnchorManager(
            model_registry=server.app.state.model_registry,
            requester_id=env("ROOT_ID"),
        )
        created: List[str] = []

        def _add(ca: Issued, **settings: Any) -> Any:
            row = manager.create(
                name=f"anchor-{uuid.uuid4().hex[:8]}", ca_cert_pem=ca.pem, **settings
            )
            created.append(str(row.id))
            return row

        yield _add
        for anchor_id in created:
            manager.delete(id=anchor_id)

    @pytest.fixture
    def proxied(self, server: Any, set_env: Callable[[str, str], None]):
        """A client connecting from the trusted TLS terminator."""
        set_env("X509_CONSUMER_TRUSTED_PROXIES", PROXY_NETWORK)
        set_env("REGISTRATION_MODE", "open")
        return TestClient(server.app, client=(PROXY, 44321))

    @staticmethod
    def login(client: TestClient, *chain: Issued) -> Any:
        return client.post(LOGIN, json={}, headers=forwarded(*chain) if chain else {})

    @staticmethod
    def links(server: Any, identity: str) -> List[Any]:
        registry = server.app.state.model_registry
        LinkDB = UserX509LinkModel.DB(registry.DB.manager.Base)
        links: List[Any] = LinkDB.list(
            requester_id=env("ROOT_ID"),
            model_registry=registry,
            return_type="dto",
            override_dto=UserX509LinkModel,
            filters=[LinkDB.identity == identity, LinkDB.deleted_at.is_(None)],
        )
        return links

    @staticmethod
    def detail(response: Any) -> str:
        """The refusal's message, as the API wraps it."""
        detail = response.json()["detail"]
        return str(detail["message"] if isinstance(detail, dict) else detail)

    @staticmethod
    def root_links(server: Any) -> UserX509LinkManager:
        return UserX509LinkManager(
            model_registry=server.app.state.model_registry,
            requester_id=env("ROOT_ID"),
        )

    @staticmethod
    def unique(prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex[:8]}"

    # -- signing in ---------------------------------------------------------

    def test_a_forwarded_certificate_signs_in_and_issues_a_session(
        self, server, proxied, pki, anchor
    ):
        row = anchor(pki.root)
        name = self.unique("alice")
        alice = pki.client(name)
        response = self.login(proxied, alice, pki.issuing)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["identity"] == name
        assert body["trust_anchor_id"] == str(row.id)
        assert body["token"] and body["session_key"]
        assert body["user"]["display_name"] == name
        cookies = response.headers.get_list("set-cookie")
        assert any(cookie.startswith("zx_session=") for cookie in cookies)
        me = server.get(
            "/v1/user", headers={"Authorization": f"Bearer {body['token']}"}
        )
        assert me.status_code == 200, me.text
        assert me.json()["user"]["id"] == body["user_id"]
        [link] = self.links(server, name)
        assert link.user_id == body["user_id"]
        assert link.trust_anchor_id == str(row.id)
        assert link.serial_number == format(alice.certificate.serial_number, "x")
        assert link.subject_dn == alice.certificate.subject.rfc4514_string()
        assert link.last_login_at is not None

    def test_a_renewed_certificate_keeps_its_account(
        self, server, proxied, pki, anchor
    ):
        anchor(pki.root)
        name = self.unique("bob")
        first = self.login(proxied, pki.client(name), pki.issuing)
        renewed = self.login(proxied, pki.client(name), pki.issuing)
        assert first.status_code == renewed.status_code == 200
        assert first.json()["user_id"] == renewed.json()["user_id"]
        assert len(self.links(server, name)) == 1

    def test_a_certificate_the_root_issued_directly_signs_in(
        self, proxied, pki, anchor
    ):
        anchor(pki.root)
        response = self.login(proxied, pki.client(self.unique("rooted"), pki.root))
        assert response.status_code == 200, response.text

    def test_an_issuing_ca_may_itself_be_the_anchor(self, proxied, pki, anchor):
        """Pinned to the issuing CA, the leaf alone is a complete path."""
        anchor(pki.issuing)
        response = self.login(proxied, pki.client(self.unique("pinned")))
        assert response.status_code == 200, response.text

    def test_the_identity_attribute_is_configurable(self, proxied, pki, anchor):
        anchor(pki.root, identity_attribute="san:email")
        email = generate_test_email("x509_san")
        response = self.login(
            proxied, pki.client(self.unique("ignored-cn"), emails=[email]), pki.issuing
        )
        assert response.status_code == 200, response.text
        assert response.json()["identity"] == email
        # Not trusted for email: the account gets no email from it.
        assert response.json()["user"]["email"] is None

    def test_a_upn_identifies_the_user(self, proxied, pki, anchor):
        anchor(pki.root, identity_attribute="san:upn")
        upn = f"{self.unique('carol')}@corp.example"
        response = self.login(proxied, pki.client("Carol", upn=upn), pki.issuing)
        assert response.status_code == 200, response.text
        assert response.json()["identity"] == upn

    def test_a_subject_uid_identifies_the_user(self, proxied, pki, anchor):
        anchor(pki.root, identity_attribute="subject:UID")
        uid = self.unique("uid")
        response = self.login(proxied, pki.client("Dave", uid=uid), pki.issuing)
        assert response.status_code == 200, response.text
        assert response.json()["identity"] == uid

    def test_the_same_name_under_two_anchors_is_two_people(
        self, server, proxied, pki, anchor
    ):
        anchor(pki.root)
        other = pki.ca("Another Root CA")
        anchor(other, revocation_check="none")
        name = self.unique("same-name")
        mine = self.login(proxied, pki.client(name), pki.issuing)
        theirs = self.login(proxied, pki.client(name, other, with_crl=False))
        assert mine.status_code == theirs.status_code == 200
        assert mine.json()["user_id"] != theirs.json()["user_id"]
        assert len(self.links(server, name)) == 2

    # -- where the certificate comes from -------------------------------------

    def test_the_header_from_a_disallowed_address_is_ignored(
        self, server, proxied, pki, anchor
    ):
        anchor(pki.root)
        name = self.unique("forged")
        stranger = TestClient(server.app, client=(STRANGER, 50000))
        response = self.login(stranger, pki.client(name), pki.issuing)
        assert response.status_code == 401
        assert "token" not in response.json()
        assert self.links(server, name) == []

    def test_with_no_trusted_proxy_the_header_is_never_believed(
        self, server, pki, anchor, set_env
    ):
        set_env("X509_CONSUMER_TRUSTED_PROXIES", "")
        anchor(pki.root)
        client = TestClient(server.app, client=(PROXY, 44321))
        assert self.login(client, pki.client("nobody"), pki.issuing).status_code == 401

    def test_a_wildcard_proxy_list_is_refused_as_misconfigured(
        self, server, pki, anchor, set_env
    ):
        set_env("X509_CONSUMER_TRUSTED_PROXIES", "*")
        anchor(pki.root)
        client = TestClient(server.app, client=(PROXY, 44321))
        assert self.login(client, pki.client("anyone"), pki.issuing).status_code == 503

    def test_a_trusted_proxy_without_the_header_presents_nothing(
        self, proxied, pki, anchor
    ):
        anchor(pki.root)
        assert self.login(proxied).status_code == 401

    def test_a_header_that_is_not_a_certificate_is_a_bad_request(
        self, proxied, pki, anchor
    ):
        anchor(pki.root)
        response = proxied.post(LOGIN, json={}, headers={HEADER: "not-a-certificate"})
        assert response.status_code == 400

    def test_a_server_terminating_tls_hands_over_the_chain(
        self, server, pki, anchor, set_env
    ):
        set_env("REGISTRATION_MODE", "open")
        anchor(pki.root)
        name = self.unique("direct")
        app = TLSTerminatingServer(server.app, [pki.client(name), pki.issuing])
        response = TestClient(app, client=(STRANGER, 50000)).post(LOGIN, json={})
        assert response.status_code == 200, response.text
        assert response.json()["identity"] == name

    def test_a_handshake_error_reported_by_the_server_refuses(
        self, server, pki, anchor, set_env
    ):
        set_env("REGISTRATION_MODE", "open")
        anchor(pki.root)
        app = TLSTerminatingServer(
            server.app, [pki.client("errored"), pki.issuing], error="unknown ca"
        )
        response = TestClient(app, client=(STRANGER, 50000)).post(LOGIN, json={})
        assert response.status_code == 401

    def test_a_trusted_proxys_own_tls_certificate_is_not_the_user(
        self, server, pki, anchor, set_env
    ):
        """Behind the terminator, the connection's client certificate is the
        proxy's: only the header speaks for the user."""
        set_env("X509_CONSUMER_TRUSTED_PROXIES", PROXY_NETWORK)
        set_env("REGISTRATION_MODE", "open")
        anchor(pki.root)
        app = TLSTerminatingServer(server.app, [pki.client("the-proxy"), pki.issuing])
        response = TestClient(app, client=(PROXY, 44321)).post(LOGIN, json={})
        assert response.status_code == 401

    def test_without_an_anchor_sign_in_is_unavailable(self, proxied, pki):
        response = self.login(proxied, pki.client("early"), pki.issuing)
        assert response.status_code == 503

    # -- the certificate's own refusals ----------------------------------------

    def test_an_expired_certificate_is_refused(self, server, proxied, pki, anchor):
        anchor(pki.root)
        name = self.unique("expired")
        expired = pki.client(name, not_before=NOW - 30 * DAY, not_after=NOW - DAY)
        response = self.login(proxied, expired, pki.issuing)
        assert response.status_code == 401
        assert "expired" in self.detail(response)
        assert self.links(server, name) == []

    def test_a_certificate_not_yet_valid_is_refused(self, proxied, pki, anchor):
        anchor(pki.root)
        early = pki.client("future", not_before=NOW + DAY, not_after=NOW + 30 * DAY)
        assert self.login(proxied, early, pki.issuing).status_code == 401

    def test_a_server_certificate_is_not_a_client_certificate(
        self, proxied, pki, anchor
    ):
        anchor(pki.root)
        server_cert = pki.client(
            "server.example", usages=[ExtendedKeyUsageOID.SERVER_AUTH]
        )
        response = self.login(proxied, server_cert, pki.issuing)
        assert response.status_code == 401
        assert "client authentication" in self.detail(response)

    def test_a_certificate_without_extended_key_usage_is_refused(
        self, proxied, pki, anchor
    ):
        """Without an EKU a certificate may act for any purpose; sign-in
        needs one issued for clientAuth."""
        anchor(pki.root)
        response = self.login(
            proxied, pki.client("any-purpose", usages=None), pki.issuing
        )
        assert response.status_code == 401

    def test_a_key_that_may_not_sign_is_refused(self, proxied, pki, anchor):
        anchor(pki.root)
        encrypt_only = pki.client("encrypt-only", key_usage_signs=False)
        assert self.login(proxied, encrypt_only, pki.issuing).status_code == 401

    def test_a_certificate_from_an_untrusted_ca_is_refused(
        self, server, proxied, pki, anchor
    ):
        anchor(pki.root)
        rogue = pki.ca("Rogue Root CA")
        name = self.unique("rogue")
        response = self.login(proxied, pki.client(name, rogue, with_crl=False))
        assert response.status_code == 401
        assert self.links(server, name) == []

    def test_an_untrusted_ca_cannot_vouch_with_a_trusted_name(
        self, proxied, pki, anchor
    ):
        """A CA named like the trusted one, with its own key, is not it."""
        anchor(pki.root)
        impostor = pki.ca("Test Root CA")
        assert self.login(proxied, pki.client("mallory", impostor)).status_code == 401

    def test_a_chain_missing_its_intermediate_is_refused(self, proxied, pki, anchor):
        anchor(pki.root)
        assert self.login(proxied, pki.client("lonely")).status_code == 401

    def test_a_disabled_anchor_signs_no_one_in(self, server, proxied, pki, anchor):
        row = anchor(pki.root)
        X509TrustAnchorManager(
            model_registry=server.app.state.model_registry,
            requester_id=env("ROOT_ID"),
        ).update(row.id, is_enabled=False)
        assert self.login(proxied, pki.client("off"), pki.issuing).status_code == 503

    def test_a_certificate_without_the_identity_attribute_is_refused(
        self, proxied, pki, anchor
    ):
        anchor(pki.root, identity_attribute="san:email")
        response = self.login(proxied, pki.client("no-email"), pki.issuing)
        assert response.status_code == 401

    def test_an_ambiguous_identity_is_refused(self, proxied, pki, anchor):
        anchor(pki.root, identity_attribute="san:email")
        two = pki.client(
            "two", emails=[generate_test_email("one"), generate_test_email("two")]
        )
        assert self.login(proxied, two, pki.issuing).status_code == 401

    # -- revocation -------------------------------------------------------------

    def test_a_revoked_certificate_is_refused(self, server, proxied, pki, anchor):
        anchor(pki.root)
        name = self.unique("revoked")
        revoked = pki.client(name)
        pki.publish_crl(pki.issuing, [revoked.certificate])
        response = self.login(proxied, revoked, pki.issuing)
        assert response.status_code == 401
        assert "revoked" in self.detail(response)
        assert self.links(server, name) == []
        # Its neighbour, not on the CRL, signs in.
        assert (
            self.login(proxied, pki.client("neighbour"), pki.issuing).status_code == 200
        )

    def test_a_revoked_issuing_ca_revokes_its_certificates(self, proxied, pki, anchor):
        anchor(pki.root)
        pki.publish_crl(pki.root, [pki.issuing.certificate])
        response = self.login(proxied, pki.client("orphan"), pki.issuing)
        assert response.status_code == 401
        assert "revoked" in self.detail(response)

    def test_an_unreachable_crl_fails_closed_when_required(self, proxied, pki, anchor):
        anchor(pki.root)
        del pki.routes[pki.crl_path(pki.issuing)]
        response = self.login(proxied, pki.client("unknown"), pki.issuing)
        assert response.status_code == 401
        assert "revocation status" in self.detail(response)

    def test_an_unreachable_crl_is_tolerated_when_not_required(
        self, proxied, pki, anchor
    ):
        anchor(pki.root, revocation_required=False)
        del pki.routes[pki.crl_path(pki.issuing)]
        assert (
            self.login(proxied, pki.client("tolerated"), pki.issuing).status_code == 200
        )

    def test_a_tolerated_unknown_still_refuses_a_known_revocation(
        self, proxied, pki, anchor
    ):
        anchor(pki.root, revocation_required=False)
        revoked = pki.client("known-revoked")
        pki.publish_crl(pki.issuing, [revoked.certificate])
        assert self.login(proxied, revoked, pki.issuing).status_code == 401

    def test_a_crl_signed_by_another_key_is_not_believed(self, proxied, pki, anchor):
        """A forged CRL clearing a revoked certificate decides nothing."""
        anchor(pki.root)
        forger = pki.ca("Forger")
        pki.publish_crl(pki.issuing, [], signer=forger)
        assert (
            self.login(proxied, pki.client("forged-crl"), pki.issuing).status_code
            == 401
        )

    def test_a_stale_crl_is_not_believed(self, proxied, pki, anchor):
        anchor(pki.root)
        pki.publish_crl(
            pki.issuing, [], last_update=NOW - 9 * DAY, next_update=NOW - 2 * DAY
        )
        assert self.login(proxied, pki.client("stale"), pki.issuing).status_code == 401

    def test_a_certificate_without_a_crl_fails_closed_when_required(
        self, proxied, pki, anchor
    ):
        anchor(pki.root)
        response = self.login(
            proxied, pki.client("no-cdp", with_crl=False), pki.issuing
        )
        assert response.status_code == 401

    def test_the_anchors_crl_url_covers_what_it_issues_itself(
        self, proxied, pki, anchor
    ):
        anchor(pki.root, crl_url=pki.base + pki.crl_path(pki.root))
        revoked = pki.client("no-cdp-revoked", pki.root, with_crl=False)
        pki.publish_crl(pki.root, [revoked.certificate])
        assert self.login(proxied, revoked).status_code == 401
        good = pki.client("no-cdp-good", pki.root, with_crl=False)
        assert self.login(proxied, good).status_code == 200

    def test_ocsp_good_signs_in(self, proxied, pki, anchor):
        anchor(pki.issuing, revocation_check="ocsp")
        holder = pki.client("ocsp-good", with_crl=False, with_ocsp=True)
        pki.publish_ocsp(holder)
        assert self.login(proxied, holder).status_code == 200

    def test_ocsp_revoked_is_refused(self, proxied, pki, anchor):
        anchor(pki.issuing, revocation_check="ocsp")
        holder = pki.client("ocsp-revoked", with_crl=False, with_ocsp=True)
        pki.publish_ocsp(holder, status=ocsp.OCSPCertStatus.REVOKED)
        response = self.login(proxied, holder)
        assert response.status_code == 401
        assert "revoked" in self.detail(response)

    def test_ocsp_unknown_fails_closed(self, proxied, pki, anchor):
        anchor(pki.issuing, revocation_check="ocsp")
        holder = pki.client("ocsp-unknown", with_crl=False, with_ocsp=True)
        pki.publish_ocsp(holder, status=ocsp.OCSPCertStatus.UNKNOWN)
        assert self.login(proxied, holder).status_code == 401

    def test_an_unreachable_responder_fails_closed(self, proxied, pki, anchor):
        anchor(pki.issuing, revocation_check="ocsp")
        holder = pki.client("ocsp-down", with_crl=False, with_ocsp=True)
        assert self.login(proxied, holder).status_code == 401

    def test_an_ocsp_answer_signed_by_a_stranger_is_not_believed(
        self, proxied, pki, anchor
    ):
        anchor(pki.issuing, revocation_check="ocsp")
        holder = pki.client("ocsp-forged", with_crl=False, with_ocsp=True)
        stranger = pki.ca("Stranger")
        pki.publish_ocsp(holder, responder=stranger)
        assert self.login(proxied, holder).status_code == 401

    def test_a_delegated_ocsp_responder_is_believed(self, proxied, pki, anchor):
        anchor(pki.issuing, revocation_check="ocsp")
        holder = pki.client("ocsp-delegated", with_crl=False, with_ocsp=True)
        pki.publish_ocsp(holder, responder=pki.ocsp_responder())
        assert self.login(proxied, holder).status_code == 200

    def test_an_answer_about_another_certificate_decides_nothing(
        self, proxied, pki, anchor
    ):
        """A good answer for one certificate replayed for a revoked one."""
        anchor(pki.issuing, revocation_check="ocsp")
        good = pki.client("ocsp-other-good", with_crl=False, with_ocsp=True)
        holder = pki.client("ocsp-replayed", with_crl=False, with_ocsp=True)
        pki.publish_ocsp(good)
        pki.routes[pki.ocsp_path(holder.certificate)] = pki.routes[
            pki.ocsp_path(good.certificate)
        ]
        assert self.login(proxied, holder).status_code == 401

    def test_ocsp_then_crl_falls_back_to_the_crl(self, proxied, pki, anchor):
        anchor(pki.root, revocation_check="ocsp_then_crl")
        # No OCSP answer for either certificate: the CRLs decide.
        holder = pki.client("fallback", with_ocsp=True)
        assert self.login(proxied, holder, pki.issuing).status_code == 200
        revoked = pki.client("fallback-revoked", with_ocsp=True)
        pki.publish_crl(pki.issuing, [revoked.certificate])
        assert self.login(proxied, revoked, pki.issuing).status_code == 401

    def test_no_revocation_check_when_the_anchor_says_none(self, proxied, pki, anchor):
        anchor(pki.root, revocation_check="none")
        revoked = pki.client("unchecked")
        pki.publish_crl(pki.issuing, [revoked.certificate])
        assert self.login(proxied, revoked, pki.issuing).status_code == 200

    # -- accounts -----------------------------------------------------------------

    @pytest.mark.parametrize("mode", ["closed", "invite"])
    def test_an_unknown_certificate_is_refused_unless_registration_is_open(
        self, server, proxied, pki, anchor, set_env, mode
    ):
        set_env("REGISTRATION_MODE", mode)
        anchor(pki.root)
        name = self.unique("unregistered")
        assert self.login(proxied, pki.client(name), pki.issuing).status_code == 403
        assert self.links(server, name) == []

    def test_an_invited_email_the_ca_vouches_for_gets_an_account(
        self, server, proxied, pki, anchor, set_env, admin_a, team_a
    ):
        from zephyrex.extensions.auth_invitations.BLL_Invitations import (
            InvitationManager,
        )

        registry = server.app.state.model_registry
        email = generate_test_email("x509_invited")
        InvitationManager(
            requester_id=admin_a.id, target_team_id=team_a.id, model_registry=registry
        ).create(team_id=team_a.id, role_id=env("USER_ROLE_ID"), email=email)
        set_env("REGISTRATION_MODE", "invite")
        anchor(pki.root, trusted_for_email=True)
        response = self.login(
            proxied, pki.client(self.unique("invited"), emails=[email]), pki.issuing
        )
        assert response.status_code == 200, response.text
        assert response.json()["user"]["email"] == email
        memberships = UserTeamModel.DB(registry.DB.manager.Base).list(
            requester_id=env("ROOT_ID"),
            model_registry=registry,
            user_id=response.json()["user_id"],
            team_id=team_a.id,
        )
        assert len(memberships) == 1

    def test_a_ca_trusted_for_email_signs_in_to_the_account_with_it(
        self, server, proxied, pki, anchor
    ):
        email = generate_test_email("x509_existing")
        user = create_user(server, email=email)
        anchor(pki.root, trusted_for_email=True)
        response = self.login(
            proxied, pki.client(self.unique("emailed"), emails=[email]), pki.issuing
        )
        assert response.status_code == 200, response.text
        assert response.json()["user_id"] == user.id

    def test_a_vouched_email_matches_whatever_its_case(
        self, server, proxied, pki, anchor
    ):
        """Registration stores emails normalized; a certificate's may not be."""
        email = generate_test_email("x509_cased")
        user = create_user(server, email=email)
        anchor(pki.root, trusted_for_email=True)
        name = self.unique("cased")
        response = self.login(
            proxied, pki.client(name, emails=[email.upper()]), pki.issuing
        )
        assert response.status_code == 200, response.text
        assert response.json()["user_id"] == user.id
        [link] = self.links(server, name)
        assert link.user_id == user.id

    @pytest.mark.parametrize("internal", INTERNAL_ACCOUNTS)
    def test_no_vouched_email_reaches_an_internal_account(
        self, server, proxied, pki, anchor, internal
    ):
        """ROOT's seeded email is predictable; a CA trusted for emails
        vouching for it must not sign in as the superuser."""
        anchor(pki.root, trusted_for_email=True)
        name = self.unique("usurper")
        registry = server.app.state.model_registry
        with internal_account_email(registry, env(internal)) as email:
            response = self.login(
                proxied, pki.client(name, emails=[email]), pki.issuing
            )
        assert response.status_code == 403, response.text
        assert self.links(server, name) == []

    @pytest.mark.parametrize("internal", INTERNAL_ACCOUNTS)
    def test_nothing_links_to_an_internal_account(self, server, pki, anchor, internal):
        row = anchor(pki.root)
        with pytest.raises(Exception) as refused:
            self.root_links(server).create(
                user_id=env(internal),
                trust_anchor_id=str(row.id),
                identity=self.unique("internal"),
            )
        assert getattr(refused.value, "status_code", None) == 403

    def test_an_identity_linked_to_root_signs_no_one_in(
        self, server, proxied, pki, anchor
    ):
        """A link to ROOT written beneath the manager (by an older version,
        or directly) still issues no session."""
        row = anchor(pki.root)
        name = self.unique("rooted")
        registry = server.app.state.model_registry
        UserX509LinkModel.DB(registry.DB.manager.Base).create(
            requester_id=env("ROOT_ID"),
            model_registry=registry,
            trust_anchor_id=str(row.id),
            identity=name,
            user_id=env("ROOT_ID"),
        )
        response = self.login(proxied, pki.client(name), pki.issuing)
        assert response.status_code == 403, response.text

    def test_an_email_alone_never_takes_over_an_account(
        self, server, proxied, pki, anchor
    ):
        """A CA not trusted for emails could put anyone's in a certificate."""
        email = generate_test_email("x509_victim")
        victim = create_user(server, email=email)
        anchor(pki.root)
        response = self.login(
            proxied, pki.client(self.unique("claimant"), emails=[email]), pki.issuing
        )
        assert response.status_code == 200, response.text
        assert response.json()["user_id"] != victim.id

    def test_a_root_linked_identity_signs_in_while_registration_is_closed(
        self, server, proxied, pki, anchor, set_env
    ):
        set_env("REGISTRATION_MODE", "closed")
        row = anchor(pki.root)
        user = create_user(server, email=generate_test_email("x509_linked"))
        name = self.unique("erin")
        link = self.root_links(server).create(
            user_id=user.id, trust_anchor_id=str(row.id), identity=name
        )
        response = self.login(proxied, pki.client(name), pki.issuing)
        assert response.status_code == 200, response.text
        assert response.json()["user_id"] == user.id
        mine = server.get(
            "/v1/auth/x509", headers={"Authorization": f"Bearer {user.jwt}"}
        )
        assert mine.status_code == 200, mine.text
        [listed] = mine.json()["user_x509_links"]
        assert listed["identity"] == name
        removed = server.delete(
            f"/v1/auth/x509/{link.id}",
            headers={"Authorization": f"Bearer {user.jwt}", **if_match_of(listed)},
        )
        assert removed.status_code == 204, removed.text
        assert self.login(proxied, pki.client(name), pki.issuing).status_code == 403

    def test_a_disabled_account_cannot_sign_in(self, server, proxied, pki, anchor):
        row = anchor(pki.root)
        user = create_user(server, email=generate_test_email("x509_disabled"))
        name = self.unique("frank")
        self.root_links(server).create(
            user_id=user.id, trust_anchor_id=str(row.id), identity=name
        )
        registry = server.app.state.model_registry
        UserModel.DB(registry.DB.manager.Base).update(
            requester_id=env("ROOT_ID"),
            model_registry=registry,
            id=user.id,
            new_properties={"active": False},
        )
        assert self.login(proxied, pki.client(name), pki.issuing).status_code == 403

    def test_a_user_cannot_claim_a_certificate_identity(
        self, server, user_b, pki, anchor
    ):
        row = anchor(pki.root)
        claim = server.post(
            "/v1/auth/x509",
            json={"user_x509_link": {"trust_anchor_id": str(row.id), "identity": "x"}},
            headers={"Authorization": f"Bearer {user_b.jwt}"},
        )
        assert claim.status_code in (404, 405)
        manager = UserX509LinkManager(
            model_registry=server.app.state.model_registry, requester_id=user_b.id
        )
        with pytest.raises(Exception) as refused:
            manager.create(user_id=user_b.id, trust_anchor_id=str(row.id), identity="x")
        assert getattr(refused.value, "status_code", None) == 403

    def test_an_identity_links_to_one_user(self, server, user_b, admin_a, pki, anchor):
        row = anchor(pki.root)
        name = self.unique("once")
        self.root_links(server).create(
            user_id=user_b.id, trust_anchor_id=str(row.id), identity=name
        )
        with pytest.raises(Exception) as refused:
            self.root_links(server).create(
                user_id=admin_a.id, trust_anchor_id=str(row.id), identity=name
            )
        assert getattr(refused.value, "status_code", None) == 409

    def test_users_see_only_their_own_links(self, server, user_b, admin_a, pki, anchor):
        row = anchor(pki.root)
        name = self.unique("private")
        self.root_links(server).create(
            user_id=admin_a.id, trust_anchor_id=str(row.id), identity=name
        )
        theirs = server.get(
            "/v1/auth/x509", headers={"Authorization": f"Bearer {user_b.jwt}"}
        )
        assert theirs.status_code == 200, theirs.text
        assert name not in [
            link["identity"] for link in theirs.json()["user_x509_links"]
        ]

    # -- trust anchors --------------------------------------------------------

    def test_a_user_cannot_add_a_trust_anchor(self, server, user_b, pki):
        """Whoever adds an anchor signs in as anyone it names."""
        manager = X509TrustAnchorManager(
            model_registry=server.app.state.model_registry, requester_id=user_b.id
        )
        with pytest.raises(Exception) as refused:
            manager.create(name="mine", ca_cert_pem=pki.root.pem)
        assert getattr(refused.value, "status_code", None) == 403

    def test_an_anchor_must_be_a_ca(self, pki, anchor):
        with pytest.raises(Exception) as refused:
            anchor(pki.client("leaf"))
        assert getattr(refused.value, "status_code", None) == 400

    def test_an_anchor_is_added_once(self, pki, anchor):
        anchor(pki.root)
        with pytest.raises(Exception) as refused:
            anchor(pki.root)
        assert getattr(refused.value, "status_code", None) == 409

    def test_an_unknown_identity_attribute_is_refused(self, pki, anchor):
        with pytest.raises(Exception) as refused:
            anchor(pki.root, identity_attribute="subject:OU")
        assert getattr(refused.value, "status_code", None) == 400

    def test_the_anchor_certificate_is_computed_and_fixed(self, server, pki, anchor):
        row = anchor(pki.root, fingerprint_sha256="forged")
        assert (
            row.fingerprint_sha256
            == pki.root.certificate.fingerprint(hashes.SHA256()).hex()
        )
        assert row.subject_dn == "CN=Test Root CA"
        updated = X509TrustAnchorManager(
            model_registry=server.app.state.model_registry,
            requester_id=env("ROOT_ID"),
        ).update(row.id, ca_cert_pem=pki.ca("Swap").pem, name="renamed")
        assert updated.name == "renamed"
        assert updated.fingerprint_sha256 == row.fingerprint_sha256
