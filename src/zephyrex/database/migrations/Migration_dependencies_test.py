# SPDX-License-Identifier: AGPL-3.0-or-later
"""Migrations cover an extension's required dependencies, as the app loads
them: a dependency the app registers (and binds the models of) but never
migrates runs with none of its tables."""

from __future__ import annotations

from zephyrex.database.migrations.Migration import MigrationManager


def _manager() -> MigrationManager:
    manager = MigrationManager.__new__(MigrationManager)
    manager._extensions_path_override = None
    return manager


def test_a_required_dependency_is_migrated_first():
    names = _manager()._with_required_dependencies(["entitlements"])
    assert "payment" in names and "entitlements" in names
    assert names.index("payment") < names.index("entitlements")


def test_a_name_that_cannot_load_is_kept():
    names = _manager()._with_required_dependencies(["entitlements", "no_such_ext"])
    assert names[-1] == "no_such_ext" and "payment" in names
