# SPDX-License-Identifier: AGPL-3.0-or-later
"""A real SAML 2.0 identity provider for the tests: pysaml2's IdP server
with its own RSA key and certificate. It reads (and checks the signature
of) the SP's AuthnRequests and writes signed, optionally encrypted,
responses, which a test can alter before they are signed."""

import base64
import datetime
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from saml2 import BINDING_HTTP_REDIRECT, class_name, samlp
from saml2.config import IdPConfig
from saml2.metadata import entity_descriptor
from saml2.saml import NAMEID_FORMAT_EMAILADDRESS, Issuer, NameID
from saml2.server import Server
from saml2.sigver import pre_signature_part, signed_instance_factory
from saml2.xmldsig import DIGEST_SHA256, SIG_RSA_SHA256

KEY_BITS = 2048
CERT_DAYS = 30
IDP_ENTITY_ID = "https://idp.example.test/saml/metadata"
IDP_SSO_URL = "https://idp.example.test/saml/sso"
PASSWORD_CONTEXT = "urn:oasis:names:tc:SAML:2.0:ac:classes:PasswordProtectedTransport"

Mutation = Callable[[Any], None]


def key_pair(common_name: str) -> Tuple[str, str]:
    """A fresh RSA key and a self-signed certificate for it, as PEM."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=KEY_BITS)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.datetime.now(datetime.timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=CERT_DAYS))
        .sign(key, hashes.SHA256())
    )
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return key_pem, certificate.public_bytes(serialization.Encoding.PEM).decode()


def saml_time(moment: datetime.datetime) -> str:
    return moment.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def query_of(location: str) -> Dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlparse(location).query).items()}


class LocalIdP:
    """One IdP: an entity ID, an SSO endpoint and a signing key."""

    def __init__(
        self, entity_id: str = IDP_ENTITY_ID, sso_url: str = IDP_SSO_URL
    ) -> None:
        self.entity_id = entity_id
        self.sso_url = sso_url
        self._directory = tempfile.TemporaryDirectory(prefix="local_idp_")
        self.key_pem, self.cert_pem = key_pair("local-idp")
        self._key_file = Path(self._directory.name) / "idp.key"
        self._cert_file = Path(self._directory.name) / "idp.crt"
        self._key_file.write_text(self.key_pem)
        self._cert_file.write_text(self.cert_pem)
        self.metadata_xml = str(entity_descriptor(self._config([])))

    def close(self) -> None:
        self._directory.cleanup()

    def _config(self, sp_metadata: List[str]) -> IdPConfig:
        config = IdPConfig()
        config.load(
            {
                "entityid": self.entity_id,
                "service": {
                    "idp": {
                        "endpoints": {
                            "single_sign_on_service": [
                                (self.sso_url, BINDING_HTTP_REDIRECT)
                            ]
                        },
                        "policy": {
                            "default": {
                                "lifetime": {"minutes": 5},
                                "attribute_restrictions": None,
                            }
                        },
                        "name_id_format": [NAMEID_FORMAT_EMAILADDRESS],
                        "want_authn_requests_signed": True,
                    }
                },
                "key_file": str(self._key_file),
                "cert_file": str(self._cert_file),
                "metadata": {"inline": sp_metadata},
                "signing_algorithm": SIG_RSA_SHA256,
                "digest_algorithm": DIGEST_SHA256,
            }
        )
        return config

    def server(self, sp_metadata_xml: str) -> Server:
        return Server(config=self._config([sp_metadata_xml]))

    def read_request(self, sp_metadata_xml: str, location: str) -> Any:
        """The AuthnRequest a redirect carries, its signature (when it has
        one) checked against the SP's metadata."""
        query = query_of(location)
        parsed = self.server(sp_metadata_xml).parse_authn_request(
            query["SAMLRequest"],
            BINDING_HTTP_REDIRECT,
            relay_state=query.get("RelayState"),
            sigalg=query.get("SigAlg"),
            signature=query.get("Signature"),
        )
        return parsed.message

    def response_xml(
        self,
        sp_metadata_xml: str,
        *,
        sp_entity_id: str,
        acs_url: str,
        in_response_to: Optional[str],
        name_id: str = "alice@example.test",
        name_id_format: str = NAMEID_FORMAT_EMAILADDRESS,
        attributes: Optional[Dict[str, List[str]]] = None,
        sign_assertion: bool = True,
        sign_response: bool = False,
        mutate: Optional[Mutation] = None,
        signer: Optional["LocalIdP"] = None,
    ) -> str:
        """A response, altered by ``mutate`` (on pysaml2's object model)
        before it is signed by ``signer`` (this IdP by default)."""
        server = self.server(sp_metadata_xml)
        unsigned = server.create_authn_response(
            (
                attributes
                if attributes is not None
                else {"mail": [name_id], "givenName": ["Alice"]}
            ),
            in_response_to=in_response_to,
            destination=acs_url,
            sp_entity_id=sp_entity_id,
            name_id=NameID(format=name_id_format, text=name_id),
            authn={"class_ref": PASSWORD_CONTEXT},
            sign_assertion=False,
            sign_response=False,
        )
        response = samlp.response_from_string(str(unsigned))
        if mutate is not None:
            mutate(response)
        security = (signer or self).server(sp_metadata_xml).sec
        to_sign = []
        if sign_assertion:
            assertion = response.assertion[0]
            assertion.signature = pre_signature_part(
                assertion.id,
                security.my_cert,
                1,
                digest_alg=DIGEST_SHA256,
                sign_alg=SIG_RSA_SHA256,
            )
            to_sign.append((class_name(assertion), assertion.id))
        if sign_response:
            response.signature = pre_signature_part(
                response.id,
                security.my_cert,
                2,
                digest_alg=DIGEST_SHA256,
                sign_alg=SIG_RSA_SHA256,
            )
            to_sign.append((class_name(response), response.id))
        return str(signed_instance_factory(response, security, to_sign))

    def encrypted_response_xml(
        self,
        sp_metadata_xml: str,
        *,
        sp_entity_id: str,
        acs_url: str,
        in_response_to: Optional[str],
    ) -> str:
        """A response whose signed assertion is encrypted to the SP's
        encryption certificate from its metadata."""
        server = self.server(sp_metadata_xml)
        return str(
            server.create_authn_response(
                {"mail": ["erin@example.test"]},
                in_response_to=in_response_to,
                destination=acs_url,
                sp_entity_id=sp_entity_id,
                name_id=NameID(
                    format=NAMEID_FORMAT_EMAILADDRESS, text="erin@example.test"
                ),
                authn={"class_ref": PASSWORD_CONTEXT},
                sign_assertion=True,
                encrypt_assertion=True,
                encrypt_assertion_self_contained=True,
            )
        )


def encoded(xml: str) -> str:
    """The HTTP-POST binding's form of a response."""
    return base64.b64encode(xml.encode()).decode()


def set_time(element: Any, attribute: str, moment: datetime.datetime) -> None:
    setattr(element, attribute, saml_time(moment))


def expire(response: Any) -> None:
    """Move every validity window of the response into the past."""
    past = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)
    assertion = response.assertion[0]
    set_time(assertion.conditions, "not_on_or_after", past)
    set_time(assertion.conditions, "not_before", past - datetime.timedelta(minutes=5))
    for confirmation in assertion.subject.subject_confirmation:
        set_time(confirmation.subject_confirmation_data, "not_on_or_after", past)


def audience(value: str) -> Mutation:
    def change(response: Any) -> None:
        response.assertion[0].conditions.audience_restriction[0].audience[
            0
        ].text = value

    return change


def recipient(value: str) -> Mutation:
    def change(response: Any) -> None:
        for confirmation in response.assertion[0].subject.subject_confirmation:
            confirmation.subject_confirmation_data.recipient = value

    return change


def destination(value: str) -> Mutation:
    def change(response: Any) -> None:
        response.destination = value

    return change


def assertion_issuer(value: str) -> Mutation:
    def change(response: Any) -> None:
        response.assertion[0].issuer = Issuer(text=value)

    return change


def failed_status(response: Any) -> None:
    response.status = samlp.Status(
        status_code=samlp.StatusCode(
            value="urn:oasis:names:tc:SAML:2.0:status:Responder"
        )
    )
