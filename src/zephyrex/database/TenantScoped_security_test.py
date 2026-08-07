"""H-3 — ``set_tenant_guc`` / ``clear_tenant_gucs`` must validate the key
against the tenant-key registry before string-formatting it into SQL.

The framework's session binder already validates registered keys, but the
helpers are exported as public API; any future caller that bypasses the
binder must still hit the registry gate.
"""

from __future__ import annotations

import pytest


def test_set_tenant_guc_refuses_unregistered_key():
    from zephyrex.database.TenantScoped import set_tenant_guc

    class _FakeConn:
        def execute(self, *a, **kw):  # pragma: no cover — must not run
            raise AssertionError("execute called for an unregistered key")

    with pytest.raises(ValueError, match="Unregistered tenant key"):
        set_tenant_guc(_FakeConn(), "team_id; DROP TABLE foo", "v")


def test_clear_tenant_gucs_refuses_unregistered_key():
    from zephyrex.database.TenantScoped import clear_tenant_gucs

    class _FakeConn:
        def execute(self, *a, **kw):  # pragma: no cover
            raise AssertionError("execute called for an unregistered key")

    with pytest.raises(ValueError, match="Unregistered tenant key"):
        clear_tenant_gucs(_FakeConn(), ("attacker_key",))


def test_set_tenant_guc_accepts_registered_key():
    """Default registry contains ``team_id``. The call succeeds and emits
    a parameterized statement against the connection."""
    from zephyrex.database.TenantScoped import set_tenant_guc

    captured = {}

    class _FakeConn:
        def execute(self, sql, params=None):
            captured["sql"] = str(sql)
            captured["params"] = params

    set_tenant_guc(_FakeConn(), "team_id", "abc-123")
    assert "SET LOCAL app.current_team_id" in captured["sql"]
    assert ":value" in captured["sql"]
    assert captured["params"] == {"value": "abc-123"}
