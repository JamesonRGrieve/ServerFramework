# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Tests for PRV_SQLite provider.

Per AGENTS.md no-mock pillar: these tests run against real SQLite files in
``tmp_path``, addressed through real provider instances of a built database
app. The one pure-utility test for ``validate_config`` against an unwritable
directory is tagged ``@pytest.mark.unit`` and locally imports
``unittest.mock`` for filesystem-call isolation only.
"""

import sqlite3
from pathlib import Path
from typing import Callable

import pytest

from zephyrex.extensions.database.EXT_Database import (
    DatabaseConnection,
    EXT_Database,
)
from zephyrex.extensions.database.PRV_SQLite import PRV_SQLite
from zephyrex.extensions.ExternalErrors import (
    InvalidInputExternalError,
    TransientExternalError,
)
from zephyrex.logic.BLL_Providers import ProviderInstanceModel


@pytest.fixture
def db_file(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


@pytest.fixture
def sqlite_instance(
    provider_instance: Callable[..., ProviderInstanceModel], db_file: Path
) -> ProviderInstanceModel:
    """A SQLite provider instance whose ``api_key`` names ``db_file``."""
    return provider_instance(PRV_SQLite, api_key=str(db_file))


class TestPRVSQLiteMetadata:
    """Static provider metadata."""

    def test_provider_metadata(self):
        assert PRV_SQLite.name == "SQLite"
        assert PRV_SQLite.friendly_name == "SQLite Database"
        assert PRV_SQLite.db_type == "sqlite"
        assert PRV_SQLite.extension == EXT_Database

    def test_provider_abilities(self):
        abilities = PRV_SQLite.get_abilities()

        assert isinstance(abilities, set)
        assert "database" in abilities
        assert "sql" in abilities
        assert "data_storage" in abilities
        assert "embedded" in abilities
        assert "file_based" in abilities
        assert "relational_db" in abilities

    def test_provider_env_variables(self):
        env_vars = PRV_SQLite._env

        assert isinstance(env_vars, dict)
        assert "DATABASE_FILE" in env_vars
        assert "DATABASE_TYPE" in env_vars

    def test_get_db_classifications(self):
        classifications = PRV_SQLite.get_db_classifications()

        assert isinstance(classifications, set)
        assert "relational" in classifications

    def test_get_provider_info(self):
        info = PRV_SQLite.get_provider_info()

        assert info["name"] == "SQLite"
        assert info["type"] == "sqlite"
        assert "relational" in info["classification"]
        assert isinstance(info["abilities"], list)


class TestPRVSQLiteConnectionConfig:
    """The database file resolves from the instance first, env last."""

    def test_bond_instance_uses_the_instance_file(self, sqlite_instance, db_file):
        connection = PRV_SQLite.bond_instance(sqlite_instance)

        assert isinstance(connection, DatabaseConnection)
        assert connection.model is sqlite_instance
        assert connection.config == {"database_file": str(db_file)}

    def test_instance_file_wins_over_env(self, sqlite_instance, db_file, set_env):
        set_env("DATABASE_FILE", "/elsewhere/env.db")

        config = PRV_SQLite.connection_config(sqlite_instance)

        assert config["database_file"] == str(db_file)

    def test_setting_used_when_the_instance_has_no_file(
        self, provider_instance, db_file, set_env
    ):
        set_env("DATABASE_FILE", "/elsewhere/env.db")
        instance = provider_instance(
            PRV_SQLite, settings={"database_file": str(db_file)}
        )

        assert PRV_SQLite.connection_config(instance)["database_file"] == str(db_file)

    def test_env_fallback(self, provider_instance, db_file, set_env):
        set_env("DATABASE_FILE", str(db_file))
        instance = provider_instance(PRV_SQLite)

        assert PRV_SQLite.connection_config(instance)["database_file"] == str(db_file)

    def test_env_only_config_without_an_instance(self, db_file, set_env):
        set_env("DATABASE_FILE", str(db_file))

        assert PRV_SQLite.connection_config(None) == {"database_file": str(db_file)}


class TestPRVSQLiteExecution:
    """Statements run against the file named by the rotated instance."""

    async def test_executes_against_the_instance_file(self, sqlite_instance, db_file):
        await PRV_SQLite.execute_sql(
            sqlite_instance, "CREATE TABLE marker (id INTEGER PRIMARY KEY);"
        )

        # The table landed in the instance's file, not anywhere else.
        with sqlite3.connect(db_file) as conn:
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table';"
            ).fetchall()
        assert tables == [("marker",)]

    async def test_creates_the_file_directory(self, provider_instance, tmp_path):
        db_path = tmp_path / "subdir" / "test.db"
        instance = provider_instance(PRV_SQLite, api_key=str(db_path))

        assert await PRV_SQLite.execute_sql(instance, "SELECT 1;") == "1"
        assert db_path.parent.is_dir()

    async def test_execute_sql_simple_query(self, sqlite_instance):
        result = await PRV_SQLite.execute_sql(
            sqlite_instance, "CREATE TABLE test (id INTEGER PRIMARY KEY, name TEXT);"
        )
        assert "rows affected" in result

        result = await PRV_SQLite.execute_sql(
            sqlite_instance, "INSERT INTO test (name) VALUES ('test_name');"
        )
        assert "1 rows affected" in result

        result = await PRV_SQLite.execute_sql(sqlite_instance, "SELECT * FROM test;")
        assert result == '"id","name"\n"1","test_name"\n'

    async def test_execute_sql_single_value(self, sqlite_instance):
        await PRV_SQLite.execute_sql(
            sqlite_instance,
            "CREATE TABLE test (id INTEGER PRIMARY KEY, value INTEGER);",
        )
        await PRV_SQLite.execute_sql(
            sqlite_instance, "INSERT INTO test (value) VALUES (42);"
        )

        result = await PRV_SQLite.execute_sql(
            sqlite_instance, "SELECT value FROM test LIMIT 1;"
        )
        assert result == "42"

    async def test_execute_sql_no_results(self, sqlite_instance):
        await PRV_SQLite.execute_sql(
            sqlite_instance, "CREATE TABLE test (id INTEGER PRIMARY KEY);"
        )

        result = await PRV_SQLite.execute_sql(sqlite_instance, "SELECT * FROM test;")
        assert "No rows returned" in result

    async def test_execute_sql_cte_returns_rows(self, sqlite_instance):
        """A row-returning CTE (leading ``WITH``) returns its rows.

        Regression for issue #229: classification keyed on the ``SELECT``
        keyword misclassified a ``WITH ... SELECT`` query as a write, which
        was committed and reported as "0 rows affected" instead of returning
        the result set. Row-vs-write is now decided on ``cursor.description``
        (the presence of a result set), matching the sibling relational
        providers.
        """
        result = await PRV_SQLite.execute_sql(
            sqlite_instance, "WITH t AS (SELECT 1 AS x) SELECT x FROM t;"
        )
        assert "rows affected" not in result
        assert result == "1"

    async def test_execute_sql_pragma_returns_rows(self, sqlite_instance):
        """A row-returning ``PRAGMA`` returns its result set, not a write count.

        Regression for issue #229: ``PRAGMA table_info(...)`` returns rows but
        does not start with ``SELECT``, so the old keyword-based classification
        treated it as a write. It must surface the introspection rows instead.
        """
        await PRV_SQLite.execute_sql(
            sqlite_instance,
            "CREATE TABLE widgets (id INTEGER PRIMARY KEY, label TEXT);",
        )
        result = await PRV_SQLite.execute_sql(
            sqlite_instance, "PRAGMA table_info(widgets);"
        )
        assert "rows affected" not in result
        # table_info returns one row per column; both column names appear
        # in the CSV payload.
        assert "id" in result
        assert "label" in result

    async def test_execute_sql_clean_query_format(self, sqlite_instance):
        query_with_markdown = """```sql
        CREATE TABLE test (id INTEGER);
        ```"""
        result = await PRV_SQLite.execute_sql(sqlite_instance, query_with_markdown)
        assert "rows affected" in result

        query_with_newlines = """SELECT
                            COUNT(*)
                            FROM test;"""
        result = await PRV_SQLite.execute_sql(sqlite_instance, query_with_newlines)
        assert result == "0"

    async def test_bad_statement_raises_invalid_input(self, sqlite_instance):
        with pytest.raises(InvalidInputExternalError) as raised:
            await PRV_SQLite.execute_sql(sqlite_instance, "INVALID SQL QUERY;")

        assert "Error executing SQL query" in raised.value.message
        assert raised.value.provider == "SQLite"
        assert raised.value.cause is not None

    async def test_unopenable_file_raises_transient(self, provider_instance, tmp_path):
        # A regular file where the database's directory should be: the
        # connection cannot be made, so the rotation should fail over.
        blocker = tmp_path / "not_a_directory"
        blocker.write_text("")
        instance = provider_instance(PRV_SQLite, api_key=str(blocker / "test.db"))

        with pytest.raises(TransientExternalError) as raised:
            await PRV_SQLite.execute_sql(instance, "SELECT 1;")

        assert "Error connecting to SQLite Database" in raised.value.message
        assert raised.value.provider == "SQLite"
        assert isinstance(raised.value.cause, OSError)

    async def test_directory_as_file_raises_transient(
        self, provider_instance, tmp_path
    ):
        instance = provider_instance(PRV_SQLite, api_key=str(tmp_path))

        with pytest.raises(TransientExternalError):
            await PRV_SQLite.execute_sql(instance, "SELECT 1;")

    async def test_unconfigured_file_raises_transient(self, provider_instance, set_env):
        set_env("DATABASE_FILE", "")
        instance = provider_instance(PRV_SQLite)

        with pytest.raises(TransientExternalError, match="database_file"):
            await PRV_SQLite.execute_sql(instance, "SELECT 1;")


class TestPRVSQLiteSchemaAndChat:
    async def test_get_schema_empty_database(self, sqlite_instance):
        result = await PRV_SQLite.get_schema(sqlite_instance)
        assert "No schema information available" in result

    async def test_get_schema_with_tables(self, sqlite_instance):
        await PRV_SQLite.execute_sql(
            sqlite_instance,
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT UNIQUE
            );
        """,
        )
        await PRV_SQLite.execute_sql(
            sqlite_instance, "CREATE INDEX idx_users_email ON users(email);"
        )

        result = await PRV_SQLite.get_schema(sqlite_instance)

        assert "CREATE TABLE users" in result
        assert "PRIMARY KEY" in result
        assert "CREATE INDEX idx_users_email" in result

    async def test_get_schema_foreign_keys(self, sqlite_instance):
        await PRV_SQLite.execute_sql(
            sqlite_instance,
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY,
                name TEXT
            );
        """,
        )
        await PRV_SQLite.execute_sql(
            sqlite_instance,
            """
            CREATE TABLE posts (
                id INTEGER PRIMARY KEY,
                user_id INTEGER,
                title TEXT,
                FOREIGN KEY (user_id) REFERENCES users (id)
            );
        """,
        )

        result = await PRV_SQLite.get_schema(sqlite_instance)

        assert "CREATE TABLE users" in result
        assert "CREATE TABLE posts" in result
        assert "-- posts.user_id can be joined with users.id" in result

    async def test_get_schema_unreachable_raises_transient(
        self, provider_instance, tmp_path
    ):
        instance = provider_instance(PRV_SQLite, api_key=str(tmp_path))

        with pytest.raises(TransientExternalError):
            await PRV_SQLite.get_schema(instance)

    async def test_chat_with_db(self, sqlite_instance):
        result = await PRV_SQLite.chat_with_db(sqlite_instance, "Show me all users")

        assert 'Natural language query: "Show me all users"' in result
        assert "convert your request to sql" in result.lower()

    async def test_execute_query_alias(self, sqlite_instance):
        result = await PRV_SQLite.execute_query(
            sqlite_instance, "SELECT 1 as test_value;"
        )
        assert result == "1"

    async def test_write_data_insert_statement(self, sqlite_instance):
        await PRV_SQLite.execute_sql(
            sqlite_instance, "CREATE TABLE test (id INTEGER, value TEXT);"
        )

        result = await PRV_SQLite.write_data(
            sqlite_instance, "INSERT INTO test (id, value) VALUES (1, 'test_data');"
        )
        assert "1 rows affected" in result

    async def test_write_data_non_insert_raises_invalid_input(self, sqlite_instance):
        with pytest.raises(
            InvalidInputExternalError, match="requires INSERT SQL statements"
        ):
            await PRV_SQLite.write_data(sqlite_instance, "{'some': 'json_data'}")


class TestPRVSQLiteValidateConfig:
    """``validate_config`` checks the environment-configured file."""

    def test_validate_config_no_file(self, set_env):
        set_env("DATABASE_FILE", "")

        issues = PRV_SQLite.validate_config()

        assert any("database file not provided" in issue.lower() for issue in issues)

    def test_validate_config_valid_file(self, db_file, set_env):
        set_env("DATABASE_FILE", str(db_file))

        assert PRV_SQLite.validate_config() == []

    @pytest.mark.unit
    def test_validate_config_unwritable_directory(self, set_env):
        """validate_config flags a directory it cannot create.

        Pure-utility unit test (``@pytest.mark.unit``): we isolate the
        ``os.makedirs`` syscall to avoid actually requiring an unwritable
        directory on the host, which is the only correct way to assert
        the function's error-path behavior cross-platform.
        """
        from unittest.mock import patch

        set_env("DATABASE_FILE", "/root/restricted/test.db")

        with patch("os.makedirs", side_effect=OSError("Permission denied")):
            with patch("os.path.exists", return_value=False):
                issues = PRV_SQLite.validate_config()

        assert any("cannot create directory" in issue.lower() for issue in issues)


class TestPRVSQLiteGetConnection:
    def test_get_connection_no_config(self):
        with pytest.raises(TransientExternalError, match="database_file"):
            PRV_SQLite._get_connection({"database_file": None})

    def test_get_connection_with_config(self, db_file):
        connection = PRV_SQLite._get_connection({"database_file": str(db_file)})
        try:
            assert connection.execute("SELECT 1").fetchone()[0] == 1
        finally:
            connection.close()
