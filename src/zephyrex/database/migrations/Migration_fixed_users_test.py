# SPDX-License-Identifier: AGPL-3.0-or-later
"""The fixed users (ROOT, SYSTEM, TEMPLATE) exist once core's migrations
have run and before any extension's do, so an extension's seed migration
can name one as a row's creator on a fresh database. Users already there
are left as they are."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    MetaData,
    String,
    Table,
    create_engine,
    insert,
    select,
)

from zephyrex.database.migrations.Migration import MigrationManager
from zephyrex.lib.Environment import env

FIXED = ("ROOT_ID", "SYSTEM_ID", "TEMPLATE_ID")


def _database(path: Path) -> tuple[str, Table, Table]:
    """A users table as core makes it, and an extension table whose rows
    name their creator by foreign key."""
    url = f"sqlite:///{path}"
    metadata = MetaData()
    users = Table(
        "users",
        metadata,
        Column("id", String, primary_key=True),
        Column("email", String),
        Column("timezone", String),
        Column("language", String),
        Column("active", Boolean),
        Column("created_at", DateTime),
        Column("created_by_user_id", String),
    )
    settings = Table(
        "settings",
        metadata,
        Column("id", String, primary_key=True),
        Column("created_by_user_id", String, ForeignKey("users.id")),
    )
    metadata.create_all(create_engine(url))
    return url, users, settings


def _manager(url: str) -> MigrationManager:
    manager = MigrationManager.__new__(MigrationManager)
    manager.db_info = {"type": "sqlite", "name": "t", "url": url}
    return manager


def test_the_fixed_users_are_there_for_an_extension_seed(tmp_path):
    url, users, settings = _database(tmp_path / "fresh.db")
    assert _manager(url).seed_fixed_users()
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        ids = set(connection.scalars(select(users.c.id)))
        connection.execute(
            insert(settings), {"id": "s1", "created_by_user_id": env("SYSTEM_ID")}
        )
    assert ids == {env(name) for name in FIXED}


def test_users_already_there_are_left_alone(tmp_path):
    url, users, _ = _database(tmp_path / "existing.db")
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(
            insert(users), {"id": env("ROOT_ID"), "email": "kept@example.test"}
        )
    manager = _manager(url)
    assert manager.seed_fixed_users() and manager.seed_fixed_users()
    with engine.connect() as connection:
        rows = dict(connection.execute(select(users.c.id, users.c.email)).all())
    assert rows[env("ROOT_ID")] == "kept@example.test"
    assert len(rows) == len(FIXED)
