# SPDX-License-Identifier: AGPL-3.0-or-later
"""Resolving the migration tree's paths leaves ``sys.path`` alone: with the
package directory on it, bare ``lib``/``extensions`` imports resolved too, so a
module could load twice under two names (as ``bootstrap`` once did)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

import zephyrex.database.migrations.Migration as migration_module
from zephyrex.database.migrations.Migration import MigrationManager


def test_resolving_paths_leaves_sys_path_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "path", list(sys.path))
    before = list(sys.path)

    env_file = Path(migration_module.__file__).with_name("env.py")
    paths = MigrationManager.env_paths(env_file)
    manager = MigrationManager.__new__(MigrationManager)
    manager.extensions_dir_name = "extensions"
    manager.database_dir_name = "database"
    manager._extensions_path_override = None
    resolved = manager._resolve_paths()

    assert sys.path == before
    package_dir = Path(migration_module.__file__).resolve().parents[2]
    assert str(package_dir) not in sys.path
    assert paths["src_dir"] == package_dir
    assert Path(resolved["src_dir"]) == package_dir
