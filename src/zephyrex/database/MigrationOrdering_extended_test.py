# SPDX-License-Identifier: AGPL-3.0-or-later
"""An extension that adds columns to another extension's table
(``@extension_model``) migrates after the table's owner, even when it
declares that owner only as an optional dependency (which adds no edge):
its migration adds a column to a table that must already exist.

Regression: ai_prompts extends conversations' ``messages`` with
``prompt_id`` and sorted first ("ai_prompts" < "conversations"), so on a
fresh database its migration found no messages table and the column was
never made."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Column, MetaData, String, Table

from zephyrex.database.MigrationOrdering import compute_migration_order


def _metadata(extenders: tuple[str, ...]) -> MetaData:
    metadata = MetaData()
    Table(
        "messages",
        metadata,
        Column("id", String, primary_key=True),
        info={"extension": "conversations", "extensions": set(extenders)},
    )
    Table(
        "prompts",
        metadata,
        Column("id", String, primary_key=True),
        info={"extension": "ai_prompts"},
    )
    return metadata


def test_an_extender_migrates_after_the_tables_owner():
    order = compute_migration_order(
        _metadata(("ai_prompts",)), {"ai_prompts": [], "conversations": []}
    )
    assert order.index("conversations") < order.index("ai_prompts")


def test_without_an_extension_the_order_is_alphabetical():
    order = compute_migration_order(
        _metadata(()), {"ai_prompts": [], "conversations": []}
    )
    assert order == ["ai_prompts", "conversations"]


def test_an_owner_extending_its_own_table_adds_no_edge():
    order = compute_migration_order(
        _metadata(("conversations",)), {"ai_prompts": [], "conversations": []}
    )
    assert order == ["ai_prompts", "conversations"]


def _manager(*extensions: type) -> Any:
    from types import SimpleNamespace

    from zephyrex.database.migrations.Migration import MigrationManager

    manager = MigrationManager.__new__(MigrationManager)
    manager._model_registry = SimpleNamespace(
        extension_registry=SimpleNamespace(extensions=list(extensions)),
        database_manager=None,
    )
    return manager


def _extension(name: str, *deps: tuple[str, bool]) -> type:
    from zephyrex.lib.Dependencies import Dependencies, EXT_Dependency

    return type(
        name,
        (),
        {
            "name": name,
            "dependencies": Dependencies(
                [EXT_Dependency(name=d, friendly_name=d, optional=o) for d, o in deps]
            ),
        },
    )


def test_a_loaded_optional_dependency_migrates_first():
    prompts = _extension("ai_prompts", ("conversations", True))
    conversations = _extension("conversations")
    manager = _manager(prompts, conversations)
    assert manager._compute_migration_order(["ai_prompts", "conversations"]) == [
        "conversations",
        "ai_prompts",
    ]


def test_an_optional_dependency_not_loaded_adds_nothing():
    prompts = _extension("ai_prompts", ("conversations", True))
    manager = _manager(prompts)
    assert manager._compute_migration_order(["ai_prompts", "beta"]) == [
        "ai_prompts",
        "beta",
    ]
