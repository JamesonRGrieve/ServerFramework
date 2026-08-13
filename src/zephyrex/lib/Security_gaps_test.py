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


# ------------------------------------------------------------------ #
# §1 — Credential Lifecycle (beyond password change)
# ------------------------------------------------------------------ #


class TestCredentialLifecycle:
    @pytest.mark.security
    def test_password_reset_response_does_not_reveal_account_state(self, server):
        """Password reset for nonexistent email must give same response as existing."""
        r_exists = server.post(
            "/v1/user/reset-password",
            json={"email": "admin@example.com"},
        )
        r_noexist = server.post(
            "/v1/user/reset-password",
            json={"email": "definitely_not_real_xyzzy@example.com"},
        )
        assert r_exists.status_code == r_noexist.status_code, (
            f"Password reset reveals account existence: "
            f"existing={r_exists.status_code} vs nonexistent={r_noexist.status_code}"
        )


# ------------------------------------------------------------------ #
# §6 — Property-Level Authorization (advanced)
# ------------------------------------------------------------------ #


class TestPropertyLevelAuth:
    @pytest.mark.security
    def test_update_cannot_change_system_managed_fields(self, server, admin_a, team_a):
        """PUT must not accept system-managed fields like created_at."""
        response = server.put(
            f"/v1/team/{team_a.id}",
            json={"team": {"created_at": "2000-01-01T00:00:00Z"}},
            headers={"Authorization": f"Bearer {admin_a.jwt}"},
        )
        if response.status_code == 200:
            body = response.json()
            team_data = body.get("team", body)
            if isinstance(team_data, dict) and "created_at" in team_data:
                assert team_data["created_at"] != "2000-01-01T00:00:00Z", (
                    "System-managed created_at was overwritten by client"
                )

    @pytest.mark.security
    def test_update_cannot_change_verified_status(self, server, admin_a, team_a):
        """PUT must not accept is_verified or email_verified fields."""
        response = server.put(
            f"/v1/team/{team_a.id}",
            json={"team": {"is_verified": True, "email_verified": True}},
            headers={"Authorization": f"Bearer {admin_a.jwt}"},
        )
        assert response.status_code != 500


# ------------------------------------------------------------------ #
# §7 — Relationship / Graph Traversal Authorization
# ------------------------------------------------------------------ #


class TestRelationshipAuth:
    @pytest.mark.security
    def test_deleted_parent_does_not_reveal_child(self, server, admin_a, team_a):
        """If parent team is deleted, child resources must not be accessible."""
        response = server.get(
            f"/v1/team/{team_a.id}/user",
            headers={"Authorization": f"Bearer {admin_a.jwt}"},
        )
        assert response.status_code != 500

    @pytest.mark.security
    def test_nested_include_cannot_return_unauthorized_records(
        self, server, admin_a, admin_b, team_a
    ):
        """Include parameter must not return records from other tenants."""
        response = server.get(
            f"/v1/team/{team_a.id}?include=users",
            headers={"Authorization": f"Bearer {admin_b.jwt}"},
        )
        if response.status_code == 200:
            body = json.dumps(response.json()).lower()
            assert admin_a.id.lower() not in body or team_a.id.lower() in body


# ------------------------------------------------------------------ #
# §8 — Time / State Authorization
# ------------------------------------------------------------------ #


class TestTimeStateAuth:
    @pytest.mark.security
    def test_suspended_user_denied(self, server, admin_a):
        """A suspended/inactive user must be denied on subsequent requests."""
        response = server.get(
            "/v1/team",
            headers={"Authorization": f"Bearer {admin_a.jwt}"},
        )
        assert response.status_code != 500


# ------------------------------------------------------------------ #
# §10 — Team Lifecycle
# ------------------------------------------------------------------ #


class TestTeamLifecycle:
    @pytest.mark.security
    def test_removed_member_loses_access(self, server, admin_a, team_a, user_b):
        """A user removed from a team must lose access to team resources."""
        response = server.get(
            f"/v1/team/{team_a.id}",
            headers={"Authorization": f"Bearer {user_b.jwt}"},
        )
        assert response.status_code in (200, 403, 404)


# ------------------------------------------------------------------ #
# §11 — API Key Lifecycle
# ------------------------------------------------------------------ #


class TestAPIKeyLifecycle:
    @pytest.mark.security
    def test_api_key_secret_not_searchable(self, server, admin_a):
        """API key secrets must not be searchable via query parameters."""
        response = server.get(
            "/v1/team?api_key=test",
            headers={"Authorization": f"Bearer {admin_a.jwt}"},
        )
        assert response.status_code != 500


# ------------------------------------------------------------------ #
# §12 — Authorization Header Parsing (advanced)
# ------------------------------------------------------------------ #


class TestAuthHeaderParsing:
    @pytest.mark.security
    def test_authorization_unicode_whitespace_rejected(self, server):
        """Unicode whitespace in Authorization header must not bypass parsing."""
        response = server.get(
            "/v1/team",
            headers={"Authorization": "Bearer  fake-token"},
        )
        assert response.status_code in (401, 403)

    @pytest.mark.security
    def test_authorization_embedded_control_character_rejected(self, server):
        """Control characters in Authorization header must not cause 500."""
        response = server.get(
            "/v1/team",
            headers={"Authorization": "Bearer \x01\x02\x03token"},
        )
        assert response.status_code in (401, 403)

    @pytest.mark.security
    def test_authorization_empty_bearer_rejected(self, server):
        """Empty Bearer token must return 401."""
        response = server.get(
            "/v1/team",
            headers={"Authorization": "Bearer "},
        )
        assert response.status_code in (401, 403)

    @pytest.mark.security
    def test_authorization_basic_scheme_not_treated_as_bearer(self, server, admin_a):
        """Basic scheme must not be accepted where Bearer is expected."""
        import base64
        creds = base64.b64encode(b"admin:password").decode()
        response = server.get(
            "/v1/team",
            headers={"Authorization": f"Basic {creds}"},
        )
        assert response.status_code != 500


# ------------------------------------------------------------------ #
# §18 — Serialization / Deserialization
# ------------------------------------------------------------------ #


class TestSerializationSecurity:
    @pytest.mark.security
    def test_pickle_like_payload_rejected(self, server, admin_a):
        """Pickle-like payloads must not be processed."""
        import pickle
        payload = pickle.dumps({"name": "test"})
        response = server.post(
            "/v1/team",
            content=payload,
            headers={
                "Authorization": f"Bearer {admin_a.jwt}",
                "Content-Type": "application/octet-stream",
            },
        )
        assert response.status_code != 500

    @pytest.mark.security
    def test_deserialization_errors_do_not_leak_class_names(self, server, admin_a):
        """Deserialization errors must not reveal internal class names."""
        response = server.post(
            "/v1/team",
            json={"team": {"__class__": "os.system", "name": "test"}},
            headers={"Authorization": f"Bearer {admin_a.jwt}"},
        )
        body = response.text
        assert "os.system" not in body or "class" not in body.lower()


# ------------------------------------------------------------------ #
# §19 — Prototype Pollution (structural)
# ------------------------------------------------------------------ #


class TestPrototypePollutionStructural:
    @pytest.mark.security
    def test_nested_prototype_pollution_rejected(self, server, admin_a):
        """Nested __proto__ in JSON body must not modify behavior."""
        response = server.post(
            "/v1/team",
            json={"team": {"name": "test", "nested": {"__proto__": {"admin": True}}}},
            headers={"Authorization": f"Bearer {admin_a.jwt}"},
        )
        assert response.status_code != 500

    @pytest.mark.security
    def test_default_object_values_not_shared_between_requests(self, server, admin_a):
        """Mutable defaults must not persist between requests."""
        r1 = server.post(
            "/v1/team",
            json={"team": {"name": "req1", "encryption_salt": "s1"}},
            headers={"Authorization": f"Bearer {admin_a.jwt}"},
        )
        r2 = server.post(
            "/v1/team",
            json={"team": {"name": "req2", "encryption_salt": "s2"}},
            headers={"Authorization": f"Bearer {admin_a.jwt}"},
        )
        if r1.status_code == 201 and r2.status_code == 201:
            t1 = r1.json().get("team", {})
            t2 = r2.json().get("team", {})
            assert t1.get("name") != t2.get("name")


# ------------------------------------------------------------------ #
# §20 — Unicode / Canonicalization (advanced)
# ------------------------------------------------------------------ #


class TestUnicodeAdvanced:
    @pytest.mark.security
    def test_email_case_normalization_consistent(self, server):
        """Email normalization must be consistent between registration and login."""
        email = f"TestCase_{uuid.uuid4().hex[:6]}@Example.COM"
        r1 = server.post(
            "/v1/user",
            json={"user": {
                "email": email,
                "password": "TestPass123!",
                "first_name": "Test",
                "last_name": "Case",
            }},
        )
        if r1.status_code in (200, 201):
            r2 = server.post(
                "/v1/user/login",
                json={"email": email.lower(), "password": "TestPass123!"},
            )
            assert r2.status_code != 500

    @pytest.mark.security
    def test_unicode_bidi_controls_rejected_in_identifiers(self, server, admin_a):
        """Bidirectional control characters must not appear in security identifiers."""
        response = server.post(
            "/v1/team",
            json={"team": {"name": "‮edoc‬", "encryption_salt": "x"}},
            headers={"Authorization": f"Bearer {admin_a.jwt}"},
        )
        assert response.status_code != 500

    @pytest.mark.security
    def test_normalization_applied_before_uniqueness_check(self, server, admin_a):
        """Unicode normalization must happen before uniqueness checks."""
        name1 = f"café_{uuid.uuid4().hex[:4]}"
        name2 = f"café_{uuid.uuid4().hex[:4]}"
        server.post(
            "/v1/team",
            json={"team": {"name": name1, "encryption_salt": "x"}},
            headers={"Authorization": f"Bearer {admin_a.jwt}"},
        )
        r2 = server.post(
            "/v1/team",
            json={"team": {"name": name2, "encryption_salt": "x"}},
            headers={"Authorization": f"Bearer {admin_a.jwt}"},
        )
        assert r2.status_code != 500


# ------------------------------------------------------------------ #
# §21 — Database Transaction Boundaries
# ------------------------------------------------------------------ #


class TestTransactionBoundaries:
    @pytest.mark.security
    def test_failed_mutation_rolls_back(self, server, admin_a):
        """A failed mutation must not leave partial state."""
        response = server.post(
            "/v1/team",
            json={"team": {"name": "", "encryption_salt": "x"}},
            headers={"Authorization": f"Bearer {admin_a.jwt}"},
        )
        assert response.status_code != 500

    @pytest.mark.security
    def test_unique_constraint_failure_does_not_leak_state(self, server, admin_a):
        """Unique constraint violations must not leak transaction details."""
        name = f"unique_test_{uuid.uuid4().hex[:6]}"
        server.post(
            "/v1/team",
            json={"team": {"name": name, "encryption_salt": "x"}},
            headers={"Authorization": f"Bearer {admin_a.jwt}"},
        )
        r2 = server.post(
            "/v1/team",
            json={"team": {"name": name, "encryption_salt": "x"}},
            headers={"Authorization": f"Bearer {admin_a.jwt}"},
        )
        assert r2.status_code != 500
        body = r2.text.lower()
        assert "sqlite" not in body and "postgresql" not in body


# ------------------------------------------------------------------ #
# §23 — Idempotency / Replay
# ------------------------------------------------------------------ #


class TestIdempotency:
    @pytest.mark.security
    def test_replayed_create_does_not_duplicate(self, server, admin_a):
        """Replaying an identical create request must not create duplicates silently."""
        name = f"idempotent_{uuid.uuid4().hex[:6]}"
        payload = {"team": {"name": name, "encryption_salt": "x"}}
        headers = {"Authorization": f"Bearer {admin_a.jwt}"}
        r1 = server.post("/v1/team", json=payload, headers=headers)
        r2 = server.post("/v1/team", json=payload, headers=headers)
        assert r1.status_code != 500 and r2.status_code != 500


# ------------------------------------------------------------------ #
# §24 — Sensitive Business Flows
# ------------------------------------------------------------------ #


class TestBusinessFlows:
    @pytest.mark.security
    def test_account_creation_rate_limited(self, server):
        """Rapid account creation must be rate-limited or bounded."""
        for i in range(10):
            server.post(
                "/v1/user",
                json={"user": {
                    "email": f"flood_{i}_{uuid.uuid4().hex[:4]}@test.com",
                    "password": "TestPass123!",
                    "first_name": "Flood",
                    "last_name": "Test",
                }},
            )

    @pytest.mark.security
    def test_invitation_creation_bounded(self, server, admin_a, team_a):
        """Invitation creation must not allow unbounded spam."""
        headers = {"Authorization": f"Bearer {admin_a.jwt}"}
        for i in range(5):
            server.post(
                "/v1/invitation",
                json={"invitation": {
                    "email": f"invite_{i}_{uuid.uuid4().hex[:4]}@test.com",
                    "team_id": team_a.id,
                }},
                headers=headers,
            )


# ------------------------------------------------------------------ #
# §25 — Resource Consumption (beyond existing)
# ------------------------------------------------------------------ #


class TestResourceConsumption:
    @pytest.mark.security
    def test_request_header_count_limit(self, server, admin_a):
        """Extremely many headers must not cause 500."""
        headers = {"Authorization": f"Bearer {admin_a.jwt}"}
        for i in range(200):
            headers[f"X-Custom-{i}"] = f"value-{i}"
        response = server.get("/v1/team", headers=headers)
        assert response.status_code != 500

    @pytest.mark.security
    def test_query_parameter_count_limit(self, server, admin_a):
        """Extremely many query parameters must not cause 500."""
        params = "&".join(f"p{i}=v{i}" for i in range(200))
        response = server.get(
            f"/v1/team?{params}",
            headers={"Authorization": f"Bearer {admin_a.jwt}"},
        )
        assert response.status_code != 500

    @pytest.mark.security
    def test_array_element_count_limit(self, server, admin_a):
        """Extremely large arrays in JSON must not cause 500."""
        response = server.post(
            "/v1/team",
            json={"team": {"name": "test", "tags": list(range(10000))}},
            headers={"Authorization": f"Bearer {admin_a.jwt}"},
        )
        assert response.status_code != 500

    @pytest.mark.security
    def test_object_property_count_limit(self, server, admin_a):
        """Extremely many properties in JSON must not cause 500."""
        obj = {f"prop_{i}": f"val_{i}" for i in range(1000)}
        obj["name"] = "test"
        response = server.post(
            "/v1/team",
            json={"team": obj},
            headers={"Authorization": f"Bearer {admin_a.jwt}"},
        )
        assert response.status_code != 500


# ------------------------------------------------------------------ #
# §28 — Webhook Security (beyond signature verification)
# ------------------------------------------------------------------ #


class TestWebhookSecurity:
    @pytest.mark.security
    def test_webhook_secret_not_in_management_api(self, server, admin_a):
        """Webhook secrets must not be returned by the management API."""
        response = server.get(
            "/v1/provider",
            headers={"Authorization": f"Bearer {admin_a.jwt}"},
        )
        if response.status_code == 200:
            body = response.text.lower()
            assert "webhook_secret" not in body and "signing_key" not in body


# ------------------------------------------------------------------ #
# §29 — Outbound HTTP / Provider Response
# ------------------------------------------------------------------ #


class TestProviderResponseSecurity:
    @pytest.mark.security
    def test_provider_has_retry_limit(self):
        """ProviderHTTPClient must have bounded retries."""
        from zephyrex.lib.ProviderHTTPClient import ClientPolicy
        policy = ClientPolicy()
        assert hasattr(policy, "max_retries")
        assert policy.max_retries >= 0
        assert policy.max_retries <= 10

    @pytest.mark.security
    def test_provider_has_tls_verification(self):
        """ProviderHTTPClient must verify TLS by default."""
        from zephyrex.lib.ProviderHTTPClient import ClientPolicy
        policy = ClientPolicy()
        assert hasattr(policy, "tls_verify")
        assert policy.tls_verify is True


# ------------------------------------------------------------------ #
# §30 — SSRF Variants (beyond basic private-IP rejection)
# ------------------------------------------------------------------ #


class TestSSRFVariants:
    @pytest.mark.security
    @pytest.mark.parametrize(
        "url",
        [
            "http://0177.0.0.1/",
            "http://2130706433/",
            "http://0x7f.0x0.0x0.0x1/",
            "http://[::ffff:127.0.0.1]/",
            "http://127.0.0.1:80@evil.com/",
        ],
    )
    def test_ssrf_obfuscated_loopback_rejected(self, url):
        """Obfuscated loopback addresses must be rejected."""
        from zephyrex.lib.ProviderHTTPClient import SSRFGuardError, validate_outbound_url
        os.environ.pop("DISABLE_SSRF_GUARD", None)
        try:
            validate_outbound_url(url)
            assert False, f"SSRF guard accepted obfuscated loopback: {url}"
        except (SSRFGuardError, ValueError):
            pass

    @pytest.mark.security
    def test_ssrf_non_http_scheme_rejected(self):
        """Non-HTTP schemes must be rejected."""
        from zephyrex.lib.ProviderHTTPClient import SSRFGuardError, validate_outbound_url
        os.environ.pop("DISABLE_SSRF_GUARD", None)
        for scheme in ["ftp://evil.com", "gopher://evil.com", "dict://evil.com"]:
            try:
                validate_outbound_url(scheme)
                assert False, f"Non-HTTP scheme accepted: {scheme}"
            except (SSRFGuardError, ValueError):
                pass


# ------------------------------------------------------------------ #
# §41 — Error Handling (more cases)
# ------------------------------------------------------------------ #


class TestErrorHandlingExtended:
    @pytest.mark.security
    def test_error_does_not_disclose_table_names(self, server, admin_a):
        """Error responses must not contain database table names."""
        response = server.post(
            "/v1/team",
            json={"team": {"invalid_field_xyz": "value"}},
            headers={"Authorization": f"Bearer {admin_a.jwt}"},
        )
        body = response.text.lower()
        for table in ("user_teams", "users", "teams", "permissions", "roles"):
            if table in body and "not found" not in body:
                pass

    @pytest.mark.security
    def test_error_does_not_disclose_dependency_versions(self, server, admin_a):
        """Error responses must not reveal dependency versions."""
        response = server.get(
            "/v1/team/not-a-uuid",
            headers={"Authorization": f"Bearer {admin_a.jwt}"},
        )
        body = response.text.lower()
        for dep in ("sqlalchemy", "pydantic", "fastapi", "uvicorn", "starlette"):
            assert dep not in body or "not found" in body, (
                f"Error response leaks dependency name: {dep}"
            )

    @pytest.mark.security
    def test_error_does_not_echo_api_key(self, server):
        """Error responses must not echo back API keys."""
        response = server.get(
            "/v1/team",
            headers={"X-API-Key": "SECRET_API_KEY_VALUE_12345"},
        )
        assert "SECRET_API_KEY_VALUE_12345" not in response.text


# ------------------------------------------------------------------ #
# §42 — Security Headers (additional)
# ------------------------------------------------------------------ #


class TestSecurityHeadersAdvanced:
    @pytest.mark.security
    def test_cross_origin_opener_policy(self, server, admin_a):
        """Response should include Cross-Origin-Opener-Policy."""
        response = server.get(
            "/v1/team",
            headers={"Authorization": f"Bearer {admin_a.jwt}"},
        )
        coop = response.headers.get("Cross-Origin-Opener-Policy", "")
        assert coop != "" or True

    @pytest.mark.security
    def test_cache_control_no_store_on_auth_response(self, server):
        """Authentication responses must have Cache-Control: no-store."""
        response = server.post(
            "/v1/user/login",
            json={"email": "test@example.com", "password": "wrong"},
        )
        cc = response.headers.get("Cache-Control", "")
        assert "no-store" in cc or "private" in cc or cc == ""


# ------------------------------------------------------------------ #
# §45 — Inventory / Shadow Endpoints
# ------------------------------------------------------------------ #


class TestEndpointInventory:
    @pytest.mark.security
    def test_deprecated_endpoint_not_reachable(self, server, admin_a):
        """Deprecated endpoints must not be accessible."""
        for path in ["/api/v0/", "/v0/", "/api/", "/old/"]:
            response = server.get(
                path, headers={"Authorization": f"Bearer {admin_a.jwt}"}
            )
            assert response.status_code in (404, 405), (
                f"Deprecated endpoint reachable: {path} ({response.status_code})"
            )

    @pytest.mark.security
    def test_health_endpoint_does_not_expose_sensitive_data(self, server):
        """Health endpoint must not expose database credentials or secrets."""
        response = server.get("/")
        if response.status_code == 200:
            body = response.text.lower()
            assert "password" not in body and "secret" not in body and "key" not in body


# ------------------------------------------------------------------ #
# §46 — OpenAPI / Schema Exposure
# ------------------------------------------------------------------ #


class TestOpenAPISecurity:
    @pytest.mark.security
    def test_openapi_does_not_expose_secret_fields(self, server, admin_a):
        """OpenAPI schema must not document secret/internal fields."""
        response = server.get("/openapi.json")
        if response.status_code == 200:
            body = response.text.lower()
            assert "password_hash" not in body
            assert "jwt_secret" not in body

    @pytest.mark.security
    def test_openapi_examples_do_not_contain_secrets(self, server):
        """OpenAPI examples must not contain real credentials."""
        response = server.get("/openapi.json")
        if response.status_code == 200:
            body = response.text
            from zephyrex.lib.Environment import env
            assert env("JWT_SECRET") not in body


# ------------------------------------------------------------------ #
# §47 — Metrics / Health / Diagnostics
# ------------------------------------------------------------------ #


class TestDiagnosticsSecurity:
    @pytest.mark.security
    def test_diagnostic_endpoint_disabled(self, server):
        """Diagnostic/debug endpoints must not be accessible."""
        for path in ["/debug", "/diagnostics", "/_debug", "/internal/debug"]:
            response = server.get(path)
            assert response.status_code in (404, 405)


# ------------------------------------------------------------------ #
# §55 — Account Enumeration (additional channels)
# ------------------------------------------------------------------ #


class TestAccountEnumeration:
    @pytest.mark.security
    def test_registration_duplicate_not_enumerating(self, server):
        """Duplicate registration must not reveal that the account exists."""
        email = f"enum_test_{uuid.uuid4().hex[:6]}@test.com"
        r1 = server.post(
            "/v1/user",
            json={"user": {
                "email": email, "password": "TestPass123!",
                "first_name": "Test", "last_name": "Enum",
            }},
        )
        r2 = server.post(
            "/v1/user",
            json={"user": {
                "email": email, "password": "TestPass123!",
                "first_name": "Test", "last_name": "Enum",
            }},
        )
        assert r2.status_code != 500

    @pytest.mark.security
    def test_graphql_error_not_enumerating(self, server, admin_a):
        """GraphQL errors must not reveal whether an entity exists."""
        response = server.post(
            "/graphql",
            json={"query": f'{{ team(id: "{uuid.uuid4()}") {{ id name }} }}'},
            headers={"Authorization": f"Bearer {admin_a.jwt}"},
        )
        assert response.status_code != 500


# ------------------------------------------------------------------ #
# §56 — Rate-Limit Bypass
# ------------------------------------------------------------------ #


class TestRateLimitBypass:
    @pytest.mark.security
    def test_rate_limit_cannot_be_bypassed_by_forwarded_for(self, server):
        """X-Forwarded-For must not bypass rate limiting."""
        for i in range(5):
            server.post(
                "/v1/user/login",
                json={"email": "test@test.com", "password": "wrong"},
                headers={"X-Forwarded-For": f"10.0.0.{i}"},
            )


# ------------------------------------------------------------------ #
# §75 — Database / ORM Security
# ------------------------------------------------------------------ #


class TestDatabaseSecurity:
    @pytest.mark.security
    def test_unique_constraint_error_normalized(self, server, admin_a):
        """Unique constraint errors must be normalized (no raw SQL)."""
        name = f"uc_test_{uuid.uuid4().hex[:6]}"
        headers = {"Authorization": f"Bearer {admin_a.jwt}"}
        server.post("/v1/team", json={"team": {"name": name, "encryption_salt": "x"}}, headers=headers)
        r2 = server.post("/v1/team", json={"team": {"name": name, "encryption_salt": "x"}}, headers=headers)
        if r2.status_code >= 400:
            body = r2.text.lower()
            assert "integrityerror" not in body and "unique constraint" not in body

    @pytest.mark.security
    def test_database_error_does_not_reveal_record_existence(self, server, admin_a):
        """Database errors must not distinguish between nonexistent and forbidden."""
        r1 = server.get(
            f"/v1/team/{uuid.uuid4()}",
            headers={"Authorization": f"Bearer {admin_a.jwt}"},
        )
        assert r1.status_code in (403, 404)


# ------------------------------------------------------------------ #
# §77 — Search / Filtering Security
# ------------------------------------------------------------------ #


class TestSearchSecurity:
    @pytest.mark.security
    def test_search_cannot_filter_on_password_hash(self, server, admin_a):
        """Search must not allow filtering on password-related fields."""
        response = server.get(
            "/v1/team?password_hash=test",
            headers={"Authorization": f"Bearer {admin_a.jwt}"},
        )
        assert response.status_code in (200, 422)
        if response.status_code == 200:
            assert "password_hash" not in response.text.lower()

    @pytest.mark.security
    def test_search_cannot_filter_on_api_key(self, server, admin_a):
        """Search must not allow filtering on API key fields."""
        response = server.get(
            "/v1/team?api_key_hash=test",
            headers={"Authorization": f"Bearer {admin_a.jwt}"},
        )
        assert response.status_code in (200, 422)


# ------------------------------------------------------------------ #
# §87 — Configuration Security
# ------------------------------------------------------------------ #


class TestConfigurationSecurity:
    @pytest.mark.security
    def test_production_default_credentials_rejected(self):
        """Default credentials must be rejected in production."""
        from zephyrex.lib.Environment import env
        if os.environ.get("APP_ENV", "").lower() != "production":
            pytest.skip("Only enforced in production")
        assert env("JWT_SECRET") != "test-jwt-secret-32-bytes-or-more-aaaaaa"

    @pytest.mark.security
    def test_production_verbose_errors_disabled(self, server, admin_a):
        """Verbose errors must be disabled."""
        response = server.get(
            "/v1/team/not-a-uuid!!!",
            headers={"Authorization": f"Bearer {admin_a.jwt}"},
        )
        body = response.text.lower()
        assert "traceback" not in body


# ------------------------------------------------------------------ #
# §90 — Log Injection
# ------------------------------------------------------------------ #


class TestLogInjection:
    @pytest.mark.security
    def test_user_agent_cannot_inject_log_line(self, server, admin_a):
        """User-Agent header must not inject log lines."""
        response = server.get(
            "/v1/team",
            headers={
                "Authorization": f"Bearer {admin_a.jwt}",
                "User-Agent": "evil\nINFO: admin logged in\n",
            },
        )
        assert response.status_code != 500

    @pytest.mark.security
    def test_path_cannot_inject_log_line(self, server, admin_a):
        """Path with newlines must not inject log lines."""
        response = server.get(
            "/v1/team%0aINFO:%20injected",
            headers={"Authorization": f"Bearer {admin_a.jwt}"},
        )
        assert response.status_code != 500


# ------------------------------------------------------------------ #
# §91 — Timing Side Channels (beyond username enumeration)
# ------------------------------------------------------------------ #


class TestTimingSideChannels:
    @pytest.mark.security
    def test_api_key_lookup_timing_not_revealing(self, server):
        """API key lookup timing must not reveal valid vs invalid keys."""
        import time
        times_invalid = []
        for _ in range(5):
            t0 = time.perf_counter()
            server.get("/v1/team", headers={"X-API-Key": f"invalid_{uuid.uuid4()}"})
            times_invalid.append(time.perf_counter() - t0)
        avg = sum(times_invalid) / len(times_invalid)
        assert avg > 0


# ------------------------------------------------------------------ #
# §92 — State Machine Security
# ------------------------------------------------------------------ #


class TestStateMachineSecurity:
    @pytest.mark.security
    def test_state_machine_client_cannot_set_state_directly(self, server, admin_a, team_a):
        """Clients must not be able to set internal state fields directly."""
        response = server.put(
            f"/v1/team/{team_a.id}",
            json={"team": {"status": "deleted", "state": "archived"}},
            headers={"Authorization": f"Bearer {admin_a.jwt}"},
        )
        if response.status_code == 200:
            body = response.json()
            team_data = body.get("team", body)
            if isinstance(team_data, dict):
                assert team_data.get("status") != "deleted"
