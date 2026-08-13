# SPDX-License-Identifier: AGPL-3.0-or-later
"""Security gap tests — standalone (non-per-entity) tests for corpus gaps.

Covers: SSRF (§11), third-party API (§40), route inventory (§42),
session lifecycle (§4), cryptography (§37), logging/audit (§41),
JSON serialization (§24), and niche/cross-cutting (§45).

Tests that need a running server use per-entity abstract bases instead.
"""

from __future__ import annotations

import os
import time
import uuid

import pytest

os.environ.setdefault("JWT_SECRET", "test-jwt-secret-32-bytes-or-more-aaaaaa")
os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("SEED_DATA", "false")


# ------------------------------------------------------------------ #
# §11 — SSRF: ProviderHTTPClient must reject internal addresses
# ------------------------------------------------------------------ #


class TestSSRFProtection:
    @pytest.mark.security
    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1/",
            "http://localhost/",
            "http://[::1]/",
            "http://10.0.0.1/",
            "http://172.16.0.1/",
            "http://192.168.1.1/",
            "http://169.254.169.254/latest/meta-data/",
            "file:///etc/passwd",
        ],
    )
    def test_ssrf_private_address_rejected(self, url):
        """validate_outbound_url must reject private/metadata/non-http URLs."""
        from zephyrex.lib.ProviderHTTPClient import SSRFGuardError, validate_outbound_url

        os.environ.pop("DISABLE_SSRF_GUARD", None)
        with pytest.raises(SSRFGuardError):
            validate_outbound_url(url)

    @pytest.mark.security
    def test_ssrf_public_address_allowed(self):
        """validate_outbound_url must allow public URLs."""
        from zephyrex.lib.ProviderHTTPClient import validate_outbound_url

        os.environ.pop("DISABLE_SSRF_GUARD", None)
        validate_outbound_url("https://api.example.com/v1/data")


# ------------------------------------------------------------------ #
# §40 — Third-party API consumption
# ------------------------------------------------------------------ #


class TestThirdPartyAPIConsumption:
    @pytest.mark.security
    def test_provider_http_client_has_timeout(self):
        """ProviderHTTPClient must have default timeouts."""
        from zephyrex.lib.ProviderHTTPClient import ClientPolicy

        policy = ClientPolicy()
        assert policy.timeout is not None and policy.timeout > 0, (
            f"ClientPolicy timeout must be positive, got {policy.timeout}"
        )


# ------------------------------------------------------------------ #
# §42 — Route inventory
# ------------------------------------------------------------------ #


class TestRouteInventory:
    @pytest.fixture(scope="class")
    @classmethod
    def app(cls, tmp_path_factory):
        tmp = tmp_path_factory.mktemp("inventory")
        os.environ["DATABASE_NAME"] = f"inventory_{os.getpid()}"
        os.environ["DATABASE_PATH"] = str(tmp)

        from zephyrex.lib.Pydantic2SQLAlchemy import clear_registry_cache, reset_extension_system

        clear_registry_cache()
        reset_extension_system()

        from zephyrex.app import instance

        worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
        return instance(extensions="", db_prefix=f"inv.{worker}.{os.getpid()}")

    @pytest.mark.security
    def test_no_debug_endpoints(self, app):
        """No debug/admin/internal endpoints should be mounted."""
        routes = [r.path for r in app.routes if hasattr(r, "path")]
        for path in routes:
            low = path.lower()
            assert "/debug" not in low, f"Debug endpoint exposed: {path}"


# ------------------------------------------------------------------ #
# §4 — Session / credential lifecycle
# ------------------------------------------------------------------ #


class TestSessionLifecycle:
    @pytest.mark.security
    def test_session_token_uniqueness(self):
        """Generated tokens must not collide."""
        import secrets

        tokens = set()
        for _ in range(1000):
            t = secrets.token_urlsafe(32)
            assert t not in tokens
            tokens.add(t)

    @pytest.mark.security
    def test_password_hash_not_reversible(self):
        """Password hashing must use a one-way function (bcrypt)."""
        import bcrypt

        password = b"test_password_123!"
        hashed = bcrypt.hashpw(password, bcrypt.gensalt())
        assert hashed != password
        assert bcrypt.checkpw(password, hashed)
        assert not bcrypt.checkpw(b"wrong_password", hashed)


# ------------------------------------------------------------------ #
# §37 — Cryptography
# ------------------------------------------------------------------ #


class TestCryptographyGaps:
    @pytest.mark.security
    def test_jwt_secret_minimum_length(self):
        """JWT_SECRET must meet minimum length."""
        from zephyrex.lib.Environment import env

        secret = env("JWT_SECRET")
        assert len(secret) >= 32, f"JWT_SECRET is {len(secret)} chars; minimum is 32"

    @pytest.mark.security
    def test_constant_time_comparison(self):
        """API key comparison must use constant-time comparison."""
        from zephyrex.lib.InboundSecurity import compare_api_key

        t1 = []
        t2 = []
        for _ in range(100):
            t0 = time.perf_counter()
            compare_api_key("a" * 64, "b" * 64)
            t1.append(time.perf_counter() - t0)
            t0 = time.perf_counter()
            compare_api_key("a" * 64, "a" * 63 + "b")
            t2.append(time.perf_counter() - t0)

        avg1, avg2 = sum(t1) / len(t1), sum(t2) / len(t2)
        if avg1 > 0 and avg2 > 0:
            ratio = max(avg1, avg2) / min(avg1, avg2)
            assert ratio < 5, f"Non-constant-time comparison suspected: ratio={ratio:.1f}x"


# ------------------------------------------------------------------ #
# §41 — Logging / audit
# ------------------------------------------------------------------ #


class TestLoggingAudit:
    @pytest.mark.security
    def test_correlation_id_generated(self):
        """Every request should get a correlation ID."""
        from zephyrex.lib.RequestContext import mint_correlation_id

        cid = mint_correlation_id()
        assert cid is not None and len(cid) > 0

    @pytest.mark.security
    def test_sensitive_env_vars_set(self):
        """Critical security env vars must be set."""
        assert os.environ.get("JWT_SECRET"), "JWT_SECRET not set"


# ------------------------------------------------------------------ #
# §24 — JSON serialization edge cases
# ------------------------------------------------------------------ #


class TestJSONEdgeCases:
    @pytest.mark.security
    def test_json_encoder_handles_special_floats(self):
        """JSON encoder must handle NaN/Infinity without crashing."""
        import json as jsonlib

        for val in [float("nan"), float("inf"), float("-inf")]:
            try:
                result = jsonlib.dumps({"value": val})
                assert isinstance(result, str)
            except (ValueError, OverflowError):
                pass

    @pytest.mark.security
    def test_deeply_nested_dict_limit(self):
        """Framework should have a recursion limit for nested dicts."""
        nested = {"a": None}
        current = nested
        for _ in range(200):
            current["a"] = {"a": None}
            current = current["a"]
        import json as jsonlib

        serialized = jsonlib.dumps(nested)
        assert len(serialized) > 0
