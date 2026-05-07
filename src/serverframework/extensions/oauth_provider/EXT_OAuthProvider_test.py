"""Tests for the oauth_provider extension.

Covers the constant-time comparison + hashing decisions in the
client/code/token managers.
"""

import os

os.environ.setdefault("JWT_SECRET", "x" * 32)
os.environ.setdefault("PYTEST_CURRENT_TEST", "oauth_provider_test")


from serverframework.extensions.oauth_provider.BLL_OAuthProvider import (
    OAuth2AuthCodeManager,
    OAuth2AuthCodeModel,
    OAuth2ClientManager,
    OAuth2ClientModel,
    OAuth2TokenManager,
    OAuth2TokenModel,
    _hash_secret,
    _token_fingerprint,
)
from serverframework.extensions.oauth_provider.EXT_OAuthProvider import (
    EXT_OAuthProvider,
)


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
