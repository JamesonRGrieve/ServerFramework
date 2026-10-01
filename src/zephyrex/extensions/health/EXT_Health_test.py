from unittest.mock import MagicMock, patch

import pytest

from zephyrex.extensions.health.EXT_Health import EXT_Health


class TestHealthExtension:
    """Test cases for Health Extension."""

    @pytest.fixture
    def extension(self):
        """Create an EXT_Health instance for testing."""
        return EXT_Health()

    def test_extension_metadata(self, extension):
        """Test extension metadata is correctly set."""
        assert extension.name == "health"
        assert extension.version == "1.0.0"
        assert "health tracking" in extension.description.lower()
        assert "fitness" in extension.description.lower()

    def test_dependencies(self, extension):
        """Test extension dependencies are properly defined."""
        # Check extension dependencies
        ext_deps = {dep.name for dep in extension.ext_dependencies}
        assert "labels" in ext_deps

        # Check pip dependencies
        pip_deps = {dep.name for dep in extension.pip_dependencies}
        assert "aiohttp" in pip_deps

        # Check sys dependencies
        assert isinstance(extension.sys_dependencies, list)

    def test_capabilities(self, extension):
        """Test extension capabilities are properly defined."""
        expected_capabilities = {
            "health_tracking",
            "fitness_data",
            "wellness_monitoring",
            "health_analysis",
            "biometric_data",
        }
        assert set(extension.capabilities) == expected_capabilities

    def test_initialization(self, extension):
        """Test extension initialization."""
        assert hasattr(extension, "provider")
        assert extension.provider is None  # Initially None

    def test_db_tables(self, extension):
        """Test database tables list."""
        assert isinstance(extension.db_tables, list)
        assert len(extension.db_tables) == 0  # Empty by default

    @patch("zephyrex.extensions.health.EXT_Health.logger")
    def test_on_initialize_success(self, mock_logger, extension):
        """Test successful extension initialization."""
        with patch.object(extension, "_create_provider"), patch.object(
            extension, "register_capability"
        ):

            result = extension.on_initialize()
            assert result is True
            mock_logger.debug.assert_called()

    @patch("zephyrex.extensions.health.EXT_Health.logger")
    def test_on_initialize_failure(self, mock_logger, extension):
        """Test extension initialization failure."""
        with patch.object(
            extension, "_create_provider", side_effect=Exception("Test error")
        ):
            result = extension.on_initialize()
            assert result is False
            mock_logger.error.assert_called()

    def test_create_provider(self, extension):
        """Test provider creation."""
        mock_provider_class = MagicMock()
        mock_provider_instance = MagicMock()
        mock_provider_class.return_value = mock_provider_instance

        with patch.object(extension, "load_provider", return_value=mock_provider_class):
            extension._create_provider()

            assert extension.provider == mock_provider_instance
            mock_provider_class.assert_called_once()

    def test_create_provider_no_class(self, extension):
        """Test provider creation when no provider class found."""
        with patch.object(extension, "load_provider", return_value=None), patch(
            "zephyrex.extensions.health.EXT_Health.logger"
        ) as mock_logger:

            extension._create_provider()

            assert extension.provider is None
            mock_logger.warning.assert_called_with("No health provider class found")

    def test_create_provider_error(self, extension):
        """Test provider creation with error."""
        with patch.object(
            extension, "load_provider", side_effect=Exception("Test error")
        ), patch("zephyrex.extensions.health.EXT_Health.logger") as mock_logger:

            extension._create_provider()

            assert extension.provider is None
            mock_logger.error.assert_called()

    def test_load_provider(self, extension):
        """Test load_provider method."""
        # This is a placeholder method that returns None
        result = extension.load_provider()
        assert result is None

    def test_capability_management(self, extension):
        """Test capability management methods."""
        # Test register_capability
        extension.register_capability("test_capability")
        assert "test_capability" in extension.capabilities

        # Test get_registered_capabilities
        capabilities = extension.get_registered_capabilities()
        assert isinstance(capabilities, set)
        assert "test_capability" in capabilities

        # Test get_capabilities
        capabilities = extension.get_capabilities()
        assert isinstance(capabilities, set)

    @pytest.mark.asyncio
    async def test_track_health_data_success(self, extension):
        """Test successful health data tracking."""
        mock_provider = MagicMock()
        mock_provider.track_health_data = MagicMock(return_value="data_tracked")
        extension.provider = mock_provider

        result = await extension.track_health_data(
            "steps", "10000", "2024-01-01T12:00:00Z"
        )

        assert result == "data_tracked"
        mock_provider.track_health_data.assert_called_once_with(
            "steps", "10000", "2024-01-01T12:00:00Z"
        )

    @pytest.mark.asyncio
    async def test_track_health_data_no_provider(self, extension):
        """Test health data tracking without provider."""
        extension.provider = None

        result = await extension.track_health_data("steps", "10000")

        assert "Health provider not configured" in result

    @pytest.mark.asyncio
    async def test_track_health_data_error(self, extension):
        """Test health data tracking with error."""
        mock_provider = MagicMock()
        mock_provider.track_health_data = MagicMock(
            side_effect=Exception("Provider error")
        )
        extension.provider = mock_provider

        result = await extension.track_health_data("steps", "10000")

        assert "Failed to track health data" in result

    @pytest.mark.asyncio
    async def test_get_health_summary_success(self, extension):
        """Test successful health summary retrieval."""
        mock_provider = MagicMock()
        mock_provider.get_health_summary = MagicMock(return_value="health_summary")
        extension.provider = mock_provider

        result = await extension.get_health_summary("user123", "month")

        assert result == "health_summary"
        mock_provider.get_health_summary.assert_called_once_with("user123", "month")

    @pytest.mark.asyncio
    async def test_get_health_summary_no_provider(self, extension):
        """Test health summary retrieval without provider."""
        extension.provider = None

        result = await extension.get_health_summary()

        assert "Health provider not configured" in result

    @pytest.mark.asyncio
    async def test_get_health_summary_error(self, extension):
        """Test health summary retrieval with error."""
        mock_provider = MagicMock()
        mock_provider.get_health_summary = MagicMock(
            side_effect=Exception("Provider error")
        )
        extension.provider = mock_provider

        result = await extension.get_health_summary()

        assert "Failed to get health summary" in result

    @pytest.mark.asyncio
    async def test_get_health_summary_default_parameters(self, extension):
        """Test health summary retrieval with default parameters."""
        mock_provider = MagicMock()
        mock_provider.get_health_summary = MagicMock(return_value="health_summary")
        extension.provider = mock_provider

        result = await extension.get_health_summary()

        mock_provider.get_health_summary.assert_called_once_with("", "week")

    @pytest.mark.asyncio
    async def test_analyze_fitness_trends_success(self, extension):
        """Test successful fitness trends analysis."""
        mock_provider = MagicMock()
        mock_provider.analyze_fitness_trends = MagicMock(return_value="fitness_trends")
        extension.provider = mock_provider

        result = await extension.analyze_fitness_trends("user123", "calories")

        assert result == "fitness_trends"
        mock_provider.analyze_fitness_trends.assert_called_once_with(
            "user123", "calories"
        )

    @pytest.mark.asyncio
    async def test_analyze_fitness_trends_no_provider(self, extension):
        """Test fitness trends analysis without provider."""
        extension.provider = None

        result = await extension.analyze_fitness_trends()

        assert "Health provider not configured" in result

    @pytest.mark.asyncio
    async def test_analyze_fitness_trends_error(self, extension):
        """Test fitness trends analysis with error."""
        mock_provider = MagicMock()
        mock_provider.analyze_fitness_trends = MagicMock(
            side_effect=Exception("Provider error")
        )
        extension.provider = mock_provider

        result = await extension.analyze_fitness_trends()

        assert "Failed to analyze fitness trends" in result

    @pytest.mark.asyncio
    async def test_analyze_fitness_trends_default_parameters(self, extension):
        """Test fitness trends analysis with default parameters."""
        mock_provider = MagicMock()
        mock_provider.analyze_fitness_trends = MagicMock(return_value="fitness_trends")
        extension.provider = mock_provider

        result = await extension.analyze_fitness_trends()

        mock_provider.analyze_fitness_trends.assert_called_once_with("", "steps")

    @pytest.mark.asyncio
    async def test_no_provider_warning(self, extension):
        """Test warning message when no provider is available."""
        result = await extension._no_provider_warning()

        assert "Health provider not configured" in result

    def test_lifecycle_methods(self, extension):
        """Test extension lifecycle methods."""
        # Test on_start
        assert extension.on_start() is True

        # Test on_stop
        extension.provider = MagicMock()
        assert extension.on_stop() is True
        assert extension.provider is None

        # Test on_startup and on_shutdown
        extension.on_startup()  # Should not raise exception
        extension.on_shutdown()  # Should not raise exception

    def test_validate_config_success(self, extension):
        """Test successful configuration validation."""
        extension.settings = {"test": "value"}

        issues = extension.validate_config()
        assert isinstance(issues, list)

    def test_validate_config_no_settings(self, extension):
        """Test configuration validation with no settings."""
        # Remove settings attribute
        if hasattr(extension, "settings"):
            delattr(extension, "settings")

        issues = extension.validate_config()

        assert any("Extension settings not provided" in issue for issue in issues)

    def test_validate_config_empty_settings(self, extension):
        """Test configuration validation with empty settings."""
        extension.settings = {}

        issues = extension.validate_config()

        assert any("Extension settings not provided" in issue for issue in issues)

    def test_get_required_permissions(self, extension):
        """Test required permissions."""
        permissions = extension.get_required_permissions()
        assert isinstance(permissions, list)
        assert len(permissions) > 0

        expected_permissions = [
            "health:read",
            "health:write",
            "health:analyze",
            "fitness:track",
        ]
        assert set(permissions) == set(expected_permissions)

    def test_provider_with_configuration_parameters(self, extension):
        """Test provider creation with configuration parameters."""
        extension.api_key = "test_api_key"
        extension.agent_name = "test_agent"
        extension.conversation_id = "conv_123"
        extension.conversation_name = "test_conversation"
        extension.user = "test_user"
        extension.settings = {"custom_setting": "value"}

        mock_provider_class = MagicMock()
        mock_provider_instance = MagicMock()
        mock_provider_class.return_value = mock_provider_instance

        with patch.object(extension, "load_provider", return_value=mock_provider_class):
            extension._create_provider()

            # Check that provider was called with correct parameters
            mock_provider_class.assert_called_once_with(
                api_key="test_api_key",
                agent_name="test_agent",
                conversation_id="conv_123",
                conversation_name="test_conversation",
                user="test_user",
                custom_setting="value",
            )

    def test_provider_with_default_parameters(self, extension):
        """Test provider creation with default parameters."""
        extension.settings = {"test": "value"}

        mock_provider_class = MagicMock()
        mock_provider_instance = MagicMock()
        mock_provider_class.return_value = mock_provider_instance

        with patch.object(extension, "load_provider", return_value=mock_provider_class):
            extension._create_provider()

            # Check that provider was called with default parameters
            mock_provider_class.assert_called_once_with(
                api_key="",
                agent_name="",
                conversation_id="",
                conversation_name="",
                user="",
                test="value",
            )

    def test_has_capability(self, extension):
        """Test has_capability method."""
        assert extension.has_capability("health_tracking") is True
        assert extension.has_capability("fitness_data") is True
        assert extension.has_capability("wellness_monitoring") is True
        assert extension.has_capability("health_analysis") is True
        assert extension.has_capability("biometric_data") is True
        assert extension.has_capability("nonexistent_capability") is False

    @pytest.mark.asyncio
    async def test_all_abilities_with_different_parameters(self, extension):
        """Test all abilities work with different parameters."""
        extension.provider = None

        # All abilities should return provider not available error
        result = await extension.track_health_data("weight", "70.5", "2024-01-01")
        assert "Health provider not configured" in result

        result = await extension.get_health_summary("user456", "year")
        assert "Health provider not configured" in result

        result = await extension.analyze_fitness_trends("user789", "distance")
        assert "Health provider not configured" in result

    @pytest.mark.asyncio
    async def test_provider_method_calls_with_parameters(self, extension):
        """Test that provider methods are called with correct parameters."""
        mock_provider = MagicMock()
        extension.provider = mock_provider

        # Test track_health_data with all parameters
        await extension.track_health_data("heart_rate", "75", "2024-01-01T10:30:00Z")
        mock_provider.track_health_data.assert_called_with(
            "heart_rate", "75", "2024-01-01T10:30:00Z"
        )

        # Test track_health_data with default timestamp
        await extension.track_health_data("blood_pressure", "120/80")
        mock_provider.track_health_data.assert_called_with(
            "blood_pressure", "120/80", ""
        )

        # Test get_health_summary with custom parameters
        await extension.get_health_summary("user999", "day")
        mock_provider.get_health_summary.assert_called_with("user999", "day")

        # Test analyze_fitness_trends with custom parameters
        await extension.analyze_fitness_trends("user888", "workout_time")
        mock_provider.analyze_fitness_trends.assert_called_with(
            "user888", "workout_time"
        )

    def test_custom_configuration_via_constructor(self):
        """Test extension with custom configuration via constructor."""
        extension = EXT_Health(custom_param="custom_value")

        # Should have the custom parameter available
        assert hasattr(extension, "custom_param")

    def test_provider_creation_error_handling(self, extension):
        """Test provider creation error handling."""
        mock_provider_class = MagicMock()
        mock_provider_class.side_effect = Exception("Creation error")

        with patch.object(
            extension, "load_provider", return_value=mock_provider_class
        ), patch("zephyrex.extensions.health.EXT_Health.logger") as mock_logger:

            extension._create_provider()

            assert extension.provider is None
            mock_logger.error.assert_called()

    def test_settings_access_in_provider_creation(self, extension):
        """Test that settings are properly accessed during provider creation."""
        # Test when settings don't exist
        if hasattr(extension, "settings"):
            delattr(extension, "settings")

        mock_provider_class = MagicMock()

        with patch.object(extension, "load_provider", return_value=mock_provider_class):
            extension._create_provider()

            # Should work without settings
            mock_provider_class.assert_called_once()

    @pytest.mark.asyncio
    async def test_abilities_with_empty_parameters(self, extension):
        """Test abilities with empty string parameters."""
        mock_provider = MagicMock()
        mock_provider.track_health_data = MagicMock(return_value="tracked")
        mock_provider.get_health_summary = MagicMock(return_value="summary")
        mock_provider.analyze_fitness_trends = MagicMock(return_value="trends")
        extension.provider = mock_provider

        # Test with empty strings
        await extension.track_health_data("", "", "")
        mock_provider.track_health_data.assert_called_with("", "", "")

        await extension.get_health_summary("", "")
        mock_provider.get_health_summary.assert_called_with("", "")

        await extension.analyze_fitness_trends("", "")
        mock_provider.analyze_fitness_trends.assert_called_with("", "")


if __name__ == "__main__":
    pytest.main([__file__])
