# Claude Code Instructions — zephyrex-server

Extensible Python (FastAPI) server framework with pluggable extensions, providers, database (SQLAlchemy + Alembic), GraphQL (Strawberry), and an SDK generator. This file documents rules **specific** to this repo; workspace-level standards live in `../CLAUDE.md` and apply here.

## Stack Standards

Read **before your first edit** in this repo:

- `/home/jameson/Source/ai-prompts/python.md` — Python language, typing, formatting, testing, packaging, pre-commit pipeline

These are the canonical home of their rules. This file does not restate them — it adds repo-specific detail only.

---

## Architecture

- **Core** (`zephyrex/`): app bootstrap, CLI, endpoints, business logic, database layer, Pydantic v2 models.
- **Extensions** (`zephyrex/extensions/`): pluggable feature modules (auth variants, billing, federation, webhooks, RPG state, etc.) — each self-contained with its own migrations, tests, and contracts.
- **Providers** (`zephyrex/extensions/PRV_Abstract_*.py`): abstract provider interfaces (AI, cache, notification, object storage, queue, search) that extensions implement.
- **SDK** (`sdk/`): auto-generated client SDK.
- **Database** (`zephyrex/database/`): SQLAlchemy ORM entities, migration ordering, tenant scoping, static seeding, read replicas.

Extensions and providers are loaded dynamically. New extensions must not require modifying the core — use the documented extension/provider contracts (`EXT.Contracts.md`, `PRV.Patterns.md`).

---

## Commands

```bash
# Install (uses pip-compile lockfile)
pip install -e ".[dev]"

# Tests
pytest                        # Full suite
pytest -k "db"                # By marker
pytest src/zephyrex/extensions/auth_mfa/  # By path

# Formatting & linting
black --check src/ tests/
ruff check src/ tests/
mypy --strict src/

# Run
python -m zephyrex     # CLI entry
```

---

## Repo-Specific Notes

- **Tests live co-located** (`*_test.py` beside the module they test), not in a separate `tests/` tree. Shared fixtures are in `src/conftest.py` and `tests/fixtures/`.
- **Extension tests** follow the abstract test patterns in `EXT.Test.md` and `PRV.Test.md`. Federation matrix tests use `AbstractFederationMatrixTest`.
- **Migrations** are per-extension, ordered by `MigrationOrdering.py`. Schema changes require versioned migrations — never `CREATE TABLE IF NOT EXISTS` alone.
- **`IMPROVEMENTS_ORDERED.md`** is the active backlog for framework hardening. Items are severity-ranked and dependency-ordered.
- **License** is currently MIT (see `pyproject.toml`). Workspace standard is AGPL-3.0 — alignment is pending.

---

## Ratchet Status

This repo currently lacks automated ratchet runners and pre-commit hooks. Adopt per `python.md` §12:

1. Wire `black --check` / `ruff check` / `mypy --strict` / `pytest` / coverage into a pre-commit hook.
2. Seed `.mypy-error-baseline`, `.ruff-warning-baseline`, `.coverage-baseline` from current state.
3. Track in a `todo.json`.
