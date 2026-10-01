from unittest.mock import MagicMock, patch

import pytest

from zephyrex.extensions.wearable.EXT_Wearable import EXT_Wearable


class TestEXTWearable:
    """
    Test suite for the Wearable extension.

    Tests extension metadata/configuration, Apple Health/FitBit provider
    creation and integration, health-data abilities (get_health_data,
    sync_devices, get_device_status, analyze_fitness_trends), capability
    management, and extension lifecycle/config validation. Mirrors
    TestAutomotiveExtension (the closest converted sibling): a plain
    pytest class with a local ``extension`` fixture, no legacy
    AbstractTest/AbstractEXTTest harness.
    """

    @pytest.fixture
    def extension(self) -> EXT_Wearable:
        """Create an EXT_Wearable instance for testing."""
        return EXT_Wearable()

    @pytest.fixture
    def mock_wearable_provider(self):
        """Mock wearable provider."""
        mock_provider = MagicMock()
        mock_provider.get_health_data.return_value = {
            "heart_rate": [72, 75, 68, 80, 73],
            "steps": 8542,
            "calories": 420,
        }
        mock_provider.sync_devices.return_value = "Devices synchronized successfully"
        mock_provider.get_device_status.return_value = {
            "device_id": "watch_001",
            "status": "connected",
            "battery": 85,
        }
        mock_provider.analyze_trends.return_value = {
            "metric": "steps",
            "trend": "increasing",
            "average": 9000,
        }
        return mock_provider

    def test_extension_metadata(self, extension):
        """Test extension metadata is correctly set."""
        assert extension.name == "wearable"
        assert extension.version == "1.0.0"
        assert "wearable health devices" in extension.description
        assert hasattr(extension, "ext_dependencies")
        assert hasattr(extension, "pip_dependencies")
        assert hasattr(extension, "sys_dependencies")

    def test_dependencies(self, extension):
        """Test that dependencies are properly structured."""
        ext_deps = extension.ext_dependencies
        assert len(ext_deps) == 2

        dep_names = {dep.name for dep in ext_deps}
        assert "oauth" in dep_names
        assert "labels" in dep_names

        for dep in ext_deps:
            assert dep.optional is True

        pip_deps = extension.pip_dependencies
        assert len(pip_deps) == 1

        pip_dep_names = {dep.name for dep in pip_deps}
        assert "requests" in pip_dep_names

        for dep in pip_deps:
            assert dep.optional is False

        assert isinstance(extension.sys_dependencies, list)
        assert len(extension.sys_dependencies) == 0

    def test_capabilities(self, extension):
        """Test extension capabilities are properly defined."""
        expected_capabilities = {
            "health_data_collection",
            "fitness_tracking",
            "device_management",
            "data_synchronization",
            "wearable_analytics",
        }
        assert set(extension.capabilities) == expected_capabilities

    def test_initialization(self, extension):
        """Test extension attributes are properly initialized."""
        assert hasattr(extension, "settings")
        assert hasattr(extension, "provider")
        assert extension.provider is None
        assert extension.settings == {}

    def test_initialization_with_custom_settings(self):
        """Test extension initialization with custom settings"""
        with patch("zephyrex.extensions.wearable.EXT_Wearable.logger"):
            extension = EXT_Wearable(
                api_key="test_api_key",
                conversation_id="conv123",
                device_platform="fitbit",
                auto_sync=True,
            )

            assert hasattr(extension, "settings")
            assert extension.settings.get("device_platform") == "fitbit"
            assert extension.settings.get("auto_sync") is True

    def test_db_tables(self, extension):
        """Test database tables list."""
        assert isinstance(extension.db_tables, list)
        assert len(extension.db_tables) == 0

    @patch("zephyrex.extensions.wearable.EXT_Wearable.logger")
    def test_on_initialize_success(self, mock_logger, extension):
        """Test successful extension initialization."""
        with patch.object(extension, "_create_provider"):
            result = extension.on_initialize()
            assert result is True
            mock_logger.debug.assert_called()

    @patch("zephyrex.extensions.wearable.EXT_Wearable.logger")
    def test_on_initialize_failure(self, mock_logger, extension):
        """Test extension initialization failure handling."""
        with patch.object(
            extension, "_create_provider", side_effect=Exception("Test error")
        ):
            result = extension.on_initialize()
            assert result is False
            mock_logger.error.assert_called()

    def test_create_provider_apple_success(self, extension, mock_wearable_provider):
        """Test successful Apple Health provider creation."""
        extension.settings = {"device_platform": "apple", "api_key": "test-key"}

        with patch(
            "zephyrex.extensions.wearable.PRV_Apple.AppleHealthProvider",
            return_value=mock_wearable_provider,
        ):
            extension._create_provider()

            assert extension.provider is not None
            assert extension.provider == mock_wearable_provider

    def test_create_provider_fitbit_success(self, extension, mock_wearable_provider):
        """Test successful FitBit provider creation."""
        extension.settings = {"device_platform": "fitbit", "api_key": "test-key"}

        with patch(
            "zephyrex.extensions.wearable.PRV_FitBit.FitBitProvider",
            return_value=mock_wearable_provider,
        ):
            extension._create_provider()

            assert extension.provider is not None
            assert extension.provider == mock_wearable_provider

    def test_create_provider_unsupported_platform(self, extension):
        """Test provider creation with an unsupported platform."""
        extension.settings = {"device_platform": "garmin"}
        extension._create_provider()

        assert extension.provider is None

    def test_create_provider_no_platform(self, extension):
        """Test provider creation without a specified device platform."""
        extension.settings = {}
        extension._create_provider()

        assert extension.provider is None

    def test_create_provider_import_failure(self, extension):
        """Test provider creation when the Apple provider import/construction fails."""
        extension.settings = {"device_platform": "apple"}

        with patch(
            "zephyrex.extensions.wearable.PRV_Apple.AppleHealthProvider",
            side_effect=ImportError("Mock import error"),
        ):
            extension._create_provider()

            assert extension.provider is None

    def test_apple_provider_creation_parameters(
        self, extension, mock_wearable_provider
    ):
        """Test that the Apple provider is created with correct parameters,
        with settings correctly split between explicit kwargs and the
        pass-through provider_kwargs bag (no duplicate-keyword collision)."""
        extension.settings = {
            "device_platform": "apple",
            "api_key": "test-api-key",
            "conversation_id": "test-conversation-id",
            "auto_sync": True,
        }

        with patch(
            "zephyrex.extensions.wearable.PRV_Apple.AppleHealthProvider",
            return_value=mock_wearable_provider,
        ) as mock_apple_class:
            extension._create_provider()

            mock_apple_class.assert_called_once_with(
                api_key="test-api-key",
                extension_id="wearable",
                conversation_directory="test-conversation-id",
                auto_sync=True,
            )

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
        first = EXT_Wearable()
        second = EXT_Wearable()

        first.register_capability("instance_only_capability")

        assert "instance_only_capability" in first.capabilities
        assert "instance_only_capability" not in second.capabilities

    @pytest.mark.asyncio
    async def test_get_health_data_success(self, extension, mock_wearable_provider):
        """Test successful health data retrieval."""
        extension.provider = mock_wearable_provider

        result = await extension.get_health_data(
            device_type="smartwatch", data_type="steps", period="today"
        )

        assert result["steps"] == 8542
        mock_wearable_provider.get_health_data.assert_called_once_with(
            "smartwatch", "steps", "today"
        )

    @pytest.mark.asyncio
    async def test_get_health_data_no_provider(self, extension):
        """Test health data retrieval without provider"""
        extension.provider = None

        result = await extension.get_health_data()

        assert "error" in result
        assert "not configured" in result["error"]

    @pytest.mark.asyncio
    async def test_get_health_data_not_supported(self, extension):
        """Test health data retrieval when not supported by provider"""
        mock_provider = MagicMock()
        if hasattr(mock_provider, "get_health_data"):
            delattr(mock_provider, "get_health_data")
        extension.provider = mock_provider

        result = await extension.get_health_data()

        assert "error" in result
        assert "not supported" in result["error"]

    @pytest.mark.asyncio
    async def test_get_health_data_provider_error(
        self, extension, mock_wearable_provider
    ):
        """Test health data retrieval when the provider raises."""
        mock_wearable_provider.get_health_data.side_effect = Exception(
            "Provider error"
        )
        extension.provider = mock_wearable_provider

        result = await extension.get_health_data()

        assert "error" in result
        assert "Failed to get health data" in result["error"]

    @pytest.mark.asyncio
    async def test_sync_devices_success(self, extension, mock_wearable_provider):
        """Test successful device synchronization."""
        extension.provider = mock_wearable_provider

        result = await extension.sync_devices()

        assert "synchronized successfully" in result
        mock_wearable_provider.sync_devices.assert_called_once()

    @pytest.mark.asyncio
    async def test_sync_devices_no_provider(self, extension):
        """Test device sync without a provider."""
        extension.provider = None

        result = await extension.sync_devices()

        assert "not configured" in result

    @pytest.mark.asyncio
    async def test_sync_devices_not_supported(self, extension):
        """Test device sync when not supported by provider."""
        mock_provider = MagicMock()
        if hasattr(mock_provider, "sync_devices"):
            delattr(mock_provider, "sync_devices")
        extension.provider = mock_provider

        result = await extension.sync_devices()

        assert "not supported" in result

    @pytest.mark.asyncio
    async def test_get_device_status_success(self, extension, mock_wearable_provider):
        """Test successful device status retrieval."""
        extension.provider = mock_wearable_provider

        result = await extension.get_device_status(device_id="watch_001")

        assert result["status"] == "connected"
        mock_wearable_provider.get_device_status.assert_called_once_with("watch_001")

    @pytest.mark.asyncio
    async def test_get_device_status_no_provider(self, extension):
        """Test device status retrieval without a provider."""
        extension.provider = None

        result = await extension.get_device_status(device_id="watch_001")

        assert "error" in result
        assert "not configured" in result["error"]

    @pytest.mark.asyncio
    async def test_get_device_status_not_supported(self, extension):
        """Test device status retrieval when not supported by provider."""
        mock_provider = MagicMock()
        if hasattr(mock_provider, "get_device_status"):
            delattr(mock_provider, "get_device_status")
        extension.provider = mock_provider

        result = await extension.get_device_status(device_id="watch_001")

        assert "error" in result
        assert "not supported" in result["error"]

    @pytest.mark.asyncio
    async def test_analyze_fitness_trends_success(
        self, extension, mock_wearable_provider
    ):
        """Test successful fitness trend analysis."""
        extension.provider = mock_wearable_provider

        result = await extension.analyze_fitness_trends(metric="steps", period="week")

        assert result["trend"] == "increasing"
        mock_wearable_provider.analyze_trends.assert_called_once_with("steps", "week")

    @pytest.mark.asyncio
    async def test_analyze_fitness_trends_no_provider(self, extension):
        """Test fitness trend analysis without a provider."""
        extension.provider = None

        result = await extension.analyze_fitness_trends()

        assert "error" in result
        assert "not configured" in result["error"]

    @pytest.mark.asyncio
    async def test_analyze_fitness_trends_not_supported(self, extension):
        """Test fitness trend analysis when not supported by provider."""
        mock_provider = MagicMock()
        if hasattr(mock_provider, "analyze_trends"):
            delattr(mock_provider, "analyze_trends")
        extension.provider = mock_provider

        result = await extension.analyze_fitness_trends()

        assert "error" in result
        assert "not supported" in result["error"]

    @pytest.mark.asyncio
    async def test_wearable_operations_without_provider(self, extension):
        """Test wearable operations without a provider."""
        extension.provider = None

        health_data = await extension.get_health_data()
        assert "not configured" in health_data["error"]

        sync_result = await extension.sync_devices()
        assert "not configured" in sync_result

        status_result = await extension.get_device_status()
        assert "not configured" in status_result["error"]

        trends_result = await extension.analyze_fitness_trends()
        assert "not configured" in trends_result["error"]

    @pytest.mark.asyncio
    async def test_no_provider_warning(self, extension):
        """Test the no-provider warning message."""
        result = await extension._no_provider_warning()

        assert "not configured" in result

    def test_lifecycle_methods(self, extension):
        """Test extension lifecycle methods."""
        assert extension.on_start() is True

        extension.provider = MagicMock()
        assert extension.on_stop() is True
        assert extension.provider is None

        extension.on_startup()
        extension.on_shutdown()

    def test_provider_cleanup_on_stop(self, extension, mock_wearable_provider):
        """Test that the provider is cleaned up when the extension stops."""
        extension.provider = mock_wearable_provider

        result = extension.on_stop()

        assert result is True
        assert extension.provider is None

    @patch("zephyrex.extensions.wearable.EXT_Wearable.logger")
    def test_extension_startup_shutdown_hooks(self, mock_logger, extension):
        """Test startup and shutdown hooks log the expected messages."""
        extension.on_startup()
        mock_logger.debug.assert_called_with("Wearable extension startup hook called")

        extension.on_shutdown()
        mock_logger.debug.assert_called_with(
            "Wearable extension shutdown hook called"
        )

    def test_validate_config_with_platform(self):
        """Test configuration validation with a device platform specified."""
        with patch("zephyrex.extensions.wearable.EXT_Wearable.logger"):
            extension = EXT_Wearable(device_platform="apple")
            issues = extension.validate_config()

            assert len(issues) == 0

    def test_validate_config_no_platform(self):
        """Test configuration validation without a device platform."""
        with patch("zephyrex.extensions.wearable.EXT_Wearable.logger"):
            extension = EXT_Wearable()
            issues = extension.validate_config()

            assert len(issues) >= 1
            issue_text = " ".join(issues).lower()
            assert "device platform not specified" in issue_text

    def test_get_required_permissions(self, extension):
        """Test getting required permissions."""
        permissions = extension.get_required_permissions()

        assert isinstance(permissions, list)
        assert len(permissions) == 4
        assert "wearable:read" in permissions
        assert "wearable:sync" in permissions
        assert "health:data" in permissions
        assert "fitness:track" in permissions

    def test_has_capability(self, extension):
        """Test capability checking."""
        for capability in extension.capabilities:
            assert extension.has_capability(capability) is True

        assert extension.has_capability("non_existent_capability") is False

    def test_platform_specific_initialization(self):
        """Test initialization with a supported platform."""
        extension = EXT_Wearable(device_platform="fitbit")
        assert extension.settings.get("device_platform") == "fitbit"

    def test_custom_configuration(self):
        """Test extension with custom configuration via constructor."""
        extension = EXT_Wearable(
            device_platform="apple",
            api_key="custom-key",
            conversation_id="custom-conversation-id",
        )

        assert extension.settings.get("device_platform") == "apple"
        assert extension.settings.get("api_key") == "custom-key"
        assert extension.settings.get("conversation_id") == "custom-conversation-id"


if __name__ == "__main__":
    pytest.main([__file__])
