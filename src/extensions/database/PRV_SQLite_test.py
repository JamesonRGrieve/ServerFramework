"""
Tests for PRV_SQLite provider.
Tests static provider functionality with Provider Rotation System.
"""

import os
import tempfile
from typing import Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from extensions.database.PRV_SQLite import PRV_SQLite
from extensions.database.EXT_Database import EXT_Database


class TestPRVSQLite:
    """Test suite for PRV_SQLite static database provider"""

    def test_provider_metadata(self):
        """Test static provider metadata"""
        assert PRV_SQLite.name == "SQLite"
        assert PRV_SQLite.friendly_name == "SQLite Database"
        assert PRV_SQLite.db_type == "sqlite"
        assert PRV_SQLite.extension == EXT_Database

    def test_provider_abilities(self):
        """Test provider abilities"""
        abilities = PRV_SQLite.get_abilities()

        assert isinstance(abilities, set)
        assert "database" in abilities
        assert "sql" in abilities
        assert "data_storage" in abilities
        assert "embedded" in abilities
        assert "file_based" in abilities
        assert "relational_db" in abilities

    def test_provider_env_variables(self):
        """Test provider environment variables"""
        env_vars = PRV_SQLite._env

        assert isinstance(env_vars, dict)
        assert "DATABASE_FILE" in env_vars
        assert "DATABASE_TYPE" in env_vars

    def test_get_db_classifications(self):
        """Test database classifications"""
        classifications = PRV_SQLite.get_db_classifications()

        assert isinstance(classifications, set)
        assert "relational" in classifications

    def test_get_provider_info(self):
        """Test provider information"""
        info = PRV_SQLite.get_provider_info()

        assert info["name"] == "SQLite"
        assert info["type"] == "sqlite"
        assert "relational" in info["classification"]
        assert isinstance(info["abilities"], list)

    def test_bond_instance_with_file(self):
        """Test bonding with explicit database file"""
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        try:
            tmp.close()
            config = {"database_file": tmp.name}

            PRV_SQLite.bond_instance(config)

            assert PRV_SQLite._connection_config["database_file"] == tmp.name
        finally:
            os.unlink(tmp.name)

    def test_bond_instance_default_file(self):
        """Test bonding with default database file generation"""
        config = {
            "conversation_name": "test_conversation",
            "conversation_directory": "/tmp",
        }

        PRV_SQLite.bond_instance(config)

        expected_path = "/tmp/test_conversation.db"
        assert PRV_SQLite._connection_config["database_file"] == expected_path

    # Helper to create a temporary DB file and close the handle (Windows-safe)
    def _make_tmp_db(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        return tmp

    def test_bond_instance_creates_directory(self):
        """Test that bonding creates necessary directories"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            test_db_path = os.path.join(tmp_dir, "subdir", "test.db")
            config = {"database_file": test_db_path}

            # Directory shouldn't exist yet
            assert not os.path.exists(os.path.dirname(test_db_path))

            PRV_SQLite.bond_instance(config)

            # Directory should now exist
            assert os.path.exists(os.path.dirname(test_db_path))

    @pytest.mark.asyncio
    async def test_execute_sql_simple_query(self):
        """Test SQL execution with simple query"""
        tmp = self._make_tmp_db()
        config = {"database_file": tmp.name}
        PRV_SQLite.bond_instance(config)

        try:
            # Test CREATE TABLE
            result = await PRV_SQLite.execute_sql(
                "CREATE TABLE test (id INTEGER PRIMARY KEY, name TEXT);"
            )
            assert "rows affected" in result

            # Test INSERT
            result = await PRV_SQLite.execute_sql(
                "INSERT INTO test (name) VALUES ('test_name');"
            )
            assert "1 rows affected" in result

            # Test SELECT
            result = await PRV_SQLite.execute_sql("SELECT * FROM test;")
            assert "id" in result
            assert "name" in result
            assert "test_name" in result

        finally:
            os.unlink(tmp.name)

    @pytest.mark.asyncio
    async def test_execute_sql_single_value(self):
        """Test SQL execution returning single value"""
        tmp = self._make_tmp_db()
        config = {"database_file": tmp.name}
        PRV_SQLite.bond_instance(config)

        try:
            # Setup test data
            await PRV_SQLite.execute_sql(
                "CREATE TABLE test (id INTEGER PRIMARY KEY, value INTEGER);"
            )
            await PRV_SQLite.execute_sql("INSERT INTO test (value) VALUES (42);")

            # Test single value return
            result = await PRV_SQLite.execute_sql("SELECT value FROM test LIMIT 1;")
            assert result == "42"

        finally:
            os.unlink(tmp.name)

    @pytest.mark.asyncio
    async def test_execute_sql_no_results(self):
        """Test SQL execution with no results"""
        tmp = self._make_tmp_db()
        config = {"database_file": tmp.name}
        PRV_SQLite.bond_instance(config)

        try:
            # Setup empty table
            await PRV_SQLite.execute_sql(
                "CREATE TABLE test (id INTEGER PRIMARY KEY);"
            )

            # Test empty result
            result = await PRV_SQLite.execute_sql("SELECT * FROM test;")
            assert "No rows returned" in result

        finally:
            os.unlink(tmp.name)

    @pytest.mark.asyncio
    async def test_execute_sql_clean_query_format(self):
        """Test SQL execution with various query formats"""
        tmp = self._make_tmp_db()
        config = {"database_file": tmp.name}
        PRV_SQLite.bond_instance(config)

        try:
            # Test SQL in code block
            query_with_markdown = """```sql
            CREATE TABLE test (id INTEGER);
            ```"""

            result = await PRV_SQLite.execute_sql(query_with_markdown)
            assert "rows affected" in result

            # Test query with newlines
            query_with_newlines = """SELECT 
                                COUNT(*) 
                                FROM test;"""

            result = await PRV_SQLite.execute_sql(query_with_newlines)
            assert result == "0"

        finally:
            os.unlink(tmp.name)

    @pytest.mark.asyncio
    async def test_execute_sql_error_handling(self):
        """Test SQL execution error handling"""
        tmp = self._make_tmp_db()
        config = {"database_file": tmp.name}
        PRV_SQLite.bond_instance(config)

        try:
            # Test invalid SQL
            result = await PRV_SQLite.execute_sql("INVALID SQL QUERY;")
            assert "Error executing SQL query" in result
        finally:
            os.unlink(tmp.name)

    @pytest.mark.asyncio
    async def test_get_schema_empty_database(self):
        """Test schema retrieval from empty database"""
        tmp = self._make_tmp_db()
        config = {"database_file": tmp.name}
        PRV_SQLite.bond_instance(config)

        try:
            result = await PRV_SQLite.get_schema()
            assert "No schema information available" in result
        finally:
            os.unlink(tmp.name)

    @pytest.mark.asyncio
    async def test_get_schema_with_tables(self):
        """Test schema retrieval with tables"""
        tmp = self._make_tmp_db()
        config = {"database_file": tmp.name}
        PRV_SQLite.bond_instance(config)

        try:
            # Create test table
            await PRV_SQLite.execute_sql(
                """
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    email TEXT UNIQUE
                );
            """
            )

            # Create index
            await PRV_SQLite.execute_sql(
                "CREATE INDEX idx_users_email ON users(email);"
            )

            result = await PRV_SQLite.get_schema()

            assert "CREATE TABLE users" in result
            assert "PRIMARY KEY" in result
            assert "CREATE INDEX idx_users_email" in result

        finally:
            os.unlink(tmp.name)

    @pytest.mark.asyncio
    async def test_get_schema_foreign_keys(self):
        """Test schema retrieval with foreign keys"""
        tmp = self._make_tmp_db()
        config = {"database_file": tmp.name}
        PRV_SQLite.bond_instance(config)

        try:
            # Enable foreign keys
            await PRV_SQLite.execute_sql("PRAGMA foreign_keys = ON;")

            # Create tables with foreign key relationship
            await PRV_SQLite.execute_sql(
                """
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY,
                    name TEXT
                );
            """
            )

            await PRV_SQLite.execute_sql(
                """
                CREATE TABLE posts (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER,
                    title TEXT,
                    FOREIGN KEY (user_id) REFERENCES users (id)
                );
            """
            )

            result = await PRV_SQLite.get_schema()

            assert "CREATE TABLE users" in result
            assert "CREATE TABLE posts" in result
            # Foreign key relationships should be documented
            assert "can be joined with" in result or "FOREIGN KEY" in result

        finally:
            os.unlink(tmp.name)

    @pytest.mark.asyncio
    async def test_chat_with_db(self):
        """Test natural language database chat"""
        tmp = self._make_tmp_db()
        config = {"database_file": tmp.name}
        PRV_SQLite.bond_instance(config)

        try:
            result = await PRV_SQLite.chat_with_db("Show me all users")

            # Should provide guidance for implementing natural language queries
            assert "natural language query" in result.lower()
            assert "convert your request to sql" in result.lower()

        finally:
            os.unlink(tmp.name)

    @pytest.mark.asyncio
    async def test_execute_query_alias(self):
        """Test execute_query as alias for execute_sql"""
        tmp = self._make_tmp_db()
        config = {"database_file": tmp.name}
        PRV_SQLite.bond_instance(config)

        try:
            result = await PRV_SQLite.execute_query("SELECT 1 as test_value;")
            # Accept either the column header or the raw value to be flexible across platforms
            assert "test_value" in result or "1" in result

        finally:
            os.unlink(tmp.name)

    @pytest.mark.asyncio
    async def test_write_data_insert_statement(self):
        """Test data writing with INSERT statement"""
        tmp = self._make_tmp_db()
        config = {"database_file": tmp.name}
        PRV_SQLite.bond_instance(config)

        try:
            # Setup table
            await PRV_SQLite.execute_sql(
                "CREATE TABLE test (id INTEGER, value TEXT);"
            )

            # Test INSERT data writing
            insert_sql = "INSERT INTO test (id, value) VALUES (1, 'test_data');"
            result = await PRV_SQLite.write_data(insert_sql)
            assert "1 rows affected" in result

        finally:
            os.unlink(tmp.name)

    @pytest.mark.asyncio
    async def test_write_data_non_insert(self):
        """Test data writing with non-INSERT data"""
        tmp = self._make_tmp_db()
        config = {"database_file": tmp.name}
        PRV_SQLite.bond_instance(config)

        try:
            result = await PRV_SQLite.write_data("{'some': 'json_data'}")
            assert "requires INSERT SQL statements" in result

        finally:
            os.unlink(tmp.name)

    def test_validate_config_no_file(self):
        """Test configuration validation without database file"""
        # Clear any existing configuration
        PRV_SQLite._connection_config = {}

        with patch("lib.Environment.env", return_value=""):
            issues = PRV_SQLite.validate_config()

            assert len(issues) > 0
            assert any(
                "database file not provided" in issue.lower() for issue in issues
            )

    def test_validate_config_valid_file(self):
        """Test configuration validation with valid file"""
        tmp = self._make_tmp_db()
        config = {"database_file": tmp.name}
        PRV_SQLite.bond_instance(config)

        try:
            issues = PRV_SQLite.validate_config()

            # Should have no issues for valid configuration
            assert len(issues) == 0

        finally:
            os.unlink(tmp.name)

    def test_validate_config_unwritable_directory(self):
        """Test configuration validation with unwritable directory"""
        # Test with a directory that doesn't exist and can't be created
        config = {"database_file": "/root/restricted/test.db"}
        PRV_SQLite._connection_config = config

        with patch("os.makedirs", side_effect=OSError("Permission denied")):
            with patch("os.path.exists", return_value=False):
                issues = PRV_SQLite.validate_config()

                assert len(issues) > 0
                assert any(
                    "cannot create directory" in issue.lower() for issue in issues
                )

    def test_get_connection_no_config(self):
        """Test connection retrieval without configuration"""
        PRV_SQLite._connection_config = {}

        connection = PRV_SQLite._get_connection()
        assert connection is None

    def test_get_connection_with_config(self):
        """Test connection retrieval with valid configuration"""
        tmp = self._make_tmp_db()
        config = {"database_file": tmp.name}
        PRV_SQLite.bond_instance(config)

        try:
            connection = PRV_SQLite._get_connection()
            assert connection is not None

            # Test that it's a proper SQLite connection
            cursor = connection.cursor()
            cursor.execute("SELECT 1")
            result = cursor.fetchone()
            assert result[0] == 1

            connection.close()

        finally:
            os.unlink(tmp.name)

    @pytest.mark.asyncio
    async def test_connection_error_handling(self):
        """Test error handling when connection fails"""
        # Configure with invalid path
        PRV_SQLite._connection_config = {"database_file": "/nonexistent/path/test.db"}

        with patch("os.makedirs", side_effect=OSError("Permission denied")):
            result = await PRV_SQLite.execute_sql("SELECT 1;")
            assert "Error connecting to SQLite Database" in result
