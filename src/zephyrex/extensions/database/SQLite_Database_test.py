# import os
# import sqlite3
# import tempfile
# from typing import List
# from unittest.mock import MagicMock, patch

# import pytest

# from AbstractTest import CategoryOfTest, ClassOfTestsConfig, SkipThisTest
# from extensions.AbstractPRVTest import AbstractPRVTest
# from extensions.database.SQLite import SQLiteProvider
# from lib.Dependencies import check_pip_dependencies, install_pip_dependencies


# # @pytest.mark.dependency(depends=["database_dependencies"])
# class TestSQLiteProvider(AbstractPRVTest):
#     """
#     Test suite for SQLite database provider.
#     Tests provider initialization, SQL execution, and SQLite database integration.
#     """

#     # Configure the test class
#     provider_class = SQLiteProvider
#     extension_id = "database"
#     test_config = ClassOfTestsConfig(
#         categories=[CategoryOfTest.EXTENSION, CategoryOfTest.INTEGRATION],
#         cleanup=True,
#     )

#     # Provider initialization parameters
#     provider_init_params = {
#         "database_file": "test.db",
#     }

#     # Expected abilities and services
#     expected_abilities = []  # SQLite provider doesn't expose abilities directly
#     expected_services = ["database", "sql", "data_storage"]

#     # Tests to skip
#     _skip_tests: List[SkipThisTest] = []

#     # @pytest.mark.dependency(name="database_dependencies")
#     def test_install_pip_dependencies(self):
#         """
#         Install PIP dependencies required by the Database extension.
#         This test must run first and all other database tests depend on it.
#         """
#         # For database tests, we need to get dependencies from the extension class
#         from extensions.database.EXT_Database import EXT_Database

#         # Get the pip dependencies from the extension class
#         pip_deps = EXT_Database.pip_dependencies

#         # Install the dependencies
#         result = install_pip_dependencies(pip_deps, only_missing=True)

#         # Check final satisfaction status after installation attempt
#         final_status = check_pip_dependencies(pip_deps)

#         # Verify installation results for required dependencies
#         for dep in pip_deps:
#             if not dep.optional:
#                 # Check if dependency is satisfied (either was already installed or just installed)
#                 is_satisfied = final_status.get(dep.name, False)
#                 was_installed = result.get(dep.name, False)

#                 assert is_satisfied or was_installed, (
#                     f"Required dependency {dep.name} is not satisfied. "
#                     f"Final status: {is_satisfied}, Installation result: {was_installed}"
#                 )

#         # SQLite is built into Python, but verify other database libraries
#         database_libs = ["psycopg2", "pymongo", "influxdb"]
#         for lib in database_libs:
#             try:
#                 __import__(lib)
#             except ImportError:
#                 # Some database libraries might not be available in test environment
#                 # but this shouldn't fail the test as they might be optional
#                 pass

#     @pytest.fixture
#     def mock_sqlite_connection(self):
#         """Mock SQLite connection"""
#         mock_connection = MagicMock()
#         mock_cursor = MagicMock()

#         mock_connection.cursor.return_value = mock_cursor
#         mock_connection.commit.return_value = None
#         mock_connection.close.return_value = None
#         mock_connection.row_factory = sqlite3.Row

#         # Mock cursor methods
#         mock_cursor.execute.return_value = None
#         mock_cursor.fetchall.return_value = []
#         mock_cursor.rowcount = 0
#         mock_cursor.description = []
#         mock_cursor.close.return_value = None

#         return mock_connection

#     @pytest.fixture
#     def provider_with_mock_connection(self, mock_sqlite_connection):
#         """SQLite provider instance with mocked connection"""
#         # We need to patch the module where sqlite3 is imported in SQLite.py
#         patcher_connect = patch(
#             "zephyrex.extensions.database.SQLite.sqlite3.connect",
#             return_value=mock_sqlite_connection,
#         )
#         patcher_exists = patch("os.path.exists", return_value=True)
#         patcher_makedirs = patch("os.makedirs")

#         # Start all patches
#         mock_connect = patcher_connect.start()
#         patcher_exists.start()
#         patcher_makedirs.start()

#         try:
#             provider = SQLiteProvider(
#                 **self.provider_init_params, extension_id=self.extension_id
#             )
#             # Store references to keep patches alive
#             provider._mock_connect = mock_connect
#             provider._mock_connection = mock_sqlite_connection
#             provider._patchers = [patcher_connect, patcher_exists, patcher_makedirs]
#             yield provider
#         finally:
#             # Stop all patches
#             patcher_connect.stop()
#             patcher_exists.stop()
#             patcher_makedirs.stop()

#     @pytest.fixture
#     def provider_with_temp_db(self):
#         """SQLite provider instance with temporary database"""
#         with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as temp_file:
#             temp_db_path = temp_file.name

#         try:
#             provider = SQLiteProvider(
#                 database_file=temp_db_path, extension_id=self.extension_id
#             )
#             yield provider
#         finally:
#             # Clean up temporary database
#             if os.path.exists(temp_db_path):
#                 os.unlink(temp_db_path)

#     def test_initialization_with_database_file(self, provider_with_mock_connection):
#         """Test provider initialization with database file"""
#         assert provider_with_mock_connection.database_file == "test.db"
#         assert provider_with_mock_connection.database_name == "test.db"
#         assert provider_with_mock_connection.database_host == ""  # Not used for SQLite
#         assert provider_with_mock_connection.database_port == 0  # Not used for SQLite
#         assert (
#             provider_with_mock_connection.database_username == ""
#         )  # Not used for SQLite
#         assert (
#             provider_with_mock_connection.database_password == ""
#         )  # Not used for SQLite

#     def test_initialization_without_database_file(self):
#         """Test provider initialization without database file"""
#         with patch("zephyrex.extensions.database.SQLite.sqlite3.connect"):
#             provider = SQLiteProvider(extension_id=self.extension_id)
#             assert provider.database_file == ""

#     def test_initialization_with_conversation_directory(self):
#         """Test provider initialization with conversation directory and name"""
#         with patch("zephyrex.extensions.database.SQLite.sqlite3.connect"):
#             with patch("os.path.exists", return_value=True):
#                 with patch("os.makedirs"):
#                     provider = SQLiteProvider(
#                         extension_id=self.extension_id,
#                         conversation_directory="./test_workspace",
#                         conversation_name="test_conversation",
#                     )
#                     expected_path = os.path.join(
#                         "./test_workspace", "test_conversation.db"
#                     )
#                     assert provider.database_file == expected_path

#     def test_configure_provider_success(self):
#         """Test successful provider configuration"""
#         with patch("zephyrex.extensions.database.SQLite.sqlite3.connect") as mock_connect:
#             with patch("os.path.exists", return_value=True):
#                 with patch("os.makedirs"):
#                     mock_connection = MagicMock()
#                     mock_connect.return_value = mock_connection

#                     provider = SQLiteProvider(
#                         database_file="test.db", extension_id=self.extension_id
#                     )

#                     # Connection should be tested during configuration
#                     mock_connect.assert_called()

#     def test_configure_provider_directory_creation(self):
#         """Test provider configuration creates database directory"""
#         with patch("zephyrex.extensions.database.SQLite.sqlite3.connect"):
#             with patch("os.path.exists", return_value=False):
#                 with patch("os.makedirs") as mock_makedirs:
#                     provider = SQLiteProvider(
#                         database_file="./test_dir/test.db",
#                         extension_id=self.extension_id,
#                     )

#                     mock_makedirs.assert_called_with("./test_dir", exist_ok=True)

#     def test_configure_provider_failure(self):
#         """Test provider configuration failure"""
#         with patch(
#             "zephyrex.extensions.database.SQLite.sqlite3.connect",
#             side_effect=Exception("DB Error"),
#         ):
#             with patch("os.path.exists", return_value=True):
#                 provider = SQLiteProvider(
#                     database_file="test.db", extension_id=self.extension_id
#                 )
#                 # Should handle the exception gracefully

#     def test_get_connection_success(self, provider_with_mock_connection):
#         """Test successful database connection"""
#         connection = provider_with_mock_connection.get_connection()
#         assert connection is not None

#     def test_get_connection_creates_directory(self):
#         """Test connection creation with directory creation"""
#         with patch("zephyrex.extensions.database.SQLite.sqlite3.connect") as mock_connect:
#             with patch("os.path.exists", return_value=False):
#                 with patch("os.makedirs") as mock_makedirs:
#                     mock_connection = MagicMock()
#                     mock_connect.return_value = mock_connection

#                     provider = SQLiteProvider(
#                         database_file="./new_dir/test.db",
#                         extension_id=self.extension_id,
#                     )

#                     connection = provider.get_connection()

#                     mock_makedirs.assert_called_with("./new_dir", exist_ok=True)
#                     assert connection is not None

#     def test_get_connection_without_database_file(self):
#         """Test connection attempt without database file"""
#         provider = SQLiteProvider(extension_id=self.extension_id)
#         connection = provider.get_connection()
#         assert connection is None

#     def test_get_connection_failure(self):
#         """Test connection failure handling"""
#         with patch(
#             "zephyrex.extensions.database.SQLite.sqlite3.connect",
#             side_effect=Exception("Connection error"),
#         ):
#             provider = SQLiteProvider(
#                 database_file="test.db", extension_id=self.extension_id
#             )
#             connection = provider.get_connection()
#             assert connection is None

#     @pytest.mark.asyncio
#     async def test_execute_sql_select_multiple_rows(
#         self, provider_with_mock_connection, mock_sqlite_connection
#     ):
#         """Test SQL execution with SELECT returning multiple rows"""
#         mock_cursor = mock_sqlite_connection.cursor.return_value
#         mock_cursor.description = [("id",), ("name",)]
#         mock_cursor.fetchall.return_value = [(1, "Alice"), (2, "Bob")]

#         result = await provider_with_mock_connection.execute_sql(
#             "SELECT id, name FROM users"
#         )

#         assert isinstance(result, str)
#         assert '"id","name"' in result  # CSV header
#         assert '"1","Alice"' in result
#         assert '"2","Bob"' in result
#         mock_cursor.execute.assert_called_once()

#     @pytest.mark.asyncio
#     async def test_execute_sql_select_single_value(
#         self, provider_with_mock_connection, mock_sqlite_connection
#     ):
#         """Test SQL execution with SELECT returning single value"""
#         mock_cursor = mock_sqlite_connection.cursor.return_value
#         mock_cursor.fetchall.return_value = [(42,)]

#         result = await provider_with_mock_connection.execute_sql(
#             "SELECT COUNT(*) FROM users"
#         )

#         assert result == "42"
#         mock_cursor.execute.assert_called_once()

#     @pytest.mark.asyncio
#     async def test_execute_sql_select_no_rows(
#         self, provider_with_mock_connection, mock_sqlite_connection
#     ):
#         """Test SQL execution with SELECT returning no rows"""
#         mock_cursor = mock_sqlite_connection.cursor.return_value
#         mock_cursor.fetchall.return_value = []

#         result = await provider_with_mock_connection.execute_sql(
#             "SELECT * FROM empty_table"
#         )

#         assert "No rows returned" in result
#         mock_cursor.execute.assert_called_once()

#     @pytest.mark.asyncio
#     async def test_execute_sql_insert_update_delete(
#         self, provider_with_mock_connection, mock_sqlite_connection
#     ):
#         """Test SQL execution with INSERT/UPDATE/DELETE"""
#         mock_cursor = mock_sqlite_connection.cursor.return_value
#         mock_cursor.rowcount = 3

#         result = await provider_with_mock_connection.execute_sql(
#             "DELETE FROM users WHERE active = 0"
#         )

#         assert "3 rows affected" in result
#         mock_cursor.execute.assert_called_once()
#         mock_sqlite_connection.commit.assert_called_once()

#     @pytest.mark.asyncio
#     async def test_execute_sql_with_code_blocks(
#         self, provider_with_mock_connection, mock_sqlite_connection
#     ):
#         """Test SQL execution with code block formatting"""
#         mock_cursor = mock_sqlite_connection.cursor.return_value
#         mock_cursor.fetchall.return_value = []

#         sql_with_blocks = """
#         ```sql
#         SELECT * FROM users
#         ```
#         """

#         result = await provider_with_mock_connection.execute_sql(sql_with_blocks)

#         # Should extract SQL from code blocks
#         mock_cursor.execute.assert_called_with("SELECT * FROM users")

#     @pytest.mark.asyncio
#     async def test_execute_sql_connection_error(self):
#         """Test SQL execution with connection error"""
#         with patch(
#             "zephyrex.extensions.database.SQLite.sqlite3.connect",
#             side_effect=Exception("Connection error"),
#         ):
#             provider = SQLiteProvider(
#                 database_file="test.db", extension_id=self.extension_id
#             )

#             result = await provider.execute_sql("SELECT * FROM users")
#             assert "Error connecting to SQLite Database" in result

#     @pytest.mark.asyncio
#     async def test_execute_sql_query_error(
#         self, provider_with_mock_connection, mock_sqlite_connection
#     ):
#         """Test SQL execution with query error"""
#         mock_cursor = mock_sqlite_connection.cursor.return_value
#         mock_cursor.execute.side_effect = sqlite3.Error("Invalid SQL")

#         result = await provider_with_mock_connection.execute_sql("INVALID SQL QUERY")

#         assert "Error executing SQL query" in result
#         assert "Invalid SQL" in result

#     @pytest.mark.asyncio
#     async def test_execute_sql_with_api_client_retry(
#         self, provider_with_mock_connection, mock_sqlite_connection
#     ):
#         """Test SQL execution with API client retry on error"""
#         # Mock ApiClient for query retry
#         mock_api_client = MagicMock()
#         mock_api_client.prompt_agent.return_value = "SELECT * FROM users"
#         provider_with_mock_connection.ApiClient = mock_api_client
#         provider_with_mock_connection.agent_name = "test_agent"

#         # Mock cursor to raise error
#         mock_cursor = mock_sqlite_connection.cursor.return_value
#         mock_cursor.execute.side_effect = sqlite3.Error("Query error")

#         result = await provider_with_mock_connection.execute_sql("INVALID QUERY")

#         # Provider should retry up to 3 times when the corrected query also fails
#         assert mock_api_client.prompt_agent.call_count == 3

#     @pytest.mark.asyncio
#     async def test_get_schema_success(
#         self, provider_with_mock_connection, mock_sqlite_connection
#     ):
#         """Test successful schema retrieval"""
#         mock_cursor = mock_sqlite_connection.cursor.return_value

#         # Mock fetchall queries
#         mock_cursor.fetchall.side_effect = [
#             [("users",), ("orders",)],  # Tables query
#             [],  # users foreign keys
#             [
#                 ("id", "INTEGER", "users", "id", 0, "CASCADE", "RESTRICT")
#             ],  # orders foreign keys
#             [],  # indexes query
#         ]

#         # Mock fetchone for CREATE TABLE queries (called once per table)
#         mock_cursor.fetchone.side_effect = [
#             (
#                 "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT);",
#             ),  # users CREATE TABLE
#             (
#                 "CREATE TABLE orders (id INTEGER PRIMARY KEY, user_id INTEGER);",
#             ),  # orders CREATE TABLE
#         ]

#         result = await provider_with_mock_connection.get_schema()

#         assert isinstance(result, str)
#         assert "CREATE TABLE users" in result
#         assert "CREATE TABLE orders" in result
#         assert "can be joined with" in result  # Foreign key relationship

#     @pytest.mark.asyncio
#     async def test_get_schema_with_indexes(
#         self, provider_with_mock_connection, mock_sqlite_connection
#     ):
#         """Test schema retrieval with indexes"""
#         mock_cursor = mock_sqlite_connection.cursor.return_value

#         # Mock queries - need to mock both fetchall and fetchone
#         mock_cursor.fetchall.side_effect = [
#             [("users",)],  # Tables query
#             [],  # Foreign keys query
#             [
#                 (
#                     "idx_users_name",
#                     "users",
#                     "CREATE INDEX idx_users_name ON users(name);",
#                 )
#             ],  # Indexes query
#         ]

#         # Mock fetchone for CREATE TABLE queries
#         mock_cursor.fetchone.return_value = (
#             "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT);",
#         )

#         result = await provider_with_mock_connection.get_schema()

#         assert "CREATE INDEX idx_users_name" in result

#     @pytest.mark.asyncio
#     async def test_get_schema_connection_error(self):
#         """Test schema retrieval with connection error"""
#         with patch(
#             "zephyrex.extensions.database.SQLite.sqlite3.connect",
#             side_effect=Exception("Connection error"),
#         ):
#             provider = SQLiteProvider(
#                 database_file="test.db", extension_id=self.extension_id
#             )

#             result = await provider.get_schema()
#             assert "Error connecting to SQLite Database" in result

#     @pytest.mark.asyncio
#     async def test_get_schema_query_error(
#         self, provider_with_mock_connection, mock_sqlite_connection
#     ):
#         """Test schema retrieval with query error"""
#         mock_cursor = mock_sqlite_connection.cursor.return_value
#         mock_cursor.fetchall.side_effect = sqlite3.Error("Schema error")

#         result = await provider_with_mock_connection.get_schema()
#         # Should handle errors gracefully and return partial schema

#     @pytest.mark.asyncio
#     async def test_chat_with_db_success(self, provider_with_mock_connection):
#         """Test successful database chat"""
#         # Mock ApiClient for natural language processing
#         mock_api_client = MagicMock()
#         mock_api_client.prompt_agent.return_value = "SELECT * FROM users"
#         provider_with_mock_connection.ApiClient = mock_api_client
#         provider_with_mock_connection.agent_name = "test_agent"

#         result = await provider_with_mock_connection.chat_with_db("Show me all users")

#         mock_api_client.prompt_agent.assert_called_once()
#         # Should execute the generated SQL

#     @pytest.mark.asyncio
#     async def test_chat_with_db_without_api_client(self, provider_with_mock_connection):
#         """Test database chat without API client"""
#         result = await provider_with_mock_connection.chat_with_db("Show me all users")
#         assert "not available" in result

#     @pytest.mark.asyncio
#     async def test_chat_with_db_includes_schema(self, provider_with_mock_connection):
#         """Test that chat includes schema information"""
#         mock_api_client = MagicMock()
#         mock_api_client.prompt_agent.return_value = "SELECT * FROM users"
#         provider_with_mock_connection.ApiClient = mock_api_client
#         provider_with_mock_connection.agent_name = "test_agent"

#         # Mock get_schema to return schema info
#         with patch.object(
#             provider_with_mock_connection,
#             "get_schema",
#             return_value="CREATE TABLE users (id INTEGER)",
#         ):
#             await provider_with_mock_connection.chat_with_db("Show me users")

#             # Check that schema was included in the prompt
#             call_args = mock_api_client.prompt_agent.call_args
#             prompt_args = call_args[1]["prompt_args"]
#             assert "CREATE TABLE users" in prompt_args["user_input"]

#     def test_get_db_type(self, provider_with_mock_connection):
#         """Test getting database type"""
#         db_type = provider_with_mock_connection.get_db_type()
#         assert db_type == "SQLite"

#     def test_validate_config_success(self, provider_with_mock_connection):
#         """Test successful configuration validation"""
#         with patch("os.path.exists", return_value=True):
#             with patch("os.access", return_value=True):
#                 result = provider_with_mock_connection.validate_config()
#                 assert result is True

#     def test_validate_config_missing_database_file(self):
#         """Test configuration validation without database file"""
#         provider = SQLiteProvider(extension_id=self.extension_id)
#         result = provider.validate_config()
#         assert result is False

#     def test_validate_config_directory_not_writable(self):
#         """Test configuration validation with non-writable directory"""
#         with patch("os.path.exists", return_value=True):
#             with patch("os.access", return_value=False):
#                 provider = SQLiteProvider(
#                     database_file="./readonly/test.db", extension_id=self.extension_id
#                 )
#                 result = provider.validate_config()
#                 assert result is False

#     def test_validate_config_directory_creation_failure(self):
#         """Test configuration validation with directory creation failure"""
#         with patch("os.path.exists", return_value=False):
#             with patch("os.makedirs", side_effect=OSError("Permission denied")):
#                 provider = SQLiteProvider(
#                     database_file="./nonexistent/test.db",
#                     extension_id=self.extension_id,
#                 )
#                 result = provider.validate_config()
#                 assert result is False

#     def test_services_property(self, provider_with_mock_connection):
#         """Test services property"""
#         services = provider_with_mock_connection.services

#         assert isinstance(services, list)
#         for expected_service in self.expected_services:
#             assert expected_service in services

#     def test_get_extension_info(self, provider_with_mock_connection):
#         """Test getting extension info"""
#         info = provider_with_mock_connection.get_extension_info()

#         assert isinstance(info, dict)
#         assert "type" in info
#         assert info["type"] == "SQLite"
#         assert "classification" in info
#         assert "description" in info
#         assert "SQLite" in info["description"]

#     def test_commands_registration(self, provider_with_mock_connection):
#         """Test that commands are properly registered"""
#         assert hasattr(provider_with_mock_connection, "commands")
#         assert isinstance(provider_with_mock_connection.commands, dict)

#         # Check for expected commands
#         expected_commands = [
#             "Custom SQL Query in SQLite Database",
#             "Get Database Schema from SQLite Database",
#             "Chat with SQLite Database",
#         ]

#         for expected_command in expected_commands:
#             assert expected_command in provider_with_mock_connection.commands
#             assert callable(provider_with_mock_connection.commands[expected_command])

#     def test_abilities_registration(self, provider_with_mock_connection):
#         """Test that abilities are registered"""
#         abilities = provider_with_mock_connection.get_abilities()
#         assert "embedded" in abilities
#         assert "file_based" in abilities

#     def test_working_directory_setup(self, provider_with_mock_connection):
#         """Test that working directory is properly set up"""
#         assert hasattr(provider_with_mock_connection, "WORKING_DIRECTORY")

#     def test_real_database_operations(self, provider_with_temp_db):
#         """Test operations with a real SQLite database"""
#         # Create a simple table
#         connection = provider_with_temp_db.get_connection()
#         assert connection is not None

#         cursor = connection.cursor()
#         cursor.execute("CREATE TABLE test_table (id INTEGER PRIMARY KEY, name TEXT)")
#         cursor.execute("INSERT INTO test_table (name) VALUES ('test_name')")
#         connection.commit()
#         cursor.close()
#         connection.close()

#     def test_database_file_path_handling(self):
#         """Test various database file path scenarios"""
#         test_cases = [
#             "test.db",
#             "./test.db",
#             "/tmp/test.db",
#             "subdir/test.db",
#         ]

#         for db_file in test_cases:
#             with patch("zephyrex.extensions.database.SQLite.sqlite3.connect"):
#                 with patch("os.path.exists", return_value=True):
#                     with patch("os.makedirs"):
#                         provider = SQLiteProvider(
#                             database_file=db_file, extension_id=self.extension_id
#                         )
#                         assert provider.database_file == db_file

#     def test_error_handling_graceful_degradation(self, provider_with_mock_connection):
#         """Test that provider handles errors gracefully"""
#         # Test that provider methods don't crash on invalid input
#         assert provider_with_mock_connection.get_db_type() == "SQLite"

#         # Test with database connection issues
#         with patch.object(
#             provider_with_mock_connection, "get_connection", return_value=None
#         ):
#             # Should handle gracefully without crashing
#             pass

#     def test_row_factory_configuration(
#         self, provider_with_mock_connection, mock_sqlite_connection
#     ):
#         """Test that SQLite row factory is configured for dictionary access"""
#         provider_with_mock_connection.get_connection()
#         assert mock_sqlite_connection.row_factory == sqlite3.Row

#     def test_database_name_from_file_path(self):
#         """Test that database_name is derived from file path"""
#         with patch("zephyrex.extensions.database.SQLite.sqlite3.connect"):
#             provider = SQLiteProvider(
#                 database_file="/path/to/my_database.db", extension_id=self.extension_id
#             )
#             assert provider.database_name == "my_database.db"
