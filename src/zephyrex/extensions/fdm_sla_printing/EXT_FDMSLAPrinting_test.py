from unittest.mock import MagicMock, patch

import pytest

from zephyrex.extensions.fdm_sla_printing.EXT_FDMSLAPrinting import EXT_FDMSLAPrinting


class TestFDMSLAPrintingExtension:
    """
    Test suite for the FDM/SLA Printing extension.

    Tests extension metadata/configuration, 3D printer provider creation and
    integration (PrusaConnect, Bambu Lab, Creality Cloud), printer control
    abilities (status, print jobs, model upload, temperature), capability
    management, and extension lifecycle/config validation.
    """

    @pytest.fixture
    def extension(self):
        """Create an EXT_FDMSLAPrinting instance for testing."""
        return EXT_FDMSLAPrinting()

    @pytest.fixture
    def mock_prusa_provider(self):
        """Mock PrusaConnect provider."""
        mock_provider = MagicMock()
        mock_provider.get_printer_status.return_value = {
            "status": "idle",
            "temperature": {"hotend": 25, "bed": 23},
            "error": False,
        }
        mock_provider.start_print_job.return_value = {
            "job_id": "test-job-id",
            "status": "started",
            "error": False,
        }
        mock_provider.pause_print_job.return_value = {
            "status": "paused",
            "error": False,
        }
        mock_provider.resume_print_job.return_value = {
            "status": "resumed",
            "error": False,
        }
        mock_provider.cancel_print_job.return_value = {
            "status": "cancelled",
            "error": False,
        }
        mock_provider.upload_model.return_value = {
            "model_id": "test-model-id",
            "status": "uploaded",
            "error": False,
        }
        mock_provider.get_print_jobs.return_value = [
            {"id": "job-1", "name": "test-print", "status": "completed"}
        ]
        mock_provider.get_printer_temperature.return_value = {
            "hotend": 210,
            "bed": 60,
        }
        mock_provider.set_printer_temperature.return_value = {
            "status": "temperature_set"
        }
        mock_provider.commands = {
            "Get PRUSA Printer Status": mock_provider.get_printer_status,
            "Start PRUSA Print Job": mock_provider.start_print_job,
        }
        return mock_provider

    @pytest.fixture
    def mock_bambu_provider(self):
        """Mock Bambu Lab provider."""
        mock_provider = MagicMock()
        mock_provider.get_printer_status.return_value = {
            "status": "printing",
            "progress": 45,
            "error": False,
        }
        mock_provider.commands = {
            "Get BAMBU Printer Status": mock_provider.get_printer_status,
        }
        return mock_provider

    @pytest.fixture
    def mock_creality_provider(self):
        """Mock Creality Cloud provider."""
        mock_provider = MagicMock()
        mock_provider.get_printer_status.return_value = {
            "status": "ready",
            "model": "Ender 3",
            "error": False,
        }
        mock_provider.commands = {
            "Get CREALITY Printer Status": mock_provider.get_printer_status,
        }
        return mock_provider

    def test_extension_metadata(self, extension):
        """Test extension metadata is correctly set."""
        assert extension.name == "3d_printing"
        assert extension.version == "1.0.0"
        assert "3D Printer extension" in extension.description
        assert hasattr(extension, "ext_dependencies")
        assert hasattr(extension, "pip_dependencies")
        assert hasattr(extension, "sys_dependencies")

    def test_dependencies(self, extension):
        """Test that dependencies are properly structured."""
        ext_deps = extension.ext_dependencies
        assert len(ext_deps) == 2

        dep_names = {dep.name for dep in ext_deps}
        assert "core" in dep_names
        assert "oauth" in dep_names

        for dep in ext_deps:
            if dep.name == "oauth":
                assert dep.optional is True
            elif dep.name == "core":
                assert dep.optional is False

        pip_deps = extension.pip_dependencies
        assert len(pip_deps) >= 3

        pip_dep_names = {dep.name for dep in pip_deps}
        assert "requests" in pip_dep_names
        assert "pydantic" in pip_dep_names
        assert "websockets" in pip_dep_names

        for dep in pip_deps:
            if dep.name == "websockets":
                assert dep.optional is True
            elif dep.name in ("requests", "pydantic"):
                assert dep.optional is False

        assert isinstance(extension.sys_dependencies, list)
        assert len(extension.sys_dependencies) == 0

    def test_capabilities(self, extension):
        """Test extension capabilities are properly defined."""
        expected_capabilities = {
            "printer_monitoring",
            "print_control",
            "model_upload",
            "temperature_control",
            "job_management",
            "status_reporting",
        }
        assert set(extension.capabilities) == expected_capabilities

    def test_initialization(self, extension):
        """Test extension attributes are properly initialized."""
        assert hasattr(extension, "printer_platform")
        assert hasattr(extension, "access_token")
        assert hasattr(extension, "printer_ip")
        assert hasattr(extension, "api_key")
        assert hasattr(extension, "provider")
        assert hasattr(extension, "commands")
        assert extension.provider is None

    def test_default_configuration(self, extension):
        """Test default configuration values."""
        assert extension.printer_platform == "prusa"
        assert extension.access_token == ""
        assert extension.printer_ip == ""
        assert extension.api_key == ""

    def test_db_tables(self, extension):
        """Test database tables list."""
        assert isinstance(extension.db_tables, list)
        assert len(extension.db_tables) == 0

    @patch("zephyrex.extensions.fdm_sla_printing.EXT_FDMSLAPrinting.logger")
    def test_on_initialize_success(self, mock_logger, extension):
        """Test successful extension initialization."""
        with patch.object(extension, "_create_provider"), patch.object(
            extension, "_register_commands"
        ):
            result = extension.on_initialize()
            assert result is True
            mock_logger.debug.assert_called()

    @patch("zephyrex.extensions.fdm_sla_printing.EXT_FDMSLAPrinting.logger")
    def test_on_initialize_failure(self, mock_logger, extension):
        """Test extension initialization failure handling."""
        with patch.object(
            extension, "_create_provider", side_effect=Exception("Test error")
        ):
            result = extension.on_initialize()
            assert result is False
            mock_logger.error.assert_called()

    def test_create_provider_prusa_success(self, extension, mock_prusa_provider):
        """Test successful Prusa provider creation."""
        extension.printer_platform = "prusa"
        extension.api_key = "test-api-key"
        extension.access_token = "test-token"

        with patch(
            "zephyrex.extensions.fdm_sla_printing.PRV_Prusa.PrusaProvider",
            return_value=mock_prusa_provider,
        ):
            extension._create_provider()

            assert extension.provider is not None
            assert extension.provider == mock_prusa_provider

    def test_create_provider_bambu_success(self, extension, mock_bambu_provider):
        """Test successful Bambu provider creation."""
        extension.printer_platform = "bambu"
        extension.access_token = "test-token"

        with patch(
            "zephyrex.extensions.fdm_sla_printing.PRV_BambuLabs.BambuLabsProvider",
            return_value=mock_bambu_provider,
        ):
            extension._create_provider()

            assert extension.provider is not None
            assert extension.provider == mock_bambu_provider

    def test_create_provider_creality_success(self, extension, mock_creality_provider):
        """Test successful Creality provider creation."""
        extension.printer_platform = "creality"
        extension.api_key = "test-api-key"

        with patch(
            "zephyrex.extensions.fdm_sla_printing.PRV_Creality.CrealityProvider",
            return_value=mock_creality_provider,
        ):
            extension._create_provider()

            assert extension.provider is not None
            assert extension.provider == mock_creality_provider

    def test_create_provider_unsupported_platform(self, extension):
        """Test provider creation with an unsupported platform."""
        extension.printer_platform = "unsupported"
        extension._create_provider()

        assert extension.provider is None

    def test_create_provider_import_failure(self, extension):
        """Test provider creation when the Prusa provider import/construction fails."""
        extension.printer_platform = "prusa"

        with patch(
            "zephyrex.extensions.fdm_sla_printing.PRV_Prusa.PrusaProvider",
            side_effect=ImportError("Mock import error"),
        ):
            extension._create_provider()

            assert extension.provider is None

    def test_provider_creation_parameters(self, extension, mock_prusa_provider):
        """Test that the provider is created with correct parameters."""
        extension.printer_platform = "prusa"
        extension.api_key = "test-api-key"
        extension.access_token = "test-token"
        extension.printer_ip = "192.168.1.50"

        with patch(
            "zephyrex.extensions.fdm_sla_printing.PRV_Prusa.PrusaProvider",
            return_value=mock_prusa_provider,
        ) as mock_prusa_class:
            extension._create_provider()

            mock_prusa_class.assert_called_once_with(
                api_key="test-api-key",
                access_token="test-token",
                printer_ip="192.168.1.50",
                extension_id="3d_printing",
            )

    def test_register_commands_with_provider(self, extension, mock_prusa_provider):
        """Test command registration with an available provider."""
        extension.provider = mock_prusa_provider
        extension._register_commands()

        assert extension.commands == mock_prusa_provider.commands

    def test_register_commands_without_provider(self, extension):
        """Test command registration without a provider."""
        extension.printer_platform = "prusa"
        extension.provider = None
        extension._register_commands()

        assert len(extension.commands) > 0
        for command_name in extension.commands:
            assert "PRUSA" in command_name

    def test_capability_management(self, extension):
        """Test capability management methods."""
        extension.register_capability("test_capability")
        assert "test_capability" in extension.capabilities

        capabilities = extension.get_registered_capabilities()
        assert isinstance(capabilities, set)
        assert "test_capability" in capabilities

        capabilities = extension.get_capabilities()
        assert isinstance(capabilities, set)

    def test_capability_registration_is_instance_scoped(self):
        """Registering a capability on one instance must not leak to another."""
        first = EXT_FDMSLAPrinting()
        second = EXT_FDMSLAPrinting()

        first.register_capability("instance_only_capability")

        assert "instance_only_capability" in first.capabilities
        assert "instance_only_capability" not in second.capabilities

    @pytest.mark.asyncio
    async def test_get_printer_status_with_provider(self, extension, mock_prusa_provider):
        """Test getting printer status with a provider."""
        extension.provider = mock_prusa_provider

        result = await extension.get_printer_status()

        assert result["success"] is True
        assert result["status"]["status"] == "idle"
        mock_prusa_provider.get_printer_status.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_printer_status_without_provider(self, extension):
        """Test getting printer status without a provider."""
        extension.provider = None

        result = await extension.get_printer_status()

        assert result["success"] is False
        assert "No 3D printer provider available" in result["message"]

    @pytest.mark.asyncio
    async def test_get_printer_status_error(self, extension, mock_prusa_provider):
        """Test getting printer status when the provider raises."""
        mock_prusa_provider.get_printer_status.side_effect = Exception("Provider error")
        extension.provider = mock_prusa_provider

        result = await extension.get_printer_status()

        assert result["success"] is False
        assert "Error getting printer status" in result["message"]

    @pytest.mark.asyncio
    async def test_start_print_job_success(self, extension, mock_prusa_provider):
        """Test successful print job start."""
        extension.provider = mock_prusa_provider

        result = await extension.start_print_job("test-model-id")

        assert result["success"] is True
        assert result["result"]["status"] == "started"
        mock_prusa_provider.start_print_job.assert_called_once_with(
            "test-model-id", None
        )

    @pytest.mark.asyncio
    async def test_pause_print_job_success(self, extension, mock_prusa_provider):
        """Test successful print job pause."""
        extension.provider = mock_prusa_provider

        result = await extension.pause_print_job()

        assert result["success"] is True
        assert result["result"]["status"] == "paused"
        mock_prusa_provider.pause_print_job.assert_called_once_with(None)

    @pytest.mark.asyncio
    async def test_resume_print_job_success(self, extension, mock_prusa_provider):
        """Test successful print job resume."""
        extension.provider = mock_prusa_provider

        result = await extension.resume_print_job("test-job-id")

        assert result["success"] is True
        assert result["result"]["status"] == "resumed"
        mock_prusa_provider.resume_print_job.assert_called_once_with("test-job-id")

    @pytest.mark.asyncio
    async def test_cancel_print_job_success(self, extension, mock_prusa_provider):
        """Test successful print job cancellation."""
        extension.provider = mock_prusa_provider

        result = await extension.cancel_print_job()

        assert result["success"] is True
        assert result["result"]["status"] == "cancelled"
        mock_prusa_provider.cancel_print_job.assert_called_once_with(None)

    @pytest.mark.asyncio
    async def test_upload_model_success(self, extension, mock_prusa_provider):
        """Test successful model upload."""
        extension.provider = mock_prusa_provider

        result = await extension.upload_model("/path/to/model.stl", "Test Model")

        assert result["success"] is True
        assert result["result"]["status"] == "uploaded"
        mock_prusa_provider.upload_model.assert_called_once_with(
            "/path/to/model.stl", "Test Model"
        )

    @pytest.mark.asyncio
    async def test_get_print_jobs_success(self, extension, mock_prusa_provider):
        """Test successful print jobs retrieval."""
        extension.provider = mock_prusa_provider

        result = await extension.get_print_jobs(limit=5, status="completed")

        assert result["success"] is True
        assert result["count"] == 1
        assert result["jobs"][0]["status"] == "completed"
        mock_prusa_provider.get_print_jobs.assert_called_once_with(5, "completed")

    @pytest.mark.asyncio
    async def test_get_printer_temperature_success(self, extension, mock_prusa_provider):
        """Test successful temperature reading."""
        extension.provider = mock_prusa_provider

        result = await extension.get_printer_temperature()

        assert result["success"] is True
        assert result["temperature"]["hotend"] == 210
        assert result["temperature"]["bed"] == 60
        mock_prusa_provider.get_printer_temperature.assert_called_once()

    @pytest.mark.asyncio
    async def test_set_printer_temperature_success(self, extension, mock_prusa_provider):
        """Test successful temperature setting."""
        extension.provider = mock_prusa_provider

        result = await extension.set_printer_temperature(hotend_temp=210, bed_temp=60)

        assert result["success"] is True
        assert result["result"]["status"] == "temperature_set"
        mock_prusa_provider.set_printer_temperature.assert_called_once_with(210, 60)

    @pytest.mark.asyncio
    async def test_print_job_operations_without_provider(self, extension):
        """Test print job operations without a provider."""
        extension.provider = None

        operations = [
            extension.start_print_job("model-id"),
            extension.pause_print_job(),
            extension.resume_print_job(),
            extension.cancel_print_job(),
            extension.upload_model("/path/to/model.stl"),
            extension.get_print_jobs(),
            extension.get_printer_temperature(),
            extension.set_printer_temperature(210, 60),
        ]

        for operation in operations:
            result = await operation
            assert result["success"] is False
            assert "No 3D printer provider available" in result["message"]

    @pytest.mark.asyncio
    async def test_no_provider_warning(self, extension):
        """Test the no-provider warning message."""
        extension.printer_platform = "test"

        result = await extension._no_provider_warning()

        assert "No 3D printer provider available for test" in result

    def test_lifecycle_methods(self, extension):
        """Test extension lifecycle methods."""
        assert extension.on_start() is True

        extension.provider = MagicMock()
        assert extension.on_stop() is True
        assert extension.provider is None

        extension.on_startup()
        extension.on_shutdown()

    def test_provider_cleanup_on_stop(self, extension, mock_prusa_provider):
        """Test that the provider is cleaned up when the extension stops."""
        extension.provider = mock_prusa_provider

        result = extension.on_stop()

        assert result is True
        assert extension.provider is None

    @patch("zephyrex.extensions.fdm_sla_printing.EXT_FDMSLAPrinting.logger")
    def test_extension_startup_shutdown_hooks(self, mock_logger, extension):
        """Test startup and shutdown hooks log the expected messages."""
        extension.on_startup()
        mock_logger.debug.assert_called_with(
            "3D Printing extension startup hook called"
        )

        extension.on_shutdown()
        mock_logger.debug.assert_called_with(
            "3D Printing extension shutdown hook called"
        )

    def test_validate_config_all_available(self):
        """Test configuration validation when all dependencies are available."""
        with patch("builtins.__import__") as mock_import:
            mock_import.return_value = MagicMock()

            extension = EXT_FDMSLAPrinting(
                printer_platform="prusa", api_key="test-key"
            )
            issues = extension.validate_config()

            assert len(issues) == 0

    def test_validate_config_missing_requests(self):
        """Test configuration validation when requests is missing."""

        def mock_import(name, *args, **kwargs):
            if name == "requests":
                raise ImportError("No module named 'requests'")
            return MagicMock()

        with patch("builtins.__import__", side_effect=mock_import):
            extension = EXT_FDMSLAPrinting()
            issues = extension.validate_config()

            assert len(issues) >= 1
            issue_text = " ".join(issues).lower()
            assert "requests" in issue_text

    def test_validate_config_missing_pydantic(self):
        """Test configuration validation when pydantic is missing."""

        def mock_import(name, *args, **kwargs):
            if name == "pydantic":
                raise ImportError("No module named 'pydantic'")
            return MagicMock()

        with patch("builtins.__import__", side_effect=mock_import):
            extension = EXT_FDMSLAPrinting()
            issues = extension.validate_config()

            assert len(issues) >= 1
            issue_text = " ".join(issues).lower()
            assert "pydantic" in issue_text

    def test_validate_config_unsupported_platform(self):
        """Test configuration validation with an unsupported platform."""
        with patch("builtins.__import__") as mock_import:
            mock_import.return_value = MagicMock()

            extension = EXT_FDMSLAPrinting(printer_platform="unsupported")
            issues = extension.validate_config()

            assert len(issues) >= 1
            issue_text = " ".join(issues).lower()
            assert "unsupported" in issue_text

    def test_validate_config_missing_prusa_credentials(self):
        """Test configuration validation with missing Prusa credentials."""
        with patch("builtins.__import__") as mock_import:
            mock_import.return_value = MagicMock()

            extension = EXT_FDMSLAPrinting(printer_platform="prusa")
            issues = extension.validate_config()

            assert len(issues) >= 1
            issue_text = " ".join(issues).lower()
            assert "prusaconnect" in issue_text or "api key" in issue_text

    def test_validate_config_missing_bambu_credentials(self):
        """Test configuration validation with missing Bambu Lab credentials."""
        with patch("builtins.__import__") as mock_import:
            mock_import.return_value = MagicMock()

            extension = EXT_FDMSLAPrinting(printer_platform="bambu")
            issues = extension.validate_config()

            assert len(issues) >= 1
            issue_text = " ".join(issues).lower()
            assert "bambu" in issue_text

    def test_validate_config_missing_creality_credentials(self):
        """Test configuration validation with missing Creality Cloud credentials."""
        with patch("builtins.__import__") as mock_import:
            mock_import.return_value = MagicMock()

            extension = EXT_FDMSLAPrinting(printer_platform="creality")
            issues = extension.validate_config()

            assert len(issues) >= 1
            issue_text = " ".join(issues).lower()
            assert "creality" in issue_text

    def test_get_required_permissions(self, extension):
        """Test getting required permissions."""
        permissions = extension.get_required_permissions()

        assert isinstance(permissions, list)
        assert len(permissions) == 5
        assert "3dprinter:read" in permissions
        assert "3dprinter:control" in permissions
        assert "3dprinter:upload" in permissions
        assert "3dprinter:temperature" in permissions
        assert "3dprinter:jobs" in permissions

    def test_has_capability(self, extension):
        """Test capability checking."""
        for capability in extension.capabilities:
            assert extension.has_capability(capability) is True

        assert extension.has_capability("non_existent_capability") is False

    def test_platform_specific_initialization(self):
        """Test initialization with each supported platform."""
        for platform in ("prusa", "bambu", "creality"):
            extension = EXT_FDMSLAPrinting(printer_platform=platform)
            assert extension.printer_platform == platform

    def test_platform_is_lowercased(self):
        """Test that the printer platform is normalized to lower case."""
        extension = EXT_FDMSLAPrinting(printer_platform="PRUSA")
        assert extension.printer_platform == "prusa"

    def test_full_initialization_flow(self, extension):
        """Test the complete initialization flow."""
        with patch.object(extension, "_create_provider"), patch.object(
            extension, "_register_commands"
        ):
            result = extension.on_initialize()

            assert result is True

    def test_custom_configuration(self):
        """Test extension with custom configuration via constructor."""
        extension = EXT_FDMSLAPrinting(
            printer_platform="bambu",
            api_key="custom-api-key",
            access_token="custom-token",
            printer_ip="10.0.0.5",
        )

        assert extension.printer_platform == "bambu"
        assert extension.api_key == "custom-api-key"
        assert extension.access_token == "custom-token"
        assert extension.printer_ip == "10.0.0.5"


if __name__ == "__main__":
    pytest.main([__file__])
