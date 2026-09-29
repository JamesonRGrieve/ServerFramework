# Extension Authoring Guide

A zephyrex extension is a self-contained directory under `extensions/` that provides models, business logic, endpoints, and migrations for a feature domain.

## Minimal Extension

```
extensions/
  my_feature/
    __init__.py          # empty
    EXT_My_Feature.py    # extension manifest
    DB_My_Feature.py     # SQLAlchemy model (DatabaseMixin)
    BLL_My_Feature.py    # business logic (AbstractBLLManager)
    EP_My_Feature.py     # endpoints (auto-generated from BLL)
    EP_My_Feature_test.py
    manifest.toml        # optional metadata
    migrations/
      versions/          # alembic migration scripts
```

## 1. Extension Manifest (`EXT_My_Feature.py`)

```python
from typing import Any, ClassVar, Dict, List, Set
from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension
from zephyrex.lib.Dependencies import Dependencies

class EXT_My_Feature(AbstractStaticExtension):
    name: ClassVar[str] = "my_feature"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Short description of what this does."

    _env: ClassVar[Dict[str, Any]] = {}
    dependencies: ClassVar[Dependencies] = Dependencies([])
    _abilities: ClassVar[Set[str]] = set()
    _providers: ClassVar[List] = []
    extension_dependencies: ClassVar[List[str]] = []

    @classmethod
    def on_initialize(cls) -> bool:
        from zephyrex.extensions.my_feature.BLL_My_Feature import register_hooks

        register_hooks()
        return True
```

### Lifecycle

The framework drives three classmethods on every loaded extension. Override
only the ones with real work; the defaults do nothing (`on_initialize` returns
True).

| Method | Called | Contract |
|--------|--------|----------|
| `on_initialize(cls) -> bool` | Once per app build (`instance()` → `ModelRegistry.commit`), in dependency order, after the extension's `BLL_*`/`PRV_*` modules and models are imported and before migrations run. | Register hooks and cross-extension participation here. Return False only when the extension cannot function at all (e.g. a required secret or dependency is missing); the build then fails with `StartupError` naming the extension. A missing *optional* dependency is not a reason to return False: degrade and log instead. |
| `on_start(cls) -> None` | At app startup (FastAPI lifespan), in dependency order, once the worker's database engine (and Valkey, when configured) is ready and before background services start. | Open per-worker resources. An exception aborts startup. |
| `on_stop(cls) -> None` | At app shutdown (FastAPI lifespan), in reverse dependency order, before the database engine closes. | Release what `on_start` opened. Every extension's `on_stop` runs even if another raises; failures are logged. |

All three are classmethods: extensions are never instantiated.

A process can build many apps (every test server is one), so `on_initialize`
runs many times per process and must be idempotent: key registrations by name
so a repeat overwrites instead of accumulating. Registration tables are
process-global, so a registration must only act on apps that loaded the
extension. Key it by the extension's `name` and have the consumer filter with
`ModelRegistry.loaded_extension_names()` (as SDK generators and account-merge
handlers do), or guard the hook on the app's registry binding the extension's
model (as the core auth hooks do).

## 2. Database Model (`DB_My_Feature.py`)

```python
from zephyrex.database.AbstractDatabaseEntity import DatabaseMixin

class ItemModel(DatabaseMixin):
    name: str
    description: str = ""
```

`DatabaseMixin` provides: `id` (UUID), `created_at`, `updated_at`, `created_by_user_id`, `team_id`, soft-delete support, and CRUD class methods.

## 3. Business Logic (`BLL_My_Feature.py`)

```python
from zephyrex.logic.AbstractLogicManager import AbstractBLLManager

class ItemManager(AbstractBLLManager):
    DB = ItemModel.DB
    Model = ItemModel
```

This gives you `create()`, `get()`, `list()`, `search()`, `update()`, `delete()`, `batch_update()`, `batch_delete()` with team-scoped permissions, field ACL, and caching.

## 4. Loading

Add to `APP_EXTENSIONS` env var or pass to `run()`:

```python
from zephyrex import run
run(extensions="my_feature")
```

## Naming Conventions

| File | Pattern | Example |
|------|---------|---------|
| Extension class | `EXT_{Name}` | `EXT_My_Feature` |
| DB model | `{Entity}Model` | `ItemModel` |
| BLL manager | `{Entity}Manager` | `ItemManager` |
| Test | `EP_{Name}_test.py` | `EP_My_Feature_test.py` |

## Checklist

- [ ] `__init__.py` exists (can be empty)
- [ ] Extension class inherits `AbstractStaticExtension`
- [ ] `name` class var matches directory name
- [ ] Hook registrations live in `on_initialize()` (or at BLL import time for a
      hook core may call before any app is built) and are safe to repeat
- [ ] DB model inherits `DatabaseMixin`
- [ ] BLL manager inherits `AbstractBLLManager` with `DB` and `Model` set
- [ ] Tests inherit `AbstractEPTest` with `ExtensionServerMixin`
- [ ] SPDX license header on every source file
