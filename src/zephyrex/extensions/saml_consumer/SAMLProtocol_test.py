# SPDX-License-Identifier: AGPL-3.0-or-later
"""The SP's protocol against a real IdP (pysaml2's server, its own key):
AuthnRequests the IdP accepts, responses accepted, and each one refused for
the reason it should be."""

import datetime
import re
from typing import Any, Dict, Iterator, Optional

import pytest

from zephyrex.extensions.ExternalErrors import InvalidInputExternalError
from zephyrex.extensions.saml_consumer.LocalIdP_test import (
    LocalIdP,
    assertion_issuer,
    audience,
    destination,
    encoded,
    expire,
    failed_status,
    key_pair,
    recipient,
    saml_time,
)
from zephyrex.extensions.saml_consumer.SAMLProtocol import (
    SAMLResponseRefused,
    ServiceProvider,
    check_key_pair,
    parse_idp_metadata,
)

SP_ENTITY_ID = "https://sp.example.test/saml/metadata"
ACS_URL = "https://sp.example.test/saml/acs"
REQUEST_ID = "id-outstanding-request"
SP_KEY, SP_CERT = key_pair("sp")


@pytest.fixture(scope="module")
def idp() -> Iterator[LocalIdP]:
    local = LocalIdP()
    yield local
    local.close()


def service_provider(idp: LocalIdP, **overrides: Any) -> ServiceProvider:
    fields: Dict[str, Any] = {
        "entity_id": SP_ENTITY_ID,
        "acs_url": ACS_URL,
        "idp_metadata_xml": idp.metadata_xml,
        "private_key_pem": SP_KEY,
        "certificate_pem": SP_CERT,
        **overrides,
    }
    return ServiceProvider(**fields)


@pytest.fixture(scope="module")
def sp(idp: LocalIdP) -> ServiceProvider:
    return service_provider(idp)


@pytest.fixture(scope="module")
def sp_metadata(sp: ServiceProvider) -> str:
    return sp.metadata_xml()


def respond(
    idp: LocalIdP,
    sp_metadata: str,
    in_response_to: Optional[str] = REQUEST_ID,
    **kwargs: Any,
) -> str:
    return idp.response_xml(
        sp_metadata,
        sp_entity_id=SP_ENTITY_ID,
        acs_url=ACS_URL,
        in_response_to=in_response_to,
        **kwargs,
    )


def refused(sp: ServiceProvider, xml: str, expected: Optional[str] = REQUEST_ID) -> str:
    with pytest.raises(SAMLResponseRefused) as caught:
        sp.validate(encoded(xml), expected)
    return caught.value.reason


class TestMetadata:
    def test_idp_metadata_names_the_idp_its_sso_and_its_key(
        self, idp: LocalIdP
    ) -> None:
        parsed = parse_idp_metadata(idp.metadata_xml)
        assert parsed.entity_id == idp.entity_id
        assert parsed.sso_redirect_url == idp.sso_url
        assert "".join(idp.cert_pem.split()) in "".join(
            parsed.signing_certificates[0].split()
        )

    def test_metadata_without_an_idp_is_refused(self, sp: ServiceProvider) -> None:
        with pytest.raises(
            InvalidInputExternalError, match="one SAML 2.0 identity provider"
        ):
            parse_idp_metadata(sp.metadata_xml())

    def test_metadata_with_a_dtd_is_refused(self, idp: LocalIdP) -> None:
        bomb = (
            '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]>'
            + idp.metadata_xml.split("?>", 1)[-1]
        )
        with pytest.raises(InvalidInputExternalError, match="not well-formed"):
            parse_idp_metadata(bomb)

    def test_expired_metadata_is_refused(self, idp: LocalIdP) -> None:
        past = saml_time(
            datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)
        )
        stale = re.sub(
            r"(EntityDescriptor )",
            rf'\1validUntil="{past}" ',
            idp.metadata_xml,
            count=1,
        )
        with pytest.raises(InvalidInputExternalError, match="expired"):
            parse_idp_metadata(stale)

    def test_sp_metadata_names_the_acs_and_the_sp_certificate(
        self, sp_metadata: str
    ) -> None:
        assert f'entityID="{SP_ENTITY_ID}"' in sp_metadata
        assert ACS_URL in sp_metadata
        cert_body = "".join(
            line for line in SP_CERT.splitlines() if "CERTIFICATE" not in line
        )
        assert cert_body[:40] in sp_metadata.replace("\n", "")

    def test_a_key_and_a_certificate_for_another_key_are_refused(self) -> None:
        _, other_cert = key_pair("other")
        with pytest.raises(InvalidInputExternalError, match="does not belong"):
            check_key_pair(SP_KEY, other_cert)
        check_key_pair(SP_KEY, SP_CERT)


class TestAuthnRequest:
    def test_the_idp_accepts_the_signed_redirect_request(
        self, idp: LocalIdP, sp: ServiceProvider, sp_metadata: str
    ) -> None:
        request = sp.authn_request()
        assert request.location.startswith(idp.sso_url + "?")
        assert "Signature=" in request.location and "SigAlg=" in request.location
        message = idp.read_request(sp_metadata, request.location)
        assert message.id == request.request_id
        assert message.issuer.text == SP_ENTITY_ID
        assert message.assertion_consumer_service_url == ACS_URL

    def test_a_tampered_request_signature_is_refused_by_the_idp(
        self, idp: LocalIdP, sp: ServiceProvider, sp_metadata: str
    ) -> None:
        location = sp.authn_request().location
        tampered = re.sub(r"Signature=[^&]+", "Signature=AAAA", location)
        with pytest.raises(Exception):
            idp.read_request(sp_metadata, tampered)

    def test_each_request_has_its_own_id(self, sp: ServiceProvider) -> None:
        assert sp.authn_request().request_id != sp.authn_request().request_id


class TestAccepted:
    def test_a_signed_assertion_answering_the_request_is_accepted(
        self, idp: LocalIdP, sp: ServiceProvider, sp_metadata: str
    ) -> None:
        identity = sp.validate(encoded(respond(idp, sp_metadata)), REQUEST_ID)
        assert identity.issuer == idp.entity_id
        assert identity.name_id == "alice@example.test"
        assert identity.in_response_to == REQUEST_ID
        assert identity.email(None) == "alice@example.test"
        assert identity.attribute(["givenName"]) == "Alice"
        assert identity.assertion_id
        assert identity.not_on_or_after > datetime.datetime.now(datetime.timezone.utc)

    def test_a_signed_response_and_assertion_are_accepted(
        self, idp: LocalIdP, sp: ServiceProvider, sp_metadata: str
    ) -> None:
        xml = respond(idp, sp_metadata, sign_response=True)
        assert sp.validate(encoded(xml), REQUEST_ID).name_id == "alice@example.test"

    def test_only_the_response_signed_is_enough_when_assertions_need_not_be(
        self, idp: LocalIdP, sp_metadata: str
    ) -> None:
        lenient = service_provider(idp, want_assertions_signed=False)
        xml = respond(idp, sp_metadata, sign_assertion=False, sign_response=True)
        assert (
            lenient.validate(encoded(xml), REQUEST_ID).name_id == "alice@example.test"
        )

    def test_an_encrypted_assertion_is_decrypted_with_the_sp_key(
        self, idp: LocalIdP, sp: ServiceProvider, sp_metadata: str
    ) -> None:
        xml = idp.encrypted_response_xml(
            sp_metadata,
            sp_entity_id=SP_ENTITY_ID,
            acs_url=ACS_URL,
            in_response_to=REQUEST_ID,
        )
        assert "EncryptedAssertion" in xml and "erin@example.test" not in xml
        assert sp.validate(encoded(xml), REQUEST_ID).name_id == "erin@example.test"

    def test_an_unsolicited_response_is_accepted_where_allowed(
        self, idp: LocalIdP, sp_metadata: str
    ) -> None:
        open_sp = service_provider(idp, allow_unsolicited=True)
        identity = open_sp.validate(
            encoded(respond(idp, sp_metadata, in_response_to=None)), None
        )
        assert identity.in_response_to is None


class TestRefused:
    def test_bad_signature(
        self, idp: LocalIdP, sp: ServiceProvider, sp_metadata: str
    ) -> None:
        tampered = respond(idp, sp_metadata).replace(
            "alice@example.test", "mallory@example.test"
        )
        assert refused(sp, tampered) == "bad_signature"

    def test_signed_by_a_key_the_metadata_does_not_name(
        self, idp: LocalIdP, sp: ServiceProvider, sp_metadata: str
    ) -> None:
        impostor = LocalIdP()
        try:
            assert (
                refused(sp, respond(idp, sp_metadata, signer=impostor))
                == "bad_signature"
            )
        finally:
            impostor.close()

    def test_unsigned(
        self, idp: LocalIdP, sp: ServiceProvider, sp_metadata: str
    ) -> None:
        assert (
            refused(sp, respond(idp, sp_metadata, sign_assertion=False))
            == "bad_signature"
        )

    def test_only_the_response_signed_when_the_assertion_must_be(
        self, idp: LocalIdP, sp: ServiceProvider, sp_metadata: str
    ) -> None:
        xml = respond(idp, sp_metadata, sign_assertion=False, sign_response=True)
        assert refused(sp, xml) == "bad_signature"

    def test_wrapping_a_forged_assertion_around_the_signed_one(
        self, idp: LocalIdP, sp: ServiceProvider, sp_metadata: str
    ) -> None:
        """XSW: the genuine signed assertion, byte for byte, moved into the
        Response's Extensions, and a forged copy (same ID, another user,
        no signature) where the profile reads the assertion."""
        xml = respond(idp, sp_metadata)
        signed = re.search(r"<ns1:Assertion .*</ns1:Assertion>", xml, re.S)
        assert signed is not None
        forged = re.sub(
            r"<ns2:Signature.*</ns2:Signature>", "", signed.group(0), flags=re.S
        ).replace("alice@example.test", "mallory@example.test")
        wrapped = xml.replace(
            signed.group(0),
            f"<ns0:Extensions>{signed.group(0)}</ns0:Extensions>{forged}",
        )
        assert refused(sp, wrapped) == "wrapped"

    def test_a_second_assertion_beside_the_signed_one(
        self, idp: LocalIdP, sp: ServiceProvider, sp_metadata: str
    ) -> None:
        xml = respond(idp, sp_metadata)
        signed = re.search(r"<ns1:Assertion .*</ns1:Assertion>", xml, re.S)
        assert signed is not None
        forged = re.sub(
            r"<ns2:Signature.*</ns2:Signature>", "", signed.group(0), flags=re.S
        )
        forged = re.sub(r'ID="[^"]+"', 'ID="id-forged"', forged, count=1).replace(
            "alice@example.test", "mallory@example.test"
        )
        assert (
            refused(sp, xml.replace(signed.group(0), forged + signed.group(0)))
            == "wrapped"
        )

    def test_a_signature_over_another_element(
        self, idp: LocalIdP, sp: ServiceProvider, sp_metadata: str
    ) -> None:
        xml = respond(idp, sp_metadata)
        response_id = re.search(r'<ns0:Response [^>]*ID="([^"]+)"', xml)
        assert response_id is not None
        retargeted = re.sub(
            r'URI="#[^"]+"', f'URI="#{response_id.group(1)}"', xml, count=1
        )
        assert refused(sp, retargeted) == "wrapped"

    def test_wrong_audience(
        self, idp: LocalIdP, sp: ServiceProvider, sp_metadata: str
    ) -> None:
        xml = respond(
            idp, sp_metadata, mutate=audience("https://other-sp.example.test/metadata")
        )
        assert refused(sp, xml) == "audience"

    def test_expired(
        self, idp: LocalIdP, sp: ServiceProvider, sp_metadata: str
    ) -> None:
        assert refused(sp, respond(idp, sp_metadata, mutate=expire)) == "expired"

    def test_wrong_recipient(
        self, idp: LocalIdP, sp: ServiceProvider, sp_metadata: str
    ) -> None:
        xml = respond(
            idp, sp_metadata, mutate=recipient("https://other-sp.example.test/acs")
        )
        assert refused(sp, xml) == "recipient"

    def test_wrong_destination(
        self, idp: LocalIdP, sp: ServiceProvider, sp_metadata: str
    ) -> None:
        xml = respond(
            idp, sp_metadata, mutate=destination("https://other-sp.example.test/acs")
        )
        assert refused(sp, xml) == "destination"

    def test_issued_by_another_entity(
        self, idp: LocalIdP, sp: ServiceProvider, sp_metadata: str
    ) -> None:
        xml = respond(
            idp,
            sp_metadata,
            mutate=assertion_issuer("https://evil.example.test/metadata"),
        )
        assert refused(sp, xml) == "issuer"

    def test_a_failure_status(
        self, idp: LocalIdP, sp: ServiceProvider, sp_metadata: str
    ) -> None:
        assert refused(sp, respond(idp, sp_metadata, mutate=failed_status)) == "status"

    def test_in_response_to_another_request(
        self, idp: LocalIdP, sp: ServiceProvider, sp_metadata: str
    ) -> None:
        xml = respond(idp, sp_metadata, in_response_to="id-someone-elses-request")
        assert refused(sp, xml) == "in_response_to"

    def test_in_response_to_when_this_browser_has_no_request(
        self, idp: LocalIdP, sp: ServiceProvider, sp_metadata: str
    ) -> None:
        assert refused(sp, respond(idp, sp_metadata), expected=None) == "in_response_to"

    def test_unsolicited_when_not_allowed(
        self, idp: LocalIdP, sp: ServiceProvider, sp_metadata: str
    ) -> None:
        assert (
            refused(sp, respond(idp, sp_metadata, in_response_to=None)) == "unsolicited"
        )

    def test_not_base64(self, sp: ServiceProvider) -> None:
        with pytest.raises(SAMLResponseRefused) as caught:
            sp.validate("not base64 at all!", REQUEST_ID)
        assert caught.value.reason == "malformed"
