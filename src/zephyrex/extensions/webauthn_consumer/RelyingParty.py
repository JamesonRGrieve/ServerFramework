# SPDX-License-Identifier: AGPL-3.0-or-later
"""This server as a WebAuthn Relying Party: its policy, the ceremony options
it hands browsers, and the checks run on what authenticators send back.

py_webauthn performs the specification's verification steps (client data
type and challenge, exact origin, rpId hash, user presence and
verification, the attestation statement of every supported format, the
assertion signature). This module adds what the library leaves to the RP:

- the allowed origins are an exact list, and an embedded (cross-origin)
  ceremony is refused;
- attestation policy: the formats accepted, and, when trust anchors are
  configured, a chain to one of them is required, so ``none`` and self
  attestation are refused;
- the signature counter: a counter that fails to advance on an assertion
  whose signature verified is evidence of a cloned authenticator, reported
  as :class:`CounterRegression` for the caller to flag the credential.

Configuration is read from the environment on each ceremony (see
``SETTINGS``); a server without an rpId and origins answers 503.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Mapping, Optional, Sequence, Tuple

from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding
from fastapi import HTTPException, status
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import (
    bytes_to_base64url,
    options_to_json_dict,
    parse_attestation_object,
    parse_authentication_credential_json,
    parse_client_data_json,
    parse_registration_credential_json,
)
from webauthn.helpers.cose import COSEAlgorithmIdentifier
from webauthn.helpers.exceptions import WebAuthnException
from webauthn.helpers.structs import (
    AttestationConveyancePreference,
    AttestationFormat,
    AuthenticationCredential,
    AuthenticatorAttachment,
    AuthenticatorSelectionCriteria,
    AuthenticatorTransport,
    CredentialDeviceType,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from zephyrex.lib.Environment import env
from zephyrex.lib.Logging import logger

# Environment settings and their defaults (EXT_WebAuthnConsumer._env).
SETTINGS: Mapping[str, str] = {
    # The registrable domain credentials are scoped to, e.g. "example.com".
    "WEBAUTHN_CONSUMER_RP_ID": "",
    # Shown by the browser while creating a credential; APP_NAME when empty.
    "WEBAUTHN_CONSUMER_RP_NAME": "",
    # Exact origins ceremonies may run on, comma or space separated.
    "WEBAUTHN_CONSUMER_ORIGINS": "",
    # Attestation conveyance asked of authenticators: none, indirect,
    # direct or enterprise.
    "WEBAUTHN_CONSUMER_ATTESTATION": "none",
    # Attestation statement formats accepted at registration.
    "WEBAUTHN_CONSUMER_ATTESTATION_FORMATS": "none packed fido-u2f tpm apple",
    # A PEM bundle of attestation trust anchors. When set, a registration
    # must chain to one of them: "none" and self attestation are refused.
    "WEBAUTHN_CONSUMER_ATTESTATION_ROOTS": "",
    # User verification asked for at registration and as a second factor:
    # required, preferred or discouraged. Passkey sign-in always requires it.
    "WEBAUTHN_CONSUMER_USER_VERIFICATION": "preferred",
    # Whether registration asks for a discoverable (passkey) credential:
    # required, preferred or discouraged.
    "WEBAUTHN_CONSUMER_RESIDENT_KEY": "preferred",
    # How long a ceremony's challenge stays redeemable.
    "WEBAUTHN_CONSUMER_TIMEOUT_MS": "60000",
}

# Algorithms accepted for credential keys, in the order offered.
SUPPORTED_ALGORITHMS: Tuple[COSEAlgorithmIdentifier, ...] = (
    COSEAlgorithmIdentifier.EDDSA,
    COSEAlgorithmIdentifier.ECDSA_SHA_256,
    COSEAlgorithmIdentifier.RSASSA_PKCS1_v1_5_SHA_256,
)
CHALLENGE_BYTES = 32
_MILLISECONDS_PER_SECOND = 1000


class AttestationTrust:
    """How far a credential's attestation was verified."""

    NONE = "none"  # the authenticator conveyed none
    SELF = "self"  # signed by the credential key itself
    UNVERIFIED = "unverified"  # a certificate chain, with no anchors to check
    TRUSTED = "trusted"  # chains to a configured trust anchor


def _setting(key: str) -> str:
    return env(key, SETTINGS[key]).strip()


def _words(value: str) -> List[str]:
    return [word for word in value.replace(",", " ").split() if word]


def _not_configured(problem: str) -> HTTPException:
    logger.error("webauthn_consumer is misconfigured: %s", problem)
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="WebAuthn sign-in is not configured on this server",
    )


def config_issues() -> List[str]:
    """What stops the Relying Party from running, in words; empty when it
    can run."""
    issues: List[str] = []
    if not _setting("WEBAUTHN_CONSUMER_RP_ID"):
        issues.append("WEBAUTHN_CONSUMER_RP_ID is unset; the relying party ID")
    if not _words(_setting("WEBAUTHN_CONSUMER_ORIGINS")):
        issues.append(
            "WEBAUTHN_CONSUMER_ORIGINS is unset; the exact origins ceremonies run on"
        )
    for key, enum in (
        ("WEBAUTHN_CONSUMER_ATTESTATION", AttestationConveyancePreference),
        ("WEBAUTHN_CONSUMER_USER_VERIFICATION", UserVerificationRequirement),
        ("WEBAUTHN_CONSUMER_RESIDENT_KEY", ResidentKeyRequirement),
    ):
        if _setting(key) not in {member.value for member in enum}:
            issues.append(f"{key} is not one of {[m.value for m in enum]}")
    known_formats = {member.value for member in AttestationFormat}
    for word in _words(_setting("WEBAUTHN_CONSUMER_ATTESTATION_FORMATS")):
        if word not in known_formats:
            issues.append(
                f"WEBAUTHN_CONSUMER_ATTESTATION_FORMATS names unknown format {word}"
            )
    roots = _setting("WEBAUTHN_CONSUMER_ATTESTATION_ROOTS")
    if roots:
        try:
            _trust_anchors(roots)
        except (OSError, ValueError) as err:
            issues.append(f"WEBAUTHN_CONSUMER_ATTESTATION_ROOTS is unreadable: {err}")
    if not _setting("WEBAUTHN_CONSUMER_TIMEOUT_MS").isdigit():
        issues.append("WEBAUTHN_CONSUMER_TIMEOUT_MS is not a number of milliseconds")
    return issues


def _trust_anchors(path: str) -> Tuple[bytes, ...]:
    certificates = x509.load_pem_x509_certificates(Path(path).read_bytes())
    return tuple(cert.public_bytes(Encoding.PEM) for cert in certificates)


def _refused(ceremony: str, err: Exception) -> HTTPException:
    """A response the authenticator or browser sent that does not verify.
    The library's reason names the failed check, never a secret."""
    code = (
        status.HTTP_400_BAD_REQUEST
        if ceremony == "registration"
        else status.HTTP_401_UNAUTHORIZED
    )
    return HTTPException(status_code=code, detail=f"WebAuthn {ceremony} refused: {err}")


@dataclass(frozen=True)
class Descriptor:
    """A credential named in options: its id, and how it can be reached."""

    credential_id: bytes
    transports: Tuple[str, ...] = ()

    def to_webauthn(self) -> PublicKeyCredentialDescriptor:
        known = {member.value for member in AuthenticatorTransport}
        return PublicKeyCredentialDescriptor(
            id=self.credential_id,
            transports=[
                AuthenticatorTransport(t) for t in self.transports if t in known
            ]
            or None,
        )


@dataclass(frozen=True)
class RegisteredCredential:
    """A credential whose registration verified, ready to store."""

    credential_id: str
    public_key: str
    sign_count: int
    aaguid: str
    attestation_format: str
    attestation_trust: str
    transports: Optional[str]
    is_discoverable: bool
    backup_eligible: bool
    backed_up: bool


@dataclass(frozen=True)
class Assertion:
    """An authentication response, parsed but not yet verified."""

    credential_id: str
    user_handle: Optional[bytes]
    parsed: AuthenticationCredential


@dataclass(frozen=True)
class VerifiedAssertion:
    new_sign_count: int
    backed_up: bool
    user_verified: bool


class CounterRegression(Exception):
    """A verified assertion whose signature counter did not advance: the
    credential's key exists in two places."""

    def __init__(self, stored: int, received: int) -> None:
        super().__init__(f"signature counter went from {stored} to {received}")
        self.stored = stored
        self.received = received


@dataclass(frozen=True)
class RelyingPartyPolicy:
    rp_id: str
    rp_name: str
    origins: Tuple[str, ...]
    attestation: AttestationConveyancePreference
    attestation_formats: FrozenSet[AttestationFormat]
    trust_anchors: Tuple[bytes, ...]
    user_verification: UserVerificationRequirement
    resident_key: ResidentKeyRequirement
    timeout_ms: int

    @classmethod
    def from_env(cls) -> "RelyingPartyPolicy":
        """The policy configured now; 503 when it cannot run."""
        rp_id = _setting("WEBAUTHN_CONSUMER_RP_ID")
        origins = tuple(_words(_setting("WEBAUTHN_CONSUMER_ORIGINS")))
        if not rp_id or not origins:
            raise _not_configured("the rpId and the allowed origins are required")
        try:
            roots = _setting("WEBAUTHN_CONSUMER_ATTESTATION_ROOTS")
            return cls(
                rp_id=rp_id,
                rp_name=_setting("WEBAUTHN_CONSUMER_RP_NAME")
                or env("APP_NAME")
                or rp_id,
                origins=origins,
                attestation=AttestationConveyancePreference(
                    _setting("WEBAUTHN_CONSUMER_ATTESTATION")
                ),
                attestation_formats=frozenset(
                    AttestationFormat(word)
                    for word in _words(
                        _setting("WEBAUTHN_CONSUMER_ATTESTATION_FORMATS")
                    )
                ),
                trust_anchors=_trust_anchors(roots) if roots else (),
                user_verification=UserVerificationRequirement(
                    _setting("WEBAUTHN_CONSUMER_USER_VERIFICATION")
                ),
                resident_key=ResidentKeyRequirement(
                    _setting("WEBAUTHN_CONSUMER_RESIDENT_KEY")
                ),
                timeout_ms=int(_setting("WEBAUTHN_CONSUMER_TIMEOUT_MS")),
            )
        except (OSError, ValueError) as err:
            raise _not_configured(str(err)) from err

    @property
    def timeout_seconds(self) -> int:
        return max(1, self.timeout_ms // _MILLISECONDS_PER_SECOND)

    # -- Options ------------------------------------------------------------

    def registration_options(
        self,
        *,
        challenge: bytes,
        user_handle: bytes,
        user_name: str,
        display_name: str,
        exclude: Sequence[Descriptor],
        attachment: Optional[AuthenticatorAttachment],
    ) -> Dict[str, Any]:
        options = generate_registration_options(
            rp_id=self.rp_id,
            rp_name=self.rp_name,
            user_id=user_handle,
            user_name=user_name,
            user_display_name=display_name,
            challenge=challenge,
            timeout=self.timeout_ms,
            attestation=self.attestation,
            authenticator_selection=AuthenticatorSelectionCriteria(
                authenticator_attachment=attachment,
                resident_key=self.resident_key,
                require_resident_key=self.resident_key
                == ResidentKeyRequirement.REQUIRED,
                user_verification=self.user_verification,
            ),
            exclude_credentials=[d.to_webauthn() for d in exclude],
            supported_pub_key_algs=list(SUPPORTED_ALGORITHMS),
        )
        public_key = options_to_json_dict(options)
        # Ask the browser whether it made a discoverable credential.
        public_key["extensions"] = {"credProps": True}
        return public_key

    def authentication_options(
        self,
        *,
        challenge: bytes,
        allow: Sequence[Descriptor],
        user_verification: UserVerificationRequirement,
    ) -> Dict[str, Any]:
        options = generate_authentication_options(
            rp_id=self.rp_id,
            challenge=challenge,
            timeout=self.timeout_ms,
            allow_credentials=[d.to_webauthn() for d in allow],
            user_verification=user_verification,
        )
        return options_to_json_dict(options)

    # -- Verification -------------------------------------------------------

    @staticmethod
    def _refuse_cross_origin(client_data_json: bytes, ceremony: str) -> None:
        if parse_client_data_json(client_data_json).cross_origin:
            raise _refused(
                ceremony, ValueError("the ceremony ran in a cross-origin frame")
            )

    def verify_registration(
        self, credential: Dict[str, Any], challenge: bytes
    ) -> RegisteredCredential:
        """The credential the response registers; 400 when it fails any
        check or the attestation policy."""
        try:
            parsed = parse_registration_credential_json(credential)
            self._refuse_cross_origin(parsed.response.client_data_json, "registration")
            verified = verify_registration_response(
                credential=parsed,
                expected_challenge=challenge,
                expected_rp_id=self.rp_id,
                expected_origin=list(self.origins),
                require_user_presence=True,
                require_user_verification=self.user_verification
                == UserVerificationRequirement.REQUIRED,
                supported_pub_key_algs=list(SUPPORTED_ALGORITHMS),
                pem_root_certs_bytes_by_fmt=(
                    {each: list(self.trust_anchors) for each in AttestationFormat}
                    if self.trust_anchors
                    else None
                ),
            )
            statement = parse_attestation_object(verified.attestation_object).att_stmt
        except WebAuthnException as err:
            raise _refused("registration", err) from err
        # The library reports the format as the attestation object spells it;
        # it verified only formats it knows, so the spelling is a member.
        fmt = AttestationFormat(verified.fmt)
        if fmt not in self.attestation_formats:
            raise _refused(
                "registration",
                ValueError(f'attestation format "{fmt.value}" is not accepted'),
            )
        trust = self._attestation_trust(fmt, bool(statement.x5c))
        if self.trust_anchors and trust != AttestationTrust.TRUSTED:
            raise _refused(
                "registration",
                ValueError(f"a trusted attestation is required, got {trust}"),
            )
        transports = [t.value for t in parsed.response.transports or []]
        return RegisteredCredential(
            credential_id=bytes_to_base64url(verified.credential_id),
            public_key=bytes_to_base64url(verified.credential_public_key),
            sign_count=verified.sign_count,
            aaguid=verified.aaguid,
            attestation_format=fmt.value,
            attestation_trust=trust,
            transports=" ".join(transports) or None,
            is_discoverable=_reports_discoverable(credential),
            backup_eligible=verified.credential_device_type
            == CredentialDeviceType.MULTI_DEVICE,
            backed_up=verified.credential_backed_up,
        )

    def _attestation_trust(self, fmt: AttestationFormat, has_chain: bool) -> str:
        if fmt == AttestationFormat.NONE:
            return AttestationTrust.NONE
        if not has_chain:
            return AttestationTrust.SELF
        # The library validated the chain against the anchors, when any.
        return (
            AttestationTrust.TRUSTED
            if self.trust_anchors
            else AttestationTrust.UNVERIFIED
        )

    @staticmethod
    def parse_assertion(credential: Dict[str, Any]) -> Assertion:
        try:
            parsed = parse_authentication_credential_json(credential)
        except WebAuthnException as err:
            raise _refused("authentication", err) from err
        return Assertion(
            credential_id=bytes_to_base64url(parsed.raw_id),
            user_handle=parsed.response.user_handle,
            parsed=parsed,
        )

    def verify_assertion(
        self,
        assertion: Assertion,
        *,
        challenge: bytes,
        public_key: bytes,
        stored_sign_count: int,
        require_user_verification: bool,
    ) -> VerifiedAssertion:
        """The verified assertion; 401 when it fails a check, and
        :class:`CounterRegression` when it verifies but its counter did not
        advance."""
        try:
            self._refuse_cross_origin(
                assertion.parsed.response.client_data_json, "authentication"
            )
            # The counter is checked below, after the signature, so that only
            # a genuine signature can mark a credential as cloned.
            verified = verify_authentication_response(
                credential=assertion.parsed,
                expected_challenge=challenge,
                expected_rp_id=self.rp_id,
                expected_origin=list(self.origins),
                credential_public_key=public_key,
                credential_current_sign_count=0,
                require_user_verification=require_user_verification,
            )
        except WebAuthnException as err:
            raise _refused("authentication", err) from err
        received = verified.new_sign_count
        if (received or stored_sign_count) and received <= stored_sign_count:
            raise CounterRegression(stored_sign_count, received)
        return VerifiedAssertion(
            new_sign_count=received,
            backed_up=verified.credential_backed_up,
            user_verified=verified.user_verified,
        )


def _reports_discoverable(credential: Dict[str, Any]) -> bool:
    """The credProps client extension's answer; False when absent."""
    results = credential.get("clientExtensionResults")
    if not isinstance(results, dict):
        return False
    props = results.get("credProps")
    return isinstance(props, dict) and props.get("rk") is True
