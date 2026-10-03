# SPDX-License-Identifier: AGPL-3.0-or-later
"""The SAML 2.0 protocol work of the Identity Provider, over pysaml2 (with
xmlsec1 for XML signatures and encryption).

Nothing here touches the database: callers hand in the IdP's site, its
signing identity and the one Service Provider a message concerns, so each
pysaml2 ``Server`` is built for one exchange and knows of no other SP.

A message from an SP is bounded in size before it is inflated or parsed,
and parsed with defusedxml (pysaml2 does). An HTTP-Redirect signature is
checked here over the query string exactly as it arrived (SAML bindings
3.4.4.1), not over a re-encoding of it; an HTTP-POST message's enveloped
signature is checked by pysaml2 against the SP's registered certificate
only, never against a key the message carries.
"""

from __future__ import annotations

import base64
import binascii
import calendar
import hashlib
import html
import shutil
import tempfile
import zlib
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Optional, Sequence, Tuple
from urllib.parse import unquote_plus, urlsplit

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from saml2 import BINDING_HTTP_POST, BINDING_HTTP_REDIRECT, md, saml, samlp
from saml2.authn_context import UNSPECIFIED
from saml2.config import IdPConfig
from saml2.metadata import create_metadata_string, do_key_descriptor
from saml2.server import Server
from saml2.time_util import str_to_time
from saml2.xmldsig import DIGEST_SHA256, SIG_RSA_SHA256, SIG_RSA_SHA384, SIG_RSA_SHA512

# A message larger than this, encoded or inflated, is refused unread.
MAX_ENCODED_MESSAGE_BYTES = 100_000
MAX_INFLATED_MESSAGE_BYTES = 200_000
# An AuthnRequest is answered only while it is fresh.
REQUEST_MAX_AGE = timedelta(minutes=5)
CLOCK_SKEW = timedelta(minutes=1)
# Redirect-binding signature algorithms accepted (SHA-1 is not).
REDIRECT_SIGNATURE_HASHES: Dict[str, Callable[[], hashes.HashAlgorithm]] = {
    SIG_RSA_SHA256: hashes.SHA256,
    SIG_RSA_SHA384: hashes.SHA384,
    SIG_RSA_SHA512: hashes.SHA512,
}
# The redirect binding's signed parameters, in the order they are signed.
SIGNED_REQUEST_PARAMETERS = ("SAMLRequest", "RelayState", "SigAlg")

NAME_ID_FORMATS: Dict[str, str] = {
    "persistent": saml.NAMEID_FORMAT_PERSISTENT,
    "email": saml.NAMEID_FORMAT_EMAILADDRESS,
}
# User fields an SP may be configured to receive, and the SAML attribute
# (by its friendly name; sent with the URI name format) each is released as.
RELEASABLE_ATTRIBUTES: Dict[str, str] = {
    "email": "mail",
    "first_name": "givenName",
    "last_name": "sn",
    "display_name": "displayName",
    "username": "uid",
}

_PEM_CERTIFICATE = "CERTIFICATE"


class RefusedMessage(ValueError):
    """A message from an SP that is not answered. The text is safe to show."""


class SignatureRefused(RefusedMessage):
    """A signature that is missing where required, or does not verify."""


def normalized_pem(value: str) -> str:
    """PEM text with escaped newlines (as an environment value may carry
    them) restored and surrounding space removed."""
    text = value.strip()
    if "\n" not in text and "\\n" in text:
        text = text.replace("\\n", "\n").strip()
    return text + "\n"


def load_certificate(pem: str) -> x509.Certificate:
    """The X.509 certificate in ``pem``; ValueError when it is not one."""
    try:
        return x509.load_pem_x509_certificate(normalized_pem(pem).encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise ValueError("not a PEM X.509 certificate") from exc


def certificate_body(pem: str) -> str:
    """The base64 DER of a PEM certificate, as metadata carries it."""
    der = load_certificate(pem).public_bytes(serialization.Encoding.DER)
    return base64.b64encode(der).decode("ascii")


def rsa_public_key(pem: str) -> rsa.RSAPublicKey:
    key = load_certificate(pem).public_key()
    if not isinstance(key, rsa.RSAPublicKey):
        raise ValueError("the certificate's key is not RSA")
    return key


@dataclass(frozen=True)
class SigningIdentity:
    """The IdP's RSA private key and the certificate it publishes for it."""

    certificate_pem: str
    private_key_pem: str

    @classmethod
    def checked(cls, certificate_pem: str, private_key_pem: str) -> "SigningIdentity":
        """The pair, refused (ValueError) unless the key is RSA and is the
        certificate's."""
        certificate = rsa_public_key(certificate_pem)
        try:
            key = serialization.load_pem_private_key(
                normalized_pem(private_key_pem).encode("ascii"), password=None
            )
        except (ValueError, TypeError, UnicodeEncodeError) as exc:
            raise ValueError(
                "the signing key is not an unencrypted PEM private key"
            ) from exc
        if not isinstance(key, rsa.RSAPrivateKey):
            raise ValueError("the signing key is not RSA")
        if key.public_key().public_numbers() != certificate.public_numbers():
            raise ValueError(
                "the signing key does not belong to the signing certificate"
            )
        return cls(normalized_pem(certificate_pem), normalized_pem(private_key_pem))


@dataclass(frozen=True)
class IdPSite:
    """Where the IdP is: its entity id and SSO endpoint (both bindings),
    and how long an assertion it issues stays valid."""

    entity_id: str
    sso_url: str
    assertion_lifetime: timedelta


@dataclass(frozen=True)
class ServiceProvider:
    """A registered SP, as the protocol needs it."""

    entity_id: str
    acs_urls: Tuple[str, ...]
    certificate_pem: Optional[str]
    encrypt_assertions: bool
    name_id_format: str

    def metadata_xml(self) -> str:
        """This SP's SAML metadata, built from its registration."""
        descriptor = md.SPSSODescriptor(
            protocol_support_enumeration=samlp.NAMESPACE,
            want_assertions_signed="true",
        )
        descriptor.assertion_consumer_service = [
            md.AssertionConsumerService(
                location=url, binding=BINDING_HTTP_POST, index=str(index)
            )
            for index, url in enumerate(self.acs_urls)
        ]
        descriptor.name_id_format = [md.NameIDFormat(text=self.name_id_format)]
        if self.certificate_pem:
            body = certificate_body(self.certificate_pem)
            descriptor.key_descriptor = do_key_descriptor(
                cert=[body],
                enc_cert=[body] if self.encrypt_assertions else None,
                use="both" if self.encrypt_assertions else "signing",
            )
        entity = md.EntityDescriptor(entity_id=self.entity_id)
        entity.spsso_descriptor = [descriptor]
        xml: bytes = entity.to_string()
        return xml.decode("utf-8")


def _decoded(encoded: str) -> bytes:
    if len(encoded) > MAX_ENCODED_MESSAGE_BYTES:
        raise RefusedMessage("the SAML message is too large")
    try:
        return base64.b64decode(encoded, validate=False)
    except (binascii.Error, ValueError) as exc:
        raise RefusedMessage("the SAML message is not base64") from exc


def _inflated(raw: bytes) -> bytes:
    inflater = zlib.decompressobj(-zlib.MAX_WBITS)
    try:
        xml = inflater.decompress(raw, MAX_INFLATED_MESSAGE_BYTES)
    except zlib.error as exc:
        raise RefusedMessage("the SAML message is not DEFLATE-compressed") from exc
    if inflater.unconsumed_tail:
        raise RefusedMessage("the SAML message is too large")
    return xml


def message_xml(encoded: str, binding: str) -> str:
    """A received AuthnRequest's XML: inflated for HTTP-Redirect; for
    HTTP-POST plain base64 (or, as pysaml2 also accepts, deflated)."""
    raw = _decoded(encoded)
    if binding == BINDING_HTTP_REDIRECT:
        xml = _inflated(raw)
    else:
        try:
            xml = _inflated(raw)
        except RefusedMessage:
            if len(raw) > MAX_INFLATED_MESSAGE_BYTES:
                raise RefusedMessage("the SAML message is too large") from None
            xml = raw
    try:
        return xml.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RefusedMessage("the SAML message is not UTF-8") from exc


def request_issuer(xml: str) -> str:
    """The entity id an AuthnRequest says it is from (unverified)."""
    try:
        request = samlp.authn_request_from_string(xml)
    except Exception as exc:  # pysaml2 raises bare parse errors of several kinds
        raise RefusedMessage("not a SAML AuthnRequest") from exc
    if (
        request is None
        or request.issuer is None
        or not (request.issuer.text or "").strip()
    ):
        raise RefusedMessage("the AuthnRequest names no issuer")
    issuer: str = request.issuer.text.strip()
    return issuer


def _query_pairs(raw_query: str) -> Dict[str, str]:
    """The raw (still percent-encoded) value of each parameter; a repeated
    signed parameter is refused, since which copy was signed is unknown."""
    pairs: Dict[str, str] = {}
    for part in raw_query.split("&"):
        name, _, value = part.partition("=")
        if name in pairs and name in (*SIGNED_REQUEST_PARAMETERS, "Signature"):
            raise SignatureRefused(f"the query repeats {name}")
        pairs.setdefault(name, value)
    return pairs


def verify_redirect_signature(raw_query: str, certificate_pem: str) -> None:
    """Check an HTTP-Redirect AuthnRequest's signature against the SP's
    certificate, over the signed parameters exactly as they arrived."""
    pairs = _query_pairs(raw_query)
    if "Signature" not in pairs or "SigAlg" not in pairs:
        raise SignatureRefused("the AuthnRequest is not signed")
    algorithm = unquote_plus(pairs["SigAlg"])
    digest = REDIRECT_SIGNATURE_HASHES.get(algorithm)
    if digest is None:
        raise SignatureRefused(f"signature algorithm {algorithm!r} is not accepted")
    signed = "&".join(
        f"{name}={pairs[name]}" for name in SIGNED_REQUEST_PARAMETERS if name in pairs
    )
    try:
        signature = base64.b64decode(unquote_plus(pairs["Signature"]), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SignatureRefused("the signature is not base64") from exc
    try:
        rsa_public_key(certificate_pem).verify(
            signature, signed.encode("ascii"), padding.PKCS1v15(), digest()
        )
    except (InvalidSignature, UnicodeEncodeError) as exc:
        raise SignatureRefused("the AuthnRequest's signature does not verify") from exc


def check_issue_instant(request: samlp.AuthnRequest, now: datetime) -> None:
    """Refuse an AuthnRequest issued too long ago, or in the future."""
    try:
        issued = datetime.fromtimestamp(
            calendar.timegm(str_to_time(request.issue_instant)), tz=timezone.utc
        )
    except (TypeError, ValueError, AttributeError) as exc:
        raise RefusedMessage("the AuthnRequest has no valid IssueInstant") from exc
    if issued > now + CLOCK_SKEW or issued < now - REQUEST_MAX_AGE - CLOCK_SKEW:
        raise RefusedMessage("the AuthnRequest is stale or from the future")


@contextmanager
def idp_server(
    site: IdPSite,
    identity: SigningIdentity,
    sp: Optional[ServiceProvider] = None,
    *,
    want_signed_post_requests: bool = False,
) -> Iterator[Server]:
    """A pysaml2 IdP for one exchange with ``sp`` (or none, for metadata).

    xmlsec1 reads the signing key from a file, so it is written into a
    private temporary directory for the exchange and removed after it.
    """
    xmlsec = shutil.which("xmlsec1")
    if xmlsec is None:
        raise RuntimeError("xmlsec1 is not installed")
    with tempfile.TemporaryDirectory(prefix="saml_idp_") as directory:
        key_file = Path(directory) / "signing.key"
        cert_file = Path(directory) / "signing.crt"
        key_file.touch(mode=0o600)
        key_file.write_text(identity.private_key_pem)
        cert_file.write_text(identity.certificate_pem)
        config = IdPConfig()
        config.load(
            {
                "entityid": site.entity_id,
                "xmlsec_binary": xmlsec,
                "key_file": str(key_file),
                "cert_file": str(cert_file),
                "metadata": {"inline": [sp.metadata_xml()] if sp else []},
                "only_use_keys_in_metadata": True,
                "delete_tmpfiles": True,
                "service": {
                    "idp": {
                        "endpoints": {
                            "single_sign_on_service": [
                                (site.sso_url, BINDING_HTTP_REDIRECT),
                                (site.sso_url, BINDING_HTTP_POST),
                            ]
                        },
                        "name_id_format": list(NAME_ID_FORMATS.values()),
                        "want_authn_requests_signed": want_signed_post_requests,
                        "policy": {
                            "default": {
                                "lifetime": {
                                    "seconds": int(
                                        site.assertion_lifetime.total_seconds()
                                    )
                                },
                                "name_form": saml.NAME_FORMAT_URI,
                                "fail_on_missing_requested": False,
                            }
                        },
                        "signing_algorithm": SIG_RSA_SHA256,
                        "digest_algorithm": DIGEST_SHA256,
                    }
                },
            }
        )
        server = Server(config=config)
        try:
            yield server
        finally:
            server.close()


def metadata_xml(site: IdPSite, identity: SigningIdentity) -> str:
    """The IdP's metadata: entity id, signing certificate, SSO endpoints
    for both bindings and the NameID formats it issues."""
    with idp_server(site, identity) as server:
        xml: bytes = create_metadata_string(None, config=server.config)
    return xml.decode("utf-8")


def parse_authn_request(
    server: Server, encoded: str, binding: str
) -> samlp.AuthnRequest:
    """The AuthnRequest, schema-valid and addressed to this IdP (when it
    names a Destination), with an enveloped signature verified against the
    SP's certificate when it carries one (and required when the server was
    built to want one)."""
    from saml2.response import IncorrectlySigned
    from saml2.sigver import MissingKey, SignatureError

    try:
        parsed = server.parse_authn_request(encoded, binding)
    except (IncorrectlySigned, SignatureError, MissingKey) as exc:
        raise SignatureRefused(
            "the AuthnRequest's signature is missing or does not verify"
        ) from exc
    except (
        Exception
    ) as exc:  # pysaml2 refuses malformed requests with many exception types
        raise RefusedMessage("the AuthnRequest is not valid for this IdP") from exc
    if parsed is None or parsed.message is None:
        raise RefusedMessage("the AuthnRequest is not valid for this IdP")
    message: samlp.AuthnRequest = parsed.message
    return message


def name_id(sp: ServiceProvider, site: IdPSite, value: str) -> saml.NameID:
    return saml.NameID(
        format=sp.name_id_format,
        text=value,
        name_qualifier=site.entity_id,
        sp_name_qualifier=sp.entity_id,
    )


def authn_response(
    server: Server,
    sp: ServiceProvider,
    *,
    in_response_to: Optional[str],
    acs_url: str,
    subject: saml.NameID,
    attributes: Dict[str, List[str]],
    authn_instant: datetime,
) -> str:
    """A signed Response carrying a signed (and, for an SP that asks,
    encrypted) Assertion: audience the SP, recipient and destination the
    ACS URL, InResponseTo the request answered."""
    response = server.create_authn_response(
        attributes,
        in_response_to,
        acs_url,
        sp.entity_id,
        name_id=subject,
        authn={
            "class_ref": UNSPECIFIED,
            "authn_instant": int(authn_instant.timestamp()),
        },
        sign_response=True,
        sign_assertion=True,
        encrypt_assertion=sp.encrypt_assertions,
        encrypt_cert_assertion=sp.certificate_pem if sp.encrypt_assertions else None,
        sign_alg=SIG_RSA_SHA256,
        digest_alg=DIGEST_SHA256,
    )
    return str(response)


def error_response(
    server: Server,
    *,
    in_response_to: Optional[str],
    acs_url: str,
    status: str,
    message: str,
) -> str:
    """A signed Response with a second-level error ``status``."""
    response = server.create_error_response(
        in_response_to,
        acs_url,
        (status, message),
        sign=True,
        sign_alg=SIG_RSA_SHA256,
        digest_alg=DIGEST_SHA256,
    )
    return str(response)


# The page that carries a Response to the SP: a form that submits itself,
# with a button for a browser that runs no script.
_AUTO_SUBMIT = "document.forms[0].submit();"


@dataclass(frozen=True)
class PostPage:
    html: str
    content_security_policy: str


def post_page(acs_url: str, response_xml: str, relay_state: Optional[str]) -> PostPage:
    """The HTTP-POST binding page delivering ``response_xml`` to the ACS.
    Every value is escaped; the Content-Security-Policy lets the page run
    only its own submit script and post only to the ACS's origin."""
    encoded = base64.b64encode(response_xml.encode("utf-8")).decode("ascii")
    fields: Sequence[Tuple[str, str]] = [("SAMLResponse", encoded)]
    if relay_state is not None:
        fields = [*fields, ("RelayState", relay_state)]
    inputs = "".join(
        f'<input type="hidden" name="{name}" value="{html.escape(value, quote=True)}">'
        for name, value in fields
    )
    page = (
        '<!doctype html><html><head><meta charset="utf-8"><title>Signing in</title></head>'
        f'<body><form method="post" action="{html.escape(acs_url, quote=True)}">{inputs}'
        '<noscript><button type="submit">Continue</button></noscript></form>'
        f"<script>{_AUTO_SUBMIT}</script></body></html>"
    )
    script_hash = base64.b64encode(
        hashlib.sha256(_AUTO_SUBMIT.encode()).digest()
    ).decode()
    target = urlsplit(acs_url)
    policy = (
        "default-src 'none'; "
        f"script-src 'sha256-{script_hash}'; "
        f"form-action {target.scheme}://{target.netloc}; "
        "base-uri 'none'; frame-ancestors 'none'"
    )
    return PostPage(page, policy)
