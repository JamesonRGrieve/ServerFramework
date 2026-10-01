from unittest.mock import MagicMock, patch

import pytest

from zephyrex.extensions.maps.EXT_Maps import EXT_Maps


class TestMapsExtension:
    """Test cases for Maps Extension."""

    @pytest.fixture
    def extension(self):
        """Create an EXT_Maps instance for testing."""
        return EXT_Maps()

    def test_extension_metadata(self, extension):
        """Test extension metadata is correctly set."""
        assert extension.name == "maps"
        assert extension.version == "1.0.0"
        assert "maps integration" in extension.description.lower()
        assert "mapping services" in extension.description.lower()

    def test_dependencies(self, extension):
        """Test extension dependencies are properly defined."""
        # Check extension dependencies
        ext_deps = {dep.name for dep in extension.ext_dependencies}
        assert "core" in ext_deps

        # Check pip dependencies
        pip_deps = {dep.name for dep in extension.pip_dependencies}
        assert "requests" in pip_deps
        assert "googlemaps" in pip_deps
        assert "polyline" in pip_deps

        # Check sys dependencies
        assert isinstance(extension.sys_dependencies, list)

    def test_capabilities(self, extension):
        """Test extension capabilities are properly defined."""
        expected_capabilities = {
            "location_search",
            "geocoding",
            "reverse_geocoding",
            "routing",
            "mapping",
            "directions",
        }
        assert set(extension.capabilities) == expected_capabilities

    def test_initialization(self, extension):
        """Test extension initialization."""
        assert hasattr(extension, "maps_type")
        assert hasattr(extension, "api_key")
        assert hasattr(extension, "api_uri")
        assert hasattr(extension, "api_version")
        assert hasattr(extension, "user_agent")
        assert hasattr(extension, "provider")

    def test_default_configuration(self, extension):
        """Test default configuration values."""
        assert extension.maps_type == "openstreetmap"
        assert extension.api_key == ""
        assert extension.api_uri == ""
        assert extension.api_version == "latest"
        assert extension.user_agent == "AGInfrastructure/1.0"

    def test_map_types_constant(self, extension):
        """Test MAP_TYPES constant is properly defined."""
        assert "open_source" in extension.MAP_TYPES
        assert "commercial" in extension.MAP_TYPES
        assert "routing" in extension.MAP_TYPES
        assert "geocoding" in extension.MAP_TYPES
        assert "satellite" in extension.MAP_TYPES

        assert "openstreetmap" in extension.MAP_TYPES["open_source"]
        assert "google" in extension.MAP_TYPES["commercial"]

    @patch("zephyrex.extensions.maps.EXT_Maps.logger")
    def test_on_initialize_success(self, mock_logger, extension):
        """Test successful extension initialization."""
        with patch.object(extension, "_create_provider"), patch.object(
            extension, "register_capability"
        ):

            result = extension.on_initialize()
            assert result is True
            mock_logger.debug.assert_called()

    @patch("zephyrex.extensions.maps.EXT_Maps.logger")
    def test_on_initialize_failure(self, mock_logger, extension):
        """Test extension initialization failure."""
        with patch.object(
            extension, "_create_provider", side_effect=Exception("Test error")
        ):
            result = extension.on_initialize()
            assert result is False
            mock_logger.error.assert_called()

    def test_create_provider_openstreetmap(self, extension):
        """Test OpenStreetMap provider creation."""
        extension.maps_type = "openstreetmap"

        with patch(
            "zephyrex.extensions.maps.openstreetmap.OpenStreetMapProvider"
        ) as mock_provider:
            mock_instance = MagicMock()
            mock_provider.return_value = mock_instance

            extension._create_provider()

            assert extension.provider == mock_instance
            mock_provider.assert_called_once()

    def test_create_provider_google(self, extension):
        """Test Google Maps provider creation."""
        extension.maps_type = "google"

        with patch(
            "zephyrex.extensions.maps.googlemaps.GoogleMapsProvider"
        ) as mock_provider:
            mock_instance = MagicMock()
            mock_provider.return_value = mock_instance

            extension._create_provider()

            assert extension.provider == mock_instance
            mock_provider.assert_called_once()

    def test_create_provider_apple(self, extension):
        """Test Apple Maps provider creation."""
        extension.maps_type = "apple"

        with patch(
            "zephyrex.extensions.maps.applemaps.AppleMapsProvider"
        ) as mock_provider:
            mock_instance = MagicMock()
            mock_provider.return_value = mock_instance

            extension._create_provider()

            assert extension.provider == mock_instance
            mock_provider.assert_called_once()

    def test_create_provider_unsupported_type(self, extension):
        """Test provider creation with unsupported maps type."""
        extension.maps_type = "unsupported_type"

        with patch("zephyrex.extensions.maps.EXT_Maps.logger") as mock_logger:
            extension._create_provider()

            assert extension.provider is None
            mock_logger.error.assert_called_with(
                "Unsupported maps type: unsupported_type"
            )

    def test_create_provider_import_error(self, extension):
        """Test provider creation with import error."""
        extension.maps_type = "openstreetmap"

        with patch(
            "zephyrex.extensions.maps.openstreetmap.OpenStreetMapProvider",
            side_effect=ImportError("Module not found"),
        ), patch("zephyrex.extensions.maps.EXT_Maps.logger") as mock_logger:

            extension._create_provider()

            assert extension.provider is None
            mock_logger.error.assert_called()

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
    async def test_search_location_success(self, extension):
        """Test successful location search."""
        mock_provider = MagicMock()
        mock_provider.search_location = MagicMock(return_value="location_found")
        extension.provider = mock_provider

        result = await extension.search_location("New York City")

        assert result == "location_found"
        mock_provider.search_location.assert_called_once_with("New York City")

    @pytest.mark.asyncio
    async def test_search_location_no_provider(self, extension):
        """Test location search without provider."""
        extension.provider = None
        extension.maps_type = "openstreetmap"

        result = await extension.search_location("Paris")

        assert "Maps provider not configured for openstreetmap" in result

    @pytest.mark.asyncio
    async def test_search_location_error(self, extension):
        """Test location search with error."""
        mock_provider = MagicMock()
        mock_provider.search_location = MagicMock(
            side_effect=Exception("Provider error")
        )
        extension.provider = mock_provider

        result = await extension.search_location("London")

        assert "Failed to search location" in result

    @pytest.mark.asyncio
    async def test_get_directions_success(self, extension):
        """Test successful directions retrieval."""
        mock_provider = MagicMock()
        mock_provider.get_directions = MagicMock(return_value="directions_found")
        extension.provider = mock_provider

        result = await extension.get_directions("Point A", "Point B", "walking")

        assert result == "directions_found"
        mock_provider.get_directions.assert_called_once_with(
            "Point A", "Point B", "walking"
        )

    @pytest.mark.asyncio
    async def test_get_directions_no_provider(self, extension):
        """Test directions retrieval without provider."""
        extension.provider = None
        extension.maps_type = "google"

        result = await extension.get_directions("A", "B")

        assert "Maps provider not configured for google" in result

    @pytest.mark.asyncio
    async def test_get_directions_error(self, extension):
        """Test directions retrieval with error."""
        mock_provider = MagicMock()
        mock_provider.get_directions = MagicMock(
            side_effect=Exception("Provider error")
        )
        extension.provider = mock_provider

        result = await extension.get_directions("A", "B")

        assert "Failed to get directions" in result

    @pytest.mark.asyncio
    async def test_get_directions_default_mode(self, extension):
        """Test directions retrieval with default mode."""
        mock_provider = MagicMock()
        mock_provider.get_directions = MagicMock(return_value="directions")
        extension.provider = mock_provider

        result = await extension.get_directions("Origin", "Destination")

        mock_provider.get_directions.assert_called_once_with(
            "Origin", "Destination", "driving"
        )

    @pytest.mark.asyncio
    async def test_geocode_success(self, extension):
        """Test successful geocoding."""
        mock_provider = MagicMock()
        mock_provider.geocode = MagicMock(return_value="coordinates")
        extension.provider = mock_provider

        result = await extension.geocode("123 Main St, City, State")

        assert result == "coordinates"
        mock_provider.geocode.assert_called_once_with("123 Main St, City, State")

    @pytest.mark.asyncio
    async def test_geocode_no_provider(self, extension):
        """Test geocoding without provider."""
        extension.provider = None
        extension.maps_type = "apple"

        result = await extension.geocode("Address")

        assert "Maps provider not configured for apple" in result

    @pytest.mark.asyncio
    async def test_geocode_error(self, extension):
        """Test geocoding with error."""
        mock_provider = MagicMock()
        mock_provider.geocode = MagicMock(side_effect=Exception("Provider error"))
        extension.provider = mock_provider

        result = await extension.geocode("Address")

        assert "Failed to geocode address" in result

    @pytest.mark.asyncio
    async def test_reverse_geocode_success(self, extension):
        """Test successful reverse geocoding."""
        mock_provider = MagicMock()
        mock_provider.reverse_geocode = MagicMock(return_value="address")
        extension.provider = mock_provider

        result = await extension.reverse_geocode(40.7128, -74.0060)

        assert result == "address"
        mock_provider.reverse_geocode.assert_called_once_with(40.7128, -74.0060)

    @pytest.mark.asyncio
    async def test_reverse_geocode_no_provider(self, extension):
        """Test reverse geocoding without provider."""
        extension.provider = None
        extension.maps_type = "openstreetmap"

        result = await extension.reverse_geocode(51.5074, -0.1278)

        assert "Maps provider not configured for openstreetmap" in result

    @pytest.mark.asyncio
    async def test_reverse_geocode_error(self, extension):
        """Test reverse geocoding with error."""
        mock_provider = MagicMock()
        mock_provider.reverse_geocode = MagicMock(
            side_effect=Exception("Provider error")
        )
        extension.provider = mock_provider

        result = await extension.reverse_geocode(48.8566, 2.3522)

        assert "Failed to reverse geocode" in result

    @pytest.mark.asyncio
    async def test_no_provider_warning(self, extension):
        """Test warning message when no provider is available."""
        extension.maps_type = "google"

        result = await extension._no_provider_warning()

        assert "Maps provider not configured for google" in result

    def test_get_maps_classifications_all(self, extension):
        """Test getting all maps classifications."""
        classifications = extension.get_maps_classifications()

        assert isinstance(classifications, dict)
        assert "open_source" in classifications
        assert "commercial" in classifications
        assert "routing" in classifications
        assert "geocoding" in classifications
        assert "satellite" in classifications

        assert "openstreetmap" in classifications["open_source"]
        assert "google" in classifications["commercial"]

    def test_get_maps_classifications_specific(self, extension):
        """Test getting classifications for specific maps type."""
        classifications = extension.get_maps_classifications("google")

        assert isinstance(classifications, dict)
        # Google should be in commercial, routing, geocoding, and satellite
        assert "commercial" in classifications
        assert "routing" in classifications
        assert "geocoding" in classifications
        assert "satellite" in classifications

        # Check that each classification contains only google
        for key, value in classifications.items():
            assert value == ["google"]

    def test_get_maps_classifications_nonexistent(self, extension):
        """Test getting classifications for non-existent maps type."""
        classifications = extension.get_maps_classifications("nonexistent")

        assert isinstance(classifications, dict)
        assert len(classifications) == 0

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
        issues = extension.validate_config()
        assert isinstance(issues, list)

    def test_validate_config_no_maps_type(self, extension):
        """Test configuration validation with no maps type."""
        extension.maps_type = ""

        issues = extension.validate_config()

        assert any("Maps type not specified" in issue for issue in issues)

    def test_validate_config_google_no_api_key(self, extension):
        """Test configuration validation for Google Maps without API key."""
        extension.maps_type = "google"
        extension.api_key = ""

        issues = extension.validate_config()

        assert any("Google Maps API key not provided" in issue for issue in issues)

    def test_validate_config_google_with_api_key(self, extension):
        """Test configuration validation for Google Maps with API key."""
        extension.maps_type = "google"
        extension.api_key = "test_api_key"

        issues = extension.validate_config()

        # Should not have API key related issues
        assert not any("Google Maps API key not provided" in issue for issue in issues)

    def test_get_required_permissions(self, extension):
        """Test required permissions."""
        permissions = extension.get_required_permissions()
        assert isinstance(permissions, list)
        assert len(permissions) > 0

        expected_permissions = [
            "maps:search",
            "maps:geocode",
            "maps:directions",
            "location:access",
        ]
        assert set(permissions) == set(expected_permissions)

    def test_custom_configuration(self):
        """Test extension with custom configuration."""
        extension = EXT_Maps(
            maps_type="google",
            api_key="test_api_key",
            api_uri="https://maps.googleapis.com",
            api_version="v1",
            user_agent="CustomApp/2.0",
        )

        assert extension.maps_type == "google"
        assert extension.api_key == "test_api_key"
        assert extension.api_uri == "https://maps.googleapis.com"
        assert extension.api_version == "v1"
        assert extension.user_agent == "CustomApp/2.0"

    def test_provider_with_configuration_parameters(self, extension):
        """Test provider creation with configuration parameters."""
        extension.maps_type = "google"
        extension.api_key = "test_api_key"
        extension.api_uri = "https://maps.googleapis.com"
        extension.api_version = "v1"
        extension.conversation_id = "conv_123"
        extension.agent_name = "test_agent"
        extension.ApiClient = MagicMock()
        extension.conversation_name = "test_conversation"
        extension.settings = {"custom_setting": "value"}

        with patch(
            "zephyrex.extensions.maps.googlemaps.GoogleMapsProvider"
        ) as mock_provider:
            extension._create_provider()

            # Check that provider was called with correct parameters
            mock_provider.assert_called_once()
            call_kwargs = mock_provider.call_args[1]
            assert call_kwargs["api_key"] == "test_api_key"
            assert call_kwargs["api_uri"] == "https://maps.googleapis.com"
            assert call_kwargs["api_version"] == "v1"
            assert call_kwargs["extension_id"] == "conv_123"
            assert call_kwargs["agent_name"] == "test_agent"
            assert call_kwargs["conversation_name"] == "test_conversation"
            assert call_kwargs["custom_setting"] == "value"

    def test_provider_with_default_parameters(self, extension):
        """Test provider creation with default parameters."""
        extension.maps_type = "openstreetmap"
        extension.settings = {"test": "value"}

        with patch(
            "zephyrex.extensions.maps.openstreetmap.OpenStreetMapProvider"
        ) as mock_provider:
            extension._create_provider()

            # Check that provider was called with default parameters
            mock_provider.assert_called_once()
            call_kwargs = mock_provider.call_args[1]
            assert call_kwargs["api_key"] == ""
            assert call_kwargs["api_uri"] == ""
            assert call_kwargs["user_agent"] == "AGInfrastructure/1.0"
            assert call_kwargs["extension_id"] == ""
            assert call_kwargs["agent_name"] == ""
            assert call_kwargs["conversation_name"] == ""
            assert call_kwargs["test"] == "value"

    def test_has_capability(self, extension):
        """Test has_capability method."""
        assert extension.has_capability("location_search") is True
        assert extension.has_capability("geocoding") is True
        assert extension.has_capability("reverse_geocoding") is True
        assert extension.has_capability("routing") is True
        assert extension.has_capability("mapping") is True
        assert extension.has_capability("directions") is True
        assert extension.has_capability("nonexistent_capability") is False

    @pytest.mark.asyncio
    async def test_all_abilities_with_different_maps_types(self, extension):
        """Test all abilities work with different maps types."""
        for maps_type in ["openstreetmap", "google", "apple"]:
            extension.maps_type = maps_type
            extension.provider = None

            # All abilities should return provider not available error
            result = await extension.search_location("Test Location")
            assert (
                result["success"] is False
                if isinstance(result, dict)
                else f"Maps provider not configured for {maps_type}" in result
            )

            result = await extension.get_directions("A", "B")
            assert (
                result["success"] is False
                if isinstance(result, dict)
                else f"Maps provider not configured for {maps_type}" in result
            )

            result = await extension.geocode("Test Address")
            assert (
                result["success"] is False
                if isinstance(result, dict)
                else f"Maps provider not configured for {maps_type}" in result
            )

            result = await extension.reverse_geocode(0.0, 0.0)
            assert (
                result["success"] is False
                if isinstance(result, dict)
                else f"Maps provider not configured for {maps_type}" in result
            )

    @pytest.mark.asyncio
    async def test_provider_method_calls_with_parameters(self, extension):
        """Test that provider methods are called with correct parameters."""
        mock_provider = MagicMock()
        extension.provider = mock_provider

        # Test search_location
        await extension.search_location("Central Park")
        mock_provider.search_location.assert_called_with("Central Park")

        # Test get_directions with all parameters
        await extension.get_directions("Home", "Work", "transit")
        mock_provider.get_directions.assert_called_with("Home", "Work", "transit")

        # Test geocode
        await extension.geocode("456 Oak Ave")
        mock_provider.geocode.assert_called_with("456 Oak Ave")

        # Test reverse_geocode
        await extension.reverse_geocode(37.7749, -122.4194)
        mock_provider.reverse_geocode.assert_called_with(37.7749, -122.4194)

    def test_provider_error_handling_during_creation(self, extension):
        """Test provider error handling during creation."""
        extension.maps_type = "google"

        with patch(
            "zephyrex.extensions.maps.googlemaps.GoogleMapsProvider",
            side_effect=Exception("Creation error"),
        ), patch("zephyrex.extensions.maps.EXT_Maps.logger") as mock_logger:

            extension._create_provider()

            assert extension.provider is None
            mock_logger.error.assert_called()

    def test_maps_type_case_insensitive(self):
        """Test that maps type is handled case-insensitively."""
        extension = EXT_Maps(maps_type="GOOGLE")
        assert extension.maps_type == "google"

        extension = EXT_Maps(maps_type="OpenStreetMap")
        assert extension.maps_type == "openstreetmap"

    @pytest.mark.asyncio
    async def test_abilities_with_empty_parameters(self, extension):
        """Test abilities with empty string parameters."""
        mock_provider = MagicMock()
        mock_provider.search_location = MagicMock(return_value="result")
        mock_provider.get_directions = MagicMock(return_value="result")
        mock_provider.geocode = MagicMock(return_value="result")
        mock_provider.reverse_geocode = MagicMock(return_value="result")
        extension.provider = mock_provider

        # Test with empty strings and zero coordinates
        await extension.search_location("")
        mock_provider.search_location.assert_called_with("")

        await extension.get_directions("", "", "")
        mock_provider.get_directions.assert_called_with("", "", "")

        await extension.geocode("")
        mock_provider.geocode.assert_called_with("")

        await extension.reverse_geocode(0.0, 0.0)
        mock_provider.reverse_geocode.assert_called_with(0.0, 0.0)

    def test_settings_passed_to_provider(self, extension):
        """Test that settings are properly passed to provider."""
        extension.maps_type = "openstreetmap"
        extension.settings = {"timeout": 30, "retries": 3}

        with patch(
            "zephyrex.extensions.maps.openstreetmap.OpenStreetMapProvider"
        ) as mock_provider:
            extension._create_provider()

            mock_provider.assert_called_once()
            call_kwargs = mock_provider.call_args[1]
            assert call_kwargs["timeout"] == 30
            assert call_kwargs["retries"] == 3


if __name__ == "__main__":
    pytest.main([__file__])
