# SPDX-License-Identifier: AGPL-3.0-or-later
"""The binding migration adds the delivery columns; an existing pairing
gets no binding, so it can never deliver a session."""

import importlib.util
from pathlib import Path
from types import ModuleType

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

_MIGRATION = (
    Path(__file__).parent
    / "migrations"
    / "versions"
    / "e5c1f2a3b4d6_bind_pairing_to_requester.py"
)
_TABLE = "device_pairing_requests"
_ADDED = {"binding_hash", "token_in_body", "consumed_at"}


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("bind_pairing", _MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _columns(connection) -> set:
    return {c["name"] for c in sa.inspect(connection).get_columns(_TABLE)}


def test_upgrade_adds_the_delivery_columns_and_downgrade_removes_them():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(sa.text(f"CREATE TABLE {_TABLE} (id VARCHAR PRIMARY KEY)"))
        connection.execute(sa.text(f"INSERT INTO {_TABLE} (id) VALUES ('old')"))
        migration = _load_migration()
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            assert _ADDED <= _columns(connection)
            row = connection.execute(
                sa.text(
                    f"SELECT binding_hash, token_in_body, consumed_at FROM {_TABLE}"
                )
            ).one()
            assert tuple(row) == (None, False, None)

            migration.downgrade()
            assert not _ADDED & _columns(connection)
