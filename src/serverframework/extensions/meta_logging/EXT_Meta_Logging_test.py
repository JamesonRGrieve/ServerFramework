# from typing import List
# from unittest.mock import AsyncMock, MagicMock, patch

# import pytest

# from AbstractTest import CategoryOfTest, ClassOfTestsConfig, SkipThisTest
# from extensions.AbstractEXTTest import AbstractEXTTest
# from extensions.meta_logging.EXT_Meta_Logging import EXT_Meta_Logging


# class TestEXTMetaLogging(AbstractEXTTest):
#     """
#     Test suite for EXT_Meta_Logging extension.

#     Tests extension initialization, logging abilities, abilities, and audit logging functionality.
#     Focuses on testing logging functionality, InfluxDB integration, and static extension
#     metadata rather than component loading.

#     Test areas:
#     - Extension metadata and configuration
#     - Audit logging abilities and abilities
#     - InfluxDB integration and time-series data storage
#     - Failed login tracking functionality
#     - Privacy compliance reporting
#     - System event logging
#     - Configuration validation and lifecycle management
#     """

#     # Configure the test class
#     extension_class = EXT_Meta_Logging
#     test_config = ClassOfTestsConfig(
#         categories=[CategoryOfTest.EXTENSION, CategoryOfTest.INTEGRATION],
#         cleanup=True,
#     )

#     # Expected extension properties
#     expected_abilities = [
#         "log_audit_event",
#         "log_failed_login",
#         "log_system_event",
#         "query_audit_logs",
#         "query_failed_logins",
#         "generate_privacy_report",
#     ]

#     expected_abilities = [
#         "audit_logging",
#         "failed_login_tracking",
#         "system_logging",
#     ]

#     # Tests to skip
#     _skip_tests: List[SkipThisTest] = []

#     @pytest.fixture
#     def mock_env_influxdb_configured(self):
#         """Mock environment with InfluxDB configured"""
#         with patch("serverframework.extensions.meta_logging.EXT_Meta_Logging.env") as mock_env:
#             mock_env.side_effect = lambda key: {
#                 "ROOT_ID": "00000000-0000-0000-0000-000000000000",
#                 "LOG_LEVEL": "INFO",
#                 "LOG_FORMAT": "%(message)s",
#             }.get(key, "")
#             yield mock_env

#     @pytest.fixture
#     def mock_env_influxdb_not_configured(self):
#         """Mock environment without InfluxDB configured"""
#         with patch("serverframework.extensions.meta_logging.EXT_Meta_Logging.env") as mock_env:
#             mock_env.side_effect = lambda key: {
#                 "ROOT_ID": "00000000-0000-0000-0000-000000000000",
#                 "LOG_LEVEL": "INFO",
#                 "LOG_FORMAT": "%(message)s",
#             }.get(key, "")
#             yield mock_env

#     @pytest.fixture
#     def mock_database_extension(self):
#         """Mock database extension with InfluxDB provider"""
#         mock_extension = AsyncMock()
#         mock_provider = MagicMock()
#         mock_provider.get_db_type.return_value = "InfluxDB 2.x"
#         mock_extension.provider = mock_provider
#         mock_extension.execute_ability.return_value = {
#             "success": True,
#             "points_written": 1,
#         }
#         return mock_extension

#     @pytest.fixture
#     def mock_database_extension_wrong_type(self):
#         """Mock database extension with non-InfluxDB provider"""
#         mock_extension = AsyncMock()
#         mock_provider = MagicMock()
#         mock_provider.get_db_type.return_value = "PostgreSQL"
#         mock_extension.provider = mock_provider
#         return mock_extension

#     @pytest.fixture
#     def mock_logging_manager(self):
#         """Mock logging manager"""
#         mock_manager = AsyncMock()
#         mock_manager.log_audit_event.return_value = {
#             "success": True,
#             "message": "Audit event logged",
#         }
#         mock_manager.log_failed_login.return_value = {
#             "success": True,
#             "message": "Failed login logged",
#         }
#         mock_manager.log_system_event.return_value = {
#             "success": True,
#             "message": "System event logged",
#         }
#         mock_manager.query_audit_logs.return_value = [
#             {"timestamp": "2023-10-01T10:00:00Z", "action": "read"}
#         ]
#         mock_manager.query_failed_logins.return_value = [
#             {"timestamp": "2023-10-01T10:00:00Z", "username": "test@example.com"}
#         ]
#         mock_manager.get_privacy_audit_report.return_value = {
#             "success": True,
#             "report": {"total_events": 5},
#         }
#         return mock_manager

#     def test_extension_metadata(self, extension):
#         """Test extension metadata and basic attributes"""
#         assert extension.name == "meta_logging"
#         assert extension.version == "1.0.0"
#         assert "Meta Logging extension" in extension.description
#         assert hasattr(extension, "abilities")
#         assert hasattr(extension, "ext_dependencies")
#         assert hasattr(extension, "pip_dependencies")

#     def test_dependencies_structure(self, extension):
#         """Test that dependencies are properly structured"""
#         # Check extension dependencies
#         assert len(extension.__class__.ext_dependencies) == 1
#         database_dep = extension.__class__.ext_dependencies[0]
#         assert database_dep.name == "database"
#         assert database_dep.optional is False

#         # Check pip dependencies
#         assert len(extension.__class__.pip_dependencies) == 1
#         pip_deps = {dep.name: dep for dep in extension.__class__.pip_dependencies}

#         assert "influxdb-client" in pip_deps
#         assert ">=1.36.0" in pip_deps["influxdb-client"].semver
#         assert not pip_deps["influxdb-client"].optional

#     def test_initialization_with_influxdb_available(
#         self, mock_env_influxdb_configured, mock_database_extension
#     ):
#         """Test extension initialization when InfluxDB is available"""
#         with patch("serverframework.lib.Logging.logger"):
#             with patch(
#                 "serverframework.extensions.meta_logging.EXT_Meta_Logging.AbstractStaticExtension"
#             ) as mock_abs:
#                 mock_abs.get_extension_by_name.return_value = mock_database_extension

#                 extension = EXT_Meta_Logging()

#                 # Initially, database should not be detected until on_initialize is called
#                 assert extension.database_extension is None
#                 assert extension.influxdb_available is False

#                 # Call on_initialize to trigger database detection
#                 result = extension.on_initialize()

#                 # Now check that InfluxDB was detected
#                 assert result is True
#                 assert extension.database_extension == mock_database_extension
#                 assert extension.influxdb_available is True

#     def test_initialization_without_influxdb_available(
#         self, mock_env_influxdb_not_configured
#     ):
#         """Test extension initialization when InfluxDB is not available"""
#         with patch("serverframework.lib.Logging.logger"):
#             with patch(
#                 "serverframework.extensions.meta_logging.EXT_Meta_Logging.AbstractStaticExtension"
#             ) as mock_abs:
#                 mock_abs.get_extension_by_name.return_value = None

#                 extension = EXT_Meta_Logging()

#                 # Initially, database should not be detected until on_initialize is called
#                 assert extension.database_extension is None
#                 assert extension.influxdb_available is False

#                 # Call on_initialize to trigger database detection
#                 result = extension.on_initialize()

#                 # Check that InfluxDB was not detected
#                 assert result is True  # initialization should still succeed
#                 assert extension.database_extension is None
#                 assert extension.influxdb_available is False

#     def test_initialization_with_wrong_database_type(
#         self, mock_env_influxdb_configured, mock_database_extension_wrong_type
#     ):
#         """Test extension initialization when database extension has wrong type"""
#         with patch("serverframework.lib.Logging.logger"):
#             with patch(
#                 "serverframework.extensions.meta_logging.EXT_Meta_Logging.AbstractStaticExtension"
#             ) as mock_abs:
#                 mock_abs.get_extension_by_name.return_value = (
#                     mock_database_extension_wrong_type
#                 )

#                 extension = EXT_Meta_Logging()

#                 # Initially, database should not be detected until on_initialize is called
#                 assert extension.database_extension is None
#                 assert extension.influxdb_available is False

#                 # Call on_initialize to trigger database detection
#                 result = extension.on_initialize()

#                 # Check that InfluxDB was not detected due to wrong type
#                 assert result is True  # initialization should still succeed
#                 assert (
#                     extension.database_extension == mock_database_extension_wrong_type
#                 )
#                 assert extension.influxdb_available is False

#     def test_on_initialize_success(self, mock_env_influxdb_configured):
#         """Test successful extension initialization"""
#         with patch("serverframework.lib.Logging.logger"):
#             with patch(
#                 "serverframework.extensions.meta_logging.EXT_Meta_Logging.AbstractStaticExtension"
#             ):
#                 with patch.object(
#                     EXT_Meta_Logging, "_detect_database_extension"
#                 ) as mock_detect:
#                     with patch.object(
#                         EXT_Meta_Logging, "_initialize_logging_manager"
#                     ) as mock_init:
#                         with patch.object(
#                             EXT_Meta_Logging, "_register_audit_hooks"
#                         ) as mock_hooks:
#                             extension = EXT_Meta_Logging()
#                             result = extension.on_initialize()

#                             assert result is True
#                             mock_detect.assert_called_once()
#                             mock_init.assert_called_once()
#                             mock_hooks.assert_called_once()

#     def test_on_initialize_failure(self, mock_env_influxdb_configured):
#         """Test extension initialization failure handling"""
#         with patch("serverframework.lib.Logging.logger"):
#             with patch(
#                 "serverframework.extensions.meta_logging.EXT_Meta_Logging.AbstractStaticExtension"
#             ):
#                 with patch.object(
#                     EXT_Meta_Logging,
#                     "_detect_database_extension",
#                     side_effect=Exception("Test error"),
#                 ):
#                     extension = EXT_Meta_Logging()
#                     result = extension.on_initialize()

#                     assert result is False

#     def test_detect_database_extension_configured(
#         self, mock_env_influxdb_configured, mock_database_extension
#     ):
#         """Test database extension detection when properly configured"""
#         with patch("serverframework.lib.Logging.logger"):
#             with patch(
#                 "serverframework.extensions.meta_logging.EXT_Meta_Logging.AbstractStaticExtension"
#             ) as mock_abs:
#                 mock_abs.get_extension_by_name.return_value = mock_database_extension

#                 extension = EXT_Meta_Logging()
#                 extension._detect_database_extension()

#                 assert extension.database_extension == mock_database_extension
#                 assert extension.influxdb_available is True

#     def test_detect_database_extension_not_configured(
#         self, mock_env_influxdb_not_configured
#     ):
#         """Test database extension detection when not configured"""
#         with patch("serverframework.lib.Logging.logger"):
#             with patch(
#                 "serverframework.extensions.meta_logging.EXT_Meta_Logging.AbstractStaticExtension"
#             ) as mock_abs:
#                 mock_abs.get_extension_by_name.return_value = None

#                 extension = EXT_Meta_Logging()
#                 extension._detect_database_extension()

#                 assert extension.database_extension is None
#                 assert extension.influxdb_available is False

#     def test_get_abilities(self, extension):
#         """Test getting extension abilities"""
#         abilities = extension.get_abilities()

#         assert isinstance(abilities, set)
#         for expected_ability in self.expected_abilities:
#             assert expected_ability in abilities

#     def test_register_ability(self, extension):
#         """Test registering new ability"""
#         new_ability = "test_logging_ability"
#         extension.register_ability(new_ability)

#         assert new_ability in extension.abilities
#         assert new_ability in extension.get_registered_abilities()

#     def test_get_registered_abilities(self, extension):
#         """Test getting registered abilities"""
#         abilities = extension.get_registered_abilities()

#         assert isinstance(abilities, set)
#         assert "audit_logging" in abilities
#         assert "failed_login_tracking" in abilities

#     @pytest.mark.asyncio
#     async def test_log_audit_event_success(self, mock_logging_manager):
#         """Test successful audit event logging"""
#         with patch("serverframework.lib.Logging.logger"):
#             with patch(
#                 "serverframework.extensions.meta_logging.EXT_Meta_Logging.AbstractStaticExtension"
#             ):
#                 # Mock the BLL imports that the method will try to use
#                 mock_audit_model = MagicMock()
#                 mock_create_data = MagicMock()
#                 mock_audit_model.Create.return_value = mock_create_data

#                 with patch(
#                     "serverframework.extensions.meta_logging.BLL_Meta_Logging.AuditLogModel",
#                     mock_audit_model,
#                 ):
#                     extension = EXT_Meta_Logging()
#                     extension.logging_manager = mock_logging_manager

#                     result = await extension.log_audit_event(
#                         user_id="test_user",
#                         action="read",
#                         resource_type="user_profile",
#                         success=True,
#                         privacy_impact=True,
#                     )

#                     assert result["success"] is True
#                     assert "Audit event logged" in result["message"]
#                     mock_logging_manager.log_audit_event.assert_called_once_with(
#                         mock_create_data
#                     )

#     @pytest.mark.asyncio
#     async def test_log_audit_event_no_manager(self):
#         """Test audit event logging when manager is not available"""
#         with patch("serverframework.lib.Logging.logger"):
#             with patch(
#                 "serverframework.extensions.meta_logging.EXT_Meta_Logging.AbstractStaticExtension"
#             ):
#                 extension = EXT_Meta_Logging()
#                 extension.logging_manager = None

#                 result = await extension.log_audit_event(
#                     action="read", resource_type="user_profile"
#                 )

#                 assert result["success"] is False
#                 assert "Logging manager not available" in result["message"]

#     @pytest.mark.asyncio
#     async def test_log_failed_login_success(self, mock_logging_manager):
#         """Test successful failed login logging"""
#         with patch("serverframework.lib.Logging.logger"):
#             with patch(
#                 "serverframework.extensions.meta_logging.EXT_Meta_Logging.AbstractStaticExtension"
#             ):
#                 # Mock the BLL imports that the method will try to use
#                 mock_failed_login_model = MagicMock()
#                 mock_create_data = MagicMock()
#                 mock_failed_login_model.Create.return_value = mock_create_data

#                 with patch(
#                     "serverframework.extensions.meta_logging.BLL_Meta_Logging.FailedLoginAttemptModel",
#                     mock_failed_login_model,
#                 ):
#                     extension = EXT_Meta_Logging()
#                     extension.logging_manager = mock_logging_manager

#                     result = await extension.log_failed_login(
#                         username="test@example.com",
#                         ip_address="192.168.1.1",
#                         failure_reason="invalid_password",
#                         attempt_count=3,
#                     )

#                     assert result["success"] is True
#                     assert "Failed login logged" in result["message"]
#                     mock_logging_manager.log_failed_login.assert_called_once_with(
#                         mock_create_data
#                     )

#     @pytest.mark.asyncio
#     async def test_log_failed_login_no_manager(self):
#         """Test failed login logging when manager is not available"""
#         with patch("serverframework.lib.Logging.logger"):
#             with patch(
#                 "serverframework.extensions.meta_logging.EXT_Meta_Logging.AbstractStaticExtension"
#             ):
#                 extension = EXT_Meta_Logging()
#                 extension.logging_manager = None

#                 result = await extension.log_failed_login(
#                     username="test@example.com", ip_address="192.168.1.1"
#                 )

#                 assert result["success"] is False
#                 assert "Logging manager not available" in result["message"]

#     @pytest.mark.asyncio
#     async def test_log_system_event_success(self, mock_logging_manager):
#         """Test successful system event logging"""
#         with patch("serverframework.lib.Logging.logger"):
#             with patch(
#                 "serverframework.extensions.meta_logging.EXT_Meta_Logging.AbstractStaticExtension"
#             ):
#                 # Mock the BLL imports that the method will try to use
#                 mock_system_model = MagicMock()
#                 mock_create_data = MagicMock()
#                 mock_system_model.Create.return_value = mock_create_data

#                 with patch(
#                     "serverframework.extensions.meta_logging.BLL_Meta_Logging.SystemLogModel",
#                     mock_system_model,
#                 ):
#                     extension = EXT_Meta_Logging()
#                     extension.logging_manager = mock_logging_manager

#                     result = await extension.log_system_event(
#                         level="ERROR",
#                         component="auth_service",
#                         message="Authentication failed",
#                         user_id="test_user",
#                     )

#                     assert result["success"] is True
#                     assert "System event logged" in result["message"]
#                     mock_logging_manager.log_system_event.assert_called_once_with(
#                         mock_create_data
#                     )

#     @pytest.mark.asyncio
#     async def test_log_system_event_no_manager(self):
#         """Test system event logging when manager is not available"""
#         with patch("serverframework.lib.Logging.logger"):
#             with patch(
#                 "serverframework.extensions.meta_logging.EXT_Meta_Logging.AbstractStaticExtension"
#             ):
#                 extension = EXT_Meta_Logging()
#                 extension.logging_manager = None

#                 result = await extension.log_system_event(
#                     level="INFO", component="test_component", message="Test message"
#                 )

#                 assert result["success"] is False
#                 assert "Logging manager not available" in result["message"]

#     @pytest.mark.asyncio
#     async def test_query_audit_logs_success(self, mock_logging_manager):
#         """Test successful audit logs querying"""
#         with patch("serverframework.lib.Logging.logger"):
#             with patch(
#                 "serverframework.extensions.meta_logging.EXT_Meta_Logging.AbstractStaticExtension"
#             ):
#                 # Mock the BLL imports that the method will try to use
#                 mock_audit_model = MagicMock()
#                 mock_search = MagicMock()
#                 mock_audit_model.Search.return_value = mock_search

#                 with patch(
#                     "serverframework.extensions.meta_logging.BLL_Meta_Logging.AuditLogModel",
#                     mock_audit_model,
#                 ):
#                     extension = EXT_Meta_Logging()
#                     extension.logging_manager = mock_logging_manager

#                     results = await extension.query_audit_logs(
#                         start_time="2023-10-01T00:00:00Z",
#                         end_time="2023-10-01T23:59:59Z",
#                         action="read",
#                         privacy_impact=True,
#                     )

#                     assert len(results) == 1
#                     assert results[0]["action"] == "read"
#                     mock_logging_manager.query_audit_logs.assert_called_once_with(
#                         mock_search
#                     )

#     @pytest.mark.asyncio
#     async def test_query_audit_logs_no_manager(self):
#         """Test audit logs querying when manager is not available"""
#         with patch("serverframework.lib.Logging.logger"):
#             with patch(
#                 "serverframework.extensions.meta_logging.EXT_Meta_Logging.AbstractStaticExtension"
#             ):
#                 extension = EXT_Meta_Logging()
#                 extension.logging_manager = None

#                 results = await extension.query_audit_logs(action="read")

#                 assert results == []

#     @pytest.mark.asyncio
#     async def test_query_failed_logins_success(self, mock_logging_manager):
#         """Test successful failed logins querying"""
#         with patch("serverframework.lib.Logging.logger"):
#             with patch(
#                 "serverframework.extensions.meta_logging.EXT_Meta_Logging.AbstractStaticExtension"
#             ):
#                 # Mock the BLL imports that the method will try to use
#                 mock_failed_login_model = MagicMock()
#                 mock_search = MagicMock()
#                 mock_failed_login_model.Search.return_value = mock_search

#                 with patch(
#                     "serverframework.extensions.meta_logging.BLL_Meta_Logging.FailedLoginAttemptModel",
#                     mock_failed_login_model,
#                 ):
#                     extension = EXT_Meta_Logging()
#                     extension.logging_manager = mock_logging_manager

#                     results = await extension.query_failed_logins(
#                         start_time="2023-10-01T00:00:00Z",
#                         username="test@example.com",
#                         is_blocked=False,
#                     )

#                     assert len(results) == 1
#                     assert "username" in results[0]
#                     mock_logging_manager.query_failed_logins.assert_called_once_with(
#                         mock_search
#                     )

#     @pytest.mark.asyncio
#     async def test_query_failed_logins_no_manager(self):
#         """Test failed logins querying when manager is not available"""
#         with patch("serverframework.lib.Logging.logger"):
#             with patch(
#                 "serverframework.extensions.meta_logging.EXT_Meta_Logging.AbstractStaticExtension"
#             ):
#                 extension = EXT_Meta_Logging()
#                 extension.logging_manager = None

#                 results = await extension.query_failed_logins(
#                     username="test@example.com"
#                 )

#                 assert results == []

#     @pytest.mark.asyncio
#     async def test_generate_privacy_report_success(self, mock_logging_manager):
#         """Test successful privacy report generation"""
#         with patch("serverframework.lib.Logging.logger"):
#             with patch(
#                 "serverframework.extensions.meta_logging.EXT_Meta_Logging.AbstractStaticExtension"
#             ):
#                 extension = EXT_Meta_Logging()
#                 extension.logging_manager = mock_logging_manager

#                 result = await extension.generate_privacy_report(days=7)

#                 assert result["success"] is True
#                 assert "report" in result
#                 assert result["report"]["total_events"] == 5
#                 mock_logging_manager.get_privacy_audit_report.assert_called_once_with(7)

#     @pytest.mark.asyncio
#     async def test_generate_privacy_report_no_manager(self):
#         """Test privacy report generation when manager is not available"""
#         with patch("serverframework.lib.Logging.logger"):
#             with patch(
#                 "serverframework.extensions.meta_logging.EXT_Meta_Logging.AbstractStaticExtension"
#             ):
#                 extension = EXT_Meta_Logging()
#                 extension.logging_manager = None

#                 result = await extension.generate_privacy_report()

#                 assert result["success"] is False
#                 assert "Logging manager not available" in result["message"]

#     def test_get_required_permissions(self, extension):
#         """Test getting required permissions"""
#         permissions = extension.get_required_permissions()

#         assert isinstance(permissions, list)
#         assert len(permissions) == 4
#         assert "logging:audit" in permissions
#         assert "logging:system" in permissions
#         assert "logging:security" in permissions
#         assert "compliance:privacy_audit" in permissions

#     def test_on_start_success(self, mock_env_influxdb_configured):
#         """Test successful extension start"""
#         with patch("serverframework.lib.Logging.logger"):
#             with patch(
#                 "serverframework.extensions.meta_logging.EXT_Meta_Logging.AbstractStaticExtension"
#             ):
#                 with patch.object(
#                     EXT_Meta_Logging, "_detect_database_extension"
#                 ) as mock_detect:
#                     with patch.object(
#                         EXT_Meta_Logging, "_initialize_logging_manager"
#                     ) as mock_init:
#                         with patch.object(
#                             EXT_Meta_Logging, "_register_audit_hooks"
#                         ) as mock_hooks:
#                             extension = EXT_Meta_Logging()
#                             extension.influxdb_available = False  # Test re-init
#                             result = extension.on_start()

#                             assert result is True
#                             mock_detect.assert_called_once()
#                             mock_hooks.assert_called_once()

#     def test_on_start_failure(self, mock_env_influxdb_configured):
#         """Test extension start failure"""
#         with patch("serverframework.lib.Logging.logger"):
#             with patch(
#                 "serverframework.extensions.meta_logging.EXT_Meta_Logging.AbstractStaticExtension"
#             ):
#                 with patch.object(
#                     EXT_Meta_Logging,
#                     "_detect_database_extension",
#                     side_effect=Exception("Test error"),
#                 ):
#                     extension = EXT_Meta_Logging()
#                     result = extension.on_start()

#                     assert result is False

#     def test_on_stop_success(self, extension):
#         """Test successful extension stop"""
#         extension.logging_manager = MagicMock()
#         result = extension.on_stop()

#         assert result is True
#         assert extension.logging_manager is None

#     def test_validate_config_all_dependencies_available(self):
#         """Test configuration validation when all dependencies are available"""
#         with patch("serverframework.lib.Logging.logger"):
#             with patch(
#                 "serverframework.extensions.meta_logging.EXT_Meta_Logging.AbstractStaticExtension"
#             ):
#                 # Mock the influxdb_client import inside the validate_config method
#                 with patch("builtins.__import__") as mock_import:
#                     mock_import.return_value = MagicMock()  # Mock successful import

#                     extension = EXT_Meta_Logging()
#                     extension.database_extension = MagicMock()
#                     extension.influxdb_available = True
#                     issues = extension.validate_config()

#                     assert len(issues) == 0

#     def test_validate_config_missing_influxdb_client(self):
#         """Test configuration validation with missing InfluxDB client library"""
#         with patch("serverframework.lib.Logging.logger"):
#             with patch(
#                 "serverframework.extensions.meta_logging.EXT_Meta_Logging.AbstractStaticExtension"
#             ):
#                 # Mock the influxdb_client import to raise ImportError
#                 with patch("builtins.__import__") as mock_import:

#                     def mock_import_side_effect(name, *args, **kwargs):
#                         if name == "influxdb_client":
#                             raise ImportError("No module named 'influxdb_client'")
#                         return MagicMock()

#                     mock_import.side_effect = mock_import_side_effect

#                     extension = EXT_Meta_Logging()
#                     issues = extension.validate_config()

#                     assert len(issues) == 3  # Missing lib + no db ext + no influx
#                     assert any(
#                         "InfluxDB client library not installed" in issue
#                         for issue in issues
#                     )

#     def test_validate_config_missing_database_extension(self):
#         """Test configuration validation with missing database extension"""
#         with patch("serverframework.lib.Logging.logger"):
#             with patch(
#                 "serverframework.extensions.meta_logging.EXT_Meta_Logging.AbstractStaticExtension"
#             ):
#                 extension = EXT_Meta_Logging()
#                 extension.database_extension = None
#                 issues = extension.validate_config()

#                 assert any(
#                     "Database extension not available" in issue for issue in issues
#                 )

#     def test_validate_config_influxdb_not_configured(self):
#         """Test configuration validation with InfluxDB not configured"""
#         with patch("serverframework.lib.Logging.logger"):
#             with patch(
#                 "serverframework.extensions.meta_logging.EXT_Meta_Logging.AbstractStaticExtension"
#             ):
#                 extension = EXT_Meta_Logging()
#                 extension.database_extension = MagicMock()
#                 extension.influxdb_available = False
#                 issues = extension.validate_config()

#                 assert any("InfluxDB not configured" in issue for issue in issues)

#     def test_has_ability(self, extension):
#         """Test ability checking"""
#         assert extension.has_ability("audit_logging") is True
#         assert extension.has_ability("failed_login_tracking") is True
#         assert extension.has_ability("non_existent_ability") is False

#     def test_get_audit_summary_with_influxdb(self, extension):
#         """Test getting audit summary when InfluxDB is available"""
#         extension.influxdb_available = True
#         summary = extension.get_audit_summary(hours=12)

#         assert summary["hours"] == 12
#         assert "audit_events" in summary
#         assert "failed_logins" in summary
#         assert "privacy_events" in summary
#         assert summary["status"] == "InfluxDB available"

#     def test_get_audit_summary_without_influxdb(self, extension):
#         """Test getting audit summary when InfluxDB is not available"""
#         extension.influxdb_available = False
#         summary = extension.get_audit_summary()

#         assert "error" in summary
#         assert summary["error"] == "InfluxDB not available"

#     def test_audit_hooks_registration(self, mock_env_influxdb_configured):
#         """Test that audit hooks are properly registered"""
#         with patch("serverframework.lib.Logging.logger"):
#             with patch(
#                 "serverframework.extensions.meta_logging.EXT_Meta_Logging.AbstractStaticExtension"
#             ) as mock_abs:
#                 mock_abs.bll_hook = MagicMock()

#                 extension = EXT_Meta_Logging()
#                 extension._register_audit_hooks()

#                 # Should register multiple hooks for different BLL operations
#                 assert mock_abs.bll_hook.called

#     def test_abilities_discovery(self, extension):
#         """Test that all expected abilities are discovered"""
#         extension.discover_abilities()

#         for expected_ability in self.expected_abilities:
#             assert expected_ability in extension.abilities
#             assert callable(extension.abilities[expected_ability])

#     @pytest.mark.asyncio
#     async def test_execute_ability_success(self, mock_logging_manager):
#         """Test successful ability execution"""
#         with patch("serverframework.lib.Logging.logger"):
#             with patch(
#                 "serverframework.extensions.meta_logging.EXT_Meta_Logging.AbstractStaticExtension"
#             ):
#                 # Mock the BLL imports
#                 mock_audit_model = MagicMock()
#                 mock_create_data = MagicMock()
#                 mock_audit_model.Create.return_value = mock_create_data

#                 with patch(
#                     "serverframework.extensions.meta_logging.BLL_Meta_Logging.AuditLogModel",
#                     mock_audit_model,
#                 ):
#                     extension = EXT_Meta_Logging()
#                     extension.logging_manager = mock_logging_manager

#                     result = await extension.execute_ability(
#                         "log_audit_event",
#                         {
#                             "action": "test_action",
#                             "resource_type": "test_resource",
#                         },
#                     )

#                     assert result["success"] is True

#     @pytest.mark.asyncio
#     async def test_execute_ability_not_found(self, extension):
#         """Test executing non-existent ability"""
#         result = await extension.execute_ability("non_existent_ability")

#         assert "not found" in result

#     def test_abilities_conditional_registration(
#         self, mock_env_influxdb_configured, mock_database_extension
#     ):
#         """Test that privacy audit abilities are only registered when InfluxDB is available"""
#         with patch("serverframework.lib.Logging.logger"):
#             with patch(
#                 "serverframework.extensions.meta_logging.EXT_Meta_Logging.AbstractStaticExtension"
#             ) as mock_abs:
#                 # Test with InfluxDB available
#                 mock_abs.get_extension_by_name.return_value = mock_database_extension

#                 extension = EXT_Meta_Logging()
#                 extension.on_initialize()

#                 assert "privacy_audit" in extension.abilities
#                 assert "compliance_reporting" in extension.abilities

#     def test_abilities_without_influxdb(self, mock_env_influxdb_not_configured):
#         """Test that privacy audit abilities are not registered when InfluxDB is unavailable"""
#         with patch("serverframework.lib.Logging.logger"):
#             with patch(
#                 "serverframework.extensions.meta_logging.EXT_Meta_Logging.AbstractStaticExtension"
#             ) as mock_abs:
#                 # Test without database extension
#                 mock_abs.get_extension_by_name.return_value = None

#                 extension = EXT_Meta_Logging()
#                 extension.on_initialize()

#                 # privacy_audit and compliance_reporting abilities should not be registered
#                 abilities = extension.get_abilities()
#                 assert "privacy_audit" not in abilities
#                 assert "compliance_reporting" not in abilities

#     def test_lifecycle_methods_integration(self, extension):
#         """Test integration of lifecycle methods"""
#         # Test startup
#         extension.on_startup()

#         # Test shutdown
#         extension.on_shutdown()

#         # These methods should not raise exceptions

#     def test_extension_dependencies_integration(self, extension):
#         """Test extension's interaction with its dependencies"""
#         # Test that the extension properly handles missing dependencies
#         assert hasattr(extension, "database_extension")
#         assert hasattr(extension, "influxdb_available")

#         # These should be initialized to reasonable defaults
#         if extension.database_extension is None:
#             assert extension.influxdb_available is False

#     @pytest.mark.asyncio
#     async def test_logging_with_exception_handling(self, mock_logging_manager):
#         """Test logging operations with exception handling"""
#         with patch("serverframework.lib.Logging.logger"):
#             with patch(
#                 "serverframework.extensions.meta_logging.EXT_Meta_Logging.AbstractStaticExtension"
#             ):
#                 # Make the logging manager throw an error
#                 mock_logging_manager.log_audit_event.side_effect = Exception(
#                     "Database error"
#                 )

#                 extension = EXT_Meta_Logging()
#                 extension.logging_manager = mock_logging_manager

#                 result = await extension.log_audit_event(
#                     action="test", resource_type="test"
#                 )

#                 assert result["success"] is False
#                 assert "Error logging audit event: Database error" in result["message"]

#     def test_initialize_logging_manager_success(
#         self, mock_env_influxdb_configured, mock_logging_manager
#     ):
#         """Test successful logging manager initialization"""
#         with patch("serverframework.lib.Logging.logger"):
#             with patch(
#                 "serverframework.extensions.meta_logging.EXT_Meta_Logging.AbstractStaticExtension"
#             ):
#                 with patch(
#                     "serverframework.extensions.meta_logging.BLL_Meta_Logging.MetaLoggingManager"
#                 ) as mock_manager_class:
#                     mock_manager_class.return_value = mock_logging_manager

#                     extension = EXT_Meta_Logging()
#                     extension.influxdb_available = True
#                     extension._initialize_logging_manager()

#                     assert extension.logging_manager == mock_logging_manager
#                     mock_manager_class.assert_called_once()

#     def test_initialize_logging_manager_no_influxdb(self, mock_env_influxdb_configured):
#         """Test logging manager initialization when InfluxDB is not available"""
#         with patch("serverframework.lib.Logging.logger"):
#             with patch(
#                 "serverframework.extensions.meta_logging.EXT_Meta_Logging.AbstractStaticExtension"
#             ):
#                 extension = EXT_Meta_Logging()
#                 extension.influxdb_available = False
#                 extension._initialize_logging_manager()

#                 assert extension.logging_manager is None
