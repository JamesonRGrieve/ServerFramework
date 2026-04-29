"""Tests for `lib.InboundSecurity` (Item 71)."""

from __future__ import annotations

import time

import pytest

from lib.InboundSecurity import (
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
