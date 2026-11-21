# SPDX-License-Identifier: AGPL-3.0-or-later
"""An extension without migrations gets its tables from its models only on a developer's machine
and in tests; anywhere else the boot refuses, naming it."""

import pytest

from zephyrex.database.migrations.Migration import refuse_unmigrated_outside_tests


@pytest.mark.parametrize("environment", ["staging", "production", "development", ""])
def test_outside_local_and_ci_the_boot_refuses(environment: str) -> None:
    with pytest.raises(RuntimeError) as refused:
        refuse_unmigrated_outside_tests(["rpg_table", "ai_chat"], environment)
    assert "ai_chat, rpg_table" in str(refused.value)


@pytest.mark.parametrize("environment", ["local", "ci"])
def test_local_and_ci_keep_the_fallback(environment: str) -> None:
    refuse_unmigrated_outside_tests(["rpg_table"], environment)


def test_nothing_unmigrated_is_never_refused() -> None:
    refuse_unmigrated_outside_tests([], "production")


def _manager_with_tables(unmigrated: list[str], owners: list[str]):
    """A MigrationManager stand-in whose registry metadata holds one table per owner."""
    from types import SimpleNamespace

    from sqlalchemy import Column, Integer, MetaData, Table

    from zephyrex.database.migrations.Migration import MigrationManager

    metadata = MetaData()
    for index, owner in enumerate(owners):
        Table(
            f"t{index}",
            metadata,
            Column("id", Integer, primary_key=True),
            info={"extension": owner},
        )
    created: list[str] = []
    base = SimpleNamespace(
        metadata=SimpleNamespace(
            sorted_tables=metadata.sorted_tables,
            create_all=lambda bind, tables: created.extend(
                table.name for table in tables
            ),
        )
    )
    manager = MigrationManager.__new__(MigrationManager)
    manager.unmigrated_extensions = unmigrated
    manager._model_registry = SimpleNamespace(
        DB=SimpleNamespace(manager=SimpleNamespace(Base=base, _setup_engine=None))
    )
    return manager, created


def test_an_extension_with_no_tables_boots_anywhere(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # privacy, cloud and ai ship no migrations and own no tables: nothing to migrate (fb6bbaa1
    # refused Chronos's boot over privacy).
    monkeypatch.setattr(
        "zephyrex.lib.Environment.env", lambda var, default="": "production"
    )
    manager, created = _manager_with_tables(["privacy", "cloud", "ai"], [])
    manager.create_unmigrated_extension_tables()
    assert created == []


def test_only_the_extensions_that_own_tables_are_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "zephyrex.lib.Environment.env", lambda var, default="": "production"
    )
    manager, _ = _manager_with_tables(["privacy", "rpg_table"], ["rpg_table"])
    with pytest.raises(RuntimeError) as refused:
        manager.create_unmigrated_extension_tables()
    assert "rpg_table" in str(refused.value) and "privacy" not in str(refused.value)
