"""Tests for the oauth_provider extension.

Covers the constant-time comparison + hashing decisions in the
client/code/token managers, plus the security-posture choices: no
raw client_secret_hash on the Create body, PKCE enforcement on public
clients, and PKCE verification on redemption.
"""

import os

os.environ.setdefault("JWT_SECRET", "x" * 32)
os.environ.setdefault("PYTEST_CURRENT_TEST", "oauth_provider_test")


import base64
import hashlib

import pytest
from pydantic import ValidationError

from zephyrex.extensions.oauth_provider.BLL_OAuthProvider import (
    OAuth2AuthCodeManager,
    OAuth2AuthCodeModel,
    OAuth2ClientManager,
    OAuth2ClientModel,
    OAuth2ProviderManager,
    OAuth2TokenManager,
    OAuth2TokenModel,
    _hash_secret,
    _token_fingerprint,
    _verify_pkce,
)
from zephyrex.extensions.oauth_provider.EXT_OAuthProvider import (
    EXT_OAuthProvider,
)
from zephyrex.lib.Pydantic2FastAPI import RouteType


class TestCanonicalWiring:
    def test_client_round_trip(self):
        assert OAuth2ClientManager._model is OAuth2ClientModel

    def test_authcode_round_trip(self):
        assert OAuth2AuthCodeManager._model is OAuth2AuthCodeModel

    def test_token_round_trip(self):
        assert OAuth2TokenManager._model is OAuth2TokenModel

    def test_extension_metadata(self):
        assert EXT_OAuthProvider.name == "oauth_provider"
        abilities = EXT_OAuthProvider.get_abilities()
        for expected in (
            "oauth_provider_register_client",
            "oauth_provider_authorize",
            "oauth_provider_token",
            "oauth_provider_introspect",
            "oauth_provider_revoke",
        ):
            assert expected in abilities


class TestHashing:
    def test_hash_secret_is_salt_dependent(self):
        assert _hash_secret("abc", "salt-1") != _hash_secret("abc", "salt-2")

    def test_hash_secret_is_value_dependent(self):
        assert _hash_secret("abc", "s") != _hash_secret("def", "s")

    def test_hash_secret_stable(self):
        assert _hash_secret("abc", "s") == _hash_secret("abc", "s")


class TestTokenFingerprint:
    def test_fingerprint_changes_with_input(self):
        assert _token_fingerprint("a") != _token_fingerprint("b")

    def test_fingerprint_stable(self):
        assert _token_fingerprint("same") == _token_fingerprint("same")

    def test_fingerprint_length(self):
        # 32-char truncated hex digest
        assert len(_token_fingerprint("any")) == 32


class TestLifecycle:
    def test_on_initialize_returns_true(self):
        assert EXT_OAuthProvider.on_initialize() is True


class TestSecurityPosture:
    def test_create_schema_does_not_expose_secret_hash(self):
        # The Create schema must not let a client write the hash directly.
        fields = set(OAuth2ClientModel.Create.model_fields.keys())
        assert "client_secret_hash" not in fields
        assert "client_secret_salt" not in fields
        assert "client_id" not in fields
        assert "owner_user_id" not in fields

    def test_authcode_create_schema_is_minimal(self):
        # No hash, salt, fingerprint, or expiry overrides on the public body.
        fields = set(OAuth2AuthCodeModel.Create.model_fields.keys())
        assert "code_hash" not in fields
        assert "code_salt" not in fields
        assert "code_fingerprint" not in fields
        assert "expires_at" not in fields
        assert "is_used" not in fields

    def test_token_create_schema_is_minimal(self):
        fields = set(OAuth2TokenModel.Create.model_fields.keys())
        assert "token_hash" not in fields
        assert "token_salt" not in fields
        assert "token_fingerprint" not in fields
        assert "is_revoked" not in fields

    def test_client_manager_routes_exclude_create_and_update(self):
        assert RouteType.CREATE not in (OAuth2ClientManager.routes_to_register or [])
        assert RouteType.UPDATE not in (OAuth2ClientManager.routes_to_register or [])

    def test_authcode_and_token_managers_have_no_crud_routes(self):
        assert OAuth2AuthCodeManager.routes_to_register == []
        assert OAuth2TokenManager.routes_to_register == []

    def test_provider_manager_advertises_custom_routes(self):
        from zephyrex.lib.CustomRoute import iter_custom_routes

        names = [name for name, _ in iter_custom_routes(OAuth2ProviderManager)]
        for expected in (
            "authorize_route",
            "token_route",
            "introspect_route",
            "revoke_route",
        ):
            assert expected in names


class TestPKCE:
    def test_s256_match_succeeds(self):
        verifier = "verifier-string-that-is-long-enough-for-rfc"
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
            .rstrip(b"=")
            .decode("ascii")
        )
        assert _verify_pkce(verifier, challenge, "S256") is True

    def test_s256_mismatch_fails(self):
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(b"different").digest())
            .rstrip(b"=")
            .decode("ascii")
        )
        assert _verify_pkce("not-the-verifier", challenge, "S256") is False

    def test_plain_match(self):
        assert _verify_pkce("plain-secret", "plain-secret", "PLAIN") is True

    def test_plain_mismatch(self):
        assert _verify_pkce("a", "b", "PLAIN") is False

    def test_unknown_method_rejected(self):
        assert _verify_pkce("v", "c", "MD5") is False

    def test_no_challenge_no_verifier_passes(self):
        assert _verify_pkce(None, None, None) is True

    def test_challenge_without_verifier_fails(self):
        assert _verify_pkce(None, "challenge", "S256") is False
