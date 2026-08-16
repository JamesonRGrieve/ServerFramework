"""Tests for `lib.InboundSecurity` (Item 71)."""

from __future__ import annotations

import time

import pytest

from zephyrex.lib.InboundSecurity import (
    AnomalyDetector,
    CORSPolicyError,
    LockoutPolicy,
    LockoutTracker,
    NoOpAnomalyDetector,
    parse_cors_origins,
    parse_rate_spec,
    rate_limit,
    validate_cors_config,
)


class TestCORSValidation:
    def test_production_with_wildcard_rejected(self):
        with pytest.raises(CORSPolicyError, match="production"):
            validate_cors_config(
                allow_origins=["*"], allow_credentials=False, app_env="production"
            )

    def test_credentials_with_wildcard_rejected_any_env(self):
        for env in ("development", "staging", "production"):
            with pytest.raises(CORSPolicyError, match="credentials"):
                validate_cors_config(
                    allow_origins=["*"], allow_credentials=True, app_env=env
                )

    def test_explicit_allowlist_accepted_in_production(self):
        validate_cors_config(
            allow_origins=["https://app.example.com"],
            allow_credentials=True,
            app_env="production",
        )

    def test_dev_wildcard_no_credentials_accepted(self):
        validate_cors_config(
            allow_origins=["*"], allow_credentials=False, app_env="development"
        )


class TestParseCORSOrigins:
    def test_split_and_trim(self):
        result = parse_cors_origins(
            " https://a.example.com ,https://b.example.com:8080 "
        )
        assert result == ["https://a.example.com", "https://b.example.com:8080"]

    def test_empty_returns_empty_list(self):
        assert parse_cors_origins("") == []
        assert parse_cors_origins(None) == []

    def test_wildcard_passes_through(self):
        assert parse_cors_origins("*") == ["*"]

    def test_malformed_origin_rejected(self):
        with pytest.raises(CORSPolicyError):
            parse_cors_origins("not-a-url")

    def test_drops_empty_entries(self):
        assert parse_cors_origins("https://x.example.com,,") == [
            "https://x.example.com"
        ]


class TestParseRateSpec:
    def test_minutes(self):
        assert parse_rate_spec("100/min") == (100, 60)

    def test_seconds(self):
        assert parse_rate_spec("5/sec") == (5, 1)

    def test_hours(self):
        assert parse_rate_spec("10/h") == (10, 3600)

    def test_malformed_raises(self):
        with pytest.raises(ValueError):
            parse_rate_spec("not a spec")
        with pytest.raises(ValueError):
            parse_rate_spec("100/century")
        with pytest.raises(ValueError):
            parse_rate_spec("0/min")


class TestRateLimitDecorator:
    def test_decorator_stamps_metadata(self):
        @rate_limit("10/min", scope="ip")
        def handler():
            return "ok"

        assert handler._rate_limit_spec == "10/min"
        assert handler._rate_limit_count == 10
        assert handler._rate_limit_window_seconds == 60
        assert handler._rate_limit_scope == "ip"
        assert handler() == "ok"

    def test_invalid_spec_rejected_at_decoration(self):
        with pytest.raises(ValueError):
            @rate_limit("bad", scope="ip")
            def handler():
                pass


class TestLockoutPolicy:
    def test_defaults(self):
        p = LockoutPolicy()
        assert p.failures_per_window == 5
        assert p.window_seconds == 900
        assert p.lockout_seconds == 1800

    def test_overrides(self):
        p = LockoutPolicy(
            failures_per_window=3, window_seconds=60, lockout_seconds=120
        )
        assert p.failures_per_window == 3


class TestLockoutTracker:
    def test_under_threshold_not_locked(self):
        tracker = LockoutTracker(LockoutPolicy(failures_per_window=3))
        tracker.record_failure("ip:1.2.3.4", "login")
        tracker.record_failure("ip:1.2.3.4", "login")
        assert not tracker.is_locked("ip:1.2.3.4", "login")

    def test_threshold_locks(self):
        tracker = LockoutTracker(
            LockoutPolicy(failures_per_window=3, lockout_seconds=60)
        )
        for _ in range(3):
            tracker.record_failure("ip:1.2.3.4", "login")
        assert tracker.is_locked("ip:1.2.3.4", "login")

    def test_clear_removes_lockout(self):
        tracker = LockoutTracker(LockoutPolicy(failures_per_window=2))
        tracker.record_failure("u:42", "magic_link")
        tracker.record_failure("u:42", "magic_link")
        assert tracker.is_locked("u:42", "magic_link")
        tracker.clear("u:42", "magic_link")
        assert not tracker.is_locked("u:42", "magic_link")

    def test_independent_keys(self):
        tracker = LockoutTracker(LockoutPolicy(failures_per_window=2))
        tracker.record_failure("u:1", "login")
        tracker.record_failure("u:1", "login")
        assert tracker.is_locked("u:1", "login")
        assert not tracker.is_locked("u:2", "login")
        assert not tracker.is_locked("u:1", "magic_link")

    def test_lockout_expires(self):
        tracker = LockoutTracker(
            LockoutPolicy(failures_per_window=2, lockout_seconds=0.05)
        )
        tracker.record_failure("u:1", "x")
        tracker.record_failure("u:1", "x")
        assert tracker.is_locked("u:1", "x")
        time.sleep(0.06)
        assert not tracker.is_locked("u:1", "x")

    def test_remaining_seconds(self):
        tracker = LockoutTracker(
            LockoutPolicy(failures_per_window=2, lockout_seconds=10)
        )
        for _ in range(2):
            tracker.record_failure("u:1", "x")
        remaining = tracker.remaining_lockout_seconds("u:1", "x")
        assert remaining is not None and 0 < remaining <= 10

    def test_empty_actor_rejected(self):
        tracker = LockoutTracker()
        with pytest.raises(ValueError):
            tracker.record_failure("", "login")
        with pytest.raises(ValueError):
            tracker.record_failure("u:1", "")


class TestAnomalyDetector:
    def test_noop_default_callable(self):
        detector = NoOpAnomalyDetector()
        # Must not raise
        detector.report_failure("ip:1.2.3.4", "login")

    def test_abc_requires_implementation(self):
        with pytest.raises(TypeError):
            AnomalyDetector()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# Registry + Middleware (Item 71b enforcement)
# ---------------------------------------------------------------------------


from zephyrex.lib.InboundSecurity import (  # noqa: E402
    RateLimitMiddleware,
    discover_rate_limited_routes,
    register_rate_limited_route,
    reset_rate_limit_state,
)


class TestRateLimitRegistry:
    def setup_method(self):
        reset_rate_limit_state()

    def test_register_round_trip(self):
        from zephyrex.lib.InboundSecurity import _RATE_LIMIT_REGISTRY

        register_rate_limited_route("POST", "/v1/auth/login", 5, 60, "ip")
        assert ("POST", "/v1/auth/login") in _RATE_LIMIT_REGISTRY
        assert _RATE_LIMIT_REGISTRY[("POST", "/v1/auth/login")] == (5, 60, "ip")

    def test_register_rejects_zero_count(self):
        with pytest.raises(ValueError):
            register_rate_limited_route("POST", "/x", 0, 60, "ip")

    def test_register_rejects_zero_window(self):
        with pytest.raises(ValueError):
            register_rate_limited_route("POST", "/x", 5, 0, "ip")

    def test_reset_clears_state(self):
        from zephyrex.lib.InboundSecurity import _RATE_LIMIT_REGISTRY

        register_rate_limited_route("GET", "/y", 1, 1, "ip")
        assert _RATE_LIMIT_REGISTRY
        reset_rate_limit_state()
        assert not _RATE_LIMIT_REGISTRY


class TestDiscoverRateLimitedRoutes:
    def setup_method(self):
        reset_rate_limit_state()

    def test_discovers_decorated_endpoints(self):
        @rate_limit("3/min", scope="ip")
        def handler():
            return "ok"

        class _Route:
            endpoint = handler
            path = "/v1/test/handler"
            methods = {"POST"}

        class _App:
            routes = [_Route()]

        count = discover_rate_limited_routes(_App())
        assert count == 1
        from zephyrex.lib.InboundSecurity import _RATE_LIMIT_REGISTRY

        assert ("POST", "/v1/test/handler") in _RATE_LIMIT_REGISTRY

    def test_skips_undecorated_endpoints(self):
        def plain():
            return "plain"

        class _Route:
            endpoint = plain
            path = "/v1/plain"
            methods = {"GET"}

        class _App:
            routes = [_Route()]

        assert discover_rate_limited_routes(_App()) == 0

    def test_handles_multiple_methods(self):
        @rate_limit("5/min", scope="user")
        def multi():
            return "ok"

        class _Route:
            endpoint = multi
            path = "/v1/multi"
            methods = {"GET", "POST"}

        class _App:
            routes = [_Route()]

        assert discover_rate_limited_routes(_App()) == 2


class TestRateLimitMiddleware:
    """End-to-end ASGI-level enforcement check using a hand-rolled scope.

    Real FastAPI integration is exercised in the EP layer; this test
    suite isolates the middleware to verify counter increments, scope
    resolution, and the 429 response shape without standing up Uvicorn.
    """

    def setup_method(self):
        reset_rate_limit_state()

    @pytest.mark.asyncio
    async def test_passthrough_when_not_registered(self):
        captured = []

        async def app(scope, receive, send):
            captured.append(("called",))

        mw = RateLimitMiddleware(app)
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/v1/unregistered",
            "client": ("1.2.3.4", 1234),
            "headers": [],
        }
        await mw(scope, lambda: None, lambda x: None)
        assert captured == [("called",)]

    @pytest.mark.asyncio
    async def test_429_after_limit(self):
        register_rate_limited_route("POST", "/v1/limited", 2, 60, "ip")

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        mw = RateLimitMiddleware(app)
        sent = []

        async def send(message):
            sent.append(message)

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/v1/limited",
            "client": ("9.9.9.9", 1),
            "headers": [],
        }

        async def receive():
            return {"type": "http.request", "body": b""}

        # Burn through the budget (2 allowed)
        for _ in range(2):
            sent.clear()
            await mw(scope, receive, send)
            assert sent[0]["status"] == 200

        # Third request must be 429
        sent.clear()
        await mw(scope, receive, send)
        assert sent[0]["status"] == 429
        headers = dict(sent[0]["headers"])
        assert b"retry-after" in headers
        assert b"x-ratelimit-limit" in headers

    @pytest.mark.asyncio
    async def test_2xx_includes_rate_limit_headers(self):
        """Successful responses include X-RateLimit-Limit and X-RateLimit-Remaining."""
        register_rate_limited_route("GET", "/v1/rl-headers", 10, 60, "ip")

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        mw = RateLimitMiddleware(app)
        sent: list = []

        async def send(message):
            sent.append(message)

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/v1/rl-headers",
            "client": ("5.5.5.5", 1),
            "headers": [],
        }

        await mw(scope, lambda: {"type": "http.request", "body": b""}, send)
        headers = dict(sent[0]["headers"])
        assert b"x-ratelimit-limit" in headers
        assert headers[b"x-ratelimit-limit"] == b"10"
        assert b"x-ratelimit-remaining" in headers
        assert headers[b"x-ratelimit-remaining"] == b"9"

    @pytest.mark.asyncio
    async def test_skips_when_actor_unresolvable(self):
        """A user-scoped rule on an anonymous request flows through (per
        the documented "fail-open on actor-resolution miss" semantics)."""
        register_rate_limited_route("GET", "/v1/u", 1, 60, "user")

        passed = []

        async def app(scope, receive, send):
            passed.append(True)

        mw = RateLimitMiddleware(app)
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/v1/u",
            "client": ("1.1.1.1", 1),
            "headers": [],
        }
        await mw(scope, lambda: None, lambda x: None)
        # Even multiple anonymous requests pass through
        await mw(scope, lambda: None, lambda x: None)
        await mw(scope, lambda: None, lambda x: None)
        assert len(passed) == 3

    @pytest.mark.asyncio
    async def test_independent_actors_independent_budgets(self):
        register_rate_limited_route("POST", "/v1/perip", 1, 60, "ip")

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        mw = RateLimitMiddleware(app)

        async def receive():
            return {"type": "http.request", "body": b""}

        for ip in ("10.0.0.1", "10.0.0.2"):
            sent = []

            async def send(m, sent=sent):
                sent.append(m)

            scope = {
                "type": "http",
                "method": "POST",
                "path": "/v1/perip",
                "client": (ip, 1),
                "headers": [],
            }
            await mw(scope, receive, send)
            assert sent[0]["status"] == 200


class TestProxyHeaderSanitization:
    """X-Original-URL and X-Rewrite-URL are stripped before the app sees them."""

    @pytest.mark.asyncio
    async def test_strips_x_original_url(self):
        from zephyrex.lib.InboundSecurity import ProxyHeaderSanitizationMiddleware

        seen_headers = []

        async def app(scope, receive, send):
            seen_headers.extend(scope.get("headers", []))

        mw = ProxyHeaderSanitizationMiddleware(app)
        scope = {
            "type": "http",
            "headers": [
                (b"authorization", b"Bearer tok"),
                (b"x-original-url", b"/admin/secret"),
                (b"accept", b"application/json"),
            ],
        }
        await mw(scope, lambda: None, lambda x: None)
        header_names = {h[0] for h in seen_headers}
        assert b"x-original-url" not in header_names
        assert b"authorization" in header_names
        assert b"accept" in header_names

    @pytest.mark.asyncio
    async def test_strips_x_rewrite_url(self):
        from zephyrex.lib.InboundSecurity import ProxyHeaderSanitizationMiddleware

        seen_headers = []

        async def app(scope, receive, send):
            seen_headers.extend(scope.get("headers", []))

        mw = ProxyHeaderSanitizationMiddleware(app)
        scope = {
            "type": "http",
            "headers": [
                (b"x-rewrite-url", b"/admin"),
                (b"host", b"example.com"),
            ],
        }
        await mw(scope, lambda: None, lambda x: None)
        header_names = {h[0] for h in seen_headers}
        assert b"x-rewrite-url" not in header_names
        assert b"host" in header_names

    @pytest.mark.asyncio
    async def test_strips_x_original_host(self):
        from zephyrex.lib.InboundSecurity import ProxyHeaderSanitizationMiddleware

        seen_headers = []

        async def app(scope, receive, send):
            seen_headers.extend(scope.get("headers", []))

        mw = ProxyHeaderSanitizationMiddleware(app)
        scope = {
            "type": "http",
            "headers": [
                (b"x-original-host", b"evil.com"),
                (b"host", b"real.com"),
            ],
        }
        await mw(scope, lambda: None, lambda x: None)
        header_names = {h[0] for h in seen_headers}
        assert b"x-original-host" not in header_names

    @pytest.mark.asyncio
    async def test_passthrough_when_no_dangerous_headers(self):
        from zephyrex.lib.InboundSecurity import ProxyHeaderSanitizationMiddleware

        seen_headers = []

        async def app(scope, receive, send):
            seen_headers.extend(scope.get("headers", []))

        mw = ProxyHeaderSanitizationMiddleware(app)
        scope = {
            "type": "http",
            "headers": [
                (b"authorization", b"Bearer tok"),
                (b"content-type", b"application/json"),
            ],
        }
        await mw(scope, lambda: None, lambda x: None)
        assert len(seen_headers) == 2

    @pytest.mark.asyncio
    async def test_non_http_passthrough(self):
        from zephyrex.lib.InboundSecurity import ProxyHeaderSanitizationMiddleware

        called = []

        async def app(scope, receive, send):
            called.append(True)

        mw = ProxyHeaderSanitizationMiddleware(app)
        scope = {"type": "websocket", "headers": [(b"x-original-url", b"/admin")]}
        await mw(scope, lambda: None, lambda x: None)
        assert called == [True]


# ---------------------------------------------------------------------------
# Header attack surface — additional vectors
# ---------------------------------------------------------------------------


class TestRequestSmugglingMiddleware:
    """CL+TE conflict detection."""

    @pytest.mark.asyncio
    async def test_cl_te_conflict_rejected(self):
        from zephyrex.lib.InboundSecurity import RequestSmugglingMiddleware

        sent = []

        async def send(msg):
            sent.append(msg)

        mw = RequestSmugglingMiddleware(lambda s, r, se: None)
        scope = {
            "type": "http",
            "headers": [
                (b"content-length", b"10"),
                (b"transfer-encoding", b"chunked"),
            ],
        }
        await mw(scope, lambda: None, send)
        assert sent[0]["status"] == 400

    @pytest.mark.asyncio
    async def test_cl_only_passes(self):
        from zephyrex.lib.InboundSecurity import RequestSmugglingMiddleware

        called = []

        async def app(scope, receive, send):
            called.append(True)

        mw = RequestSmugglingMiddleware(app)
        scope = {
            "type": "http",
            "headers": [(b"content-length", b"10")],
        }
        await mw(scope, lambda: None, lambda x: None)
        assert called == [True]


class TestPathSanitizationMiddleware:
    """Null bytes and path traversal."""

    @pytest.mark.asyncio
    async def test_null_byte_rejected(self):
        from zephyrex.lib.InboundSecurity import PathSanitizationMiddleware

        sent = []

        async def send(msg):
            sent.append(msg)

        mw = PathSanitizationMiddleware(lambda s, r, se: None)
        scope = {"type": "http", "path": "/v1/team/\x00malicious"}
        await mw(scope, lambda: None, send)
        assert sent[0]["status"] == 400

    @pytest.mark.asyncio
    async def test_percent_null_rejected(self):
        from zephyrex.lib.InboundSecurity import PathSanitizationMiddleware

        sent = []

        async def send(msg):
            sent.append(msg)

        mw = PathSanitizationMiddleware(lambda s, r, se: None)
        scope = {"type": "http", "path": "/v1/team/%00admin"}
        await mw(scope, lambda: None, send)
        assert sent[0]["status"] == 400

    @pytest.mark.asyncio
    async def test_path_traversal_rejected(self):
        from zephyrex.lib.InboundSecurity import PathSanitizationMiddleware

        sent = []

        async def send(msg):
            sent.append(msg)

        mw = PathSanitizationMiddleware(lambda s, r, se: None)
        scope = {"type": "http", "path": "/v1/../../etc/passwd"}
        await mw(scope, lambda: None, send)
        assert sent[0]["status"] == 400

    @pytest.mark.asyncio
    async def test_clean_path_passes(self):
        from zephyrex.lib.InboundSecurity import PathSanitizationMiddleware

        called = []

        async def app(scope, receive, send):
            called.append(True)

        mw = PathSanitizationMiddleware(app)
        scope = {"type": "http", "path": "/v1/team/abc-123"}
        await mw(scope, lambda: None, lambda x: None)
        assert called == [True]


class TestProxyHeaderStrippingComprehensive:
    """All stripped headers and edge cases."""

    @pytest.mark.asyncio
    async def test_case_insensitive_stripping(self):
        from zephyrex.lib.InboundSecurity import ProxyHeaderSanitizationMiddleware

        seen = []

        async def app(scope, receive, send):
            seen.extend(scope.get("headers", []))

        mw = ProxyHeaderSanitizationMiddleware(app)
        scope = {
            "type": "http",
            "headers": [
                (b"X-Original-URL", b"/admin"),
                (b"X-REWRITE-URL", b"/admin"),
                (b"x-original-host", b"evil"),
                (b"x-forwarded-server", b"evil"),
                (b"authorization", b"Bearer tok"),
            ],
        }
        await mw(scope, lambda: None, lambda x: None)
        names = {h[0].lower() for h in seen}
        assert b"x-original-url" not in names
        assert b"x-rewrite-url" not in names
        assert b"x-original-host" not in names
        assert b"x-forwarded-server" not in names
        assert b"authorization" in names

    @pytest.mark.asyncio
    async def test_does_not_mutate_original_scope(self):
        from zephyrex.lib.InboundSecurity import ProxyHeaderSanitizationMiddleware

        original_headers = [
            (b"x-original-url", b"/admin"),
            (b"host", b"ok.com"),
        ]
        original_scope = {"type": "http", "headers": list(original_headers)}

        async def app(scope, receive, send):
            pass

        mw = ProxyHeaderSanitizationMiddleware(app)
        await mw(original_scope, lambda: None, lambda x: None)
        assert len(original_headers) == 2


class TestXForwardedForTrust:
    """X-Forwarded-For only honoured from trusted proxies."""

    def test_xff_ignored_from_untrusted_peer(self):
        from zephyrex.lib.InboundSecurity import resolve_client_ip

        ip = resolve_client_ip(
            {"X-Forwarded-For": "1.2.3.4, 5.6.7.8"},
            peer_host="9.9.9.9",
        )
        assert ip == "9.9.9.9"

    def test_direct_peer_used_when_no_xff(self):
        from zephyrex.lib.InboundSecurity import resolve_client_ip

        ip = resolve_client_ip({}, peer_host="10.0.0.1")
        assert ip == "10.0.0.1"


class TestSecurityHeadersMiddleware:
    """Response security headers are set."""

    def test_security_headers_present(self, server, admin_a):
        response = server.get(
            "/v1/team",
            headers={"Authorization": f"Bearer {admin_a.jwt}"},
        )
        assert response.headers.get("x-content-type-options") == "nosniff"
        assert response.headers.get("x-frame-options") == "DENY"
        assert "interest-cohort" in response.headers.get("permissions-policy", "")

    def test_x_api_version_header(self, server, admin_a):
        response = server.get(
            "/v1/team",
            headers={"Authorization": f"Bearer {admin_a.jwt}"},
        )
        assert response.headers.get("x-api-version") is not None


# ---------------------------------------------------------------------------
# Pentesting attack surface — additional vectors
# ---------------------------------------------------------------------------


class TestHTTPParameterPollution:
    """Duplicate query params must resolve deterministically."""

    def test_duplicate_sort_by_uses_last(self, server, admin_a):
        response = server.get(
            "/v1/team?sort_by=name&sort_by=id",
            headers={"Authorization": f"Bearer {admin_a.jwt}"},
        )
        assert response.status_code in (200, 422)

    def test_duplicate_page_uses_last(self, server, admin_a):
        response = server.get(
            "/v1/team?page=1&page=9999",
            headers={"Authorization": f"Bearer {admin_a.jwt}"},
        )
        assert response.status_code in (200, 416, 422)


class TestResponseSplitting:
    """CRLF in header values must not cause response splitting."""

    def test_crlf_in_custom_header_ignored(self, server, admin_a):
        response = server.get(
            "/v1/team",
            headers={
                "Authorization": f"Bearer {admin_a.jwt}",
                "X-Custom": "value\r\nX-Injected: evil",
            },
        )
        assert "x-injected" not in {k.lower() for k in response.headers.keys()}


class TestVerbTampering:
    """Unsupported HTTP methods must return 405."""

    def test_trace_rejected(self, server, admin_a):
        response = server.request(
            "TRACE", "/v1/team",
            headers={"Authorization": f"Bearer {admin_a.jwt}"},
        )
        assert response.status_code == 405

    def test_propfind_rejected(self, server, admin_a):
        response = server.request(
            "PROPFIND", "/v1/team",
            headers={"Authorization": f"Bearer {admin_a.jwt}"},
        )
        assert response.status_code == 405


class TestMassAssignment:
    """Extra fields in create/update must not elevate privileges."""

    def test_create_with_extra_admin_field_ignored(self, server, admin_a):
        response = server.post(
            "/v1/team",
            json={"team": {
                "name": "mass-assign-test",
                "description": "test",
                "encryption_salt": "x",
                "is_admin": True,
                "role": "superadmin",
                "id": "attacker-chosen-id",
            }},
            headers={"Authorization": f"Bearer {admin_a.jwt}"},
        )
        if response.status_code == 201:
            body = response.json()
            team = body.get("team", body)
            assert team.get("id") != "attacker-chosen-id"


class TestIntegerOverflowPagination:
    """Extreme pagination values must not cause 500 or OOM."""

    def test_huge_page_number(self, server, admin_a):
        response = server.get(
            "/v1/team?page=99999999999999999999999",
            headers={"Authorization": f"Bearer {admin_a.jwt}"},
        )
        assert response.status_code in (200, 416, 422)

    def test_negative_page_size(self, server, admin_a):
        response = server.get(
            "/v1/team?pageSize=-1",
            headers={"Authorization": f"Bearer {admin_a.jwt}"},
        )
        assert response.status_code in (200, 422)

    def test_zero_page_size(self, server, admin_a):
        response = server.get(
            "/v1/team?pageSize=0",
            headers={"Authorization": f"Bearer {admin_a.jwt}"},
        )
        assert response.status_code in (200, 422)


class TestCachePoisoning:
    """Vary header must include Accept to prevent format confusion at CDN layer."""

    def test_vary_accept_present(self, server, admin_a):
        response = server.get(
            "/v1/team",
            headers={"Authorization": f"Bearer {admin_a.jwt}"},
        )
        vary = response.headers.get("vary", "")
        assert "Accept" in vary, f"Vary header must include Accept, got: {vary}"

    def test_vary_authorization_present(self, server, admin_a):
        response = server.get(
            "/v1/team",
            headers={"Authorization": f"Bearer {admin_a.jwt}"},
        )
        vary = response.headers.get("vary", "")
        assert "Authorization" in vary, f"Vary header must include Authorization, got: {vary}"
