"""
Fixtures for migration system tests.

Reuses the session-scoped `server` fixture from src/conftest.py for tests that
need a fully-booted app with extensions; provides per-function MigrationManager
fixtures backed by a tmp_path SQLite for tests that exercise MigrationManager
directly.
"""

import os
import sys
import sqlite3
from pathlib import Path

import pytest


@pytest.fixture(scope="function")
def migration_db_info(tmp_path):
    """Per-function SQLite DB info dict suitable for MigrationManager(custom_db_info=...)."""
    worker_id = os.environ.get("PYTEST_XDIST_WORKER", "main")
    db_file = tmp_path / f"test.migration.{worker_id}.database.db"
    return {
        "type": "sqlite",
        "name": f"test.migration.{worker_id}.database",
        "url": f"sqlite:///{db_file}",
        "file_path": str(db_file),
    }


@pytest.fixture(scope="function")
def migration_manager(migration_db_info):
    """A fresh MigrationManager in test_mode bound to a tmp_path SQLite."""
    src_path = Path(__file__).resolve().parent.parent.parent
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))

    from zephyrex.database.migrations.Migration import MigrationManager

    return MigrationManager(test_mode=True, custom_db_info=migration_db_info)


@pytest.fixture(scope="function")
def sqlite_connect():
    """Helper to open a sqlite3 connection from a `sqlite:///<path>` URL."""

    def _connect(url: str):
        prefix = "sqlite:///"
        assert url.startswith(prefix), f"unexpected db url: {url}"
        return sqlite3.connect(url[len(prefix):])

    return _connect
