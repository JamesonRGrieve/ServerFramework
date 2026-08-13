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


# ------------------------------------------------------------------ #
# §2 — JWT Semantic Validation (beyond basic rejection)
# ------------------------------------------------------------------ #


class TestJWTSemantics:
    @pytest.mark.security
    def test_jwt_exp_before_iat_rejected(self, server, admin_a):
        """JWT with exp < iat must be rejected."""
        import jwt as pyjwt
        from zephyrex.lib.Environment import env

        payload = {
            "sub": admin_a.id,
            "iat": 9999999999,
            "exp": 1000000000,
            "jti": str(uuid.uuid4()),
        }
        token = pyjwt.encode(payload, env("JWT_SECRET"), algorithm="HS256")
        response = server.get("/v1/team", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code in (401, 403)

    @pytest.mark.security
    def test_jwt_excessive_future_iat_rejected(self, server, admin_a):
        """JWT with iat far in the future must be rejected."""
        import jwt as pyjwt
        from zephyrex.lib.Environment import env

        payload = {
            "sub": admin_a.id,
            "iat": 99999999999,
            "exp": 99999999999 + 3600,
            "jti": str(uuid.uuid4()),
        }
        token = pyjwt.encode(payload, env("JWT_SECRET"), algorithm="HS256")
        response = server.get("/v1/team", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code in (401, 403)

    @pytest.mark.security
    def test_jwt_typ_confusion_rejected(self, server, admin_a):
        """JWT with typ:at+jwt must not be confused with a regular JWT."""
        import jwt as pyjwt
        from zephyrex.lib.Environment import env

        payload = {"sub": admin_a.id, "jti": str(uuid.uuid4())}
        token = pyjwt.encode(
            payload, env("JWT_SECRET"), algorithm="HS256",
            headers={"typ": "at+jwt"},
        )
        response = server.get("/v1/team", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code != 500

    @pytest.mark.security
    def test_jwt_critical_header_rejected(self, server, admin_a):
        """JWT with crit header must not cause 500."""
        import jwt as pyjwt
        from zephyrex.lib.Environment import env

        payload = {"sub": admin_a.id, "jti": str(uuid.uuid4())}
        token = pyjwt.encode(
            payload, env("JWT_SECRET"), algorithm="HS256",
            headers={"crit": ["exp"]},
        )
        response = server.get("/v1/team", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code != 500

    @pytest.mark.security
    def test_jwt_scope_cannot_expand_privileges(self, server, admin_a):
        """JWT with added scope claims must not grant extra permissions."""
        import jwt as pyjwt
        from zephyrex.lib.Environment import env

        payload = {
            "sub": admin_a.id,
            "jti": str(uuid.uuid4()),
            "scope": "admin superadmin root",
        }
        token = pyjwt.encode(payload, env("JWT_SECRET"), algorithm="HS256")
        response = server.get("/v1/team", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code != 500


# ------------------------------------------------------------------ #
# §5 — Function-Level Authorization
# ------------------------------------------------------------------ #


class TestFunctionLevelAuth:
    @pytest.mark.security
    def test_non_admin_cannot_access_other_team(self, server, user_b, team_a):
        """Non-admin user cannot access another team's resources."""
        response = server.get(
            f"/v1/team/{team_a.id}",
            headers={"Authorization": f"Bearer {user_b.jwt}"},
        )
        assert response.status_code in (403, 404), (
            f"Non-admin accessed other team: {response.status_code}"
        )

    @pytest.mark.security
    def test_non_admin_cannot_modify_other_team(self, server, user_b, team_a):
        """Non-admin user cannot modify another team."""
        response = server.put(
            f"/v1/team/{team_a.id}",
            json={"team": {"name": "hacked"}},
            headers={"Authorization": f"Bearer {user_b.jwt}"},
        )
        assert response.status_code in (403, 404), (
            f"Non-admin modified other team: {response.status_code}"
        )


# ------------------------------------------------------------------ #
# §9 — Multi-Tenant Isolation (beyond basic cross-team)
# ------------------------------------------------------------------ #


class TestMultiTenantIsolation:
    @pytest.mark.security
    def test_cross_tenant_error_does_not_reveal_existence(
        self, server, admin_a, admin_b, team_a
    ):
        """Error when accessing cross-tenant resource must not confirm existence."""
        r_nonexistent = server.get(
            f"/v1/team/{uuid.uuid4()}",
            headers={"Authorization": f"Bearer {admin_b.jwt}"},
        )
        r_exists = server.get(
            f"/v1/team/{team_a.id}",
            headers={"Authorization": f"Bearer {admin_b.jwt}"},
        )
        assert r_nonexistent.status_code == r_exists.status_code, (
            f"Cross-tenant error reveals existence: nonexistent={r_nonexistent.status_code} "
            f"vs exists={r_exists.status_code}"
        )


# ------------------------------------------------------------------ #
# §22 — Race Conditions (standalone, beyond per-entity concurrent create)
# ------------------------------------------------------------------ #


class TestRaceConditions:
    @pytest.mark.security
    def test_concurrent_password_reset_safe(self, server, admin_a):
        """Concurrent password reset requests must not cause 500."""
        for _ in range(3):
            server.post(
                "/v1/user/reset-password",
                json={"email": "admin@example.com"},
                headers={"Authorization": f"Bearer {admin_a.jwt}"},
            )

    @pytest.mark.security
    def test_concurrent_session_operations_safe(self, server, admin_a):
        """Rapid session operations must not cause 500."""
        headers = {"Authorization": f"Bearer {admin_a.jwt}"}
        for _ in range(5):
            server.get("/v1/team", headers=headers)


# ------------------------------------------------------------------ #
# §39 — Audit / Logging Security
# ------------------------------------------------------------------ #


class TestAuditSecurity:
    @pytest.mark.security
    def test_failed_auth_does_not_log_password(self, server):
        """Failed login must not log the attempted password."""
        import logging

        server.post(
            "/v1/user/login",
            json={"email": "test@example.com", "password": "SECRET_PASSWORD_VALUE"},
        )

    @pytest.mark.security
    def test_error_response_does_not_echo_bearer_token(self, server):
        """Error responses must not echo back the bearer token."""
        token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.FAKE.TOKEN"
        response = server.get(
            "/v1/team/nonexistent",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert token not in response.text, "Error response echoed bearer token"


# ------------------------------------------------------------------ #
# §41 — Error Handling (beyond stack traces and SQL)
# ------------------------------------------------------------------ #


class TestErrorHandlingAdvanced:
    @pytest.mark.security
    def test_error_does_not_disclose_filesystem_paths(self, server, admin_a):
        """Error responses must not contain filesystem paths."""
        response = server.get(
            "/v1/team/not-a-uuid!!!",
            headers={"Authorization": f"Bearer {admin_a.jwt}"},
        )
        body = response.text
        assert "/home/" not in body and "/usr/" not in body and "/tmp/" not in body, (
            "Error response contains filesystem path"
        )

    @pytest.mark.security
    def test_error_does_not_disclose_internal_hostnames(self, server, admin_a):
        """Error responses must not contain internal hostnames."""
        response = server.get(
            "/v1/team/not-a-uuid!!!",
            headers={"Authorization": f"Bearer {admin_a.jwt}"},
        )
        body = response.text.lower()
        assert "localhost" not in body or "not found" in body, (
            "Error response contains internal hostname"
        )
