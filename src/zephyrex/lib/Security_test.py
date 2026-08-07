# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for security hardening of AppSettings and related guards.

These verify that non-production environments auto-generate safe defaults
for secrets, that production fails closed on insecure config, and that
security-relevant settings have safe defaults.
"""

from __future__ import annotations

import os

import pytest

from zephyrex.lib.Environment import AppSettings


# ---------------------------------------------------------------------------
# JWT_SECRET auto-generation
# ---------------------------------------------------------------------------


class TestJWTSecretAutoGeneration:
    """JWT_SECRET auto-generates in non-production when empty."""

    def test_empty_jwt_secret_gets_random_value(self):
        s = AppSettings.model_validate({"JWT_SECRET": ""})
        assert len(s.JWT_SECRET) >= 32

    def test_whitespace_jwt_secret_gets_random_value(self):
        s = AppSettings.model_validate({"JWT_SECRET": "   "})
        assert s.JWT_SECRET.strip() != ""
        assert len(s.JWT_SECRET) >= 32

    def test_auto_generated_secrets_are_unique(self):
        s1 = AppSettings.model_validate({"JWT_SECRET": ""})
        s2 = AppSettings.model_validate({"JWT_SECRET": ""})
        assert s1.JWT_SECRET != s2.JWT_SECRET

    def test_explicit_jwt_secret_preserved(self):
        secret = "my-explicit-secret-value-for-testing"
        s = AppSettings.model_validate({"JWT_SECRET": secret})
        assert s.JWT_SECRET == secret

    def test_production_rejects_empty_jwt_secret(self):
        with pytest.raises(ValueError, match="JWT_SECRET"):
            AppSettings.model_validate({
                "ENVIRONMENT": "production",
                "JWT_SECRET": "",
                "ROOT_API_KEY": "x" * 32,
                "ALLOWED_DOMAINS": "example.com",
                "DATABASE_PASSWORD": "secure",
                "DATABASE_SSL": "require",
                "APP_EXTENSIONS": "",
            })

    def test_staging_rejects_empty_jwt_secret(self):
        with pytest.raises(ValueError, match="JWT_SECRET"):
            AppSettings.model_validate({
                "ENVIRONMENT": "staging",
                "JWT_SECRET": "",
                "ROOT_API_KEY": "x" * 32,
                "ALLOWED_DOMAINS": "example.com",
                "DATABASE_PASSWORD": "secure",
                "DATABASE_SSL": "require",
                "APP_EXTENSIONS": "",
            })

    def test_production_rejects_short_jwt_secret(self):
        with pytest.raises(ValueError, match="at least 32 characters"):
            AppSettings.model_validate({
                "ENVIRONMENT": "production",
                "JWT_SECRET": "tooshort",
                "ROOT_API_KEY": "x" * 32,
                "ALLOWED_DOMAINS": "example.com",
                "DATABASE_PASSWORD": "secure",
                "DATABASE_SSL": "require",
                "APP_EXTENSIONS": "",
            })


# ---------------------------------------------------------------------------
# ROOT_API_KEY auto-generation
# ---------------------------------------------------------------------------


class TestRootAPIKeyAutoGeneration:
    """ROOT_API_KEY default 'n0ne' auto-generates in non-production."""

    def test_default_n0ne_gets_random_value(self):
        s = AppSettings.model_validate({"ROOT_API_KEY": "n0ne"})
        assert s.ROOT_API_KEY != "n0ne"
        assert len(s.ROOT_API_KEY) >= 16

    def test_empty_root_api_key_gets_random_value(self):
        s = AppSettings.model_validate({"ROOT_API_KEY": ""})
        assert s.ROOT_API_KEY != ""
        assert len(s.ROOT_API_KEY) >= 16

    def test_explicit_root_api_key_preserved(self):
        key = "my-custom-root-api-key-value"
        s = AppSettings.model_validate({"ROOT_API_KEY": key})
        assert s.ROOT_API_KEY == key

    def test_production_rejects_default_n0ne(self):
        with pytest.raises(ValueError, match="ROOT_API_KEY"):
            AppSettings.model_validate({
                "ENVIRONMENT": "production",
                "ROOT_API_KEY": "n0ne",
                "JWT_SECRET": "x" * 32,
                "ALLOWED_DOMAINS": "example.com",
                "DATABASE_PASSWORD": "secure",
                "DATABASE_SSL": "require",
                "APP_EXTENSIONS": "",
            })

    def test_production_rejects_empty_root_api_key(self):
        with pytest.raises(ValueError, match="ROOT_API_KEY"):
            AppSettings.model_validate({
                "ENVIRONMENT": "production",
                "ROOT_API_KEY": "",
                "JWT_SECRET": "x" * 32,
                "ALLOWED_DOMAINS": "example.com",
                "DATABASE_PASSWORD": "secure",
                "DATABASE_SSL": "require",
                "APP_EXTENSIONS": "",
            })


# ---------------------------------------------------------------------------
# CORS / ALLOWED_DOMAINS
# ---------------------------------------------------------------------------


class TestCORSDefaultEmpty:
    """ALLOWED_DOMAINS defaults to empty (no origins allowed)."""

    def test_default_allowed_domains_empty(self):
        s = AppSettings.model_validate({})
        assert s.ALLOWED_DOMAINS == ""

    def test_production_rejects_wildcard(self):
        with pytest.raises(ValueError, match="ALLOWED_DOMAINS"):
            AppSettings.model_validate({
                "ENVIRONMENT": "production",
                "ALLOWED_DOMAINS": "*",
                "JWT_SECRET": "x" * 32,
                "ROOT_API_KEY": "x" * 32,
                "DATABASE_PASSWORD": "secure",
                "DATABASE_SSL": "require",
                "APP_EXTENSIONS": "",
            })

    def test_production_rejects_empty_domains(self):
        with pytest.raises(ValueError, match="ALLOWED_DOMAINS"):
            AppSettings.model_validate({
                "ENVIRONMENT": "production",
                "ALLOWED_DOMAINS": "",
                "JWT_SECRET": "x" * 32,
                "ROOT_API_KEY": "x" * 32,
                "DATABASE_PASSWORD": "secure",
                "DATABASE_SSL": "require",
                "APP_EXTENSIONS": "",
            })

    def test_production_accepts_explicit_domain(self):
        s = AppSettings.model_validate({
            "ENVIRONMENT": "production",
            "ALLOWED_DOMAINS": "myapp.example.com",
            "JWT_SECRET": "x" * 32,
            "ROOT_API_KEY": "x" * 32,
            "DATABASE_PASSWORD": "secure",
            "DATABASE_SSL": "require",
            "APP_EXTENSIONS": "",
        })
        assert s.ALLOWED_DOMAINS == "myapp.example.com"


# ---------------------------------------------------------------------------
# DATABASE_SSL default
# ---------------------------------------------------------------------------


class TestDatabaseSSLDefault:
    """DATABASE_SSL defaults to 'require' (TLS enforced)."""

    def test_default_ssl_require(self):
        s = AppSettings.model_validate({})
        assert s.DATABASE_SSL == "require"

    @pytest.mark.parametrize(
        "unsafe_mode",
        ["disable", "allow", "prefer", ""],
    )
    def test_production_rejects_unsafe_ssl_modes(self, unsafe_mode):
        with pytest.raises(ValueError, match="DATABASE_SSL"):
            AppSettings.model_validate({
                "ENVIRONMENT": "production",
                "DATABASE_SSL": unsafe_mode,
                "JWT_SECRET": "x" * 32,
                "ROOT_API_KEY": "x" * 32,
                "ALLOWED_DOMAINS": "example.com",
                "DATABASE_PASSWORD": "secure",
                "APP_EXTENSIONS": "",
            })

    def test_production_accepts_verify_full(self):
        s = AppSettings.model_validate({
            "ENVIRONMENT": "production",
            "DATABASE_SSL": "verify-full",
            "JWT_SECRET": "x" * 32,
            "ROOT_API_KEY": "x" * 32,
            "ALLOWED_DOMAINS": "example.com",
            "DATABASE_PASSWORD": "secure",
            "APP_EXTENSIONS": "",
        })
        assert s.DATABASE_SSL == "verify-full"


# ---------------------------------------------------------------------------
# BCRYPT_ROUNDS
# ---------------------------------------------------------------------------


class TestBcryptRoundsConfigurable:
    """BCRYPT_ROUNDS is configurable with a safe default."""

    def test_default_rounds(self):
        s = AppSettings.model_validate({})
        assert s.BCRYPT_ROUNDS == 12

    def test_custom_rounds(self):
        s = AppSettings.model_validate({"BCRYPT_ROUNDS": "14"})
        assert s.BCRYPT_ROUNDS == 14

    def test_rounds_coerced_from_string(self):
        s = AppSettings.model_validate({"BCRYPT_ROUNDS": "10"})
        assert s.BCRYPT_ROUNDS == 10
        assert isinstance(s.BCRYPT_ROUNDS, int)


# ---------------------------------------------------------------------------
# JWT_ALGORITHM
# ---------------------------------------------------------------------------


class TestJWTAlgorithmConfigurable:
    """JWT_ALGORITHM defaults to HS256."""

    def test_default_hs256(self):
        s = AppSettings.model_validate({})
        assert s.JWT_ALGORITHM == "HS256"

    def test_custom_algorithm(self):
        s = AppSettings.model_validate({"JWT_ALGORITHM": "HS384"})
        assert s.JWT_ALGORITHM == "HS384"


# ---------------------------------------------------------------------------
# DATABASE_PASSWORD in production
# ---------------------------------------------------------------------------


class TestDatabasePasswordProduction:
    """Production rejects empty or default database passwords."""

    def test_production_rejects_empty_password(self):
        with pytest.raises(ValueError, match="DATABASE_PASSWORD"):
            AppSettings.model_validate({
                "ENVIRONMENT": "production",
                "DATABASE_PASSWORD": "",
                "JWT_SECRET": "x" * 32,
                "ROOT_API_KEY": "x" * 32,
                "ALLOWED_DOMAINS": "example.com",
                "DATABASE_SSL": "require",
                "APP_EXTENSIONS": "",
            })

    def test_production_rejects_default_password(self):
        with pytest.raises(ValueError, match="DATABASE_PASSWORD"):
            AppSettings.model_validate({
                "ENVIRONMENT": "production",
                "DATABASE_PASSWORD": "Password1!",
                "JWT_SECRET": "x" * 32,
                "ROOT_API_KEY": "x" * 32,
                "ALLOWED_DOMAINS": "example.com",
                "DATABASE_SSL": "require",
                "APP_EXTENSIONS": "",
            })


# ---------------------------------------------------------------------------
# SSRF guard in production
# ---------------------------------------------------------------------------


class TestSSRFGuardProduction:
    """DISABLE_SSRF_GUARD must not be truthy in production."""

    def test_production_rejects_ssrf_guard_disabled(self, monkeypatch):
        monkeypatch.setenv("DISABLE_SSRF_GUARD", "true")
        with pytest.raises(ValueError, match="SSRF"):
            AppSettings.model_validate({
                "ENVIRONMENT": "production",
                "JWT_SECRET": "x" * 32,
                "ROOT_API_KEY": "x" * 32,
                "ALLOWED_DOMAINS": "example.com",
                "DATABASE_PASSWORD": "secure",
                "DATABASE_SSL": "require",
                "APP_EXTENSIONS": "",
            })

    def test_nonproduction_allows_ssrf_guard_disabled(self, monkeypatch):
        monkeypatch.setenv("DISABLE_SSRF_GUARD", "true")
        s = AppSettings.model_validate({"ENVIRONMENT": "local"})
        assert s.ENVIRONMENT == "local"


# ---------------------------------------------------------------------------
# Production valid config acceptance (positive path)
# ---------------------------------------------------------------------------


class TestProductionValidConfig:
    """A fully-configured production deployment boots without error."""

    def test_all_required_fields_present(self):
        s = AppSettings.model_validate({
            "ENVIRONMENT": "production",
            "JWT_SECRET": "a" * 32,
            "ROOT_API_KEY": "a" * 32,
            "ALLOWED_DOMAINS": "example.com",
            "DATABASE_PASSWORD": "securepass123",
            "DATABASE_SSL": "require",
            "APP_EXTENSIONS": "",
        })
        assert s.ENVIRONMENT == "production"
        assert s.JWT_SECRET == "a" * 32
        assert s.ROOT_API_KEY == "a" * 32


# ---------------------------------------------------------------------------
# Password history: no date leak in error messages (placeholder)
# ---------------------------------------------------------------------------


class TestPasswordHistoryNoLeak:
    """Login with old password returns generic error, not change date.

    This test verifies the error message contract. The actual auth flow
    requires a running server; the security fix changes error messages in
    BLL_Auth to remove date information from password-change-related
    rejection messages.
    """
    # Full auth-flow tests live in the auth extension test suite.
    # This class is a placeholder confirming the security requirement
    # is tracked. Tests that exercise the error-message content belong
    # alongside the BLL_Auth tests where the server fixture is available.


# ---------------------------------------------------------------------------
# Token verification: no internal detail leak (placeholder)
# ---------------------------------------------------------------------------


class TestTokenVerificationNoLeak:
    """Token verification errors don't leak internal details.

    Requires a server fixture to test the full token verification path.
    The security contract is: error responses from token verification
    endpoints must not contain stack traces, internal module paths,
    or cryptographic details.
    """
    # Full endpoint-level tests live in the auth extension test suite.
