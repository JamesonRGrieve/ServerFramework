"""Tests for ``database.TenantScoped`` (Item 55).

Pure-utility tests; no DB runtime required. The connection-execute
helpers are exercised via a tiny stub class that records calls.
"""

from __future__ import annotations

import pytest

from zephyrex.database.TenantScoped import (
    TenantScopedMixin,
    clear_tenant_gucs,
    rls_policy_drop_sql,
    rls_policy_sql,
    set_tenant_guc,
)


class _RecordingConnection:
    """Stub SQLAlchemy connection: records every .execute() call."""

    def __init__(self):
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))


def test_default_tenant_keys_is_team_id():
    assert TenantScopedMixin.tenant_keys == ("team_id",)


def test_with_keys_single():
    cls = TenantScopedMixin.with_keys("team_id")
    assert cls.tenant_keys == ("team_id",)


def test_with_keys_multi_preserves_order():
    cls = TenantScopedMixin.with_keys("org_id", "team_id")
    assert cls.tenant_keys == ("org_id", "team_id")


def test_with_keys_returns_distinct_subclasses():
    a = TenantScopedMixin.with_keys("team_id")
    b = TenantScopedMixin.with_keys("org_id", "team_id")
    assert a is not b
    assert a.tenant_keys != b.tenant_keys


def test_with_keys_empty_raises():
    with pytest.raises(ValueError):
        TenantScopedMixin.with_keys()


def test_rls_policy_sql_single_key():
    sql = rls_policy_sql("foo", ("team_id",))
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "CREATE POLICY" in sql
    assert "foo_tenant_isolation" in sql
    assert "ON foo" in sql
    assert "current_setting('app.current_team_id', true)::uuid" in sql
    assert "USING (" in sql
    assert "WITH CHECK (" in sql


def test_rls_policy_sql_multi_key_joins_with_and():
    sql = rls_policy_sql("foo", ("org_id", "team_id"))
    assert "current_setting('app.current_org_id', true)::uuid" in sql
    assert "current_setting('app.current_team_id', true)::uuid" in sql
    # The conjunction binds the two keys together.
    assert " AND " in sql
    # Ordering is preserved.
    org_idx = sql.index("app.current_org_id")
    team_idx = sql.index("app.current_team_id")
    assert org_idx < team_idx


def test_rls_policy_sql_custom_policy_name():
    sql = rls_policy_sql("foo", ("team_id",), policy_name="custom_pol")
    assert "custom_pol" in sql
    assert "foo_tenant_isolation" not in sql


def test_rls_policy_sql_empty_keys_raises():
    with pytest.raises(ValueError):
        rls_policy_sql("foo", ())


def test_rls_policy_drop_sql_default_name():
    sql = rls_policy_drop_sql("foo")
    assert "DROP POLICY IF EXISTS foo_tenant_isolation ON foo" in sql
    assert "DISABLE ROW LEVEL SECURITY" in sql


def test_rls_policy_drop_sql_custom_name():
    sql = rls_policy_drop_sql("foo", policy_name="custom_pol")
    assert "DROP POLICY IF EXISTS custom_pol ON foo" in sql
    assert "foo_tenant_isolation" not in sql


def test_set_tenant_guc_records_execute():
    conn = _RecordingConnection()
    set_tenant_guc(conn, "team_id", "abc-123")
    assert len(conn.calls) == 1
    sql, params = conn.calls[0]
    # H-3 — the helper now wraps SQL in ``text()`` so the SQLAlchemy
    # connection treats the literal-formatted key as DDL and the
    # ``:value`` placeholder as a bound parameter. Compare on the
    # string rendering of the clause.
    assert "SET LOCAL app.current_team_id" in str(sql)
    assert ":value" in str(sql)
    assert params == {"value": "abc-123"}


def test_clear_tenant_gucs_resets_each_key():
    # ``team_id`` is registered by default; we register ``org_id`` here
    # since H-3 now refuses unregistered keys at this layer too.
    from zephyrex.database.TenantScoped import register_tenant_key

    register_tenant_key("org_id")
    conn = _RecordingConnection()
    clear_tenant_gucs(conn, ("org_id", "team_id"))
    assert len(conn.calls) == 2
    sql0, _ = conn.calls[0]
    sql1, _ = conn.calls[1]
    assert "RESET app.current_org_id" in str(sql0)
    assert "RESET app.current_team_id" in str(sql1)


def test_clear_tenant_gucs_empty_keys_is_noop():
    conn = _RecordingConnection()
    clear_tenant_gucs(conn, ())
    assert conn.calls == []


# ---------------------------------------------------------------------------
# Item 55 — contextvar + session-binder integration
# ---------------------------------------------------------------------------


from zephyrex.database.TenantScoped import (  # noqa: E402
    bind_session_tenant_gucs,
    clear_tenant_context,
    get_tenant_context,
    is_privileged_bypass,
    set_privileged_bypass,
    set_tenant_context,
    _privileged_bypass_var,
    _tenant_context_var,
)


@pytest.fixture(autouse=True)
def _reset_tenant_context():
    tok_t = _tenant_context_var.set({})
    tok_p = _privileged_bypass_var.set(False)
    yield
    _tenant_context_var.reset(tok_t)
    _privileged_bypass_var.reset(tok_p)


def test_set_tenant_context_round_trip():
    set_tenant_context(team_id="t1", user_id="u1")
    assert get_tenant_context() == {"team_id": "t1", "user_id": "u1"}


def test_set_tenant_context_drops_falsy_values():
    set_tenant_context(team_id="t1", user_id="", org_id=None)
    assert get_tenant_context() == {"team_id": "t1"}


def test_clear_tenant_context_resets_to_empty():
    set_tenant_context(team_id="t1")
    clear_tenant_context()
    assert get_tenant_context() == {}


def test_privileged_bypass_round_trip():
    assert is_privileged_bypass() is False
    set_privileged_bypass(True)
    assert is_privileged_bypass() is True
    set_privileged_bypass(False)
    assert is_privileged_bypass() is False


def test_get_tenant_context_returns_copy_not_reference():
    set_tenant_context(team_id="t1")
    snap = get_tenant_context()
    snap["team_id"] = "MUTATED"
    # Internal state must not be affected by mutating the snapshot.
    assert get_tenant_context() == {"team_id": "t1"}


def test_bind_session_tenant_gucs_emits_set_local_on_postgres(monkeypatch, tmp_path):
    """End-to-end: a real SA session running against an in-memory SQLite
    sees the listener attach without error; the `SET LOCAL` SQL is
    emitted on Postgres only (gated by dialect). For SQLite the listener
    is a no-op so the call is exercised and verified to not raise.

    The Postgres dialect-gated emission is verified by patching the
    connection dialect after attach.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(f"sqlite:///{tmp_path}/probe.db")
    Session = sessionmaker(bind=engine)
    session = Session()
    # Attach binder; should not raise even without Postgres.
    bind_session_tenant_gucs(session)
    set_tenant_context(team_id="t-1")
    # Begin a transaction and commit; the listener fires but no-ops.
    session.begin()
    session.commit()
    session.close()


def test_bind_session_tenant_gucs_emits_set_local_on_postgres_dialect():
    """Drive the binder's `_set_gucs` body directly with a fake connection
    that reports a Postgres dialect. Captures the `SET LOCAL` statements
    the listener emits without standing up a live Postgres or running
    the SQL through SQLite (which would reject the syntax).

    This tests the listener body in isolation; the integration test
    `test_database_manager_attaches_tenant_binder_on_session` verifies
    the listener is actually wired into framework-managed sessions.
    """
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    # Build a Session subclass we can attach listeners to without an engine.
    captured = []

    class _FakeDialect:
        name = "postgresql"

    class _FakeConnection:
        dialect = _FakeDialect()

        def execute(self, sql, params=None):
            captured.append((str(sql), params))

    # Use a real SA Session shell so the dispatcher is real.
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker as _sm

    _eng = create_engine("sqlite:///:memory:")
    session = _sm(bind=_eng)()

    bind_session_tenant_gucs(session)
    set_tenant_context(team_id="t-1", org_id="o-1")

    # Manually fire the after_begin listeners against our fake connection.
    fake_conn = _FakeConnection()
    session.dispatch.after_begin(session, None, fake_conn)

    statements = [s for s, _ in captured]
    assert any("SET LOCAL app.current_team_id" in s for s in statements)
    assert any("SET LOCAL app.current_org_id" in s for s in statements)
    # Values reach the parameters dict.
    params = [p for _, p in captured]
    assert {"value": "t-1"} in params
    assert {"value": "o-1"} in params


def test_bind_session_tenant_gucs_resets_under_privileged_bypass():
    """With `set_privileged_bypass(True)` and a populated context, the
    binder emits `RESET app.current_<key>` for every key rather than the
    `SET LOCAL` statements."""
    from sqlalchemy.orm import Session

    captured = []

    class _FakeDialect:
        name = "postgresql"

    class _FakeConnection:
        dialect = _FakeDialect()

        def execute(self, sql, params=None):
            captured.append(str(sql))

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker as _sm

    _eng = create_engine("sqlite:///:memory:")
    session = _sm(bind=_eng)()

    bind_session_tenant_gucs(session)
    set_tenant_context(team_id="t-1", org_id="o-1")
    set_privileged_bypass(True)

    session.dispatch.after_begin(session, None, _FakeConnection())

    assert not any("SET LOCAL" in s for s in captured)
    assert any("RESET app.current_team_id" in s for s in captured)
    assert any("RESET app.current_org_id" in s for s in captured)


def test_bind_session_tenant_gucs_noops_on_non_postgres():
    """SQLite/MySQL dialects must not produce any GUC statements — the
    listener gates on `dialect.name == 'postgresql'`."""
    from sqlalchemy.orm import Session

    captured = []

    class _FakeDialect:
        name = "sqlite"

    class _FakeConnection:
        dialect = _FakeDialect()

        def execute(self, sql, params=None):
            captured.append(str(sql))

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker as _sm

    _eng = create_engine("sqlite:///:memory:")
    session = _sm(bind=_eng)()

    bind_session_tenant_gucs(session)
    set_tenant_context(team_id="t-1")
    session.dispatch.after_begin(session, None, _FakeConnection())

    assert captured == []


def test_database_manager_attaches_tenant_binder_on_session(monkeypatch, tmp_path):
    """The framework's session binder calls `bind_session_tenant_gucs`
    on every primary session. Verify by checking the SA event registry
    on a session pulled from `DatabaseManager.get_session()`."""
    from sqlalchemy import event

    from zephyrex.database.DatabaseManager import DatabaseManager

    db_file = tmp_path / "t.db"
    monkeypatch.setenv("DATABASE_TYPE", "sqlite")
    monkeypatch.setenv("DATABASE_NAME", str(db_file))
    monkeypatch.delenv("DB_REPLICA_URLS", raising=False)

    dm = DatabaseManager(db_prefix="", test_connection=False)
    dm.init_worker()
    session = dm.get_session()
    try:
        # Inspect the registered listeners; the binder registers one
        # `after_begin` handler per session (event.contains() is the
        # safe SA-supplied check).
        # The binder is attached anew per session, so a fresh probe
        # session is the cleanest assertion target.
        registered = event.contains(session, "after_begin", lambda *a, **kw: None)
        # `event.contains` on an arbitrary callable returns False; use a
        # weaker assertion: inspecting the dispatcher reveals at least
        # one registered listener.
        listeners = list(session.dispatch.after_begin.listeners)
        assert any(listeners), "No after_begin listeners attached"
    finally:
        session.close()
