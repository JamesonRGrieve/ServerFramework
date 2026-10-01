from unittest.mock import MagicMock, patch

import pytest

from zephyrex.extensions.automotive.EXT_Automotive import EXT_Automotive


class TestAutomotiveExtension:
    """
    Test suite for the Automotive extension.

    Tests extension metadata/configuration, Tesla provider creation and
    integration, vehicle control abilities (doors, climate, charging,
    navigation), capability management, and extension lifecycle/config
    validation.
    """

    @pytest.fixture
    def extension(self):
        """Create an EXT_Automotive instance for testing."""
        return EXT_Automotive()

    @pytest.fixture
    def mock_tesla_provider(self):
        """Mock Tesla provider."""
        mock_provider = MagicMock()
        mock_provider.get_vehicle_info.return_value = (
            "Tesla Model S - Battery: 85%, Range: 265 miles"
        )
        mock_provider.lock_doors.return_value = "Vehicle doors locked"
        mock_provider.unlock_doors.return_value = "Vehicle doors unlocked"
        mock_provider.set_climate.return_value = "Climate set to 22°C"
        mock_provider.start_charging.return_value = "Charging started"
        mock_provider.stop_charging.return_value = "Charging stopped"
        mock_provider.navigate_to.return_value = "Navigation set to destination"
        mock_provider.commands = {
            "Get TESLA Vehicle Info": mock_provider.get_vehicle_info,
            "Control TESLA Vehicle": mock_provider.lock_doors,
        }
        return mock_provider

    def test_extension_metadata(self, extension):
        """Test extension metadata is correctly set."""
        assert extension.name == "automotive"
        assert extension.version == "1.0.0"
        assert "Automotive extension" in extension.description
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
        assert "cryptography" in pip_dep_names

        for dep in pip_deps:
            if dep.name == "cryptography":
                assert dep.optional is True
            elif dep.name in ("requests", "pydantic"):
                assert dep.optional is False

        assert isinstance(extension.sys_dependencies, list)
        assert len(extension.sys_dependencies) == 0

    def test_capabilities(self, extension):
        """Test extension capabilities are properly defined."""
        expected_capabilities = {
            "vehicle_info",
            "door_control",
            "climate_control",
            "charging_control",
            "navigation",
            "vehicle_monitoring",
        }
        assert set(extension.capabilities) == expected_capabilities

    def test_initialization(self, extension):
        """Test extension attributes are properly initialized."""
        assert hasattr(extension, "vehicle_platform")
        assert hasattr(extension, "vehicle_id")
        assert hasattr(extension, "access_token")
        assert hasattr(extension, "provider")
        assert hasattr(extension, "commands")
        assert extension.provider is None

    def test_default_configuration(self, extension):
        """Test default configuration values."""
        assert extension.vehicle_platform == "tesla"
        assert extension.vehicle_id == ""
        assert extension.access_token == ""

    def test_db_tables(self, extension):
        """Test database tables list."""
        assert isinstance(extension.db_tables, list)
        assert len(extension.db_tables) == 0

    @patch("zephyrex.extensions.automotive.EXT_Automotive.logger")
    def test_on_initialize_success(self, mock_logger, extension):
        """Test successful extension initialization."""
        with patch.object(extension, "_create_provider"), patch.object(
            extension, "_register_commands"
        ):
            result = extension.on_initialize()
            assert result is True
            mock_logger.debug.assert_called()

    @patch("zephyrex.extensions.automotive.EXT_Automotive.logger")
    def test_on_initialize_failure(self, mock_logger, extension):
        """Test extension initialization failure handling."""
        with patch.object(
            extension, "_create_provider", side_effect=Exception("Test error")
        ):
            result = extension.on_initialize()
            assert result is False
            mock_logger.error.assert_called()

    def test_create_provider_tesla_success(self, extension, mock_tesla_provider):
        """Test successful Tesla provider creation."""
        extension.vehicle_platform = "tesla"
        extension.vehicle_id = "test-vehicle-id"
        extension.access_token = "test-token"

        with patch(
            "zephyrex.extensions.automotive.PRV_Tesla.TeslaProvider",
            return_value=mock_tesla_provider,
        ):
            extension._create_provider()

            assert extension.provider is not None
            assert extension.provider == mock_tesla_provider

    def test_create_provider_unsupported_platform(self, extension):
        """Test provider creation with an unsupported platform."""
        extension.vehicle_platform = "unsupported"
        extension._create_provider()

        assert extension.provider is None

    def test_create_provider_import_failure(self, extension):
        """Test provider creation when the Tesla provider import/construction fails."""
        with patch(
            "zephyrex.extensions.automotive.PRV_Tesla.TeslaProvider",
            side_effect=ImportError("Mock import error"),
        ):
            extension._create_provider()

            assert extension.provider is None

    def test_tesla_provider_creation_parameters(self, extension, mock_tesla_provider):
        """Test that the Tesla provider is created with correct parameters."""
        extension.vehicle_platform = "tesla"
        extension.vehicle_id = "test-vehicle-id"
        extension.access_token = "test-token"
        extension.api_key = "test-api-key"
        extension.conversation_id = "test-conversation-id"

        with patch(
            "zephyrex.extensions.automotive.PRV_Tesla.TeslaProvider",
            return_value=mock_tesla_provider,
        ) as mock_tesla_class:
            extension._create_provider()

            mock_tesla_class.assert_called_once_with(
                api_key="test-api-key",
                vehicle_id="test-vehicle-id",
                access_token="test-token",
                extension_id="automotive",
                conversation_directory="test-conversation-id",
            )

    def test_register_commands_with_provider(self, extension, mock_tesla_provider):
        """Test command registration with an available provider."""
        extension.provider = mock_tesla_provider
        extension._register_commands()

        assert extension.commands == mock_tesla_provider.commands

    def test_register_commands_without_provider(self, extension):
        """Test command registration without a provider."""
        extension.vehicle_platform = "tesla"
        extension.provider = None
        extension._register_commands()

        assert len(extension.commands) > 0
        for command_name in extension.commands:
            assert "TESLA" in command_name

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
        first = EXT_Automotive()
        second = EXT_Automotive()

        first.register_capability("instance_only_capability")

        assert "instance_only_capability" in first.capabilities
        assert "instance_only_capability" not in second.capabilities

    @pytest.mark.asyncio
    async def test_get_vehicle_info_with_provider(self, extension, mock_tesla_provider):
        """Test getting vehicle info with a provider."""
        extension.provider = mock_tesla_provider

        result = await extension.get_vehicle_info()

        assert "Tesla Model S" in result
        mock_tesla_provider.get_vehicle_info.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_vehicle_info_without_provider(self, extension):
        """Test getting vehicle info without a provider."""
        extension.provider = None

        result = await extension.get_vehicle_info()

        assert "No automotive provider available" in result

    @pytest.mark.asyncio
    async def test_get_vehicle_info_error(self, extension, mock_tesla_provider):
        """Test getting vehicle info when the provider raises."""
        mock_tesla_provider.get_vehicle_info.side_effect = Exception("Provider error")
        extension.provider = mock_tesla_provider

        result = await extension.get_vehicle_info()

        assert "Error getting vehicle info" in result

    @pytest.mark.asyncio
    async def test_lock_doors_success(self, extension, mock_tesla_provider):
        """Test successful door locking."""
        extension.provider = mock_tesla_provider

        result = await extension.lock_doors()

        assert "doors locked" in result
        mock_tesla_provider.lock_doors.assert_called_once()

    @pytest.mark.asyncio
    async def test_unlock_doors_success(self, extension, mock_tesla_provider):
        """Test successful door unlocking."""
        extension.provider = mock_tesla_provider

        result = await extension.unlock_doors()

        assert "doors unlocked" in result
        mock_tesla_provider.unlock_doors.assert_called_once()

    @pytest.mark.asyncio
    async def test_set_climate_success(self, extension, mock_tesla_provider):
        """Test successful climate control."""
        extension.provider = mock_tesla_provider

        result = await extension.set_climate(22.0, enabled=True)

        assert "Climate set" in result
        mock_tesla_provider.set_climate.assert_called_once_with(22.0, True)

    @pytest.mark.asyncio
    async def test_start_charging_success(self, extension, mock_tesla_provider):
        """Test successful charging start."""
        extension.provider = mock_tesla_provider

        result = await extension.start_charging()

        assert "Charging started" in result
        mock_tesla_provider.start_charging.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_charging_success(self, extension, mock_tesla_provider):
        """Test successful charging stop."""
        extension.provider = mock_tesla_provider

        result = await extension.stop_charging()

        assert "Charging stopped" in result
        mock_tesla_provider.stop_charging.assert_called_once()

    @pytest.mark.asyncio
    async def test_navigate_to_success(self, extension, mock_tesla_provider):
        """Test successful navigation."""
        extension.provider = mock_tesla_provider

        result = await extension.navigate_to("123 Main St, City, State")

        assert "Navigation set" in result
        mock_tesla_provider.navigate_to.assert_called_once_with(
            "123 Main St, City, State"
        )

    @pytest.mark.asyncio
    async def test_vehicle_operations_without_provider(self, extension):
        """Test vehicle operations without a provider."""
        extension.provider = None

        operations = [
            extension.lock_doors(),
            extension.unlock_doors(),
            extension.set_climate(22.0),
            extension.start_charging(),
            extension.stop_charging(),
            extension.navigate_to("test address"),
        ]

        for operation in operations:
            result = await operation
            assert "No automotive provider available" in result

    @pytest.mark.asyncio
    async def test_no_provider_warning(self, extension):
        """Test the no-provider warning message."""
        extension.vehicle_platform = "test"

        result = await extension._no_provider_warning()

        assert "No automotive provider available for test" in result

    def test_lifecycle_methods(self, extension):
        """Test extension lifecycle methods."""
        assert extension.on_start() is True

        extension.provider = MagicMock()
        assert extension.on_stop() is True
        assert extension.provider is None

        extension.on_startup()
        extension.on_shutdown()

    def test_provider_cleanup_on_stop(self, extension, mock_tesla_provider):
        """Test that the provider is cleaned up when the extension stops."""
        extension.provider = mock_tesla_provider

        result = extension.on_stop()

        assert result is True
        assert extension.provider is None

    @patch("zephyrex.extensions.automotive.EXT_Automotive.logger")
    def test_extension_startup_shutdown_hooks(self, mock_logger, extension):
        """Test startup and shutdown hooks log the expected messages."""
        extension.on_startup()
        mock_logger.debug.assert_called_with(
            "Automotive extension startup hook called"
        )

        extension.on_shutdown()
        mock_logger.debug.assert_called_with(
            "Automotive extension shutdown hook called"
        )

    def test_validate_config_all_available(self):
        """Test configuration validation when all dependencies are available."""
        with patch("builtins.__import__") as mock_import:
            mock_import.return_value = MagicMock()

            extension = EXT_Automotive(
                vehicle_platform="tesla",
                vehicle_id="test-vehicle-id",
                access_token="test-token",
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
            extension = EXT_Automotive()
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
            extension = EXT_Automotive()
            issues = extension.validate_config()

            assert len(issues) >= 1
            issue_text = " ".join(issues).lower()
            assert "pydantic" in issue_text

    def test_validate_config_unsupported_platform(self):
        """Test configuration validation with an unsupported platform."""
        with patch("builtins.__import__") as mock_import:
            mock_import.return_value = MagicMock()

            extension = EXT_Automotive(vehicle_platform="unsupported")
            issues = extension.validate_config()

            assert len(issues) >= 1
            issue_text = " ".join(issues).lower()
            assert "unsupported" in issue_text

    def test_validate_config_missing_tesla_credentials(self):
        """Test configuration validation with missing Tesla credentials."""
        with patch("builtins.__import__") as mock_import:
            mock_import.return_value = MagicMock()

            extension = EXT_Automotive(vehicle_platform="tesla")
            issues = extension.validate_config()

            assert len(issues) >= 2  # access token and vehicle ID issues
            issue_text = " ".join(issues).lower()
            assert "tesla" in issue_text
            assert "access token" in issue_text or "vehicle id" in issue_text

    def test_get_required_permissions(self, extension):
        """Test getting required permissions."""
        permissions = extension.get_required_permissions()

        assert isinstance(permissions, list)
        assert len(permissions) == 5
        assert "vehicle:read" in permissions
        assert "vehicle:control" in permissions
        assert "vehicle:climate" in permissions
        assert "vehicle:charging" in permissions
        assert "vehicle:navigation" in permissions

    def test_has_capability(self, extension):
        """Test capability checking."""
        for capability in extension.capabilities:
            assert extension.has_capability(capability) is True

        assert extension.has_capability("non_existent_capability") is False

    def test_platform_specific_initialization(self):
        """Test initialization with the supported platform."""
        extension = EXT_Automotive(vehicle_platform="tesla")
        assert extension.vehicle_platform == "tesla"

    def test_platform_is_lowercased(self):
        """Test that the vehicle platform is normalized to lower case."""
        extension = EXT_Automotive(vehicle_platform="TESLA")
        assert extension.vehicle_platform == "tesla"

    def test_full_initialization_flow(self, extension):
        """Test the complete initialization flow."""
        with patch.object(extension, "_create_provider"), patch.object(
            extension, "_register_commands"
        ):
            result = extension.on_initialize()

            assert result is True

    def test_custom_configuration(self):
        """Test extension with custom configuration via constructor."""
        extension = EXT_Automotive(
            vehicle_platform="tesla",
            vehicle_id="custom-vehicle-id",
            access_token="custom-token",
        )

        assert extension.vehicle_platform == "tesla"
        assert extension.vehicle_id == "custom-vehicle-id"
        assert extension.access_token == "custom-token"


if __name__ == "__main__":
    pytest.main([__file__])
