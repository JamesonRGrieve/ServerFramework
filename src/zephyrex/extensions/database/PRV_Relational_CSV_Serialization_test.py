# SPDX-License-Identifier: AGPL-3.0-or-later
"""Byte-identity tests for the relational providers' SQL->CSV serialization.

Regression guard for issue #230 (finding E1): the result->CSV serialization
accumulated with ``out += ",".join(...) + "\\n"`` inside the ``for row in rows``
loop, rebuilding the whole string every iteration -> O(rows^2) in result-set
size. The accumulation is now an O(rows) list-append + single ``"\\n".join(...)``.

These tests pin the EXACT output bytes so the linear-time rewrite stays
byte-for-byte identical to the prior quadratic one: the quoted column header,
per-cell quoting, comma separators, the trailing newline, the single-scalar
special case (returned unquoted), and the empty-result message.

The serialization is database-engine-agnostic -- it formats
``cursor.description`` and the sequence returned by ``cursor.fetchall()``, with
nothing dialect-specific. Postgres/MySQL/MSSQL have no live server or driver in
CI, so their real ``execute_sql`` classmethod is exercised against a REAL
sqlite3 database injected through ``_get_connection`` (a genuine DB, not a
hand-fed fake cursor -- the rows the serialization formats are actual query
results). SQLite runs natively against its own tempfile DB. The DB-boundary
substitution is why these carry ``@pytest.mark.unit``.
"""

import os
import sqlite3
import tempfile
import types

import pytest

from zephyrex.extensions.database import PRV_Postgres as pg_mod
from zephyrex.extensions.database.EXT_Database import (
    AbstractDatabaseExtensionProvider,
)
from zephyrex.extensions.database.PRV_MSSQL import PRV_MSSQL
from zephyrex.extensions.database.PRV_MySQL import PRV_MySQL
from zephyrex.extensions.database.PRV_Postgres import PRV_Postgres
from zephyrex.extensions.database.PRV_SQLite import PRV_SQLite

# Fixed multi-column, multi-row dataset seeded into a real sqlite3 DB.
ROWS = [(1, "alpha"), (2, "beta"), (3, "gamma")]

QUERY_MULTI_ROW = "SELECT id, name FROM demo ORDER BY id;"
QUERY_SINGLE_CELL = "SELECT name FROM demo WHERE id = 1;"
QUERY_EMPTY = "SELECT id, name FROM demo WHERE id = 999;"

# Byte-exact expected serializations (the #230 regression pins). The header and
# every row are wrapped in double quotes, comma-separated, and each line --
# including the last -- is newline-terminated.
EXPECTED_MULTI_ROW = '"id","name"\n"1","alpha"\n"2","beta"\n"3","gamma"\n'
# Single scalar special case: returned raw via ``str(...)``, with NO quoting.
EXPECTED_SINGLE_CELL = "alpha"
EXPECTED_EMPTY = "Query executed successfully. No rows returned."

ALL_PROVIDERS = [PRV_SQLite, PRV_Postgres, PRV_MySQL, PRV_MSSQL]
PROVIDER_IDS = [p.__name__ for p in ALL_PROVIDERS]


class _SqliteConnAdapter:
    """Wrap a real sqlite3 connection to satisfy the provider call shapes.

    The only shim is tolerating the ``cursor_factory=`` keyword the Postgres
    provider passes to ``connection.cursor(...)``; every call forwards to the
    REAL sqlite3 connection, so the rows the serialization formats are genuine
    query results rather than fabricated values.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def cursor(self, *args, **kwargs):
        return self._conn.cursor()

    def commit(self) -> None:
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


def _seed(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE demo (id INTEGER PRIMARY KEY, name TEXT);")
        conn.executemany("INSERT INTO demo (id, name) VALUES (?, ?);", ROWS)
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def demo_db():
    """A real, seeded sqlite3 database file (per-connection reopen safe)."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    _seed(tmp.name)
    try:
        yield tmp.name
    finally:
        os.unlink(tmp.name)


def _point_at_real_sqlite(
    monkeypatch, provider_cls: type[AbstractDatabaseExtensionProvider], db_path: str
) -> None:
    """Point a non-sqlite provider's ``_get_connection`` at a real sqlite3 DB.

    Each call opens a fresh connection (the providers close it in ``finally``),
    matching how the real drivers behave. Only the DB boundary is substituted;
    the serialization under test runs unmodified on real result rows.
    """

    def _fake_get_connection():
        return _SqliteConnAdapter(sqlite3.connect(db_path))

    monkeypatch.setattr(
        provider_cls, "_get_connection", staticmethod(_fake_get_connection)
    )
    if provider_cls is PRV_Postgres:
        # The Postgres path names ``psycopg2.extras.DictCursor`` when building
        # its cursor; the driver is absent in CI, so provide a harmless
        # stand-in the adapter ignores.
        monkeypatch.setattr(
            pg_mod,
            "psycopg2",
            types.SimpleNamespace(extras=types.SimpleNamespace(DictCursor=object)),
            raising=False,
        )


async def _execute(
    monkeypatch,
    provider_cls: type[AbstractDatabaseExtensionProvider],
    db_path: str,
    query: str,
) -> str:
    if provider_cls is PRV_SQLite:
        provider_cls.bond_instance({"database_file": db_path})
    else:
        _point_at_real_sqlite(monkeypatch, provider_cls, db_path)
    # ``execute_sql`` is contractually ``-> str`` (EXT_Database base); the
    # provider base chain is opaque to mypy, so narrow at this boundary.
    result: str = await provider_cls.execute_sql(query)
    return result


@pytest.mark.unit
@pytest.mark.parametrize("provider_cls", ALL_PROVIDERS, ids=PROVIDER_IDS)
class TestRelationalCsvByteIdentity:
    """The list-join rewrite must reproduce the prior bytes exactly."""

    async def test_multi_row_multi_column_is_byte_identical(
        self, provider_cls, demo_db, monkeypatch
    ):
        out = await _execute(monkeypatch, provider_cls, demo_db, QUERY_MULTI_ROW)
        assert out == EXPECTED_MULTI_ROW

    async def test_single_cell_scalar_special_case(
        self, provider_cls, demo_db, monkeypatch
    ):
        out = await _execute(monkeypatch, provider_cls, demo_db, QUERY_SINGLE_CELL)
        assert out == EXPECTED_SINGLE_CELL

    async def test_empty_result_message(self, provider_cls, demo_db, monkeypatch):
        out = await _execute(monkeypatch, provider_cls, demo_db, QUERY_EMPTY)
        assert out == EXPECTED_EMPTY
