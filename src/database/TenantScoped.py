"""Tenant data-isolation primitives (Item 55).

Tenant-scoped models declare themselves via TenantScopedMixin which
adds the configured tenant-key columns and registers a Postgres
Row-Level Security (RLS) policy at table-creation migration time.

Default tenant key is `team_id`; multi-key declarations are supported
for hierarchical tenancy:

    class FooModel(ApplicationModel, TenantScopedMixin.with_keys("team_id")):
        ...

    class BarModel(ApplicationModel, TenantScopedMixin.with_keys("org_id", "team_id")):
        ...

The session binder sets `app.current_<key>` Postgres GUCs on each
connection; the generated RLS policy combines the active keys with
AND. A missing GUC for any declared key returns zero rows.

System-level operations (admin endpoints, cross-tenant reporting) bind
a privileged session via a Postgres role with BYPASSRLS. The privilege
boundary is the session-bind layer; there is no per-query bypass.

This module ships:
    - TenantScopedMixin / .with_keys(...): the declarative entry point
    - rls_policy_sql(table, keys): generates the CREATE POLICY DDL
    - set_tenant_guc(session, key, value): runtime GUC setter helper
    - clear_tenant_gucs(session): clear all tenant GUCs
"""

from typing import ClassVar, Tuple, Type


class _TenantScopedBase:
    """Marker for TenantScopedMixin subclasses; holds the tenant-key
    tuple."""

    tenant_keys: ClassVar[Tuple[str, ...]] = ("team_id",)


class TenantScopedMixin(_TenantScopedBase):
    """Default mixin: tenant key is `team_id` only.

    Use `with_keys(...)` for multi-key tenancy.
    """

    @classmethod
    def with_keys(cls, *keys: str) -> Type["_TenantScopedBase"]:
        """Return a parametrized mixin subclass with the given tenant
        keys. Order matters: declared GUCs and policy clauses follow
        this order (most-specific last is conventional).

        Usage::

            class TeamFooModel(ApplicationModel, TenantScopedMixin.with_keys("team_id")):
                ...

            class OrgTeamFooModel(ApplicationModel, TenantScopedMixin.with_keys("org_id", "team_id")):
                ...
        """
        if not keys:
            raise ValueError("with_keys requires at least one tenant key")
        new_cls = type(
            f"_TenantScoped_{'_'.join(keys)}",
            (_TenantScopedBase,),
            {"tenant_keys": tuple(keys)},
        )
        return new_cls


def rls_policy_sql(table: str, keys: Tuple[str, ...], policy_name: str = None) -> str:
    """Generate the CREATE POLICY DDL for an RLS-enforced tenant
    isolation policy.

    The policy filters reads and writes by matching every declared
    tenant key against its corresponding `app.current_<key>` Postgres
    GUC. A missing or unset GUC for any declared key causes the policy
    to return zero rows -- the failure mode is invisibility rather than
    visibility.

    Returns:
        Multi-statement SQL string (ALTER TABLE ENABLE RLS; CREATE
        POLICY ...). Drop with rls_policy_drop_sql.
    """
    if not keys:
        raise ValueError("at least one tenant key required")
    pname = policy_name or f"{table}_tenant_isolation"
    clauses = [
        f"{k} = current_setting('app.current_{k}', true)::uuid"
        for k in keys
    ]
    using_expr = " AND ".join(clauses)
    return (
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;\n"
        f"CREATE POLICY {pname} ON {table} USING ({using_expr}) "
        f"WITH CHECK ({using_expr});"
    )


def rls_policy_drop_sql(table: str, policy_name: str = None) -> str:
    pname = policy_name or f"{table}_tenant_isolation"
    return (
        f"DROP POLICY IF EXISTS {pname} ON {table};\n"
        f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;"
    )


def set_tenant_guc(connection, key: str, value: str) -> None:
    """Set `app.current_<key>` on the given connection for the current
    transaction (`SET LOCAL`).

    `connection` is a SQLAlchemy connection object (the framework's
    session binder calls this on every checked-out connection).
    """
    connection.execute(f"SET LOCAL app.current_{key} = :value", {"value": value})


def clear_tenant_gucs(connection, keys: Tuple[str, ...]) -> None:
    """Clear `app.current_<key>` on the given connection. Used when
    binding a privileged BYPASSRLS session."""
    for k in keys:
        connection.execute(f"RESET app.current_{k}")


__all__ = [
    "TenantScopedMixin",
    "clear_tenant_gucs",
    "rls_policy_drop_sql",
    "rls_policy_sql",
    "set_tenant_guc",
]
