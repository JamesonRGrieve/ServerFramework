from unittest.mock import MagicMock, patch

import pytest

from zephyrex.extensions.media.EXT_Media import EXT_Media
from zephyrex.extensions.media.PRV_Amazon import AmazonProvider
from zephyrex.extensions.media.PRV_IMDB import IMDBProvider
from zephyrex.extensions.media.PRV_Netflix import NetflixProvider
from zephyrex.extensions.media.PRV_YouTube import YouTubeProvider


class TestMediaExtension:
    """
    Test suite for the Media extension.

    Tests extension metadata/configuration, provider creation for each of
    the four supported platforms (Amazon Prime Video, IMDB, Netflix,
    YouTube), the search/info/recommendations abilities, capability
    management, and extension lifecycle/config validation.
    """

    @pytest.fixture
    def extension(self):
        """Create an EXT_Media instance for testing."""
        return EXT_Media()

    @pytest.fixture
    def mock_provider(self):
        """Mock media provider."""
        mock_provider = MagicMock()
        mock_provider.search.return_value = {
            "results": [{"title": "Test Content", "type": "movie"}],
            "total": 1,
        }
        mock_provider.get_info.return_value = {
            "title": "Test Content",
            "duration": "120 minutes",
            "genre": "Action",
        }
        mock_provider.get_recommendations.return_value = {
            "recommendations": [{"title": "Recommended Content", "score": 0.8}]
        }
        return mock_provider

    def test_extension_metadata(self, extension):
        """Test extension metadata is correctly set."""
        assert extension.name == "media"
        assert extension.version == "1.0.0"
        assert "media streaming" in extension.description.lower()
        assert hasattr(extension, "ext_dependencies")
        assert hasattr(extension, "pip_dependencies")
        assert hasattr(extension, "sys_dependencies")

    def test_dependencies(self, extension):
        """Test that dependencies are properly structured."""
        ext_deps = extension.ext_dependencies
        assert len(ext_deps) == 1
        assert ext_deps[0].name == "labels"
        assert ext_deps[0].optional is True

        pip_deps = extension.pip_dependencies
        assert len(pip_deps) == 1
        assert pip_deps[0].name == "requests"
        assert pip_deps[0].optional is False

        assert isinstance(extension.sys_dependencies, list)
        assert len(extension.sys_dependencies) == 0

    def test_capabilities(self, extension):
        """Test extension capabilities are properly defined."""
        expected_capabilities = {
            "media_search",
            "media_info",
            "media_recommendations",
            "content_discovery",
            "streaming_integration",
        }
        assert set(extension.capabilities) == expected_capabilities

    def test_initialization(self, extension):
        """Test extension attributes are properly initialized."""
        assert hasattr(extension, "media_platform")
        assert hasattr(extension, "api_key")
        assert hasattr(extension, "provider")
        assert hasattr(extension, "commands")
        assert extension.provider is None

    def test_default_configuration(self, extension):
        """Test default configuration values."""
        assert extension.media_platform == "netflix"
        assert extension.api_key == ""

    def test_platform_is_lowercased(self):
        """Test that the media platform is normalized to lower case."""
        extension = EXT_Media(media_platform="NETFLIX")
        assert extension.media_platform == "netflix"

    def test_db_tables(self, extension):
        """Test database tables list."""
        assert isinstance(extension.db_tables, list)
        assert len(extension.db_tables) == 0

    def test_extra_kwargs_become_settings(self):
        """Test that unrecognized constructor kwargs are captured as settings."""
        extension = EXT_Media(netflix_api_uri="https://example.com/netflix")
        assert (
            extension.settings.get("netflix_api_uri") == "https://example.com/netflix"
        )

    @patch("zephyrex.extensions.media.EXT_Media.logger")
    def test_on_initialize_success(self, mock_logger, extension):
        """Test successful extension initialization."""
        with patch.object(extension, "_create_provider"), patch.object(
            extension, "_register_commands"
        ):
            result = extension.on_initialize()
            assert result is True
            mock_logger.debug.assert_called()

    @patch("zephyrex.extensions.media.EXT_Media.logger")
    def test_on_initialize_failure(self, mock_logger, extension):
        """Test extension initialization failure handling."""
        with patch.object(
            extension, "_create_provider", side_effect=Exception("Test error")
        ):
            result = extension.on_initialize()
            assert result is False
            mock_logger.error.assert_called()

    def test_create_provider_amazon_success(self, extension, mock_provider):
        """Test successful Amazon Prime Video provider creation."""
        extension.media_platform = "amazon"

        with patch(
            "zephyrex.extensions.media.PRV_Amazon.AmazonProvider",
            return_value=mock_provider,
        ):
            extension._create_provider()

            assert extension.provider is not None
            assert extension.provider == mock_provider

    def test_create_provider_imdb_success(self, extension, mock_provider):
        """Test successful IMDB provider creation."""
        extension.media_platform = "imdb"

        with patch(
            "zephyrex.extensions.media.PRV_IMDB.IMDBProvider",
            return_value=mock_provider,
        ):
            extension._create_provider()

            assert extension.provider is not None
            assert extension.provider == mock_provider

    def test_create_provider_netflix_success(self, extension, mock_provider):
        """Test successful Netflix provider creation (the default)."""
        extension.media_platform = "netflix"

        with patch(
            "zephyrex.extensions.media.PRV_Netflix.NetflixProvider",
            return_value=mock_provider,
        ):
            extension._create_provider()

            assert extension.provider is not None
            assert extension.provider == mock_provider

    def test_create_provider_youtube_success(self, extension, mock_provider):
        """Test successful YouTube provider creation."""
        extension.media_platform = "youtube"

        with patch(
            "zephyrex.extensions.media.PRV_YouTube.YouTubeProvider",
            return_value=mock_provider,
        ):
            extension._create_provider()

            assert extension.provider is not None
            assert extension.provider == mock_provider

    def test_create_provider_unsupported_platform(self, extension):
        """Test provider creation with an unsupported media platform."""
        extension.media_platform = "unsupported"
        extension._create_provider()

        assert extension.provider is None

    def test_create_provider_import_failure(self, extension):
        """Test provider creation when the provider import/construction fails."""
        with patch(
            "zephyrex.extensions.media.PRV_Netflix.NetflixProvider",
            side_effect=ImportError("Mock import error"),
        ):
            extension._create_provider()

            assert extension.provider is None

    def test_provider_creation_parameters(self, extension, mock_provider):
        """Test that the provider is created with the correct parameters."""
        extension.media_platform = "netflix"
        extension.api_key = "test-api-key"
        extension.agent_name = "Test Agent"
        extension.conversation_name = "Test Conversation"
        extension.conversation_id = "conv-123"
        extension.user = "test-user"

        with patch(
            "zephyrex.extensions.media.PRV_Netflix.NetflixProvider",
            return_value=mock_provider,
        ) as mock_provider_class:
            extension._create_provider()

            mock_provider_class.assert_called_once_with(
                api_key="test-api-key",
                agent_name="Test Agent",
                conversation_name="Test Conversation",
                conversation_id="conv-123",
                user="test-user",
                extension_id="media",
            )

    def test_register_commands_with_provider(self, extension, mock_provider):
        """Test command registration with an available provider."""
        mock_provider.commands = {"Search Netflix": mock_provider.search}
        extension.provider = mock_provider
        extension._register_commands()

        assert extension.commands == mock_provider.commands

    def test_register_commands_without_provider(self, extension):
        """Test command registration without a provider."""
        extension.media_platform = "netflix"
        extension.provider = None
        extension._register_commands()

        assert len(extension.commands) > 0
        for command_name in extension.commands:
            assert "NETFLIX" in command_name

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
        first = EXT_Media()
        second = EXT_Media()

        first.register_capability("instance_only_capability")

        assert "instance_only_capability" in first.capabilities
        assert "instance_only_capability" not in second.capabilities

    @pytest.mark.asyncio
    async def test_search_media_with_provider(self, extension, mock_provider):
        """Test searching media with a provider."""
        extension.provider = mock_provider

        result = await extension.search_media(query="action movies", service="netflix")

        assert result["results"] == [{"title": "Test Content", "type": "movie"}]
        mock_provider.search.assert_called_once_with("action movies", "netflix")

    @pytest.mark.asyncio
    async def test_search_media_without_provider(self, extension):
        """Test searching media without a provider configured."""
        extension.provider = None

        result = await extension.search_media(query="action movies")

        assert "error" in result
        assert "No media provider available" in result["error"]

    @pytest.mark.asyncio
    async def test_search_media_provider_error(self, extension, mock_provider):
        """Test searching media when the provider raises."""
        mock_provider.search.side_effect = Exception("Search error")
        extension.provider = mock_provider

        result = await extension.search_media(query="action movies")

        assert "error" in result
        assert "Search error" in result["error"]

    @pytest.mark.asyncio
    async def test_get_media_info_with_provider(self, extension, mock_provider):
        """Test getting media info with a provider."""
        extension.provider = mock_provider

        result = await extension.get_media_info(media_id="movie123")

        assert result["title"] == "Test Content"
        mock_provider.get_info.assert_called_once_with("movie123")

    @pytest.mark.asyncio
    async def test_get_media_info_without_provider(self, extension):
        """Test getting media info without a provider configured."""
        extension.provider = None

        result = await extension.get_media_info(media_id="movie123")

        assert "error" in result
        assert "No media provider available" in result["error"]

    @pytest.mark.asyncio
    async def test_get_media_info_provider_error(self, extension, mock_provider):
        """Test getting media info when the provider raises."""
        mock_provider.get_info.side_effect = Exception("Info error")
        extension.provider = mock_provider

        result = await extension.get_media_info(media_id="movie123")

        assert "error" in result
        assert "Info error" in result["error"]

    @pytest.mark.asyncio
    async def test_get_media_recommendations_with_provider(
        self, extension, mock_provider
    ):
        """Test getting media recommendations with a provider."""
        extension.provider = mock_provider

        result = await extension.get_media_recommendations(
            user_id="user123", genre="action"
        )

        assert "recommendations" in result
        mock_provider.get_recommendations.assert_called_once_with("user123", "action")

    @pytest.mark.asyncio
    async def test_get_media_recommendations_without_provider(self, extension):
        """Test getting media recommendations without a provider configured."""
        extension.provider = None

        result = await extension.get_media_recommendations(user_id="user123")

        assert "error" in result
        assert "No media provider available" in result["error"]

    @pytest.mark.asyncio
    async def test_get_media_recommendations_provider_error(
        self, extension, mock_provider
    ):
        """Test getting media recommendations when the provider raises."""
        mock_provider.get_recommendations.side_effect = Exception("Recs error")
        extension.provider = mock_provider

        result = await extension.get_media_recommendations(user_id="user123")

        assert "error" in result
        assert "Recs error" in result["error"]

    def test_lifecycle_methods(self, extension):
        """Test extension lifecycle methods."""
        assert extension.on_start() is True

        extension.provider = MagicMock()
        assert extension.on_stop() is True
        assert extension.provider is None

        extension.on_startup()
        extension.on_shutdown()

    def test_provider_cleanup_on_stop(self, extension, mock_provider):
        """Test that the provider is cleaned up when the extension stops."""
        extension.provider = mock_provider

        result = extension.on_stop()

        assert result is True
        assert extension.provider is None

    @patch("zephyrex.extensions.media.EXT_Media.logger")
    def test_extension_startup_shutdown_hooks(self, mock_logger, extension):
        """Test startup and shutdown hooks log the expected messages."""
        extension.on_startup()
        mock_logger.debug.assert_called_with("Media extension startup hook called")

        extension.on_shutdown()
        mock_logger.debug.assert_called_with("Media extension shutdown hook called")

    def test_validate_config_all_available(self):
        """Test configuration validation when all dependencies are available."""
        extension = EXT_Media(media_platform="netflix")
        issues = extension.validate_config()

        assert len(issues) == 0

    def test_validate_config_unsupported_platform(self):
        """Test configuration validation with an unsupported media platform."""
        extension = EXT_Media(media_platform="unsupported")
        issues = extension.validate_config()

        assert len(issues) >= 1
        issue_text = " ".join(issues).lower()
        assert "unsupported" in issue_text

    def test_get_required_permissions(self, extension):
        """Test getting required permissions."""
        permissions = extension.get_required_permissions()

        assert isinstance(permissions, list)
        assert len(permissions) == 4
        assert "media:search" in permissions
        assert "media:info" in permissions
        assert "media:recommend" in permissions
        assert "content:access" in permissions

    def test_has_capability(self, extension):
        """Test capability checking."""
        for capability in extension.capabilities:
            assert extension.has_capability(capability) is True

        assert extension.has_capability("non_existent_capability") is False

    def test_full_initialization_flow(self, extension):
        """Test the complete initialization flow."""
        with patch.object(extension, "_create_provider"), patch.object(
            extension, "_register_commands"
        ):
            result = extension.on_initialize()

            assert result is True

    def test_custom_configuration(self):
        """Test extension with custom configuration via constructor."""
        extension = EXT_Media(
            media_platform="imdb",
            api_key="custom-api-key",
            agent_name="Custom Agent",
        )

        assert extension.media_platform == "imdb"
        assert extension.api_key == "custom-api-key"
        assert extension.agent_name == "Custom Agent"


class TestAmazonProvider:
    """Unit tests for the Amazon Prime Video provider."""

    @pytest.fixture
    def provider(self):
        return AmazonProvider()

    def test_get_platform_name(self, provider):
        assert provider.get_platform_name() == "Amazon Prime Video"

    def test_commands_registered(self, provider):
        assert "Search Amazon Prime Video" in provider.commands
        assert "Get Amazon Prime Video Info" in provider.commands
        assert "Get Amazon Prime Video Recommendations" in provider.commands
        assert "Get Amazon Rentals" in provider.commands
        assert "Get Amazon Purchases" in provider.commands

    def test_search(self, provider):
        result = provider.search("action movies")
        assert result["service"] == "Amazon Prime Video"
        assert len(result["results"]) == 2

    def test_get_info(self, provider):
        result = provider.get_info("amzn123")
        assert result["id"] == "amzn123"
        assert "title" in result

    def test_get_recommendations(self, provider):
        result = provider.get_recommendations("user123", genre="comedy")
        assert result["genre"] == "comedy"
        assert len(result["recommendations"]) == 2

    def test_get_rentals(self, provider):
        result = provider.get_rentals("user123")
        assert result["user_id"] == "user123"
        assert len(result["rentals"]) == 2

    def test_get_purchases(self, provider):
        result = provider.get_purchases("user123")
        assert result["user_id"] == "user123"
        assert len(result["purchases"]) == 2

    def test_default_api_uri(self, provider):
        assert provider.api_uri == "https://api.amazon.com/v1"

    def test_custom_api_uri(self):
        provider = AmazonProvider(amazon_api_uri="https://custom.example.com")
        assert provider.api_uri == "https://custom.example.com"


class TestIMDBProvider:
    """Unit tests for the IMDB provider."""

    @pytest.fixture
    def provider(self):
        return IMDBProvider()

    def test_get_platform_name(self, provider):
        assert provider.get_platform_name() == "IMDB"

    def test_commands_registered(self, provider):
        assert "Search IMDB" in provider.commands
        assert "Get IMDB Info" in provider.commands
        assert "Get IMDB Recommendations" in provider.commands
        assert "Get IMDB Ratings" in provider.commands
        assert "Get IMDB Reviews" in provider.commands

    def test_search(self, provider):
        result = provider.search("space opera")
        assert result["service"] == "IMDB"
        assert len(result["results"]) == 2

    def test_get_info(self, provider):
        result = provider.get_info("tt1234567")
        assert result["id"] == "tt1234567"
        assert "director" in result

    def test_get_recommendations(self, provider):
        result = provider.get_recommendations("user123", genre="drama")
        assert result["genre"] == "drama"
        assert len(result["recommendations"]) == 2

    def test_get_ratings(self, provider):
        result = provider.get_ratings("tt1234567")
        assert result["id"] == "tt1234567"
        assert "imdb_rating" in result

    def test_get_reviews_respects_limit(self, provider):
        result = provider.get_reviews("tt1234567", limit=1)
        assert len(result["reviews"]) == 1

    def test_get_reviews_default_limit(self, provider):
        result = provider.get_reviews("tt1234567")
        assert len(result["reviews"]) == 2


class TestNetflixProvider:
    """Unit tests for the Netflix provider."""

    @pytest.fixture
    def provider(self):
        return NetflixProvider()

    def test_get_platform_name(self, provider):
        assert provider.get_platform_name() == "Netflix"

    def test_commands_registered(self, provider):
        assert "Search Netflix" in provider.commands
        assert "Get Netflix Info" in provider.commands
        assert "Get Netflix Recommendations" in provider.commands
        assert "Get Netflix Watch History" in provider.commands
        assert "Get Netflix My List" in provider.commands
        assert "Add To Netflix My List" in provider.commands

    def test_search(self, provider):
        result = provider.search("comedy specials")
        assert result["service"] == "Netflix"
        assert len(result["results"]) == 2

    def test_get_info_series(self, provider):
        result = provider.get_info("nflx123")
        assert result["seasons"] == 3

    def test_get_info_movie(self, provider):
        result = provider.get_info("nflx456")
        assert result["seasons"] is None

    def test_get_recommendations(self, provider):
        result = provider.get_recommendations("user123")
        assert len(result["recommendations"]) == 2

    def test_get_watch_history_respects_limit(self, provider):
        result = provider.get_watch_history("user123", limit=1)
        assert len(result["history"]) == 1

    def test_get_my_list(self, provider):
        result = provider.get_my_list("user123")
        assert result["user_id"] == "user123"
        assert len(result["items"]) == 2

    def test_add_to_my_list(self, provider):
        result = provider.add_to_my_list("user123", "nflx901")
        assert result["status"] == "success"
        assert "nflx901" in result["message"]


class TestYouTubeProvider:
    """Unit tests for the YouTube provider."""

    @pytest.fixture
    def provider(self):
        return YouTubeProvider()

    def test_get_platform_name(self, provider):
        assert provider.get_platform_name() == "YouTube"

    def test_commands_registered(self, provider):
        assert "Search YouTube" in provider.commands
        assert "Get YouTube Info" in provider.commands
        assert "Get YouTube Recommendations" in provider.commands
        assert "Get YouTube Channel Info" in provider.commands
        assert "Get YouTube Playlists" in provider.commands
        assert "Get YouTube Comments" in provider.commands

    def test_search(self, provider):
        result = provider.search("python tutorials")
        assert result["service"] == "YouTube"
        assert len(result["results"]) == 2

    def test_get_info(self, provider):
        result = provider.get_info("yt123abc")
        assert result["id"] == "yt123abc"
        assert "duration" in result

    def test_get_recommendations(self, provider):
        result = provider.get_recommendations("user123", genre="gaming")
        assert result["category"] == "gaming"

    def test_get_channel_info(self, provider):
        result = provider.get_channel_info("UC123456")
        assert result["id"] == "UC123456"
        assert "subscribers" in result

    def test_get_playlists(self, provider):
        result = provider.get_playlists("UC123456")
        assert result["channel_id"] == "UC123456"
        assert len(result["playlists"]) == 2

    def test_get_comments_respects_limit(self, provider):
        result = provider.get_comments("yt123abc", limit=1)
        assert len(result["comments"]) == 1
