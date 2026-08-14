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
from zephyrex.lib.Logging import logger

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
        from zephyrex.extensions.my_feature import BLL_My_Feature  # noqa: F401
        logger.debug("my_feature initialized")
        return True
```

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
- [ ] `on_initialize()` imports BLL module
- [ ] DB model inherits `DatabaseMixin`
- [ ] BLL manager inherits `AbstractBLLManager` with `DB` and `Model` set
- [ ] Tests inherit `AbstractEPTest` with `ExtensionServerMixin`
- [ ] SPDX license header on every source file
