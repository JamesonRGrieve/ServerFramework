# Meta Logging Extension

> **Extension Architecture**: For general extension patterns, see [EXT.Patterns.md](../EXT.Patterns.md).

`meta_logging` owns the framework's durable audit and system log records: two
database tables and the managers over them. It has no providers, endpoints,
environment variables, dependencies or lifecycle work.

## Components

| File | Contents |
|------|----------|
| `EXT_Meta_Logging.py` | `EXT_Meta_Logging` — name, version, description only. |
| `BLL_Meta_Logging.py` | `AuditLogModel`, `SystemLogModel`, `AuditLogManager`, `SystemLogManager`, and the hooks bound to both managers. |
| `migrations/versions/` | `audit_logs` and `system_logs` tables (branch `ext_meta_logging`). |

## Models

**`AuditLogModel`** (`audit_logs`) — one audited action: `timestamp`,
`user_id`, `action`, `resource_type`, `resource_id`, `ip_address`,
`user_agent`, `success`, `error_message`, `additional_data` (JSON),
`privacy_impact`, `data_categories` (JSON list).

**`SystemLogModel`** (`system_logs`) — one system event: `timestamp`, `level`,
`component`, `message`, `user_id`, `request_id`, `additional_data` (JSON).

`timestamp` is when the event occurred. A Create body may supply it; otherwise
it is the time the body was built. It is always written (the column is
NOT NULL with no database default).

Model fields are spelled `Optional[...]` rather than `X | None`: the SQLAlchemy
column builder unwraps `typing.Union` only.

## Managers

Both managers carry the standard `AbstractBLLManager` CRUD surface; neither is
routed, so the logs are reached programmatically.

`AuditLogManager.log_audit_event(event: AuditLogModel.Create)` persists one
event and returns it. It is the entry point for audit producers — billing's
`make_cost_audit_emitter` writes each provider cost through it as an
`action="provider_cost"` row.

## Hooks

Bound at import of `BLL_Meta_Logging` to `AuditLogManager` and
`SystemLogManager` only, so they act solely where those managers run (apps that
loaded `meta_logging`) and are registered once per process.

| Hook | Timing | Behavior |
|------|--------|----------|
| `require_requester_hook` | before, priority 1 | 401 when the manager has no authenticated requester. |
| `access_log_hook` | before 5 / after 95 | Debug-logs the method, requester, success, duration and result count. |
| `query_rate_limit_hook` | before, priority 10 | `list`/`search` limited to `META_LOG_QUERY_LIMIT` (20) per requester per 60 s through the shared rate-limit counter; 429 beyond it. |

## Out of scope

Failed-login tracking and lockout belong to `auth_lockout`
(`FailedLoginAttemptModel`) and `logic.BLL_Auth`; retention of audit data
belongs to `audit_retention`.

## Testing

`BLL_Meta_Logging_test.py` drives the hooks directly and, on a real app build
(private SQLite) that loaded `meta_logging`, persists and reads both log types,
runs billing's cost-audit emitter through `AuditLogManager`, and exercises the
bound hooks. `EXT_Meta_Logging_test.py` checks the extension's model set, that
the hooks are bound to the log managers only, and that an app without
`meta_logging` binds neither model.
