"""Unit tests for the request-context surface.

Covers:
  - the user-info contextvar (pre-existing behavior)
  - Item 47 — per-request deadline budget propagation, monotonic semantics,
    `DeadlineExceededError`, and the `check_deadline_or_raise` failsafe.

These are pure-utility tests of the contextvar surface — no mocks of
business logic, no FastAPI runtime needed.
"""

from __future__ import annotations

import time

import pytest

from zephyrex.lib.RequestContext import (
    DeadlineExceededError,
    check_deadline_or_raise,
    clear_request_context,
    get_request_deadline,
    get_request_user,
    get_user_timezone,
    remaining_deadline_ms,
    set_request_deadline_ms,
    set_request_user,
)


@pytest.fixture(autouse=True)
def _reset_context():
    clear_request_context()
    yield
    clear_request_context()


# ----- Pre-existing behavior -----------------------------------------------


@pytest.mark.unit
def test_user_context_roundtrip():
    set_request_user({"user_id": "u1", "timezone": "America/Denver"})
    info = get_request_user()
    assert info is not None
    assert info["user_id"] == "u1"
    assert get_user_timezone() == "America/Denver"


@pytest.mark.unit
def test_user_context_default_timezone():
    assert get_user_timezone() == "UTC"


@pytest.mark.unit
def test_clear_request_context_resets_user_and_deadline():
    set_request_user({"user_id": "u1"})
    set_request_deadline_ms(1000)
    clear_request_context()
    assert get_request_user() is None
    assert get_request_deadline() is None


# ----- Item 47 — deadline budget -------------------------------------------


@pytest.mark.unit
def test_deadline_unset_returns_none_for_remaining():
    assert remaining_deadline_ms() is None
    # check_deadline_or_raise is a no-op when no deadline is set.
    check_deadline_or_raise(layer="rotation")


@pytest.mark.unit
def test_set_deadline_then_remaining_within_budget():
    set_request_deadline_ms(500)
    remaining = remaining_deadline_ms()
    assert remaining is not None
    # Test runs in microseconds; remaining must still be > 0 and ≤ 500.
    assert 0 < remaining <= 500


@pytest.mark.unit
def test_zero_or_negative_timeout_clears_deadline():
    set_request_deadline_ms(500)
    assert get_request_deadline() is not None
    set_request_deadline_ms(0)
    assert get_request_deadline() is None
    set_request_deadline_ms(-10)
    assert get_request_deadline() is None


@pytest.mark.unit
def test_deadline_exhausted_returns_zero_then_raises():
    """When the budget elapses, `remaining_deadline_ms` reports 0 and
    `check_deadline_or_raise` surfaces the typed error with the offending
    layer named in the message."""
    # Set a tiny deadline and busy-wait past it.
    set_request_deadline_ms(1)  # 1 ms
    deadline_at = get_request_deadline()
    assert deadline_at is not None
    # Wait until past the deadline.
    while time.monotonic() < deadline_at + 0.005:
        pass
    assert remaining_deadline_ms() == 0
    with pytest.raises(DeadlineExceededError) as exc_info:
        check_deadline_or_raise(layer="provider_http")
    assert exc_info.value.layer == "provider_http"
    assert "provider_http" in str(exc_info.value)


@pytest.mark.unit
def test_deadline_exceeded_error_carries_elapsed_ms():
    err = DeadlineExceededError(elapsed_ms=42, layer="db")
    assert err.elapsed_ms == 42
    assert err.layer == "db"
    assert "42ms" in str(err)
    assert "db" in str(err)
