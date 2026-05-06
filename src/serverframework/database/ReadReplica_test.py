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


# ---------------------------------------------------------------------------
# Item 54 session-binder integration tests
# ---------------------------------------------------------------------------


def test_database_manager_select_session_factory_returns_primary_when_no_replicas(
    monkeypatch, tmp_path
):
    """Without `DB_REPLICA_URLS`, `_select_session_factory` always returns
    the primary factory regardless of `@read_only` state."""
    from serverframework.database.DatabaseManager import DatabaseManager

    db_file = tmp_path / "primary.db"
    monkeypatch.setenv("DATABASE_TYPE", "sqlite")
    monkeypatch.setenv("DATABASE_NAME", str(db_file))
    monkeypatch.delenv("DB_REPLICA_URLS", raising=False)

    dm = DatabaseManager(db_prefix="", test_connection=False)
    dm.init_worker()
    try:
        set_read_only(True)
        assert dm._select_session_factory() is dm._session_factory
        # Even with read-only set, no replicas means primary.
    finally:
        set_read_only(False)


def test_database_manager_select_session_factory_picks_replica(
    monkeypatch, tmp_path
):
    """With `DB_REPLICA_URLS` configured AND `@read_only` set,
    `_select_session_factory` returns a replica factory. The primary
    factory is the fallback when read-only is unset."""
    from serverframework.database.DatabaseManager import DatabaseManager

    primary = tmp_path / "primary.db"
    replica = tmp_path / "replica.db"
    monkeypatch.setenv("DATABASE_TYPE", "sqlite")
    monkeypatch.setenv("DATABASE_NAME", str(primary))
    monkeypatch.setenv("DB_REPLICA_URLS", f"sqlite:///{replica}")

    dm = DatabaseManager(db_prefix="", test_connection=False)
    dm.init_worker()
    try:
        # Read-only off → primary
        assert dm._select_session_factory() is dm._session_factory

        # Read-only on → replica
        set_read_only(True)
        factory = dm._select_session_factory()
        assert factory is not dm._session_factory
        assert factory in dm._replica_session_factories.values()
    finally:
        set_read_only(False)


def test_database_manager_replica_routing_falls_back_after_primary_write(
    monkeypatch, tmp_path
):
    """Read-after-write: once `mark_primary_write_seen()` has fired in
    the request context, even `@read_only` reads bind primary."""
    from serverframework.database.DatabaseManager import DatabaseManager

    primary = tmp_path / "primary.db"
    replica = tmp_path / "replica.db"
    monkeypatch.setenv("DATABASE_TYPE", "sqlite")
    monkeypatch.setenv("DATABASE_NAME", str(primary))
    monkeypatch.setenv("DB_REPLICA_URLS", f"sqlite:///{replica}")

    dm = DatabaseManager(db_prefix="", test_connection=False)
    dm.init_worker()
    try:
        set_read_only(True)
        # Pre-write: replica
        assert dm._select_session_factory() is not dm._session_factory
        mark_primary_write_seen()
        # Post-write: primary even though read_only is still set
        assert dm._select_session_factory() is dm._session_factory
    finally:
        set_read_only(False)


def test_database_manager_attaches_primary_write_listener_on_primary_session(
    monkeypatch, tmp_path
):
    """When `get_session()` returns a primary session, the SA `before_flush`
    listener is registered. The contract we verify: a real flush against
    the session trips `primary_write_seen()`."""
    from sqlalchemy import Column, Integer
    from sqlalchemy.orm import declarative_base

    from serverframework.database.DatabaseManager import DatabaseManager

    primary = tmp_path / "primary.db"
    monkeypatch.setenv("DATABASE_TYPE", "sqlite")
    monkeypatch.setenv("DATABASE_NAME", str(primary))
    monkeypatch.delenv("DB_REPLICA_URLS", raising=False)

    dm = DatabaseManager(db_prefix="", test_connection=False)
    dm.init_worker()

    Base = declarative_base()

    class _Probe(Base):
        __tablename__ = "_item54_probe"
        id = Column(Integer, primary_key=True, autoincrement=True)

    Base.metadata.create_all(dm.engine)

    session = dm.get_session()
    try:
        assert primary_write_seen() is False
        session.add(_Probe())
        session.flush()
        assert primary_write_seen() is True
    finally:
        session.close()
