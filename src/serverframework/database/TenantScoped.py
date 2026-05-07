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
    - tenant_context contextvar: the per-request tenant-key mapping
      consumed by the session binder's after-begin hook
    - bind_session_tenant_gucs(session): the SA-event-driven binder that
      reads `tenant_context` and emits `SET LOCAL app.current_<key>`
      statements at the start of each transaction
"""

import re
from contextvars import ContextVar
from threading import RLock
from typing import ClassVar, Dict, Optional, Set, Tuple, Type


# M-2 — registry of every key name declared via TenantScopedMixin or
# `with_keys`. The session binder validates each key against this set
# before formatting it into `SET LOCAL app.current_<key>`. The value side
# is parameterized; the key side is concatenated. Without a registry,
# a misuse that ever sourced a key from request data would be SQLi.
_REGISTERED_TENANT_KEYS: Set[str] = {"team_id"}
_REGISTRY_LOCK = RLock()
_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def register_tenant_key(*keys: str) -> None:
    """Register a tenant-key column name. Refuses anything that is not a
    valid SQL identifier so a key cannot smuggle SQL through the session
    binder. Called by `TenantScopedMixin.with_keys`."""
    with _REGISTRY_LOCK:
        for k in keys:
            if not isinstance(k, str) or not _KEY_PATTERN.match(k):
                raise ValueError(
                    f"Refusing to register tenant key {k!r}: must match "
                    f"[A-Za-z_][A-Za-z0-9_]*"
                )
            _REGISTERED_TENANT_KEYS.add(k)


def is_registered_tenant_key(key: str) -> bool:
    with _REGISTRY_LOCK:
        return key in _REGISTERED_TENANT_KEYS


def registered_tenant_keys() -> Set[str]:
    with _REGISTRY_LOCK:
        return set(_REGISTERED_TENANT_KEYS)


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
        # M-2 — validate-and-register before the keys ever reach SQL.
        register_tenant_key(*keys)
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

    The key is validated against the tenant-key registry before string-
    formatting into SQL. An unregistered key is a programming error (or
    an injection attempt) and is refused outright — the helper is exported
    as public API and any caller that bypasses ``bind_session_tenant_gucs``
    must still go through this gate (H-3).
    """
    from sqlalchemy import text

    if not is_registered_tenant_key(key):
        raise ValueError(
            f"Unregistered tenant key {key!r}; refusing to emit SET LOCAL"
        )
    connection.execute(
        text(f"SET LOCAL app.current_{key} = :value"), {"value": value}
    )


def clear_tenant_gucs(connection, keys: Tuple[str, ...]) -> None:
    """Clear `app.current_<key>` on the given connection. Used when
    binding a privileged BYPASSRLS session.

    Like ``set_tenant_guc``, every key is validated against the registry
    before string-formatting; a literal ``RESET`` of an unknown identifier
    is a no-op in Postgres but the interpolation site is still a latent
    injection sink we refuse to expose (H-3).
    """
    from sqlalchemy import text

    for k in keys:
        if not is_registered_tenant_key(k):
            raise ValueError(
                f"Unregistered tenant key {k!r}; refusing to emit RESET"
            )
        connection.execute(text(f"RESET app.current_{k}"))


# ---------------------------------------------------------------------------
# Tenant-context contextvar + session-binder integration (Item 55)
# ---------------------------------------------------------------------------


# Per-request tenant-key mapping. Populated by middleware (or a request
# handler) at request ingress; consumed by the SQLAlchemy `after_begin`
# event handler the framework attaches to every primary session.
#
# Contract: keys are the column names declared on `TenantScopedMixin`
# (e.g. "team_id", "org_id", "user_id"); values are stringified UUIDs.
# An empty mapping means "no tenant context" — the RLS policy will see
# unset GUCs and filter every row out, which is the documented safe
# default per `DB.Patterns.md`'s defense-in-depth section.
_tenant_context_var: ContextVar[Dict[str, str]] = ContextVar(
    "_tenant_context", default={}
)

# Per-request privileged-bypass flag. When True, the binder emits
# `RESET app.current_<key>` for every key (clearing the GUCs entirely)
# AND assumes the session was bound under a Postgres role with the
# BYPASSRLS attribute, so the policy is not enforced at all. This is
# the only path that escapes RLS, and it is intentionally not reachable
# from per-query code — the binder reads it once at transaction begin.
_privileged_bypass_var: ContextVar[bool] = ContextVar(
    "_privileged_bypass", default=False
)


def set_tenant_context(**keys: str) -> None:
    """Set the per-request tenant-key mapping.

    Typically called from middleware after authentication resolves the
    requester's team/org/user. Values are stringified UUIDs; empty
    strings are filtered (the GUC stays unset, the policy filters every
    row out as the documented zero-row failure mode).

    Example::

        set_tenant_context(team_id="...", user_id="...")
    """
    _tenant_context_var.set({k: v for k, v in keys.items() if v})


def get_tenant_context() -> Dict[str, str]:
    """Return the current per-request tenant context (read-only view)."""
    return dict(_tenant_context_var.get())


def clear_tenant_context() -> None:
    """Reset the contextvar to the empty mapping. Called by middleware
    on response — important so a long-running worker process does not
    leak one request's tenant onto the next."""
    _tenant_context_var.set({})


def set_privileged_bypass(value: bool = True) -> None:
    """Mark the current request as privileged (BYPASSRLS).

    Admin endpoints, cross-tenant reporting jobs, and the framework's
    own internal cleanup processes call this. The flag is read once at
    transaction begin; flipping it mid-transaction has no effect.
    """
    _privileged_bypass_var.set(value)


def is_privileged_bypass() -> bool:
    return _privileged_bypass_var.get()


def bind_session_tenant_gucs(session) -> None:
    """Attach the `after_begin` listener that emits the per-tenant GUCs.

    Called once per session checkout from the framework's session
    binder. The listener fires at every transaction begin and runs
    `SET LOCAL app.current_<key> = '<value>'` for each key in the
    contextvar — `LOCAL` scopes the setting to the current transaction
    so concurrent transactions on a pool-recycled connection cannot see
    each other's tenant.

    For non-Postgres engines (SQLite under tests), the SQL is a no-op
    because SQLite ignores the `app.*` namespace. The listener gates
    on the connection dialect; tests on SQLite never observe an error,
    and Postgres deployments get correctness.
    """
    from sqlalchemy import event, text

    @event.listens_for(session, "after_begin")
    def _set_gucs(_session, transaction, connection):
        dialect = getattr(getattr(connection, "dialect", None), "name", "")
        if dialect != "postgresql":
            # SQLite/MySQL/etc. don't have GUCs in the `app.` namespace.
            # Item 55 is Postgres-specific by design; other engines
            # silently no-op so the test suite stays portable.
            return
        if is_privileged_bypass():
            # BYPASSRLS path: clear the GUCs so a misbehaving policy
            # cannot accidentally re-engage filtering. Skip unregistered
            # keys defensively (M-2) — they should never appear, but a
            # RESET on a typo is a no-op so this is just hygiene.
            for k in _tenant_context_var.get().keys():
                if is_registered_tenant_key(k):
                    connection.execute(text(f"RESET app.current_{k}"))
            return
        ctx = _tenant_context_var.get()
        for k, v in ctx.items():
            # M-2 — validate against the registry before string-formatting
            # the key into the SQL. Anything outside the registry is a
            # bug (or worse) and we refuse to emit the statement.
            if not is_registered_tenant_key(k):
                raise ValueError(
                    f"Unregistered tenant key {k!r} in tenant_context; "
                    f"refusing to emit SET LOCAL"
                )
            connection.execute(
                text(f"SET LOCAL app.current_{k} = :value"),
                {"value": str(v)},
            )


__all__ = [
    "TenantScopedMixin",
    "bind_session_tenant_gucs",
    "clear_tenant_context",
    "clear_tenant_gucs",
    "get_tenant_context",
    "is_privileged_bypass",
    "rls_policy_drop_sql",
    "rls_policy_sql",
    "set_privileged_bypass",
    "set_tenant_context",
    "set_tenant_guc",
]
