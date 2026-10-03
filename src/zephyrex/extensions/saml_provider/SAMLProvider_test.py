# SPDX-License-Identifier: AGPL-3.0-or-later
"""The SAML IdP against a real Service Provider: pysaml2's SP client builds
the AuthnRequests (signed or not, either binding), they go through the
app's real routes, and the SP validates each Response it gets, signature
included, against the metadata the IdP publishes.

No SP extension is involved: the SP here is pysaml2's own client."""

import base64
import os
import uuid
import xml.etree.ElementTree as ElementTree
import zlib
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, urlsplit

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID
from fastapi import HTTPException
from saml2 import BINDING_HTTP_POST, BINDING_HTTP_REDIRECT, md, saml
from saml2.client import Saml2Client
from saml2.config import SPConfig
from saml2.response import (
    StatusAuthnFailed,
    StatusInvalidNameidPolicy,
    StatusNoPassive,
)
from saml2.sigver import SignatureError
from saml2.xmldsig import DIGEST_SHA256, SIG_RSA_SHA256

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.saml_provider import IdP
from zephyrex.extensions.saml_provider.BLL_SAMLProvider import (
    SamlPendingRequestManager,
    SamlServiceProviderManager,
    acs_url_problem,
    checked_registration,
)
from zephyrex.extensions.saml_provider.EXT_SAMLProvider import EXT_SAMLProvider
from zephyrex.extensions.saml_provider.PRV_SAMLSigning import (
    CERTIFICATE_SETTING,
    KEY_SETTING,
    PRV_SAMLSigningKey,
)
from zephyrex.lib.Environment import env, refresh_settings
from zephyrex.lib.SessionCookies import SESSION_COOKIE
from zephyrex.logic.BLL_Providers import (
    ProviderInstanceManager,
    ProviderInstanceSettingManager,
    ProviderManager,
)
from zephyrex.testing.factories import authorize_user

# Minted and verified tokens must agree on issuer and audience whichever
# module runs first (see EP_Conversations_test).
os.environ["JWT_AUDIENCE"] = "test-aud"
os.environ["JWT_ISSUER"] = "test-iss"
refresh_settings()

PREFIX = "/v1/saml_provider"
DS = "{http://www.w3.org/2000/09/xmldsig#}"
SAMLP = "{urn:oasis:names:tc:SAML:2.0:protocol}"
SAML = "{urn:oasis:names:tc:SAML:2.0:assertion}"


def keypair(common_name: str) -> Tuple[str, str]:
    """A fresh RSA key and a self-signed certificate for it, as PEM."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=30))
        .sign(key, hashes.SHA256())
    )
    return (
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode(),
        certificate.public_bytes(serialization.Encoding.PEM).decode(),
    )


def root_headers() -> Dict[str, str]:
    """The administrator's credential (read when used: settings refresh)."""
    return {"X-API-Key": env("ROOT_API_KEY")}


def session(user: Any) -> Dict[str, str]:
    """What a browser sends: the session cookie, no Authorization header."""
    return {"Cookie": f"{SESSION_COOKIE}={user.jwt}"}


class _FormReader(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.action: Optional[str] = None
        self.fields: Dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        found = dict(attrs)
        if tag == "form":
            self.action = found.get("action")
        if tag == "input" and found.get("name"):
            self.fields[str(found["name"])] = found.get("value") or ""


def posted_form(page: str) -> Tuple[Optional[str], Dict[str, str]]:
    reader = _FormReader()
    reader.feed(page)
    return reader.action, reader.fields


class ServiceProvider:
    """A real SAML SP (pysaml2's client) that trusts the IdP's metadata."""

    def __init__(
        self,
        directory: Path,
        idp_metadata: str,
        *,
        acs_url: Optional[str] = None,
        allow_unsolicited: bool = False,
    ) -> None:
        host = f"sp-{uuid.uuid4().hex[:12]}.example.test"
        self.entity_id = f"https://{host}/saml/metadata"
        self.acs_url = acs_url or f"https://{host}/saml/acs"
        self.key_pem, self.certificate_pem = keypair(host)
        directory.mkdir(parents=True, exist_ok=True)
        key_file, cert_file = directory / "sp.key", directory / "sp.crt"
        key_file.write_text(self.key_pem)
        cert_file.write_text(self.certificate_pem)
        self.idp_entity_id = md.entity_descriptor_from_string(idp_metadata).entity_id
        config = SPConfig()
        config.load(
            {
                "entityid": self.entity_id,
                "key_file": str(key_file),
                "cert_file": str(cert_file),
                "encryption_keypairs": [
                    {"key_file": str(key_file), "cert_file": str(cert_file)}
                ],
                "metadata": {"inline": [idp_metadata]},
                "service": {
                    "sp": {
                        "endpoints": {
                            "assertion_consumer_service": [
                                (self.acs_url, BINDING_HTTP_POST)
                            ]
                        },
                        "want_response_signed": True,
                        "want_assertions_signed": True,
                        "allow_unsolicited": allow_unsolicited,
                        "signing_algorithm": SIG_RSA_SHA256,
                        "digest_algorithm": DIGEST_SHA256,
                    }
                },
            }
        )
        self.client = Saml2Client(config=config)

    def redirect_request(
        self, *, sign: bool, relay_state: str = "state-1", **request: Any
    ) -> Tuple[str, str]:
        """An HTTP-Redirect AuthnRequest: its id, and the IdP path + query
        the browser is sent to."""
        request_id, info = self.client.prepare_for_authenticate(
            entityid=self.idp_entity_id,
            relay_state=relay_state,
            binding=BINDING_HTTP_REDIRECT,
            sign=sign,
            sigalg=SIG_RSA_SHA256 if sign else None,
            **request,
        )
        location = urlsplit(dict(info["headers"])["Location"])
        return request_id, f"{location.path}?{location.query}"

    def post_request(self, *, sign: bool) -> Tuple[str, str]:
        """An HTTP-POST AuthnRequest: its id and its base64 form value."""
        destination = self.client.sso_location(self.idp_entity_id, BINDING_HTTP_POST)
        request_id, request = self.client.create_authn_request(
            destination,
            binding=BINDING_HTTP_POST,
            sign=sign,
            sign_alg=SIG_RSA_SHA256 if sign else None,
            digest_alg=DIGEST_SHA256 if sign else None,
        )
        return request_id, base64.b64encode(str(request).encode()).decode()

    def accept(self, saml_response: str, request_id: Optional[str]) -> Any:
        """The Response, validated as this SP validates any: signatures
        against the IdP's metadata, audience, destination, recipient,
        InResponseTo and time."""
        outstanding = {request_id: "/"} if request_id else {}
        return self.client.parse_authn_request_response(
            saml_response, BINDING_HTTP_POST, outstanding=outstanding
        )


class TestSAMLIdentityProvider(ExtensionServerMixin):
    extension_class = EXT_SAMLProvider

    @pytest.fixture(scope="module")
    def signing(self, server) -> Dict[str, Any]:
        """The IdP's signing key, held by a provider instance as root sets
        one up."""
        registry = server.app.state.model_registry
        root = env("ROOT_ID")
        key_pem, certificate_pem = keypair("idp.example.test")
        provider = ProviderManager(model_registry=registry, requester_id=root).get(
            name=PRV_SAMLSigningKey.name
        )
        instance = ProviderInstanceManager(
            model_registry=registry, requester_id=root
        ).create(name="saml_idp", provider_id=provider.id, scope="root")
        settings = ProviderInstanceSettingManager(
            model_registry=registry, requester_id=root
        )
        settings.create(
            provider_instance_id=instance.id,
            key=CERTIFICATE_SETTING,
            value=certificate_pem,
        )
        settings.create(
            provider_instance_id=instance.id, key=KEY_SETTING, value=key_pem
        )
        return {
            "instance": instance,
            "certificate": certificate_pem,
            "settings": settings,
        }

    @pytest.fixture(scope="module")
    def idp_metadata(self, server, signing) -> str:
        response = server.get(f"{PREFIX}/metadata")
        assert response.status_code == 200, response.text
        metadata: str = response.text
        return metadata

    @pytest.fixture
    def make_sp(self, server, idp_metadata, tmp_path):
        """A real SP, registered with the IdP as root registers one."""

        def _make(
            *,
            register: bool = True,
            allow_unsolicited: bool = False,
            with_certificate: bool = True,
            **registration: Any,
        ) -> Tuple[ServiceProvider, Optional[Dict[str, Any]]]:
            sp = ServiceProvider(
                tmp_path / uuid.uuid4().hex,
                idp_metadata,
                allow_unsolicited=allow_unsolicited,
            )
            if not register:
                return sp, None
            fields = {
                "name": "Test SP",
                "entity_id": sp.entity_id,
                "acs_urls": [sp.acs_url],
                "certificate": sp.certificate_pem if with_certificate else None,
                **registration,
            }
            response = server.post(
                f"{PREFIX}/service_provider",
                json={"saml_service_provider": fields},
                headers=root_headers(),
            )
            assert response.status_code == 201, response.text
            return sp, response.json()["saml_service_provider"]

        return _make

    def _continue(self, server, user, response) -> Any:
        """Follow the IdP's 303 to its continue step, as the browser."""
        assert response.status_code == 303, response.text
        location = response.headers["location"]
        assert location.startswith(f"{PREFIX}/sso/continue?ticket=")
        return server.get(
            location, headers=session(user) if user else {}, follow_redirects=False
        )

    def _sign_in(self, server, sp, user, *, sign=True, **request) -> Tuple[str, Any]:
        """SP-initiated sign-in over the redirect binding: the request id
        and the IdP's answer page."""
        request_id, target = sp.redirect_request(sign=sign, **request)
        received = server.get(target, headers=session(user), follow_redirects=False)
        return request_id, self._continue(server, user, received)

    def _delivered(self, page) -> Tuple[Dict[str, str], str]:
        assert page.status_code == 200, page.text
        action, fields = posted_form(page.text)
        assert action is not None
        return fields, action

    # -- metadata ------------------------------------------------------------

    def test_metadata_publishes_the_signing_certificate_and_both_bindings(
        self, idp_metadata, signing
    ):
        descriptor = md.entity_descriptor_from_string(idp_metadata)
        idp = descriptor.idpsso_descriptor[0]
        bindings = {sso.binding for sso in idp.single_sign_on_service}
        assert bindings == {BINDING_HTTP_REDIRECT, BINDING_HTTP_POST}
        assert all(
            sso.location.endswith(f"{PREFIX}/sso") for sso in idp.single_sign_on_service
        )
        published = idp.key_descriptor[0].key_info.x509_data[0].x509_certificate.text
        assert published.replace("\n", "") == IdP.certificate_body(
            signing["certificate"]
        )
        assert {f.text for f in idp.name_id_format} == {
            saml.NAMEID_FORMAT_PERSISTENT,
            saml.NAMEID_FORMAT_EMAILADDRESS,
        }

    async def test_the_metadata_ability_returns_the_same_document(
        self, server, idp_metadata
    ):
        found = await EXT_SAMLProvider.saml_idp_metadata()
        assert found["metadata"] == idp_metadata
        assert (
            found["entity_id"]
            == md.entity_descriptor_from_string(idp_metadata).entity_id
        )

    def test_the_signing_key_is_write_only(self, signing):
        stored = signing["settings"].list(
            provider_instance_id=signing["instance"].id, key=KEY_SETTING
        )
        assert [row.write_only for row in stored] == [True]
        assert stored[0].model_dump()["value"] is None
        assert "PRIVATE KEY" not in stored[0].model_dump_json()

    # -- SP-initiated sign-in --------------------------------------------------

    def test_a_signed_in_user_gets_a_signed_response_the_sp_accepts(
        self, server, make_sp, admin_a
    ):
        sp, _ = make_sp(released_attributes=["email", "last_name"])
        request_id, page = self._sign_in(server, sp, admin_a, sign=True)
        fields, action = self._delivered(page)
        assert action == sp.acs_url
        assert fields["RelayState"] == "state-1"
        assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
        assert page.headers["cache-control"] == "no-store"

        accepted = sp.accept(fields["SAMLResponse"], request_id)
        assert accepted.in_response_to == request_id
        assertion = accepted.assertion
        audiences = [
            a.text
            for restriction in assertion.conditions.audience_restriction
            for a in restriction.audience
        ]
        assert audiences == [sp.entity_id]
        confirmation = assertion.subject.subject_confirmation[0]
        assert confirmation.subject_confirmation_data.recipient == sp.acs_url
        assert confirmation.subject_confirmation_data.in_response_to == request_id
        assert accepted.response.destination == sp.acs_url
        assert accepted.name_id.format == saml.NAMEID_FORMAT_PERSISTENT
        assert accepted.name_id.text not in (admin_a.id, admin_a.email)
        # Only what the registration releases.
        assert set(accepted.ava) == {"mail", "sn"}
        assert accepted.ava["mail"] == [admin_a.email]

    def test_the_response_and_assertion_are_each_signed_with_rsa_sha256(
        self, server, make_sp, admin_a
    ):
        sp, _ = make_sp()
        _, page = self._sign_in(server, sp, admin_a)
        fields, _ = self._delivered(page)
        document = ElementTree.fromstring(base64.b64decode(fields["SAMLResponse"]))
        assertion = document.find(f"{SAML}Assertion")
        assert assertion is not None
        for signed in (document, assertion):
            signature = signed.find(f"{DS}Signature")
            assert signature is not None, signed.tag
            method = signature.find(f"{DS}SignedInfo/{DS}SignatureMethod")
            assert method is not None and method.get("Algorithm") == SIG_RSA_SHA256

    def test_the_assertion_is_valid_for_five_minutes(self, server, make_sp, admin_a):
        sp, _ = make_sp()
        request_id, page = self._sign_in(server, sp, admin_a)
        fields, _ = self._delivered(page)
        conditions = sp.accept(fields["SAMLResponse"], request_id).assertion.conditions
        start = datetime.strptime(conditions.not_before, "%Y-%m-%dT%H:%M:%SZ")
        end = datetime.strptime(conditions.not_on_or_after, "%Y-%m-%dT%H:%M:%SZ")
        assert end - start == timedelta(seconds=300)

    def test_a_tampered_response_is_refused_by_the_sp(self, server, make_sp, admin_a):
        sp, _ = make_sp(released_attributes=["email"])
        request_id, page = self._sign_in(server, sp, admin_a)
        fields, _ = self._delivered(page)
        xml = base64.b64decode(fields["SAMLResponse"]).decode()
        forged = xml.replace(admin_a.email, "mallory@example.test")
        assert forged != xml
        with pytest.raises(SignatureError):
            sp.accept(base64.b64encode(forged.encode()).decode(), request_id)

    def test_the_persistent_name_id_is_stable_per_sp_and_differs_between_sps(
        self, server, make_sp, admin_a
    ):
        first, _ = make_sp()
        second, _ = make_sp()

        def subject(sp: ServiceProvider) -> str:
            request_id, page = self._sign_in(server, sp, admin_a)
            fields, _ = self._delivered(page)
            return str(sp.accept(fields["SAMLResponse"], request_id).name_id.text)

        at_first = subject(first)
        assert subject(first) == at_first
        assert subject(second) != at_first

    def test_an_sp_configured_for_email_gets_the_email_name_id(
        self, server, make_sp, admin_a
    ):
        sp, _ = make_sp(name_id_format="email")
        request_id, page = self._sign_in(server, sp, admin_a)
        fields, _ = self._delivered(page)
        accepted = sp.accept(fields["SAMLResponse"], request_id)
        assert accepted.name_id.format == saml.NAMEID_FORMAT_EMAILADDRESS
        assert accepted.name_id.text == admin_a.email
        assert accepted.ava == {}

    def test_an_sp_with_a_certificate_may_have_its_assertions_encrypted(
        self, server, make_sp, admin_a
    ):
        sp, _ = make_sp(encrypt_assertions=True, released_attributes=["email"])
        request_id, page = self._sign_in(server, sp, admin_a)
        fields, _ = self._delivered(page)
        document = ElementTree.fromstring(base64.b64decode(fields["SAMLResponse"]))
        assert document.find(f"{SAML}EncryptedAssertion") is not None
        assert document.find(f"{SAML}Assertion") is None
        accepted = sp.accept(fields["SAMLResponse"], request_id)
        assert accepted.ava["mail"] == [admin_a.email]

    def test_the_post_binding_answers_like_the_redirect_binding(
        self, server, make_sp, admin_a
    ):
        sp, _ = make_sp(require_signed_requests=True)
        request_id, encoded = sp.post_request(sign=True)
        received = server.post(
            f"{PREFIX}/sso",
            data={"SAMLRequest": encoded, "RelayState": "posted"},
            follow_redirects=False,
        )
        fields, _ = self._delivered(self._continue(server, admin_a, received))
        assert fields["RelayState"] == "posted"
        assert (
            sp.accept(fields["SAMLResponse"], request_id).in_response_to == request_id
        )

    def test_the_post_binding_takes_a_browsers_cross_site_form(
        self, server, make_sp, admin_a
    ):
        """A browser posts the SP's form from the SP's origin, with the IdP's
        session cookie (where the browser sends it) and no CSRF token."""
        sp, _ = make_sp()
        request_id, encoded = sp.post_request(sign=True)
        origin = "https://" + urlsplit(sp.acs_url).netloc
        received = server.post(
            f"{PREFIX}/sso",
            data={"SAMLRequest": encoded},
            headers={"Origin": origin, **session(admin_a)},
            follow_redirects=False,
        )
        fields, _ = self._delivered(self._continue(server, admin_a, received))
        assert (
            sp.accept(fields["SAMLResponse"], request_id).in_response_to == request_id
        )

    # -- signing in first --------------------------------------------------------

    def test_a_browser_without_a_session_is_sent_to_sign_in_and_returns(
        self, server, make_sp, admin_a
    ):
        sp, _ = make_sp()
        request_id, target = sp.redirect_request(sign=True)
        received = server.get(target, follow_redirects=False)
        to_sign_in = self._continue(server, None, received)
        assert to_sign_in.status_code == 303
        assert to_sign_in.headers["location"] == "/user"
        cookie = to_sign_in.headers["set-cookie"]
        assert cookie.startswith("href=")
        return_to = cookie.split(";", 1)[0].removeprefix("href=").strip('"')
        assert return_to.startswith(f"{PREFIX}/sso/continue?ticket=")
        # Signed in, the sign-in pages send the browser back there.
        page = server.get(return_to, headers=session(admin_a), follow_redirects=False)
        fields, _ = self._delivered(page)
        assert (
            sp.accept(fields["SAMLResponse"], request_id).in_response_to == request_id
        )

    def test_a_passive_request_without_a_session_gets_no_passive(self, server, make_sp):
        sp, _ = make_sp()
        request_id, target = sp.redirect_request(sign=True, is_passive="true")
        page = self._continue(server, None, server.get(target, follow_redirects=False))
        fields, _ = self._delivered(page)
        with pytest.raises(StatusNoPassive):
            sp.accept(fields["SAMLResponse"], request_id)

    def test_force_authn_wants_a_session_begun_after_the_request(
        self, server, make_sp, admin_a
    ):
        sp, _ = make_sp()
        request_id, target = sp.redirect_request(sign=True, force_authn="true")
        received = server.get(target, headers=session(admin_a), follow_redirects=False)
        location = received.headers["location"]
        # admin_a's session predates the request: sign in again.
        stale = server.get(location, headers=session(admin_a), follow_redirects=False)
        assert stale.status_code == 303 and stale.headers["location"] == "/user"
        fresh = authorize_user(server, admin_a.email)
        page = server.get(
            location,
            headers={"Cookie": f"{SESSION_COOKIE}={fresh}"},
            follow_redirects=False,
        )
        fields, _ = self._delivered(page)
        assert (
            sp.accept(fields["SAMLResponse"], request_id).in_response_to == request_id
        )

    def test_force_authn_answered_with_the_old_session_again_fails(
        self, server, make_sp, admin_a
    ):
        sp, _ = make_sp()
        request_id, target = sp.redirect_request(sign=True, force_authn="true")
        received = server.get(target, headers=session(admin_a), follow_redirects=False)
        location = received.headers["location"]
        server.get(location, headers=session(admin_a), follow_redirects=False)
        page = server.get(location, headers=session(admin_a), follow_redirects=False)
        fields, _ = self._delivered(page)
        with pytest.raises(StatusAuthnFailed):
            sp.accept(fields["SAMLResponse"], request_id)

    def test_a_ticket_answers_once(self, server, make_sp, admin_a):
        sp, _ = make_sp()
        _, target = sp.redirect_request(sign=True)
        received = server.get(target, headers=session(admin_a), follow_redirects=False)
        location = received.headers["location"]
        first = server.get(location, headers=session(admin_a), follow_redirects=False)
        assert first.status_code == 200
        again = server.get(location, headers=session(admin_a), follow_redirects=False)
        assert again.status_code == 400

    def test_an_unknown_ticket_is_refused(self, server, admin_a):
        response = server.get(
            f"{PREFIX}/sso/continue",
            params={"ticket": "not-a-ticket"},
            headers=session(admin_a),
        )
        assert response.status_code == 400

    # -- refusals ----------------------------------------------------------------

    def test_an_unregistered_sp_is_refused(self, server, make_sp, admin_a):
        sp, _ = make_sp(register=False)
        _, target = sp.redirect_request(sign=True)
        response = server.get(target, headers=session(admin_a), follow_redirects=False)
        assert response.status_code == 403, response.text

    def test_a_disabled_sp_is_refused(self, server, make_sp, admin_a):
        sp, registration = make_sp()
        assert registration is not None
        disabled = server.put(
            f"{PREFIX}/service_provider/{registration['id']}",
            json={"saml_service_provider": {"enabled": False}},
            headers=root_headers(),
        )
        assert disabled.status_code == 200, disabled.text
        _, target = sp.redirect_request(sign=True)
        response = server.get(target, headers=session(admin_a), follow_redirects=False)
        assert response.status_code == 403, response.text

    def test_an_acs_url_not_registered_is_refused(self, server, make_sp, admin_a):
        sp, _ = make_sp()
        _, target = sp.redirect_request(
            sign=True, assertion_consumer_service_url=sp.acs_url + "/elsewhere"
        )
        response = server.get(target, headers=session(admin_a), follow_redirects=False)
        assert response.status_code == 400, response.text
        assert "not registered" in response.text

    def test_an_unsigned_request_is_refused_when_signing_is_required(
        self, server, make_sp, admin_a
    ):
        sp, _ = make_sp(require_signed_requests=True)
        _, target = sp.redirect_request(sign=False)
        response = server.get(target, headers=session(admin_a), follow_redirects=False)
        assert response.status_code == 403, response.text

    def test_an_unsigned_request_is_answered_when_signing_is_optional(
        self, server, make_sp, admin_a
    ):
        sp, _ = make_sp()
        request_id, page = self._sign_in(server, sp, admin_a, sign=False)
        fields, _ = self._delivered(page)
        assert (
            sp.accept(fields["SAMLResponse"], request_id).in_response_to == request_id
        )

    def test_a_request_signed_by_another_key_is_refused(self, server, make_sp, admin_a):
        sp, registration = make_sp()
        assert registration is not None
        _, other_certificate = keypair("someone-else")
        changed = server.put(
            f"{PREFIX}/service_provider/{registration['id']}",
            json={"saml_service_provider": {"certificate": other_certificate}},
            headers=root_headers(),
        )
        assert changed.status_code == 200, changed.text
        _, target = sp.redirect_request(sign=True)
        response = server.get(target, headers=session(admin_a), follow_redirects=False)
        assert response.status_code == 403, response.text

    def test_a_signed_request_with_its_relay_state_changed_is_refused(
        self, server, make_sp, admin_a
    ):
        sp, _ = make_sp()
        _, target = sp.redirect_request(sign=True, relay_state="mine")
        forged = target.replace("RelayState=mine", "RelayState=theirs")
        assert forged != target
        response = server.get(forged, headers=session(admin_a), follow_redirects=False)
        assert response.status_code == 403, response.text

    def test_a_signed_request_from_an_sp_without_a_certificate_is_refused(
        self, server, make_sp, admin_a
    ):
        sp, _ = make_sp(with_certificate=False)
        _, target = sp.redirect_request(sign=True)
        response = server.get(target, headers=session(admin_a), follow_redirects=False)
        assert response.status_code == 403, response.text

    def test_a_tampered_post_request_is_refused(self, server, make_sp):
        sp, _ = make_sp()
        _, encoded = sp.post_request(sign=True)
        xml = base64.b64decode(encoded).decode()
        forged = xml.replace(sp.acs_url, sp.acs_url + "?x=1")
        response = server.post(
            f"{PREFIX}/sso",
            data={"SAMLRequest": base64.b64encode(forged.encode()).decode()},
            follow_redirects=False,
        )
        assert response.status_code == 403, response.text

    def test_a_replayed_request_is_refused(self, server, make_sp, admin_a):
        sp, _ = make_sp()
        _, target = sp.redirect_request(sign=True)
        first = server.get(target, headers=session(admin_a), follow_redirects=False)
        assert first.status_code == 303
        again = server.get(target, headers=session(admin_a), follow_redirects=False)
        assert again.status_code == 400, again.text

    def test_a_stale_request_is_refused(self, server, make_sp, admin_a):
        sp, _ = make_sp()
        stale = (datetime.now(timezone.utc) - timedelta(minutes=30)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        request_id, request = sp.client.create_authn_request(
            sp.client.sso_location(sp.idp_entity_id, BINDING_HTTP_REDIRECT),
            binding=BINDING_HTTP_POST,
        )
        request.issue_instant = stale
        compressed = zlib.compress(str(request).encode())[2:-4]
        target = f"{PREFIX}/sso?SAMLRequest=" + quote(base64.b64encode(compressed))
        response = server.get(target, headers=session(admin_a), follow_redirects=False)
        assert response.status_code == 400, response.text

    def test_a_name_id_format_the_sp_is_not_registered_for_gets_an_error(
        self, server, make_sp, admin_a
    ):
        sp, _ = make_sp()
        request_id, target = sp.redirect_request(
            sign=True, nameid_format=saml.NAMEID_FORMAT_EMAILADDRESS
        )
        page = server.get(target, headers=session(admin_a), follow_redirects=False)
        fields, action = self._delivered(page)
        assert action == sp.acs_url
        with pytest.raises(StatusInvalidNameidPolicy):
            sp.accept(fields["SAMLResponse"], request_id)

    def test_an_api_key_is_not_a_browser_session(self, server, make_sp):
        sp, _ = make_sp()
        _, target = sp.redirect_request(sign=True)
        received = server.get(target, follow_redirects=False)
        location = received.headers["location"]
        response = server.get(location, headers=root_headers(), follow_redirects=False)
        # An API key is no browser session: sent to sign in, not answered.
        assert response.status_code == 303
        assert response.headers["location"] == "/user"

    # -- IdP-initiated -----------------------------------------------------------

    def test_idp_initiated_sign_in_is_off_by_default(self, server, make_sp, admin_a):
        sp, _ = make_sp()
        response = server.get(
            f"{PREFIX}/initiate",
            params={"sp": sp.entity_id},
            headers=session(admin_a),
            follow_redirects=False,
        )
        assert response.status_code == 403, response.text

    def test_idp_initiated_sign_in_when_the_sp_allows_it(
        self, server, make_sp, admin_a
    ):
        sp, _ = make_sp(allow_idp_initiated=True, allow_unsolicited=True)
        started = server.get(
            f"{PREFIX}/initiate",
            params={"sp": sp.entity_id, "relay_state": "/home"},
            headers=session(admin_a),
            follow_redirects=False,
        )
        fields, action = self._delivered(self._continue(server, admin_a, started))
        assert action == sp.acs_url and fields["RelayState"] == "/home"
        accepted = sp.accept(fields["SAMLResponse"], None)
        assert accepted.in_response_to is None
        assert accepted.name_id.format == saml.NAMEID_FORMAT_PERSISTENT

    # -- registration ------------------------------------------------------------

    def test_only_root_or_system_manage_registrations(self, server, admin_a, make_sp):
        _, registration = make_sp()
        assert registration is not None
        user = {"Authorization": f"Bearer {admin_a.jwt}"}
        listed = server.get(f"{PREFIX}/service_provider", headers=user)
        assert listed.status_code == 403
        created = server.post(
            f"{PREFIX}/service_provider",
            json={
                "saml_service_provider": {
                    "name": "Mine",
                    "entity_id": "https://mine.example.test",
                    "acs_urls": ["https://mine.example.test/acs"],
                }
            },
            headers=user,
        )
        assert created.status_code == 403
        changed = server.put(
            f"{PREFIX}/service_provider/{registration['id']}",
            json={
                "saml_service_provider": {"acs_urls": ["https://evil.example.test/acs"]}
            },
            headers=user,
        )
        assert changed.status_code == 403
        assert (
            server.get(f"{PREFIX}/service_provider", headers=root_headers()).status_code
            == 200
        )

    def test_an_entity_id_registers_once(self, server, make_sp):
        sp, _ = make_sp()
        again = server.post(
            f"{PREFIX}/service_provider",
            json={
                "saml_service_provider": {
                    "name": "Again",
                    "entity_id": sp.entity_id,
                    "acs_urls": [sp.acs_url],
                }
            },
            headers=root_headers(),
        )
        assert again.status_code == 409, again.text

    def test_a_batch_registration_is_checked_entry_by_entry(self, server):
        registry = server.app.state.model_registry
        manager = SamlServiceProviderManager(
            model_registry=registry, requester_id=env("ROOT_ID")
        )
        with pytest.raises(HTTPException) as refused:
            manager.create(
                entities=[
                    {
                        "name": "Fine",
                        "entity_id": "https://fine.example.test",
                        "acs_urls": ["https://fine.example.test/acs"],
                    },
                    {
                        "name": "Plain",
                        "entity_id": "https://plain.example.test",
                        "acs_urls": ["http://plain.example.test/acs"],
                    },
                ]
            )
        assert refused.value.status_code == 422
        assert manager.list(entity_id="https://fine.example.test") == []

    def test_expired_requests_are_purged(self, server):
        registry = server.app.state.model_registry
        pending = SamlPendingRequestManager(
            model_registry=registry, requester_id=env("ROOT_ID")
        )
        sp_manager = SamlServiceProviderManager(
            model_registry=registry, requester_id=env("ROOT_ID")
        )
        registration = sp_manager.create(
            name="Purge",
            entity_id=f"https://purge-{uuid.uuid4().hex}.example.test",
            acs_urls=["https://purge.example.test/acs"],
        )
        now = datetime.now(timezone.utc)
        ticket = uuid.uuid4().hex
        pending.create(
            saml_service_provider_id=registration.id,
            ticket=ticket,
            request_id=f"id-{ticket}",
            acs_url="https://purge.example.test/acs",
            expires_at=now - timedelta(minutes=1),
        )
        assert pending.by_ticket(ticket) is not None
        pending.purge_expired(now)
        assert pending.by_ticket(ticket) is None
        # Root sees deleted rows; purging again must not trip over them.
        pending.purge_expired(now)

    def test_a_deleted_sp_is_refused(self, server, make_sp, admin_a):
        """Lookups run as root, who sees deleted rows: a deleted
        registration must still be no registration."""
        sp, registration = make_sp()
        assert registration is not None
        deleted = server.delete(
            f"{PREFIX}/service_provider/{registration['id']}",
            headers=root_headers(),
        )
        assert deleted.status_code in (200, 204), deleted.text
        _, target = sp.redirect_request(sign=True)
        response = server.get(target, headers=session(admin_a), follow_redirects=False)
        assert response.status_code == 403, response.text

    def test_a_deleted_sp_cannot_answer_a_parked_request(
        self, server, make_sp, admin_a
    ):
        sp, registration = make_sp()
        assert registration is not None
        _, target = sp.redirect_request(sign=True)
        received = server.get(target, headers=session(admin_a), follow_redirects=False)
        server.delete(
            f"{PREFIX}/service_provider/{registration['id']}",
            headers=root_headers(),
        )
        page = server.get(
            received.headers["location"],
            headers=session(admin_a),
            follow_redirects=False,
        )
        assert page.status_code in (403, 404), page.text


# -- registration and protocol rules (no app) --------------------------------


def _registration(**fields: Any) -> Dict[str, Any]:
    return {
        "name": "SP",
        "entity_id": "https://sp.example.test",
        "acs_urls": ["https://sp.example.test/acs"],
        **fields,
    }


@pytest.mark.parametrize(
    "url, problem",
    [
        ("https://sp.example.test/acs", None),
        ("http://localhost:8000/acs", None),
        ("http://sp.example.test/acs", "must use https"),
        ("https://sp.example.test/acs#x", "no fragment"),
        ("https://user:pw@sp.example.test/acs", "no fragment or credentials"),
        ("/acs", "not an absolute"),
        ("javascript:alert(1)", "not an absolute"),
    ],
)
def test_acs_urls_are_https_absolute_urls(url, problem):
    found = acs_url_problem(url)
    if problem is None:
        assert found is None
    else:
        assert found is not None and problem in found


@pytest.mark.parametrize(
    "fields, message",
    [
        ({"acs_urls": []}, "acs_urls"),
        ({"entity_id": "has space"}, "entity_id"),
        ({"require_signed_requests": True}, "needs the SP's certificate"),
        ({"encrypt_assertions": True}, "needs the SP's certificate"),
        ({"certificate": "not a certificate"}, "certificate"),
        ({"released_attributes": ["password"]}, "not releasable"),
    ],
)
def test_a_registration_is_refused_for(fields, message):
    with pytest.raises(HTTPException) as refused:
        checked_registration(_registration(**fields))
    assert refused.value.status_code == 422
    assert message in str(refused.value.detail)


def test_a_registration_is_normalized():
    _, certificate = keypair("sp")
    checked = checked_registration(
        _registration(
            name="  SP  ",
            acs_urls=["https://sp.example.test/acs", "https://sp.example.test/acs"],
            certificate=certificate.replace("\n", "\\n"),
            released_attributes=["email", "email"],
        )
    )
    assert checked["name"] == "SP"
    assert checked["acs_urls"] == ["https://sp.example.test/acs"]
    assert checked["certificate"] == certificate
    assert checked["released_attributes"] == ["email"]


def _signed_query(key_pem: str, pairs: List[Tuple[str, str]]) -> str:
    """A redirect-binding query signed over ``pairs`` as given (already
    percent-encoded), the way an SP that encodes in lower case signs."""
    key = serialization.load_pem_private_key(key_pem.encode(), password=None)
    assert isinstance(key, rsa.RSAPrivateKey)
    signed = "&".join(f"{name}={value}" for name, value in pairs)
    signature = key.sign(signed.encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{signed}&Signature={quote(base64.b64encode(signature), safe='')}"


def test_a_redirect_signature_is_checked_over_the_query_as_sent():
    key, certificate = keypair("sp")
    pairs = [
        ("SAMLRequest", "abc%2bdef%3d"),
        ("RelayState", "a%2fb"),
        ("SigAlg", quote(SIG_RSA_SHA256, safe="").lower()),
    ]
    IdP.verify_redirect_signature(_signed_query(key, pairs), certificate)


@pytest.mark.parametrize(
    "change",
    [
        lambda q: q.replace("RelayState=a%2fb", "RelayState=a%2fc"),
        lambda q: q.replace("RelayState=a%2fb&", ""),
        lambda q: q + "&RelayState=x",
        lambda q: q.split("&Signature=")[0],
    ],
    ids=["changed", "dropped", "repeated", "unsigned"],
)
def test_a_redirect_signature_over_anything_else_is_refused(change):
    key, certificate = keypair("sp")
    pairs = [
        ("SAMLRequest", "abc"),
        ("RelayState", "a%2fb"),
        ("SigAlg", quote(SIG_RSA_SHA256, safe="")),
    ]
    with pytest.raises(IdP.SignatureRefused):
        IdP.verify_redirect_signature(change(_signed_query(key, pairs)), certificate)


def test_sha1_redirect_signatures_are_not_accepted():
    key, certificate = keypair("sp")
    pairs = [
        ("SAMLRequest", "abc"),
        ("SigAlg", quote("http://www.w3.org/2000/09/xmldsig#rsa-sha1", safe="")),
    ]
    with pytest.raises(IdP.SignatureRefused, match="not accepted"):
        IdP.verify_redirect_signature(_signed_query(key, pairs), certificate)


def test_an_oversized_or_bomb_message_is_refused_unread():
    with pytest.raises(IdP.RefusedMessage, match="too large"):
        IdP.message_xml(
            "A" * (IdP.MAX_ENCODED_MESSAGE_BYTES + 1), BINDING_HTTP_REDIRECT
        )
    bomb = zlib.compress(b"<" + b"a" * (IdP.MAX_INFLATED_MESSAGE_BYTES * 4))[2:-4]
    with pytest.raises(IdP.RefusedMessage, match="too large"):
        IdP.message_xml(base64.b64encode(bomb).decode(), BINDING_HTTP_REDIRECT)


def test_the_post_page_escapes_what_it_carries():
    page = IdP.post_page(
        'https://sp.example.test/acs?a="1"', "<Response/>", '"><script>x</script>'
    )
    assert "<script>x</script>" not in page.html
    assert 'action="https://sp.example.test/acs?a=&quot;1&quot;"' in page.html
    assert "form-action https://sp.example.test" in page.content_security_policy
    assert "default-src 'none'" in page.content_security_policy


def test_a_signing_key_must_be_the_certificates():
    key, certificate = keypair("idp")
    other_key, _ = keypair("other")
    assert IdP.SigningIdentity.checked(certificate, key).certificate_pem == certificate
    with pytest.raises(ValueError, match="does not belong"):
        IdP.SigningIdentity.checked(certificate, other_key)
