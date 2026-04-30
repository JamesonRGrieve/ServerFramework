# import json
# from typing import List
# from unittest.mock import MagicMock, patch

# import pytest

# from AbstractTest import CategoryOfTest, ClassOfTestsConfig, SkipThisTest
# from extensions.AbstractPRVTest import AbstractPRVTest
# from extensions.database.InfluxDB import InfluxDBProvider
# from lib.Dependencies import check_pip_dependencies, install_pip_dependencies


# # @pytest.mark.dependency(depends=["database_dependencies"])
# class TestInfluxDBProvider(AbstractPRVTest):
#     """
#     Test suite for InfluxDB database provider.
#     Tests provider initialization, query execution, and InfluxDB API integration.
#     """

#     # Configure the test class
#     provider_class = InfluxDBProvider
#     extension_id = "database"
#     test_config = ClassOfTestsConfig(
#         categories=[CategoryOfTest.EXTENSION, CategoryOfTest.INTEGRATION],
#         cleanup=True,
#     )

#     # Provider initialization parameters
#     provider_init_params = {
#         "database_host": "localhost",
#         "database_port": 8086,
#         "database_name": "test_db",
#         "database_username": "test_user",
#         "database_password": "test_password",
#     }

#     # Expected abilities and services
#     expected_abilities = []  # InfluxDB provider doesn't expose abilities directly
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

#         # Verify InfluxDB libraries specifically
#         try:
#             import influxdb
#         except ImportError:
#             # InfluxDB library might not be available in test environment
#             # but this shouldn't fail the test as it might be optional
#             pass

#     @pytest.fixture
#     def mock_influxdb_v1_client(self):
#         """Mock InfluxDB 1.x client with proper serializable responses"""
#         mock_client = MagicMock()

#         # Create a proper mock result that can be JSON serialized
#         mock_result = MagicMock()
#         mock_result.raw = {
#             "series": [
#                 {
#                     "name": "temperature",
#                     "columns": ["time", "value", "location"],
#                     "values": [["2023-01-01T00:00:00Z", 23.5, "room1"]],
#                 }
#             ]
#         }
#         mock_client.query.return_value = mock_result

#         mock_client.write_points.return_value = True
#         mock_client.get_list_database.return_value = [{"name": "test_db"}]
#         mock_client.get_list_measurements.return_value = [{"name": "temperature"}]
#         mock_client.switch_database.return_value = None
#         mock_client.close.return_value = None
#         return mock_client

#     @pytest.fixture
#     def mock_influxdb_v2_client(self):
#         """Mock InfluxDB 2.x client with proper serializable responses"""
#         mock_client = MagicMock()
#         mock_query_api = MagicMock()
#         mock_write_api = MagicMock()
#         mock_buckets_api = MagicMock()

#         mock_client.query_api.return_value = mock_query_api
#         mock_client.write_api.return_value = mock_write_api
#         mock_client.buckets_api.return_value = mock_buckets_api
#         mock_client.close.return_value = None

#         # Create properly structured mock data that can be serialized
#         mock_record = MagicMock()
#         mock_record.values = {
#             "_time": "2023-01-01T00:00:00Z",
#             "_measurement": "temperature",
#             "_field": "value",
#             "_value": 23.5,
#             "location": "room1",
#         }

#         mock_table = MagicMock()
#         mock_table.columns = ["_time", "_measurement", "_field", "_value", "location"]
#         mock_table.records = [mock_record]

#         mock_query_api.query.return_value = [mock_table]

#         # Mock buckets API
#         mock_bucket = MagicMock()
#         mock_bucket.name = "test_bucket"
#         mock_buckets_result = MagicMock()
#         mock_buckets_result.buckets = [mock_bucket]
#         mock_buckets_api.find_buckets.return_value = mock_buckets_result

#         return mock_client

#     @pytest.fixture
#     def provider_v1_with_mock_client(self, mock_influxdb_v1_client):
#         """InfluxDB 1.x provider instance with mocked client"""
#         # Use persistent patchers instead of context managers
#         patcher_client = patch(
#             "serverframework.extensions.database.InfluxDB.InfluxDBClient",
#             return_value=mock_influxdb_v1_client,
#         )

#         mock_client_class = patcher_client.start()

#         try:
#             provider = InfluxDBProvider(
#                 influxdb_version="1",
#                 extension_id=self.extension_id,
#                 **self.provider_init_params,
#             )
#             # Store references to keep patches alive
#             provider._mock_client = mock_influxdb_v1_client
#             provider._patcher_client = patcher_client
#             yield provider
#         finally:
#             patcher_client.stop()

#     @pytest.fixture
#     def provider_v2_with_mock_client(self, mock_influxdb_v2_client):
#         """InfluxDB 2.x provider instance with mocked client"""
#         # Use persistent patchers instead of context managers
#         patcher_has_influxdb2 = patch(
#             "serverframework.extensions.database.InfluxDB.has_influxdb2", True
#         )
#         patcher_client = patch(
#             "serverframework.extensions.database.InfluxDB.InfluxDBClient2",
#             return_value=mock_influxdb_v2_client,
#         )
#         patcher_synchronous = patch("serverframework.extensions.database.InfluxDB.SYNCHRONOUS")

#         patcher_has_influxdb2.start()
#         mock_client_class = patcher_client.start()
#         patcher_synchronous.start()

#         try:
#             provider = InfluxDBProvider(
#                 influxdb_version="2",
#                 database_host="localhost",
#                 influxdb_org="test_org",
#                 influxdb_token="test_token",
#                 influxdb_bucket="test_bucket",
#                 extension_id=self.extension_id,
#             )
#             # Store references to keep patches alive
#             provider._mock_client = mock_influxdb_v2_client
#             provider._patcher_has_influxdb2 = patcher_has_influxdb2
#             provider._patcher_client = patcher_client
#             provider._patcher_synchronous = patcher_synchronous
#             yield provider
#         finally:
#             patcher_has_influxdb2.stop()
#             patcher_client.stop()
#             patcher_synchronous.stop()

#     @pytest.fixture
#     def provider_without_influxdb2(self):
#         """Provider instance when InfluxDB 2.x library is not available"""
#         with patch("serverframework.extensions.database.InfluxDB.has_influxdb2", False):
#             provider = InfluxDBProvider(
#                 influxdb_version="2",
#                 extension_id=self.extension_id,
#                 **self.provider_init_params,
#             )
#             return provider

#     def test_initialization_influxdb_v1(self, provider_v1_with_mock_client):
#         """Test provider initialization for InfluxDB 1.x"""
#         assert provider_v1_with_mock_client.influxdb_version == "1"
#         assert provider_v1_with_mock_client.database_host == "localhost"
#         assert provider_v1_with_mock_client.database_port == 8086
#         assert provider_v1_with_mock_client.database_name == "test_db"
#         assert provider_v1_with_mock_client.database_username == "test_user"
#         assert provider_v1_with_mock_client.database_password == "test_password"

#     def test_initialization_influxdb_v2(self, provider_v2_with_mock_client):
#         """Test provider initialization for InfluxDB 2.x"""
#         assert provider_v2_with_mock_client.influxdb_version == "2"
#         assert provider_v2_with_mock_client.influxdb_org == "test_org"
#         assert provider_v2_with_mock_client.influxdb_token == "test_token"
#         assert provider_v2_with_mock_client.influxdb_bucket == "test_bucket"

#     def test_initialization_without_influxdb2_library(self, provider_without_influxdb2):
#         """Test provider initialization when InfluxDB 2.x library is not available"""
#         assert provider_without_influxdb2.influxdb_version == "2"

#     def test_configure_provider_v1_success(self):
#         """Test successful InfluxDB 1.x provider configuration"""
#         with patch("serverframework.extensions.database.InfluxDB.InfluxDBClient") as mock_client_class:
#             mock_client = MagicMock()
#             mock_client_class.return_value = mock_client

#             provider = InfluxDBProvider(
#                 influxdb_version="1",
#                 extension_id=self.extension_id,
#                 **self.provider_init_params,
#             )

#             # Configuration should be called during initialization
#             mock_client_class.assert_called_once()

#     def test_configure_provider_v2_success(self):
#         """Test successful InfluxDB 2.x provider configuration"""
#         with patch("serverframework.extensions.database.InfluxDB.has_influxdb2", True):
#             with patch(
#                 "serverframework.extensions.database.InfluxDB.InfluxDBClient2"
#             ) as mock_client_class:
#                 mock_client = MagicMock()
#                 mock_client_class.return_value = mock_client

#                 provider = InfluxDBProvider(
#                     influxdb_version="2",
#                     influxdb_org="test_org",
#                     influxdb_token="test_token",
#                     extension_id=self.extension_id,
#                     database_host="localhost",
#                 )

#                 mock_client_class.assert_called_once_with(
#                     url="http://localhost:8086", token="test_token", org="test_org"
#                 )

#     def test_configure_provider_v2_without_library(self):
#         """Test InfluxDB 2.x provider configuration without library"""
#         with patch("serverframework.extensions.database.InfluxDB.has_influxdb2", False):
#             provider = InfluxDBProvider(
#                 influxdb_version="2",
#                 extension_id=self.extension_id,
#                 **self.provider_init_params,
#             )
#             # Should handle gracefully

#     def test_get_connection_v1(self, provider_v1_with_mock_client):
#         """Test getting InfluxDB 1.x connection"""
#         connection = provider_v1_with_mock_client.get_connection()
#         assert connection is not None

#     def test_get_connection_v2(self, provider_v2_with_mock_client):
#         """Test getting InfluxDB 2.x connection"""
#         connection = provider_v2_with_mock_client.get_connection()
#         assert connection is not None

#     def test_get_connection_failure(self):
#         """Test connection failure handling"""
#         with patch(
#             "serverframework.extensions.database.InfluxDB.InfluxDBClient",
#             side_effect=Exception("Connection error"),
#         ):
#             provider = InfluxDBProvider(
#                 influxdb_version="1",
#                 extension_id=self.extension_id,
#                 **self.provider_init_params,
#             )
#             connection = provider.get_connection()
#             assert connection is None

#     @pytest.mark.asyncio
#     async def test_execute_query_v1_success(
#         self, provider_v1_with_mock_client, mock_influxdb_v1_client
#     ):
#         """Test successful InfluxQL query execution"""
#         result = await provider_v1_with_mock_client.execute_query(
#             "SELECT * FROM temperature WHERE time > now() - 1h"
#         )

#         assert isinstance(result, str)
#         result_data = json.loads(result)
#         assert isinstance(result_data, list)
#         assert len(result_data) > 0
#         mock_influxdb_v1_client.query.assert_called_once()

#     @pytest.mark.asyncio
#     async def test_execute_query_with_code_blocks(self, provider_v1_with_mock_client):
#         """Test query execution with code block formatting"""
#         query_with_blocks = """
#         ```influxql
#         SELECT * FROM temperature
#         ```
#         """

#         result = await provider_v1_with_mock_client.execute_query(query_with_blocks)
#         assert isinstance(result, str)

#     @pytest.mark.asyncio
#     async def test_execute_query_connection_error(self):
#         """Test query execution with connection error"""
#         with patch(
#             "serverframework.extensions.database.InfluxDB.InfluxDBClient",
#             side_effect=Exception("Connection error"),
#         ):
#             provider = InfluxDBProvider(
#                 influxdb_version="1",
#                 extension_id=self.extension_id,
#                 **self.provider_init_params,
#             )

#             result = await provider.execute_query("SELECT * FROM temperature")
#             assert "Error connecting to InfluxDB" in result

#     @pytest.mark.asyncio
#     async def test_write_data_v1_success(
#         self, provider_v1_with_mock_client, mock_influxdb_v1_client
#     ):
#         """Test successful data writing to InfluxDB 1.x"""
#         test_data = {
#             "database": "test_db",
#             "points": [
#                 {
#                     "measurement": "temperature",
#                     "time": "2023-01-01T00:00:00Z",
#                     "fields": {"value": 23.5},
#                     "tags": {"location": "room1"},
#                 }
#             ],
#         }

#         result = await provider_v1_with_mock_client.write_data(json.dumps(test_data))

#         assert "Successfully wrote" in result
#         assert "test_db" in result
#         mock_influxdb_v1_client.write_points.assert_called_once()

#     @pytest.mark.asyncio
#     async def test_write_data_with_code_blocks(self, provider_v1_with_mock_client):
#         """Test data writing with JSON code block formatting"""
#         test_data = {
#             "database": "test_db",
#             "points": [{"measurement": "test", "fields": {"value": 1}}],
#         }

#         data_with_blocks = f"""
#         ```json
#         {json.dumps(test_data)}
#         ```
#         """

#         result = await provider_v1_with_mock_client.write_data(data_with_blocks)
#         assert "Successfully wrote" in result

#     @pytest.mark.asyncio
#     async def test_write_data_invalid_json(self, provider_v1_with_mock_client):
#         """Test data writing with invalid JSON"""
#         result = await provider_v1_with_mock_client.write_data("invalid json")
#         assert "Invalid JSON data format" in result

#     @pytest.mark.asyncio
#     async def test_write_data_missing_points(self, provider_v1_with_mock_client):
#         """Test data writing with missing points"""
#         test_data = {"database": "test_db"}
#         result = await provider_v1_with_mock_client.write_data(json.dumps(test_data))
#         assert "No data points specified" in result

#     @pytest.mark.asyncio
#     async def test_write_data_connection_error(self):
#         """Test data writing with connection error"""
#         with patch(
#             "serverframework.extensions.database.InfluxDB.InfluxDBClient",
#             side_effect=Exception("Connection error"),
#         ):
#             provider = InfluxDBProvider(
#                 influxdb_version="1",
#                 extension_id=self.extension_id,
#                 **self.provider_init_params,
#             )

#             test_data = {"points": [{"measurement": "test", "fields": {"value": 1}}]}
#             result = await provider.write_data(json.dumps(test_data))
#             assert "Error connecting to InfluxDB" in result

#     @pytest.mark.asyncio
#     async def test_execute_sql_translation(self, provider_v1_with_mock_client):
#         """Test SQL to InfluxQL/Flux translation"""
#         # Mock ApiClient for SQL translation
#         mock_api_client = MagicMock()
#         mock_api_client.prompt_agent.return_value = "SELECT * FROM temperature"
#         provider_v1_with_mock_client.ApiClient = mock_api_client
#         provider_v1_with_mock_client.agent_name = "test_agent"

#         result = await provider_v1_with_mock_client.execute_sql(
#             "SELECT * FROM temperature"
#         )
#         mock_api_client.prompt_agent.assert_called_once()

#     @pytest.mark.asyncio
#     async def test_execute_sql_without_api_client(self, provider_v1_with_mock_client):
#         """Test SQL execution without API client"""
#         result = await provider_v1_with_mock_client.execute_sql(
#             "SELECT * FROM temperature"
#         )
#         assert "InfluxQL" in result or "not SQL" in result

#     @pytest.mark.asyncio
#     async def test_get_schema_v1_success(
#         self, provider_v1_with_mock_client, mock_influxdb_v1_client
#     ):
#         """Test successful schema retrieval for InfluxDB 1.x"""
#         # Mock database and measurement queries
#         mock_influxdb_v1_client.get_list_database.return_value = [{"name": "test_db"}]
#         mock_influxdb_v1_client.get_list_measurements.return_value = [
#             {"name": "temperature"}
#         ]

#         # Mock field and tag key queries
#         mock_field_result = MagicMock()
#         mock_field_result.get_points.return_value = [
#             {"fieldKey": "value", "fieldType": "float"}
#         ]

#         mock_tag_result = MagicMock()
#         mock_tag_result.get_points.return_value = [{"tagKey": "location"}]

#         mock_influxdb_v1_client.query.side_effect = [mock_field_result, mock_tag_result]

#         result = await provider_v1_with_mock_client.get_schema()

#         assert isinstance(result, str)
#         assert "Measurement: temperature" in result
#         assert "Fields:" in result
#         assert "Tags:" in result

#     @pytest.mark.asyncio
#     async def test_get_schema_v2_success(
#         self, provider_v2_with_mock_client, mock_influxdb_v2_client
#     ):
#         """Test successful schema retrieval for InfluxDB 2.x"""
#         # Mock measurements, fields, and tags queries with proper mock structure
#         mock_measurements_record = MagicMock()
#         mock_measurements_record.values = {"_value": "temperature"}
#         mock_measurements_table = MagicMock()
#         mock_measurements_table.records = [mock_measurements_record]

#         mock_fields_record = MagicMock()
#         mock_fields_record.values = {"_value": "value"}
#         mock_fields_table = MagicMock()
#         mock_fields_table.records = [mock_fields_record]

#         mock_tags_record = MagicMock()
#         mock_tags_record.values = {"_value": "location"}
#         mock_tags_table = MagicMock()
#         mock_tags_table.records = [mock_tags_record]

#         mock_influxdb_v2_client.query_api().query.side_effect = [
#             [mock_measurements_table],  # measurements query
#             [mock_fields_table],  # field keys query
#             [mock_tags_table],  # tag keys query
#         ]

#         result = await provider_v2_with_mock_client.get_schema()

#         assert isinstance(result, str)
#         assert "Measurement: temperature" in result

#     @pytest.mark.asyncio
#     async def test_get_schema_connection_error(self):
#         """Test schema retrieval with connection error"""
#         with patch(
#             "serverframework.extensions.database.InfluxDB.InfluxDBClient",
#             side_effect=Exception("Connection error"),
#         ):
#             provider = InfluxDBProvider(
#                 influxdb_version="1",
#                 extension_id=self.extension_id,
#                 **self.provider_init_params,
#             )

#             result = await provider.get_schema()
#             assert "Error connecting to InfluxDB" in result

#     @pytest.mark.asyncio
#     async def test_chat_with_db_success(self, provider_v1_with_mock_client):
#         """Test successful database chat"""
#         # Mock ApiClient for natural language processing
#         mock_api_client = MagicMock()
#         mock_api_client.prompt_agent.return_value = "SELECT * FROM temperature"
#         provider_v1_with_mock_client.ApiClient = mock_api_client
#         provider_v1_with_mock_client.agent_name = "test_agent"

#         result = await provider_v1_with_mock_client.chat_with_db(
#             "Show me temperature readings"
#         )

#         mock_api_client.prompt_agent.assert_called_once()
#         # Should execute the generated query

#     @pytest.mark.asyncio
#     async def test_chat_with_db_without_api_client(self, provider_v1_with_mock_client):
#         """Test database chat without API client"""
#         result = await provider_v1_with_mock_client.chat_with_db(
#             "Show me temperature readings"
#         )
#         assert "not available" in result

#     def test_get_db_type_v1(self, provider_v1_with_mock_client):
#         """Test getting database type for InfluxDB 1.x"""
#         db_type = provider_v1_with_mock_client.get_db_type()
#         assert db_type == "InfluxDB 1.x"

#     def test_get_db_type_v2(self, provider_v2_with_mock_client):
#         """Test getting database type for InfluxDB 2.x"""
#         db_type = provider_v2_with_mock_client.get_db_type()
#         assert db_type == "InfluxDB 2.x"

#     def test_validate_config_v1_success(self, provider_v1_with_mock_client):
#         """Test successful configuration validation for InfluxDB 1.x"""
#         result = provider_v1_with_mock_client.validate_config()
#         assert result is True  # Credentials are provided in provider_init_params

#     def test_validate_config_v1_missing_credentials(self):
#         """Test configuration validation for InfluxDB 1.x with missing credentials"""
#         with patch("serverframework.extensions.database.InfluxDB.InfluxDBClient"):
#             provider = InfluxDBProvider(
#                 influxdb_version="1",
#                 database_host="localhost",
#                 extension_id=self.extension_id,
#             )
#             result = provider.validate_config()
#             assert result is False

#     def test_validate_config_v2_success(self):
#         """Test successful configuration validation for InfluxDB 2.x"""
#         with patch("serverframework.extensions.database.InfluxDB.has_influxdb2", True):
#             with patch("serverframework.extensions.database.InfluxDB.InfluxDBClient2"):
#                 provider = InfluxDBProvider(
#                     influxdb_version="2",
#                     database_host="localhost",
#                     influxdb_token="test_token",
#                     influxdb_org="test_org",
#                     extension_id=self.extension_id,
#                 )
#                 result = provider.validate_config()
#                 assert result is True

#     def test_validate_config_v2_missing_token(self):
#         """Test configuration validation for InfluxDB 2.x with missing token"""
#         with patch("serverframework.extensions.database.InfluxDB.has_influxdb2", True):
#             provider = InfluxDBProvider(
#                 influxdb_version="2",
#                 database_host="localhost",
#                 extension_id=self.extension_id,
#             )
#             result = provider.validate_config()
#             assert result is False

#     def test_validate_config_v2_without_library(self, provider_without_influxdb2):
#         """Test configuration validation for InfluxDB 2.x without library"""
#         result = provider_without_influxdb2.validate_config()
#         assert result is False

#     def test_validate_config_missing_host(self):
#         """Test configuration validation with missing host"""
#         with patch("serverframework.extensions.database.InfluxDB.InfluxDBClient"):
#             provider = InfluxDBProvider(
#                 influxdb_version="1", extension_id=self.extension_id
#             )
#             result = provider.validate_config()
#             assert result is False

#     def test_services_property(self, provider_v1_with_mock_client):
#         """Test services property"""
#         services = provider_v1_with_mock_client.services

#         assert isinstance(services, list)
#         for expected_service in self.expected_services:
#             assert expected_service in services

#     def test_get_extension_info(self, provider_v1_with_mock_client):
#         """Test getting extension info"""
#         info = provider_v1_with_mock_client.get_extension_info()

#         assert isinstance(info, dict)
#         assert "type" in info
#         assert "InfluxDB" in info["type"]
#         assert "classification" in info
#         assert "description" in info

#     def test_commands_registration(self, provider_v1_with_mock_client):
#         """Test that commands are properly registered"""
#         assert hasattr(provider_v1_with_mock_client, "commands")
#         assert isinstance(provider_v1_with_mock_client.commands, dict)

#         # Check for expected commands
#         expected_commands = [
#             "Execute InfluxDB Query",
#             "Get InfluxDB Schema",
#             "Chat with InfluxDB",
#             "Write Data to InfluxDB",
#         ]

#         for expected_command in expected_commands:
#             assert expected_command in provider_v1_with_mock_client.commands
#             assert callable(provider_v1_with_mock_client.commands[expected_command])

#     def test_abilities_registration(self, provider_v1_with_mock_client):
#         """Test that abilities are registered"""
#         abilities = provider_v1_with_mock_client.get_abilities()
#         assert "time_series" in abilities
#         assert "metrics" in abilities

#     def test_working_directory_setup(self, provider_v1_with_mock_client):
#         """Test that working directory is properly set up"""
#         assert hasattr(provider_v1_with_mock_client, "WORKING_DIRECTORY")

#     def test_influxdb_version_parameter_handling(self):
#         """Test InfluxDB version parameter handling"""
#         # Test version 1
#         with patch("serverframework.extensions.database.InfluxDB.InfluxDBClient"):
#             provider_v1 = InfluxDBProvider(
#                 influxdb_version="1",
#                 extension_id=self.extension_id,
#                 **self.provider_init_params,
#             )
#             assert provider_v1.influxdb_version == "1"

#         # Test version 2
#         with patch("serverframework.extensions.database.InfluxDB.has_influxdb2", True):
#             with patch("serverframework.extensions.database.InfluxDB.InfluxDBClient2"):
#                 provider_v2 = InfluxDBProvider(
#                     influxdb_version="2",
#                     influxdb_org="test_org",
#                     influxdb_token="test_token",
#                     extension_id=self.extension_id,
#                 )
#                 assert provider_v2.influxdb_version == "2"

#     # @pytest.mark.dependency(name="basic_connection")
#     def test_basic_connection_v1(self, provider_v1_with_mock_client):
#         """Basic connection test that other tests depend on"""
#         connection = provider_v1_with_mock_client.get_connection()
#         assert connection is not None
#         # This test serves as a dependency for other connection-based tests

#     # @pytest.mark.dependency(name="basic_connection_v2")
#     def test_basic_connection_v2(self, provider_v2_with_mock_client):
#         """Basic connection test for v2 that other tests depend on"""
#         connection = provider_v2_with_mock_client.get_connection()
#         assert connection is not None
#         # This test serves as a dependency for other connection-based tests

#     # @pytest.mark.dependency(depends=["basic_connection_v2"])
#     @pytest.mark.asyncio
#     async def test_execute_query_v2_success(
#         self, provider_v2_with_mock_client, mock_influxdb_v2_client
#     ):
#         """Test successful Flux query execution"""
#         result = await provider_v2_with_mock_client.execute_query(
#             'from(bucket: "test_bucket") |> range(start: -1h)'
#         )

#         assert isinstance(result, str)
#         result_data = json.loads(result)
#         assert isinstance(result_data, list)
#         assert len(result_data) > 0

#     # @pytest.mark.dependency(depends=["basic_connection"])
#     @pytest.mark.asyncio
#     async def test_execute_query_with_api_client_retry(
#         self, provider_v1_with_mock_client
#     ):
#         """Test query execution with API client retry on error"""
#         # Mock ApiClient for query retry
#         mock_api_client = MagicMock()
#         mock_api_client.prompt_agent.return_value = "SELECT * FROM temperature"
#         provider_v1_with_mock_client.ApiClient = mock_api_client
#         provider_v1_with_mock_client.agent_name = "test_agent"

#         # Mock the get_connection to return a client that fails query execution
#         with patch.object(
#             provider_v1_with_mock_client._mock_client,
#             "query",
#             side_effect=Exception("Query error"),
#         ):
#             result = await provider_v1_with_mock_client.execute_query("INVALID QUERY")

#         # Should retry up to 3 times when the corrected query also fails
#         assert mock_api_client.prompt_agent.call_count <= 3

#     # @pytest.mark.dependency(depends=["basic_connection_v2"])
#     @pytest.mark.asyncio
#     async def test_write_data_v2_success(
#         self, provider_v2_with_mock_client, mock_influxdb_v2_client
#     ):
#         """Test successful data writing to InfluxDB 2.x"""
#         test_data = {
#             "bucket": "test_bucket",
#             "points": [
#                 {
#                     "measurement": "temperature",
#                     "time": "2023-01-01T00:00:00Z",
#                     "fields": {"value": 23.5},
#                     "tags": {"location": "room1"},
#                 }
#             ],
#         }

#         result = await provider_v2_with_mock_client.write_data(json.dumps(test_data))

#         assert "Successfully wrote" in result

#     # @pytest.mark.dependency(depends=["basic_connection"])
#     @pytest.mark.asyncio
#     async def test_execute_sql_translation(self, provider_v1_with_mock_client):
#         """Test SQL to InfluxQL/Flux translation without retry behavior"""
#         # Mock ApiClient for SQL translation
#         mock_api_client = MagicMock()
#         mock_api_client.prompt_agent.return_value = "SELECT * FROM temperature"
#         provider_v1_with_mock_client.ApiClient = mock_api_client
#         provider_v1_with_mock_client.agent_name = "test_agent"

#         result = await provider_v1_with_mock_client.execute_sql(
#             "SELECT * FROM temperature"
#         )
#         # Should call API client only once for translation (not for retries)
#         mock_api_client.prompt_agent.assert_called_once()
