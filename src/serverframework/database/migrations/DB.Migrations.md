# Migration System

This document describes the migration system implemented in `Migration.py` and `env.py`. The system runs Alembic for both core schema and extension-contributed schema with a single shared `env.py` and per-extension `version_locations`.

## Architecture

```
app.instance(extensions=...)
  -> ModelRegistry.commit()
       -> MigrationManager(model_registry=self)
            -> _compute_migration_order(extensions)        # FK-aware toposort
            -> run_alembic_command("upgrade", "head")     # core
            -> run_extension_migration(...)               # each extension
                 -> _make_alembic_config(...)             # in-memory Config
                      -> alembic.command.upgrade(cfg, ...)
                          -> env.py reads cfg.attributes["model_registry"]
                             -> Base.metadata is the live target_metadata
```

Key separations:

- **`script_location`** is shared: `src/database/migrations/`. Holds the single `env.py` and the materialized `script.py.mako` template. Never copied per-extension.
- **`version_locations`** is per-extension: `src/extensions/<name>/migrations/versions/` (or the path returned by `_resolve_extension_versions_dir` when `test_versions_root` is set).
- **`version_table`** is per-extension: `alembic_version_<name>` for extensions, `alembic_version` for core. Lets each extension keep an independent history line.
- **Extension target** is propagated via `cfg.attributes["extension"]` (set by `_make_alembic_config`); env.py reads it via `context.config.attributes.get("extension")`.

Extension folders only ever contain author-owned source files plus `migrations/versions/<rev>.py`. No `env.py` copy, no `alembic.ini`, no `script.py.mako`, no `__init__.py`, no `tmp*.ini` — ever, even transiently.

## Configuration

The set of extensions to run is sourced from the `APP_EXTENSIONS` environment variable (comma-separated). It can be overridden per call by passing `extensions=` to `MigrationManager.run_all_migrations` or to `app.instance`.

```bash
APP_EXTENSIONS="payment,auth_mfa"
```

Extensions absent from this variable are skipped even if their files exist on disk.

## Table ownership

Every SQLAlchemy `Table` carries deterministic ownership stamps written by `ModelRegistry._stamp_extension_table_ownership` at commit time:

- `table.info["extension"] = "<name>"` — for **extension-owned tables**: tables whose Pydantic model lives at `extensions/<name>/BLL_*.py`.
- `table.info["extensions"] = {"<name>", ...}` — for **core tables that have been extended** by one or more `@extension_model` decorators (field injections).

The single canonical resolution rule is exposed by:

```python
MigrationManager.env_is_table_owned_by_extension(table) -> Optional[str]
MigrationManager.env_table_extenders(table) -> List[str]
```

`env_is_table_owned_by_extension` consults `info["extension"]` first, then falls back to file-path inspection of the SA model's source module for tables built outside the registry path. Core tables — even those with field injections — return `None`; the extender names live in `info["extensions"]` and are surfaced via `env_table_extenders`.

### Audit CLI

```
python src/database/migrations/Migration.py audit-ownership
```

Prints one tab-separated row per table:

```
table             owner       extenders
users             core        payment
multifactor_methods   auth_mfa    -
```

## Migration ordering

`MigrationManager._compute_migration_order(extensions: List[str]) -> List[str]` is a topological sort over two edge sources:

1. **Declared `EXT_Dependency`** — for each non-optional `EXT_Dependency(name="A")` on extension B, add the edge `A -> B`.
2. **FK-discovered** — for each `ForeignKey` on a table owned by extension B referencing a table owned by extension A (A != B), add the edge `A -> B`. Reads the ownership stamps above. FK references between core and extensions don't add edges; core is sequenced before extensions unconditionally.

`run_all_migrations` invokes the helper before iterating; the resolved order replaces declared-only ordering. Cycles raise a `RuntimeError` naming the offending extensions, the FK columns that closed the cycle, and the recommended workaround (a join table owned by one side rather than mutual direct FKs).

Extensions whose tables FK into another extension's tables therefore get the right order automatically — no need to declare a redundant `EXT_Dependency`.

## Public API

### `MigrationManager`

```python
MigrationManager(
    test_mode: bool = False,
    custom_db_info: dict | None = None,
    extensions_dir: str = "extensions",
    database_dir: str = "database",
    model_registry: ModelRegistry | None = None,
    test_versions_root: str | Path | None = None,
)
```

- `model_registry`: pass the live `ModelRegistry` so env.py resolves it via `cfg.attributes["model_registry"]`. Required when migrations run during `commit()` because env.py's lazy-loaded Base is a different SA declarative instance from the registry-driven Base.
- `test_versions_root`: pass a directory (e.g. `tmp_path`) to route test-mode revision files outside `src/`, keeping the source tree pristine across runs.

Methods:

- `run_alembic_command(command, *args, extension=None) -> bool`
- `run_extension_migration(extension_name, command, target="head", auto=True) -> bool`
- `create_extension_migration(extension_name, message, auto=True) -> bool`
- `run_all_migrations(command, target="head", extensions=None) -> bool`
- `regenerate_migrations(extension_name=None, all_extensions=False, message=None) -> bool`
- `cleanup_test_artifacts() -> bool` — test-mode-only sweep of in-tree `test_versions/` and the test SQLite file
- `audit_table_ownership() -> bool` — drives the `audit-ownership` CLI subcommand
- `env_is_table_owned_by_extension(table) -> Optional[str]` (static)
- `env_table_extenders(table) -> List[str]` (static)
- `_compute_migration_order(extensions: List[str]) -> List[str]`

### CLI

```
python src/database/migrations/Migration.py <command>
```

Commands:

- `upgrade [--all | --extension NAME] [target]` — defaults to `head`
- `downgrade [--all | --extension NAME] [target]`
- `revision -m MSG [--autogenerate] [--extension NAME]`
- `history [--extension NAME]`
- `current [--extension NAME]`
- `create EXTENSION_NAME [--skip-model] [--skip-migrate]` — bootstrap a new extension scaffold
- `regenerate [--extension NAME | --all] [-m MSG]`
- `audit-ownership` — list every table → owner → extenders
- `debug` — print discovered paths and configuration

## env.py contract

env.py requires a `ModelRegistry` to be reachable via either:

1. `context.config.attributes["model_registry"]` — set by `MigrationManager._make_alembic_config` when `commit()` drives migrations.
2. `Base._model_registry` — set when migrations run on the same Base instance the registry was attached to.

If neither is set, env.py raises a `RuntimeError`. The legacy import-discovery fallback (which used to scan `sys.modules` for `DatabaseMixin` classes and hardcode imports of `BLL_Auth`/`BLL_Extensions`/`BLL_Providers`) was removed in Phase 3 — see commit history for context. Direct alembic CLI invocations that bypass `MigrationManager` must arrange to attach a registry to Base before running.

## Extension-author guide

Define your models under `extensions/<name>/BLL_*.py`:

```python
from serverframework.logic.BLL_Auth import UserModel
from serverframework.lib.Pydantic import BaseModel
from serverframework.lib.Pydantic2SQLAlchemy import DatabaseMixin
from serverframework.pydantic2 import extension_model

class MyExtTableModel(BaseModel, DatabaseMixin):
    name: str

@extension_model(UserModel)
class UserExtension(BaseModel):
    extra_field: str | None = None
```

The framework will:
- Pick up your tables via file-path detection and stamp `table.info["extension"] = "<name>"`.
- Pick up your `@extension_model` field injections and stamp the target table's `info["extensions"]` set.
- Run your migrations after core (and after any extension you FK into) automatically.

You don't need to write `__table_args__` or explicit `EXT_Dependency` for FK-implied dependencies — those are discovered. Use `EXT_Dependency` only for non-FK ordering constraints (e.g. you depend on the other extension's seed data running first).

## Test-author guide

`Migration_test.py` contains the canonical patterns:

- `booted_app` fixture boots `app.instance()` against a `tmp_path` SQLite for full integration tests.
- `migration_manager` fixture creates a standalone `MigrationManager` for unit-style tests of CLI / API surface.
- Use `test_versions_root=tmp_path` when calling `MigrationManager(...)` directly to keep `src/` pristine across the test run.
- Tests should assert on `table.info["extension"]` / `info["extensions"]` rather than fuzzy module-name patterns.

## Implementation reference

| Concern | Where |
|---------|-------|
| Single canonical mechanism for ownership detection | `env_is_table_owned_by_extension` + `audit-ownership` CLI |
| Cross-extension FK-aware migration ordering | `_compute_migration_order` |
| Extension-aware migration discovery (in-tree and out-of-tree) | `lib.Paths.extensions_dir()` + `version_locations` per extension |
| Forward-compat namespace (`serverframework.database.migrations`) | Paths route through `self.paths` / `lib.Paths` |
