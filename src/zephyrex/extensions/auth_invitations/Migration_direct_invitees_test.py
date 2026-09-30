# SPDX-License-Identifier: AGPL-3.0-or-later
"""The 002 data migration gives each unrevoked direct invitation lacking one
an invitee row, and leaves everything else alone."""

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any, List, Tuple

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

_MIGRATION = (
    Path(__file__).parent / "migrations" / "versions" / "002_direct_invitees.py"
)
_SCHEMA = [
    "CREATE TABLE users (id VARCHAR PRIMARY KEY, email VARCHAR)",
    "CREATE TABLE invitations (id VARCHAR PRIMARY KEY, user_id VARCHAR,"
    " created_by_user_id VARCHAR, deleted_at DATETIME)",
    "CREATE TABLE invitees (id VARCHAR PRIMARY KEY, invitation_id VARCHAR,"
    " user_id VARCHAR, email VARCHAR NOT NULL, created_at DATETIME,"
    " created_by_user_id VARCHAR)",
]
_ROWS = [
    "INSERT INTO users VALUES ('u1', 'Direct@Example.com'), ('u2', 'two@example.com')",
    # needs a row; already has one (by user); revoked; email invitation
    "INSERT INTO invitations VALUES ('direct', 'u1', 'admin', NULL),"
    " ('covered', 'u2', 'admin', NULL), ('revoked', 'u1', 'admin', '2026-01-01'),"
    " ('by_email', NULL, 'admin', NULL)",
    "INSERT INTO invitees (id, invitation_id, user_id, email) VALUES"
    " ('i1', 'covered', 'u2', 'two@example.com'),"
    " ('i2', 'by_email', NULL, 'someone@example.com')",
]


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("direct_invitees", _MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _invitees(connection: Any) -> List[Tuple[Any, ...]]:
    return sorted(
        connection.execute(
            sa.text(
                "SELECT invitation_id, user_id, email, created_by_user_id"
                " FROM invitees"
            )
        ).all()
    )


def test_backfills_only_uncovered_live_direct_invitations():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        for statement in [*_SCHEMA, *_ROWS]:
            connection.execute(sa.text(statement))
        migration = _load_migration()
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            migration.upgrade()  # idempotent: the new row now covers it

        assert _invitees(connection) == [
            ("by_email", None, "someone@example.com", None),
            ("covered", "u2", "two@example.com", None),
            ("direct", "u1", "direct@example.com", "admin"),
        ]
