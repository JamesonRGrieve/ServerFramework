# SPDX-License-Identifier: AGPL-3.0-or-later
"""SAML 2.0 Web Browser SSO, Service Provider side, over pysaml2.

:class:`ServiceProvider` is one SP facing one IdP: it writes the
HTTP-Redirect AuthnRequest, publishes the SP metadata, and validates what
the IdP posts to the Assertion Consumer Service. :func:`parse_idp_metadata`
reads an IdP's metadata.

A response is accepted only when all of these hold:

* the document is shaped as SAML expects, the shape that leaves nothing to
  wrap: one ``Response`` holding exactly one assertion (plain or
  encrypted) as its direct child, every element ID unique, and every
  signature a direct child of the element it signs, referring to that
  element's ID and nothing else;
* the Response or the Assertion carries a valid signature by a key in the
  IdP's metadata (pysaml2 checks it with xmlsec1 after validating the
  document against the SAML schema), and each one the IdP is configured
  to sign is signed;
* Issuer is the IdP, Destination and the bearer Recipient are this ACS,
  the AudienceRestriction names this SP, the status is Success;
* InResponseTo is the outstanding request's ID, or absent when unsolicited
  responses are allowed;
* the assertion is within NotBefore/NotOnOrAfter, give or take
  ``CLOCK_SKEW_SECONDS``.

Encrypted assertions are decrypted with the SP key when one is configured.
Single use (of request IDs and assertion IDs) is the caller's to record.
"""

import base64
import binascii
import re
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple
from xml.etree.ElementTree import Element

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from defusedxml import DefusedXmlException
from defusedxml.ElementTree import ParseError, fromstring
from saml2 import BINDING_HTTP_POST, BINDING_HTTP_REDIRECT, SAMLError
from saml2.client import Saml2Client
from saml2.config import SPConfig
from saml2.metadata import entity_descriptor
from saml2.response import IncorrectlySigned, UnsolicitedResponse
from saml2.sigver import MissingKey, SigverError
from saml2.xmldsig import DIGEST_SHA256, SIG_RSA_SHA256

from zephyrex.extensions.ExternalErrors import (
    AuthExternalError,
    InvalidInputExternalError,
)
from zephyrex.lib.Logging import logger

CLOCK_SKEW_SECONDS = 60
MAX_RESPONSE_BYTES = 256 * 1024
MAX_METADATA_BYTES = 1024 * 1024

NS_PROTOCOL = "urn:oasis:names:tc:SAML:2.0:protocol"
NS_ASSERTION = "urn:oasis:names:tc:SAML:2.0:assertion"
NS_METADATA = "urn:oasis:names:tc:SAML:2.0:metadata"
NS_DSIG = "http://www.w3.org/2000/09/xmldsig#"
PROTOCOL_SAML2 = NS_PROTOCOL
STATUS_SUCCESS = "urn:oasis:names:tc:SAML:2.0:status:Success"
METHOD_BEARER = "urn:oasis:names:tc:SAML:2.0:cm:bearer"
NAMEID_FORMAT_EMAILADDRESS = "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress"
NAMEID_FORMAT_TRANSIENT = "urn:oasis:names:tc:SAML:2.0:nameid-format:transient"

_RESPONSE = f"{{{NS_PROTOCOL}}}Response"
_STATUS_CODE = f"{{{NS_PROTOCOL}}}StatusCode"
_ASSERTION = f"{{{NS_ASSERTION}}}Assertion"
_ENCRYPTED_ASSERTION = f"{{{NS_ASSERTION}}}EncryptedAssertion"
_ISSUER = f"{{{NS_ASSERTION}}}Issuer"
_SUBJECT_CONFIRMATION = f"{{{NS_ASSERTION}}}SubjectConfirmation"
_SUBJECT_CONFIRMATION_DATA = f"{{{NS_ASSERTION}}}SubjectConfirmationData"
_CONDITIONS = f"{{{NS_ASSERTION}}}Conditions"
_AUDIENCE_RESTRICTION = f"{{{NS_ASSERTION}}}AudienceRestriction"
_AUDIENCE = f"{{{NS_ASSERTION}}}Audience"
_AUTHN_STATEMENT = f"{{{NS_ASSERTION}}}AuthnStatement"
_SIGNATURE = f"{{{NS_DSIG}}}Signature"
_REFERENCE = f"{{{NS_DSIG}}}Reference"
_X509_CERTIFICATE = f"{{{NS_DSIG}}}X509Certificate"
_ENTITY_DESCRIPTOR = f"{{{NS_METADATA}}}EntityDescriptor"
_IDP_DESCRIPTOR = f"{{{NS_METADATA}}}IDPSSODescriptor"
_KEY_DESCRIPTOR = f"{{{NS_METADATA}}}KeyDescriptor"
_SSO_SERVICE = f"{{{NS_METADATA}}}SingleSignOnService"

# More than six fractional digits (ADFS writes seven) is cut to six.
_LONG_FRACTION = re.compile(r"(\.\d{6})\d+")


class SAMLResponseRefused(AuthExternalError):
    """The IdP's response was not accepted. ``reason`` is one of a fixed set
    of words (``bad_signature``, ``wrapped``, ``audience``, ``expired``,
    ``unsolicited``, ``in_response_to``, ``replayed``, ...), safe to show;
    the specifics are logged."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"SAML response refused: {reason}", provider="saml")
        self.reason = reason
        if detail:
            logger.info("saml_consumer: refused (%s): %s", reason, detail)


def _invalid(message: str) -> InvalidInputExternalError:
    return InvalidInputExternalError(message, provider="saml")


def parse_xml(text: str, what: str) -> Element:
    """``text`` parsed with DTDs, entities and external references refused."""
    try:
        root: Element = fromstring(text, forbid_dtd=True)
    except (ParseError, DefusedXmlException) as exc:
        raise _invalid(f"{what} is not well-formed XML: {exc}") from exc
    return root


def parse_saml_time(value: Optional[str]) -> Optional[datetime]:
    """An ``xs:dateTime`` as an aware UTC datetime; None when absent."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(_LONG_FRACTION.sub(r"\1", value.strip()))
    except ValueError as exc:
        raise SAMLResponseRefused("malformed", f"bad timestamp {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def pem_certificate(text: str) -> str:
    """A certificate as PEM, from PEM or the bare base64 metadata carries."""
    body = text.strip()
    if "-----BEGIN CERTIFICATE-----" not in body:
        body = "\n".join(
            [
                "-----BEGIN CERTIFICATE-----",
                *_wrap("".join(body.split())),
                "-----END CERTIFICATE-----",
            ]
        )
    try:
        x509.load_pem_x509_certificate(body.encode())
    except ValueError as exc:
        raise _invalid(f"not an X.509 certificate: {exc}") from exc
    return body + "\n"


def _wrap(text: str, width: int = 64) -> List[str]:
    return [text[i : i + width] for i in range(0, len(text), width)]


def check_key_pair(private_key_pem: str, certificate_pem: str) -> None:
    """The SP key is an RSA key, and the certificate is for it."""
    try:
        key = serialization.load_pem_private_key(
            private_key_pem.encode(), password=None
        )
    except (ValueError, TypeError) as exc:
        raise _invalid(
            f"sp_private_key is not an unencrypted PEM private key: {exc}"
        ) from exc
    if not isinstance(key, RSAPrivateKey):
        raise _invalid(
            "sp_private_key must be an RSA key (xmlsec signs with RSA-SHA256)"
        )
    certificate = x509.load_pem_x509_certificate(
        pem_certificate(certificate_pem).encode()
    )
    if certificate.public_key() != key.public_key():
        raise _invalid("sp_certificate does not belong to sp_private_key")


# ---------------------------------------------------------------------------
# IdP metadata
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IdPMetadata:
    entity_id: str
    sso_redirect_url: str
    signing_certificates: Tuple[str, ...]
    wants_signed_requests: bool


def parse_idp_metadata(xml: str) -> IdPMetadata:
    """The one SAML 2.0 identity provider ``xml`` describes: an
    EntityDescriptor, or an EntitiesDescriptor holding exactly one IdP."""
    if len(xml.encode()) > MAX_METADATA_BYTES:
        raise _invalid(f"IdP metadata is over {MAX_METADATA_BYTES} bytes")
    root = parse_xml(xml, "IdP metadata")
    candidates = (
        [root]
        if root.tag == _ENTITY_DESCRIPTOR
        else list(root.iter(_ENTITY_DESCRIPTOR))
    )
    idps = [
        (entity, descriptor)
        for entity in candidates
        for descriptor in entity.findall(_IDP_DESCRIPTOR)
        if PROTOCOL_SAML2
        in (descriptor.get("protocolSupportEnumeration") or "").split()
    ]
    if len(idps) != 1:
        raise _invalid(
            f"IdP metadata must describe one SAML 2.0 identity provider, not {len(idps)}"
        )
    entity, descriptor = idps[0]
    entity_id = (entity.get("entityID") or "").strip()
    if not entity_id:
        raise _invalid("IdP metadata has no entityID")
    valid_until = entity.get("validUntil") or root.get("validUntil")
    if valid_until:
        until = parse_saml_time(valid_until)
        if until is not None and until < datetime.now(timezone.utc):
            raise _invalid(f"IdP metadata expired at {valid_until}")
    redirects = [
        (service.get("Location") or "").strip()
        for service in descriptor.findall(_SSO_SERVICE)
        if service.get("Binding") == BINDING_HTTP_REDIRECT
    ]
    redirects = [location for location in redirects if location]
    if not redirects:
        raise _invalid("the IdP offers no HTTP-Redirect SingleSignOnService")
    certificates = tuple(
        pem_certificate(cert.text or "")
        for key in descriptor.findall(_KEY_DESCRIPTOR)
        if key.get("use") in (None, "signing")
        for cert in key.iter(_X509_CERTIFICATE)
    )
    if not certificates:
        raise _invalid("IdP metadata names no signing certificate")
    return IdPMetadata(
        entity_id=entity_id,
        sso_redirect_url=redirects[0],
        signing_certificates=certificates,
        wants_signed_requests=(descriptor.get("WantAuthnRequestsSigned") or "").lower()
        in ("true", "1"),
    )


# ---------------------------------------------------------------------------
# What a validated response asserts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AssertedIdentity:
    issuer: str
    name_id: str
    name_id_format: Optional[str]
    assertion_id: str
    not_on_or_after: datetime
    in_response_to: Optional[str]
    session_index: Optional[str]
    attributes: Mapping[str, Tuple[str, ...]] = field(default_factory=dict)

    @property
    def is_transient(self) -> bool:
        return self.name_id_format == NAMEID_FORMAT_TRANSIENT

    def attribute(self, names: Sequence[str]) -> Optional[str]:
        """The first value of the first of ``names`` (a Name or FriendlyName,
        matched without regard to case) the assertion carries."""
        lowered = {key.lower(): values for key, values in self.attributes.items()}
        for name in names:
            values = lowered.get(name.lower())
            if values:
                return values[0]
        return None

    def email(self, configured_attribute: Optional[str]) -> Optional[str]:
        if configured_attribute:
            return self.attribute([configured_attribute])
        if self.name_id_format == NAMEID_FORMAT_EMAILADDRESS:
            return self.name_id
        return self.attribute(EMAIL_ATTRIBUTES)


# The names Okta, Entra ID/ADFS, Keycloak and Shibboleth (eduPerson) send.
EMAIL_ATTRIBUTES = (
    "email",
    "mail",
    "emailaddress",
    "urn:oid:0.9.2342.19200300.100.1.3",
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
)
FIRST_NAME_ATTRIBUTES = (
    "givenName",
    "firstName",
    "urn:oid:2.5.4.42",
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/givenname",
)
LAST_NAME_ATTRIBUTES = (
    "sn",
    "surname",
    "lastName",
    "urn:oid:2.5.4.4",
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/surname",
)
DISPLAY_NAME_ATTRIBUTES = (
    "displayName",
    "urn:oid:2.16.840.1.113730.3.1.241",
    "http://schemas.microsoft.com/identity/claims/displayname",
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name",
)


# ---------------------------------------------------------------------------
# The SP
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuthnRequestRedirect:
    request_id: str
    location: str


@dataclass(frozen=True)
class ServiceProvider:
    """This server as the SP of one IdP. ``private_key_pem`` and
    ``certificate_pem`` (both or neither) sign AuthnRequests and decrypt
    encrypted assertions."""

    entity_id: str
    acs_url: str
    idp_metadata_xml: str
    want_assertions_signed: bool = True
    want_response_signed: bool = False
    allow_unsolicited: bool = False
    name_id_format: Optional[str] = None
    private_key_pem: Optional[str] = None
    certificate_pem: Optional[str] = None

    @property
    def idp(self) -> IdPMetadata:
        return parse_idp_metadata(self.idp_metadata_xml)

    @property
    def signs_requests(self) -> bool:
        return bool(self.private_key_pem and self.certificate_pem)

    @contextmanager
    def client(self, allow_unsolicited: bool = False) -> Iterator[Saml2Client]:
        """A pysaml2 client for this SP. xmlsec1 reads the key from a file,
        so the key lives in a private temporary directory for the call."""
        with tempfile.TemporaryDirectory(prefix="saml_sp_") as directory:
            config: Dict[str, object] = {
                "entityid": self.entity_id,
                "service": {
                    "sp": {
                        "endpoints": {
                            "assertion_consumer_service": [
                                (self.acs_url, BINDING_HTTP_POST)
                            ]
                        },
                        "allow_unsolicited": allow_unsolicited,
                        "want_assertions_signed": self.want_assertions_signed,
                        "want_response_signed": self.want_response_signed,
                        "want_assertions_or_response_signed": True,
                        "authn_requests_signed": self.signs_requests,
                        "only_use_keys_in_metadata": True,
                        "allow_unknown_attributes": True,
                        **(
                            {"name_id_format": [self.name_id_format]}
                            if self.name_id_format
                            else {}
                        ),
                    }
                },
                "metadata": {"inline": [self.idp_metadata_xml]},
                "accepted_time_diff": CLOCK_SKEW_SECONDS,
                "signing_algorithm": SIG_RSA_SHA256,
                "digest_algorithm": DIGEST_SHA256,
                "delete_tmpfiles": True,
            }
            if self.private_key_pem and self.certificate_pem:
                key_file = Path(directory) / "sp.key"
                cert_file = Path(directory) / "sp.crt"
                key_file.touch(mode=0o600)
                key_file.write_text(self.private_key_pem)
                cert_file.write_text(pem_certificate(self.certificate_pem))
                config["key_file"] = str(key_file)
                config["cert_file"] = str(cert_file)
                config["encryption_keypairs"] = [
                    {"key_file": str(key_file), "cert_file": str(cert_file)}
                ]
            sp_config = SPConfig()
            sp_config.load(config)
            yield Saml2Client(config=sp_config)

    def metadata_xml(self) -> str:
        with self.client() as client:
            return str(entity_descriptor(client.config))

    def authn_request(self, relay_state: str = "") -> AuthnRequestRedirect:
        """An AuthnRequest over HTTP-Redirect: its ID, to hold until the
        response, and the IdP URL to send the browser to. Signed when the
        SP has a key."""
        idp = self.idp
        if idp.wants_signed_requests and not self.signs_requests:
            raise _invalid(
                "the IdP wants signed AuthnRequests; configure sp_private_key and sp_certificate"
            )
        with self.client() as client:
            request_id, info = client.prepare_for_authenticate(
                entityid=idp.entity_id,
                relay_state=relay_state,
                binding=BINDING_HTTP_REDIRECT,
                sign=self.signs_requests,
                sigalg=SIG_RSA_SHA256 if self.signs_requests else None,
                digest_alg=DIGEST_SHA256,
                nameid_format=self.name_id_format,
            )
        location = dict(info["headers"])["Location"]
        return AuthnRequestRedirect(request_id=str(request_id), location=str(location))

    def validate(
        self, saml_response: str, expected_request_id: Optional[str]
    ) -> AssertedIdentity:
        """What the IdP's ``SAMLResponse`` (the HTTP-POST binding's base64
        value) asserts, once every check in the module doc passes.
        ``expected_request_id`` is the AuthnRequest this browser has
        outstanding, None when it has none."""
        root = parse_xml(decode_response(saml_response), "SAMLResponse")
        assertion = check_shape(root)
        idp = self.idp
        in_response_to = check_solicitation(
            root, expected_request_id, self.allow_unsolicited
        )
        check_response(root, self.acs_url, idp.entity_id)
        if assertion.tag == _ASSERTION:
            check_assertion(assertion, self, idp.entity_id, in_response_to)
        verified = self.verify_signatures(saml_response, in_response_to)
        if assertion.tag == _ENCRYPTED_ASSERTION:
            # The decrypted document is held to the same shape: what was
            # inside the encryption is as untrusted as what was outside.
            decrypted = verified.xmlstr
            text = (
                decrypted.decode("utf-8")
                if isinstance(decrypted, bytes)
                else str(decrypted)
            )
            assertion = check_shape(
                parse_xml(text, "decrypted SAMLResponse"), decrypted=True
            )
            check_assertion(assertion, self, idp.entity_id, in_response_to)
        if len(verified.assertions) != 1:
            raise SAMLResponseRefused(
                "wrapped", f"{len(verified.assertions)} assertions after decryption"
            )
        return asserted_identity(verified, assertion, idp.entity_id, in_response_to)

    def verify_signatures(
        self, saml_response: str, in_response_to: Optional[str]
    ) -> Any:
        """pysaml2's verdict: the schema, the signatures (each one the IdP is
        configured to make, and at least one) and its own profile checks;
        an encrypted assertion comes back decrypted."""
        outstanding = {in_response_to: ""} if in_response_to else {}
        try:
            with self.client(allow_unsolicited=in_response_to is None) as client:
                verified = client.parse_authn_request_response(
                    saml_response, BINDING_HTTP_POST, outstanding=outstanding
                )
        except (SigverError, IncorrectlySigned, MissingKey) as exc:
            raise SAMLResponseRefused("bad_signature", repr(exc)) from exc
        except UnsolicitedResponse as exc:
            raise SAMLResponseRefused("in_response_to", repr(exc)) from exc
        except (SAMLError, ValueError, AttributeError, TypeError) as exc:
            raise SAMLResponseRefused("invalid", repr(exc)) from exc
        except Exception as exc:
            # pysaml2 raises bare Exception for some profile failures
            # (audience, unknown conditions); each is a refusal.
            raise SAMLResponseRefused("invalid", repr(exc)) from exc
        if verified is None or verified.assertion is None:
            raise SAMLResponseRefused("invalid", "pysaml2 returned no assertion")
        return verified


def decode_response(saml_response: str) -> str:
    if len(saml_response) > MAX_RESPONSE_BYTES:
        raise SAMLResponseRefused("malformed", "SAMLResponse too large")
    try:
        return base64.b64decode(saml_response, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise SAMLResponseRefused(
            "malformed", f"SAMLResponse is not base64 XML: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# Checks on the received document
# ---------------------------------------------------------------------------


def check_shape(root: Element, decrypted: bool = False) -> Element:
    """The single assertion, after refusing any document shaped the way
    signature-wrapping attacks need: a second assertion (or one moved out
    of place), a duplicated ID, or a signature anywhere but directly in the
    element it signs, over that element's ID. The assertion is a child of
    the Response, or, ``decrypted``, of the Response's EncryptedAssertion."""
    if root.tag != _RESPONSE:
        raise SAMLResponseRefused("malformed", f"root element is {root.tag}")
    ids: Dict[str, int] = {}
    for element in root.iter():
        for name in ("ID", "AssertionID"):
            value = element.get(name)
            if value is not None:
                ids[value] = ids.get(value, 0) + 1
    if any(count > 1 for count in ids.values()):
        raise SAMLResponseRefused("wrapped", "an ID appears more than once")
    wanted = (_ASSERTION,) if decrypted else (_ASSERTION, _ENCRYPTED_ASSERTION)
    assertions = [e for e in root.iter() if e.tag in wanted]
    if len(assertions) != 1:
        raise SAMLResponseRefused("wrapped", f"{len(assertions)} assertions")
    assertion = assertions[0]
    containers = [root]
    if decrypted:
        containers = [e for e in root.findall(_ENCRYPTED_ASSERTION)]
        if len(containers) != 1:
            raise SAMLResponseRefused(
                "wrapped", "the decrypted assertion is not where it was encrypted"
            )
    if assertion not in list(containers[0]):
        raise SAMLResponseRefused(
            "wrapped", "the assertion is not where the profile puts it"
        )
    signed_parents = [root] + ([assertion] if assertion.tag == _ASSERTION else [])
    expected_signatures = [
        s for parent in signed_parents for s in parent.findall(_SIGNATURE)
    ]
    every_signature = list(root.iter(_SIGNATURE))
    if len(every_signature) != len(expected_signatures):
        raise SAMLResponseRefused(
            "wrapped", "a signature outside the Response or the Assertion"
        )
    for parent in signed_parents:
        signatures = parent.findall(_SIGNATURE)
        if len(signatures) > 1:
            raise SAMLResponseRefused(
                "wrapped", "more than one signature on an element"
            )
        for signature in signatures:
            references = list(signature.iter(_REFERENCE))
            if (
                len(references) != 1
                or references[0].get("URI") != f"#{parent.get('ID')}"
            ):
                raise SAMLResponseRefused("wrapped", "a signature over something else")
    return assertion


def check_solicitation(
    root: Element, expected_request_id: Optional[str], allow_unsolicited: bool
) -> Optional[str]:
    """The request this response answers: the outstanding one, or None for
    an unsolicited response the IdP may send."""
    in_response_to = root.get("InResponseTo")
    if in_response_to is None:
        if not allow_unsolicited:
            raise SAMLResponseRefused(
                "unsolicited", "no InResponseTo and unsolicited responses are off"
            )
        return None
    if expected_request_id is None or in_response_to != expected_request_id:
        raise SAMLResponseRefused(
            "in_response_to",
            f"InResponseTo {in_response_to!r} is not this browser's request",
        )
    return in_response_to


def _issuer(element: Element) -> Optional[str]:
    issuer = element.find(_ISSUER)
    return (issuer.text or "").strip() if issuer is not None else None


def check_response(root: Element, acs_url: str, idp_entity_id: str) -> None:
    if root.get("Version") != "2.0":
        raise SAMLResponseRefused("malformed", f"Version {root.get('Version')!r}")
    if root.get("Destination") != acs_url:
        raise SAMLResponseRefused(
            "destination", f"Destination {root.get('Destination')!r}"
        )
    issuer = _issuer(root)
    if issuer is not None and issuer != idp_entity_id:
        raise SAMLResponseRefused("issuer", f"Response Issuer {issuer!r}")
    status = root.find(f"{{{NS_PROTOCOL}}}Status/{_STATUS_CODE}")
    if status is None or status.get("Value") != STATUS_SUCCESS:
        raise SAMLResponseRefused(
            "status", f"status {status.get('Value') if status is not None else None!r}"
        )


def check_assertion(
    assertion: Element,
    sp: ServiceProvider,
    idp_entity_id: str,
    in_response_to: Optional[str],
) -> None:
    """The Web SSO profile's rules for the assertion itself."""
    now = datetime.now(timezone.utc)
    skew = timedelta(seconds=CLOCK_SKEW_SECONDS)
    if _issuer(assertion) != idp_entity_id:
        raise SAMLResponseRefused("issuer", f"Assertion Issuer {_issuer(assertion)!r}")
    if assertion.find(_AUTHN_STATEMENT) is None:
        raise SAMLResponseRefused("malformed", "no AuthnStatement")
    conditions = assertion.find(_CONDITIONS)
    if conditions is None:
        raise SAMLResponseRefused("audience", "no Conditions")
    _check_window(conditions, now, skew)
    restrictions = conditions.findall(_AUDIENCE_RESTRICTION)
    if not restrictions or not all(
        sp.entity_id in [(a.text or "").strip() for a in restriction.findall(_AUDIENCE)]
        for restriction in restrictions
    ):
        raise SAMLResponseRefused(
            "audience", f"this SP ({sp.entity_id}) is not the audience"
        )
    bearer = [
        confirmation.find(_SUBJECT_CONFIRMATION_DATA)
        for confirmation in assertion.iter(_SUBJECT_CONFIRMATION)
        if confirmation.get("Method") == METHOD_BEARER
    ]
    data = [d for d in bearer if d is not None]
    if not data:
        raise SAMLResponseRefused("malformed", "no bearer SubjectConfirmationData")
    for item in data:
        if item.get("Recipient") != sp.acs_url:
            raise SAMLResponseRefused(
                "recipient", f"Recipient {item.get('Recipient')!r}"
            )
        if item.get("InResponseTo") != in_response_to:
            raise SAMLResponseRefused(
                "in_response_to",
                f"SubjectConfirmation InResponseTo {item.get('InResponseTo')!r}",
            )
        if item.get("NotOnOrAfter") is None:
            raise SAMLResponseRefused(
                "expired", "bearer confirmation has no NotOnOrAfter"
            )
        _check_window(item, now, skew)


def _check_window(element: Element, now: datetime, skew: timedelta) -> None:
    not_before = parse_saml_time(element.get("NotBefore"))
    not_on_or_after = parse_saml_time(element.get("NotOnOrAfter"))
    if not_before is not None and now + skew < not_before:
        raise SAMLResponseRefused(
            "not_yet_valid", f"NotBefore {not_before.isoformat()}"
        )
    if not_on_or_after is not None and now - skew >= not_on_or_after:
        raise SAMLResponseRefused(
            "expired", f"NotOnOrAfter {not_on_or_after.isoformat()}"
        )


def asserted_identity(
    verified: Any, assertion: Element, issuer: str, in_response_to: Optional[str]
) -> AssertedIdentity:
    """The subject and attributes of the verified assertion (pysaml2's
    parsed copy, whose signature it checked), with the latest
    NotOnOrAfter that bounds it, to remember its ID until then."""
    parsed = verified.assertion
    name_id = parsed.subject.name_id if parsed.subject is not None else None
    if name_id is None or not (name_id.text or "").strip():
        raise SAMLResponseRefused("no_subject", "the assertion names no subject")
    attributes: Dict[str, Tuple[str, ...]] = {}
    for statement in parsed.attribute_statement:
        for attribute in statement.attribute:
            values = tuple(
                (value.text or "").strip()
                for value in attribute.attribute_value
                if value.text
            )
            for key in (attribute.name, attribute.friendly_name):
                if key:
                    attributes[key] = attributes.get(key, ()) + values
    bounds = [
        parse_saml_time(element.get("NotOnOrAfter"))
        for element in [
            *assertion.iter(_SUBJECT_CONFIRMATION_DATA),
            *assertion.iter(_CONDITIONS),
        ]
    ]
    known = [bound for bound in bounds if bound is not None]
    authn = parsed.authn_statement[0] if parsed.authn_statement else None
    return AssertedIdentity(
        issuer=issuer,
        name_id=name_id.text.strip(),
        name_id_format=name_id.format,
        assertion_id=str(parsed.id),
        not_on_or_after=max(known) if known else datetime.now(timezone.utc),
        in_response_to=in_response_to,
        session_index=authn.session_index if authn is not None else None,
        attributes=attributes,
    )
