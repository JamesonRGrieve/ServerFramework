"""Tests for ``database.ReadReplica`` (Item 54).

Pure-utility tests; no DB runtime required. Each test resets the
contextvars so it's hermetic.
"""

from __future__ import annotations

import asyncio

import pytest

from serverframework.database.ReadReplica import (
    ReplicaPool,
    get_read_only,
    is_read_only,
    mark_primary_write_seen,
    primary_write_seen,
    read_only,
    set_read_only,
    should_route_to_replica,
    _primary_write_seen_var,
    _read_only_var,
)


@pytest.fixture(autouse=True)
def _reset_contextvars():
    """Each test starts with a clean slate."""
    ro_token = _read_only_var.set(False)
    pw_token = _primary_write_seen_var.set(False)
    yield
    _read_only_var.reset(ro_token)
    _primary_write_seen_var.reset(pw_token)


def test_set_and_get_read_only_round_trip():
    assert get_read_only() is False
    set_read_only(True)
    assert get_read_only() is True
    set_read_only(False)
    assert get_read_only() is False


def test_should_route_requires_read_only_and_no_primary_write():
    assert should_route_to_replica() is False  # default: nothing set
    set_read_only(True)
    assert should_route_to_replica() is True
    mark_primary_write_seen()
    assert should_route_to_replica() is False


def test_primary_write_seen_sticky_overrides_read_only():
    set_read_only(True)
    assert should_route_to_replica() is True
    mark_primary_write_seen()
    assert primary_write_seen() is True
    # Even though read_only is still set, primary write trumps.
    assert get_read_only() is True
    assert should_route_to_replica() is False


def test_read_only_decorator_sets_contextvar_during_call_and_resets():
    observed = {}

    @read_only
    def f():
        observed["inside"] = get_read_only()
        return "ok"

    assert get_read_only() is False
    assert f() == "ok"
    assert observed["inside"] is True
    # Reset after call
    assert get_read_only() is False


def test_read_only_decorator_async():
    observed = {}

    @read_only
    async def f():
        observed["inside"] = get_read_only()
        return "async-ok"

    assert get_read_only() is False
    result = asyncio.run(f())
    assert result == "async-ok"
    assert observed["inside"] is True
    assert get_read_only() is False


def test_is_read_only_marker():
    @read_only
    def decorated():
        pass

    def plain():
        pass

    assert is_read_only(decorated) is True
    assert is_read_only(plain) is False


def test_is_read_only_marker_async():
    @read_only
    async def decorated():
        pass

    async def plain():
        pass

    assert is_read_only(decorated) is True
    assert is_read_only(plain) is False


def test_replica_pool_empty_returns_none():
    pool = ReplicaPool([])
    assert pool.next_url() is None


def test_replica_pool_round_robin():
    urls = ["r1", "r2", "r3"]
    pool = ReplicaPool(urls)
    assert pool.next_url() == "r1"
    assert pool.next_url() == "r2"
    assert pool.next_url() == "r3"
    assert pool.next_url() == "r1"  # wraps


def test_replica_pool_skips_unhealthy():
    pool = ReplicaPool(["r1", "r2", "r3"])
    pool.mark_unhealthy("r2")
    seen = {pool.next_url() for _ in range(6)}
    assert "r2" not in seen
    assert seen == {"r1", "r3"}


def test_replica_pool_recovers_after_mark_healthy():
    pool = ReplicaPool(["r1", "r2", "r3"])
    pool.mark_unhealthy("r2")
    seen_after_unhealthy = {pool.next_url() for _ in range(6)}
    assert "r2" not in seen_after_unhealthy
    pool.mark_healthy("r2")
    seen_after_recovery = {pool.next_url() for _ in range(9)}
    assert "r2" in seen_after_recovery


def test_replica_pool_all_unhealthy_returns_none():
    pool = ReplicaPool(["r1", "r2"])
    pool.mark_unhealthy("r1")
    pool.mark_unhealthy("r2")
    assert pool.next_url() is None
