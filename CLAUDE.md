# Claude Code Instructions — zephyrex (server)

Extensible Python (FastAPI) server framework. Installable via `pip install zephyrex` (PyPI). Consumer projects define extensions in a separate directory and boot via `zephyrex.run()`.

**PyPI package:** `zephyrex` v1.0.0a1
**Import:** `from zephyrex import run, instance`

## Stack Standards

Read **before your first edit**:

- `/home/jameson/Source/ai-prompts/python.md` — Python language, typing, formatting, testing, packaging, pre-commit

---

## Architecture

```
src/zephyrex/
  app.py                App bootstrap, instance(), build_app()
  cli.py                CLI entry point (run, bootstrap, version)
  __init__.py           Public facade: run(), instance(), set_extensions_root()
  logic/                Business logic managers (BLL_Auth, BLL_Providers, AbstractLogicManager)
  endpoints/            REST endpoints, AbstractEPTest
  database/             SQLAlchemy ORM, migrations, seeding, read replicas
  extensions/           59 pluggable extensions (auth, billing, federation, etc.)
  lib/                  Utilities (Environment, Pydantic, ContentNegotiation, etc.)
  sdk/                  Auto-generated client SDK
```

### Consumer Pattern

```python
from zephyrex import run
run(extensions="my_ext", extensions_path="./extensions", port=2000)
```

### Extension System

59 extensions, each self-contained with its own models, BLL, endpoints, tests, and migrations. Extensions loaded dynamically via `APP_EXTENSIONS` env var or `extensions=` parameter.

---

## Commands

```bash
pip install -e ".[dev]"
pytest                        # Full suite (7746 tests, 20-worker xdist)
black --check src/
mypy --ignore-missing-imports src/zephyrex/
python -m zephyrex run        # Boot server on port 1996
```

## Quality Gates

- **Tests:** 7746 passed, 0 failed, 0 errors (full xdist parallelism)
- **Mypy:** 0 errors
- **Black:** 0 violations
- **Pre-commit hook:** tests + mypy ratchet + black check

## Companion Client

The `zephyrex` npm package (client-framework repo) provides the frontend. 59 client extensions match server extensions 1:1. Consumer apps install both:

```bash
pip install zephyrex          # Server
npm install zephyrex          # Client
```

---

## Repo-Specific Notes

- Tests live co-located (`*_test.py` beside the module they test)
- Extension tests follow `EXT.Test.md` and `PRV.Test.md` patterns
- Migrations per-extension, ordered by `MigrationOrdering.py`
- Blocking/non-blocking hooks via `hook_bll` (Items 21/22/41)

## License

AGPL-3.0-or-later. SPDX header on every source file.
