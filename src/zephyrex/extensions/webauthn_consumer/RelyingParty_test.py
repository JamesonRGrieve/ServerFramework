# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Relying Party policy against py_webauthn and a software
authenticator: what registers and signs in, and every refusal. No app, no
database: these are the checks the ceremonies run."""

import hashlib
import secrets
from pathlib import Path
from typing import Any, Callable, Dict

import cbor2
import pytest
from fastapi import HTTPException
from webauthn.helpers.structs import UserVerificationRequirement

from zephyrex.extensions.webauthn_consumer.RelyingParty import (
    AttestationTrust,
    CounterRegression,
    Descriptor,
    RelyingPartyPolicy,
    config_issues,
)
from zephyrex.extensions.webauthn_consumer.SoftwareAuthenticator_test import (
    CertificateAuthority,
    SoftwareAuthenticator,
    b64url,
    from_b64url,
)

RP_ID = "example.test"
ORIGIN = "https://app.example.test"


@pytest.fixture
def configure(set_env: Callable[[str, str], None]) -> Callable[..., RelyingPartyPolicy]:
    """The policy, with the given settings over a working base."""

    def _configure(**overrides: str) -> RelyingPartyPolicy:
        settings = {
            "WEBAUTHN_CONSUMER_RP_ID": RP_ID,
            "WEBAUTHN_CONSUMER_ORIGINS": f"{ORIGIN}, https://admin.example.test",
            "WEBAUTHN_CONSUMER_ATTESTATION_ROOTS": "",
            "WEBAUTHN_CONSUMER_ATTESTATION_FORMATS": "none packed fido-u2f tpm apple",
            "WEBAUTHN_CONSUMER_USER_VERIFICATION": "preferred",
            **overrides,
        }
        for key, value in settings.items():
            set_env(key, value)
        return RelyingPartyPolicy.from_env()

    return _configure


@pytest.fixture
def policy(configure: Callable[..., RelyingPartyPolicy]) -> RelyingPartyPolicy:
    return configure()


def _creation_options(policy: RelyingPartyPolicy, challenge: bytes) -> Dict[str, Any]:
    return policy.registration_options(
        challenge=challenge,
        user_handle=b"user-1",
        user_name="ada",
        display_name="Ada",
        exclude=[],
        attachment=None,
    )


def _register(
    policy: RelyingPartyPolicy,
    authenticator: SoftwareAuthenticator,
    **create: Any,
) -> Any:
    challenge = secrets.token_bytes(32)
    options = _creation_options(policy, challenge)
    response = authenticator.create(options, create.pop("origin", ORIGIN), **create)
    return policy.verify_registration(response, challenge)


def _refused(call: Callable[[], Any], status: int, reason: str) -> None:
    with pytest.raises(HTTPException) as refused:
        call()
    assert refused.value.status_code == status
    assert reason in str(refused.value.detail)


class TestConfiguration:
    def test_unconfigured_is_503_and_named(self, set_env) -> None:
        set_env("WEBAUTHN_CONSUMER_RP_ID", "")
        set_env("WEBAUTHN_CONSUMER_ORIGINS", "")
        _refused(RelyingPartyPolicy.from_env, 503, "not configured")
        issues = " ".join(config_issues())
        assert "WEBAUTHN_CONSUMER_RP_ID" in issues
        assert "WEBAUTHN_CONSUMER_ORIGINS" in issues

    def test_bad_values_are_503_and_named(self, configure, set_env) -> None:
        configure()
        set_env("WEBAUTHN_CONSUMER_USER_VERIFICATION", "sometimes")
        set_env("WEBAUTHN_CONSUMER_ATTESTATION_FORMATS", "none bogus")
        _refused(RelyingPartyPolicy.from_env, 503, "not configured")
        issues = " ".join(config_issues())
        assert "WEBAUTHN_CONSUMER_USER_VERIFICATION" in issues
        assert "bogus" in issues

    def test_a_working_configuration_has_no_issues(self, configure) -> None:
        policy = configure()
        assert config_issues() == []
        assert policy.origins == (ORIGIN, "https://admin.example.test")


class TestOptions:
    def test_creation_options_name_the_rp_user_and_excluded_keys(self, policy) -> None:
        existing = secrets.token_bytes(16)
        options = policy.registration_options(
            challenge=b"\x02" * 32,
            user_handle=b"user-1",
            user_name="ada",
            display_name="Ada",
            exclude=[Descriptor(existing, ("usb", "bogus"))],
            attachment=None,
        )
        assert options["rp"]["id"] == RP_ID
        assert options["user"]["id"] == b64url(b"user-1")
        assert options["challenge"] == b64url(b"\x02" * 32)
        assert options["excludeCredentials"] == [
            {"id": b64url(existing), "type": "public-key", "transports": ["usb"]}
        ]
        assert options["extensions"] == {"credProps": True}
        assert {p["alg"] for p in options["pubKeyCredParams"]} == {-8, -7, -257}

    def test_request_options_name_the_allowed_keys(self, policy) -> None:
        allowed = secrets.token_bytes(16)
        options = policy.authentication_options(
            challenge=b"\x03" * 32,
            allow=[Descriptor(allowed)],
            user_verification=UserVerificationRequirement.REQUIRED,
        )
        assert options["rpId"] == RP_ID
        assert options["userVerification"] == "required"
        assert options["allowCredentials"][0]["id"] == b64url(allowed)


class TestRegistration:
    @pytest.mark.parametrize("algorithm", ["es256", "rs256", "ed25519"])
    def test_none_attestation_registers_each_key_type(self, policy, algorithm) -> None:
        authenticator = SoftwareAuthenticator(algorithm=algorithm, backup_eligible=True)
        registered = _register(policy, authenticator)
        assert from_b64url(registered.credential_id) == authenticator.credential_id
        assert from_b64url(registered.public_key) == authenticator.cose_public_key()
        assert registered.attestation_format == "none"
        assert registered.attestation_trust == AttestationTrust.NONE
        assert registered.transports == "usb"
        assert registered.is_discoverable is True
        assert registered.backup_eligible and registered.backed_up

    def test_self_attestation(self, policy) -> None:
        registered = _register(policy, SoftwareAuthenticator(), fmt="packed-self")
        assert registered.attestation_format == "packed"
        assert registered.attestation_trust == AttestationTrust.SELF

    @pytest.mark.parametrize(
        "fmt,algorithm",
        [("packed", "es256"), ("fido-u2f", "es256"), ("tpm", "rs256")],
    )
    def test_certified_attestation_without_anchors_is_unverified(
        self, policy, fmt, algorithm
    ) -> None:
        registered = _register(
            policy,
            SoftwareAuthenticator(algorithm=algorithm),
            fmt=fmt,
            ca=CertificateAuthority(),
        )
        assert registered.attestation_format == fmt
        assert registered.attestation_trust == AttestationTrust.UNVERIFIED

    @pytest.mark.parametrize(
        "fmt,algorithm",
        [
            ("packed", "es256"),
            ("fido-u2f", "es256"),
            ("tpm", "rs256"),
            ("apple", "es256"),
        ],
    )
    def test_attestation_chained_to_an_anchor_is_trusted(
        self, configure, tmp_path: Path, fmt, algorithm
    ) -> None:
        ca = CertificateAuthority()
        roots = tmp_path / "roots.pem"
        roots.write_bytes(ca.pem)
        policy = configure(WEBAUTHN_CONSUMER_ATTESTATION_ROOTS=str(roots))
        registered = _register(
            policy, SoftwareAuthenticator(algorithm=algorithm), fmt=fmt, ca=ca
        )
        assert registered.attestation_trust == AttestationTrust.TRUSTED

    def test_with_anchors_untrusted_attestation_is_refused(
        self, configure, tmp_path: Path
    ) -> None:
        roots = tmp_path / "roots.pem"
        roots.write_bytes(CertificateAuthority().pem)
        policy = configure(WEBAUTHN_CONSUMER_ATTESTATION_ROOTS=str(roots))
        for fmt, ca in (
            ("none", None),
            ("packed-self", None),
            ("packed", CertificateAuthority("Some Other Root")),
        ):
            _refused(
                lambda: _register(policy, SoftwareAuthenticator(), fmt=fmt, ca=ca),
                400,
                "registration refused",
            )

    def test_a_format_not_accepted_is_refused(self, configure) -> None:
        policy = configure(WEBAUTHN_CONSUMER_ATTESTATION_FORMATS="packed")
        _refused(
            lambda: _register(policy, SoftwareAuthenticator()),
            400,
            'attestation format "none" is not accepted',
        )

    def test_wrong_origin_is_refused(self, policy) -> None:
        _refused(
            lambda: _register(
                policy, SoftwareAuthenticator(), origin="https://evil.example"
            ),
            400,
            "origin",
        )

    def test_an_origin_that_only_shares_the_domain_is_refused(self, policy) -> None:
        _refused(
            lambda: _register(
                policy, SoftwareAuthenticator(), origin="http://app.example.test"
            ),
            400,
            "origin",
        )

    def test_wrong_rp_id_is_refused(self, policy) -> None:
        _refused(
            lambda: _register(policy, SoftwareAuthenticator(), rp_id="evil.example"),
            400,
            "RP ID",
        )

    def test_a_foreign_challenge_is_refused(self, policy) -> None:
        _refused(
            lambda: _register(
                policy, SoftwareAuthenticator(), challenge=b64url(b"x" * 32)
            ),
            400,
            "challenge",
        )

    def test_a_cross_origin_frame_is_refused(self, policy) -> None:
        _refused(
            lambda: _register(policy, SoftwareAuthenticator(), cross_origin=True),
            400,
            "cross-origin",
        )

    def test_no_user_presence_is_refused(self, policy) -> None:
        _refused(
            lambda: _register(policy, SoftwareAuthenticator(), user_present=False),
            400,
            "presence",
        )

    def test_required_user_verification_is_enforced(self, configure) -> None:
        policy = configure(WEBAUTHN_CONSUMER_USER_VERIFICATION="required")
        _refused(
            lambda: _register(policy, SoftwareAuthenticator(), user_verified=False),
            400,
            "verification",
        )
        assert _register(policy, SoftwareAuthenticator()).attestation_format == "none"

    def test_a_self_attestation_signed_by_another_key_is_refused(self, policy) -> None:
        challenge = secrets.token_bytes(32)
        response = SoftwareAuthenticator().create(
            _creation_options(policy, challenge), ORIGIN, fmt="packed-self"
        )
        attestation = cbor2.loads(
            from_b64url(response["response"]["attestationObject"])
        )
        client_data = from_b64url(response["response"]["clientDataJSON"])
        attestation["attStmt"]["sig"] = SoftwareAuthenticator().sign(
            attestation["authData"] + hashlib.sha256(client_data).digest()
        )
        response["response"]["attestationObject"] = b64url(cbor2.dumps(attestation))
        _refused(
            lambda: policy.verify_registration(response, challenge),
            400,
            "signature",
        )


class TestAssertion:
    def _assert(
        self,
        policy: RelyingPartyPolicy,
        authenticator: SoftwareAuthenticator,
        stored_sign_count: int = 0,
        require_user_verification: bool = True,
        **get: Any,
    ) -> Any:
        registered = _register(policy, authenticator)
        challenge = secrets.token_bytes(32)
        options = policy.authentication_options(
            challenge=challenge,
            allow=[Descriptor(authenticator.credential_id)],
            user_verification=UserVerificationRequirement.REQUIRED,
        )
        response = authenticator.get(options, get.pop("origin", ORIGIN), **get)
        assertion = policy.parse_assertion(response)
        return policy.verify_assertion(
            assertion,
            challenge=challenge,
            public_key=from_b64url(registered.public_key),
            stored_sign_count=stored_sign_count,
            require_user_verification=require_user_verification,
        )

    @pytest.mark.parametrize("algorithm", ["es256", "rs256", "ed25519"])
    def test_a_genuine_assertion_verifies(self, policy, algorithm) -> None:
        verified = self._assert(policy, SoftwareAuthenticator(algorithm=algorithm))
        assert verified.new_sign_count == 1 and verified.user_verified

    def test_a_bad_signature_is_refused(self, policy) -> None:
        _refused(
            lambda: self._assert(
                policy, SoftwareAuthenticator(), corrupt_signature=True
            ),
            401,
            "signature",
        )

    def test_wrong_origin_is_refused(self, policy) -> None:
        _refused(
            lambda: self._assert(
                policy, SoftwareAuthenticator(), origin="https://evil.example"
            ),
            401,
            "origin",
        )

    def test_wrong_rp_id_is_refused(self, policy) -> None:
        _refused(
            lambda: self._assert(policy, SoftwareAuthenticator(), rp_id="evil.example"),
            401,
            "RP ID",
        )

    def test_a_foreign_challenge_is_refused(self, policy) -> None:
        _refused(
            lambda: self._assert(
                policy, SoftwareAuthenticator(), challenge=b64url(b"y" * 32)
            ),
            401,
            "challenge",
        )

    def test_required_user_verification_is_enforced(self, policy) -> None:
        _refused(
            lambda: self._assert(policy, SoftwareAuthenticator(), user_verified=False),
            401,
            "verified",
        )

    def test_unverified_user_passes_when_not_required(self, policy) -> None:
        verified = self._assert(
            policy,
            SoftwareAuthenticator(),
            user_verified=False,
            require_user_verification=False,
        )
        assert verified.user_verified is False

    def test_a_counter_that_does_not_advance_is_a_clone(self, policy) -> None:
        authenticator = SoftwareAuthenticator(sign_count=4)
        with pytest.raises(CounterRegression) as regression:
            self._assert(policy, authenticator, stored_sign_count=9)
        assert (regression.value.stored, regression.value.received) == (9, 5)

    def test_a_zero_counter_authenticator_is_not_a_clone(self, policy) -> None:
        verified = self._assert(policy, SoftwareAuthenticator(), advance_counter=False)
        assert verified.new_sign_count == 0

    def test_only_a_verified_signature_can_report_a_clone(self, policy) -> None:
        """A forged assertion with a stale counter is a bad signature, not a
        clone: a stranger cannot get a credential disabled."""
        _refused(
            lambda: self._assert(
                policy,
                SoftwareAuthenticator(sign_count=1),
                stored_sign_count=9,
                corrupt_signature=True,
            ),
            401,
            "signature",
        )

    def test_a_cross_origin_frame_is_refused(self, policy) -> None:
        _refused(
            lambda: self._assert(policy, SoftwareAuthenticator(), cross_origin=True),
            401,
            "cross-origin",
        )

    def test_a_malformed_response_is_refused(self, policy) -> None:
        _refused(
            lambda: policy.parse_assertion({"id": "x", "type": "public-key"}),
            401,
            "authentication refused",
        )
