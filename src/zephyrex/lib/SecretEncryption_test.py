"""Tests for the SecretEncryption helper.

The env-var defaults are read through ``zephyrex.lib.Environment.env``,
which proxies a cached ``EnvironmentSettings`` Pydantic model. To exercise
specific policy branches, we patch the underlying ``env`` callable rather
than mutating ``os.environ``.
"""

import os

os.environ.setdefault("JWT_SECRET", "x" * 32)
os.environ.setdefault("PYTEST_CURRENT_TEST", "secret_encryption_test")

import pytest

from zephyrex.lib import SecretEncryption


def _patch_env(monkeypatch, **values):
    """Override ``zephyrex.lib.SecretEncryption.env`` to return our values."""

    def fake_env(name, default=None):
        return values.get(name, default)

    monkeypatch.setattr(SecretEncryption, "env", fake_env)


class TestPolicy:
    def test_no_key_no_fallback_raises(self, monkeypatch):
        _patch_env(monkeypatch, ENVIRONMENT="production")
        with pytest.raises(SecretEncryption.MissingFernetKeyError):
            SecretEncryption.encrypt_secret("hello")

    def test_no_key_with_fallback_in_dev_returns_plaintext(self, monkeypatch):
        _patch_env(
            monkeypatch, ENVIRONMENT="local", ALLOW_PLAINTEXT_SECRETS="true"
        )
        assert SecretEncryption.encrypt_secret("hello") == "hello"

    def test_no_key_fallback_rejected_in_production(self, monkeypatch):
        _patch_env(
            monkeypatch, ENVIRONMENT="production", ALLOW_PLAINTEXT_SECRETS="true"
        )
        with pytest.raises(SecretEncryption.MissingFernetKeyError):
            SecretEncryption.encrypt_secret("hello")

    def test_assert_encryption_available_raises_without_key(self, monkeypatch):
        _patch_env(monkeypatch, ENVIRONMENT="production")
        with pytest.raises(SecretEncryption.MissingFernetKeyError):
            SecretEncryption.assert_encryption_available()

    def test_assert_encryption_available_passes_with_dev_fallback(
        self, monkeypatch
    ):
        _patch_env(
            monkeypatch, ENVIRONMENT="local", ALLOW_PLAINTEXT_SECRETS="true"
        )
        # Should NOT raise.
        SecretEncryption.assert_encryption_available()


class TestRoundTrip:
    def test_encrypt_decrypt_roundtrips(self, monkeypatch):
        from cryptography.fernet import Fernet

        key = Fernet.generate_key().decode()
        _patch_env(
            monkeypatch, FRAMEWORK_FERNET_KEY=key, ENVIRONMENT="production"
        )

        ciphertext = SecretEncryption.encrypt_secret("super-secret")
        assert ciphertext.startswith(SecretEncryption.FERNET_PREFIX)
        assert SecretEncryption.decrypt_secret(ciphertext) == "super-secret"

    def test_plaintext_passthrough_on_decrypt(self, monkeypatch):
        from cryptography.fernet import Fernet

        key = Fernet.generate_key().decode()
        _patch_env(
            monkeypatch, FRAMEWORK_FERNET_KEY=key, ENVIRONMENT="production"
        )
        # Untagged values left over from before the rollout should pass through.
        assert SecretEncryption.decrypt_secret("legacy-plain") == "legacy-plain"

    def test_double_encrypt_is_idempotent(self, monkeypatch):
        from cryptography.fernet import Fernet

        key = Fernet.generate_key().decode()
        _patch_env(
            monkeypatch, FRAMEWORK_FERNET_KEY=key, ENVIRONMENT="production"
        )

        once = SecretEncryption.encrypt_secret("v")
        twice = SecretEncryption.encrypt_secret(once)
        assert once == twice
