# SPDX-License-Identifier: AGPL-3.0-or-later
"""``schema_differences``: the live tables a component owns, compared with
its models, against a real (in-memory SQLite) database; the gate before a
stamp adopts an existing schema. Column differences block a stamp; index
differences are advisory."""

from __future__ import annotations

from sqlalchemy import Column, Index, Integer, MetaData, String, Table, create_engine
from sqlalchemy.engine import Engine

from zephyrex.database.migrations.Migration import schema_differences


def _campaign(metadata: MetaData, *extra: Column) -> Table:
    return Table(
        "rpg_campaign",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("name", String(100)),
        *extra,
    )


def _models() -> MetaData:
    metadata = MetaData()
    _campaign(metadata, Column("setting", String(100)))
    Table("other_ext_table", metadata, Column("id", Integer, primary_key=True))
    return metadata


def _live(*extra: Column, index: bool = False) -> Engine:
    """A database holding rpg_campaign as given, plus an unrelated table."""
    engine = create_engine("sqlite://")
    live = MetaData()
    table = _campaign(live, *extra)
    if index:
        Index("ix_rpg_campaign_name", table.c.name)
    Table("unrelated", live, Column("id", Integer, primary_key=True))
    live.create_all(engine)
    return engine


def test_matching_tables_have_no_differences():
    engine = _live(Column("setting", String(100)))
    with engine.connect() as connection:
        found = schema_differences(connection, _models(), ["rpg_campaign"])
    assert found == {"blocking": [], "advisory": []}


def test_a_missing_column_blocks():
    engine = _live()
    with engine.connect() as connection:
        found = schema_differences(connection, _models(), ["rpg_campaign"])
    assert found["advisory"] == []
    (line,) = found["blocking"]
    assert (
        line.startswith("add_column") and "rpg_campaign" in line and "setting" in line
    )


def test_an_index_difference_is_advisory():
    engine = _live(Column("setting", String(100)), index=True)
    with engine.connect() as connection:
        found = schema_differences(connection, _models(), ["rpg_campaign"])
    assert found["blocking"] == []
    (line,) = found["advisory"]
    assert line.startswith("remove_index") and "ix_rpg_campaign_name" in line


def test_tables_outside_the_component_are_ignored():
    # other_ext_table is missing from the DB and unrelated is extra in it;
    # neither is rpg_campaign's component, so neither is reported.
    engine = _live(Column("setting", String(100)))
    with engine.connect() as connection:
        found = schema_differences(connection, _models(), ["rpg_campaign"])
    assert found == {"blocking": [], "advisory": []}
