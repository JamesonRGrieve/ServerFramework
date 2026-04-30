"""Tests for ``database.TenantScoped`` (Item 55).

Pure-utility tests; no DB runtime required. The connection-execute
helpers are exercised via a tiny stub class that records calls.
"""

from __future__ import annotations

import pytest

from serverframework.database.TenantScoped import (
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
    assert "SET LOCAL app.current_team_id" in sql
    assert params == {"value": "abc-123"}


def test_clear_tenant_gucs_resets_each_key():
    conn = _RecordingConnection()
    clear_tenant_gucs(conn, ("org_id", "team_id"))
    assert len(conn.calls) == 2
    sql0, _ = conn.calls[0]
    sql1, _ = conn.calls[1]
    assert "RESET app.current_org_id" in sql0
    assert "RESET app.current_team_id" in sql1


def test_clear_tenant_gucs_empty_keys_is_noop():
    conn = _RecordingConnection()
    clear_tenant_gucs(conn, ())
    assert conn.calls == []
