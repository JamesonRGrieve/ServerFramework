from unittest.mock import MagicMock, patch

import pytest
import requests

from zephyrex.extensions.wiki.EXT_Wiki import EXT_Wiki
from zephyrex.extensions.wiki.PRV_Kanka import KankaProvider


class TestWikiExtension:
    """
    Test suite for the Wiki extension.

    Tests extension metadata/configuration, wiki provider creation and
    integration (Wikipedia, Fandom), content abilities (search, article
    retrieval, summarization), capability management, and extension
    lifecycle/config validation.
    """

    @pytest.fixture
    def extension(self):
        """Create an EXT_Wiki instance for testing."""
        return EXT_Wiki()

    @pytest.fixture
    def mock_wiki_provider(self):
        """Mock wiki provider."""
        mock_provider = MagicMock()
        mock_provider.search.return_value = [
            {
                "title": "Python (programming language)",
                "snippet": "Python is a high-level programming language",
                "url": "https://en.wikipedia.org/wiki/Python_(programming_language)",
            }
        ]
        mock_provider.get_article.return_value = {
            "title": "Python (programming language)",
            "content": "Python is a high-level, general-purpose programming language...",
            "url": "https://en.wikipedia.org/wiki/Python_(programming_language)",
            "pageid": "23862",
        }
        mock_provider.get_summary.return_value = (
            "Python is a programming language that lets you work quickly."
        )
        mock_provider.commands = {
            "Search Wikipedia": mock_provider.search,
            "Get Wikipedia Article": mock_provider.get_article,
        }
        return mock_provider

    def test_extension_metadata(self, extension):
        """Test extension metadata is correctly set."""
        assert extension.name == "wiki"
        assert extension.version == "1.0.0"
        assert "Wiki content access extension" in extension.description
        assert hasattr(extension, "ext_dependencies")
        assert hasattr(extension, "pip_dependencies")
        assert hasattr(extension, "sys_dependencies")

    def test_dependencies(self, extension):
        """Test that dependencies are properly structured."""
        ext_deps = extension.ext_dependencies
        assert len(ext_deps) == 1

        dep_names = {dep.name for dep in ext_deps}
        assert "labels" in dep_names
        for dep in ext_deps:
            if dep.name == "labels":
                assert dep.optional is True

        pip_deps = extension.pip_dependencies
        assert len(pip_deps) == 2

        pip_dep_names = {dep.name for dep in pip_deps}
        assert "requests" in pip_dep_names
        assert "wikipedia" in pip_dep_names

        for dep in pip_deps:
            if dep.name == "wikipedia":
                assert dep.optional is True
            elif dep.name == "requests":
                assert dep.optional is False

        assert isinstance(extension.sys_dependencies, list)
        assert len(extension.sys_dependencies) == 0

    def test_capabilities(self, extension):
        """Test extension capabilities are properly defined."""
        expected_capabilities = {
            "wiki_search",
            "page_retrieval",
            "content_parsing",
            "multi_wiki",
        }
        assert set(extension.capabilities) == expected_capabilities

    def test_initialization(self, extension):
        """Test extension attributes are properly initialized."""
        assert hasattr(extension, "wiki_platform")
        assert hasattr(extension, "api_key")
        assert hasattr(extension, "language")
        assert hasattr(extension, "wiki_domain")
        assert hasattr(extension, "provider")
        assert hasattr(extension, "commands")
        assert extension.provider is None

    def test_default_configuration(self, extension):
        """Test default configuration values."""
        assert extension.wiki_platform == "wikipedia"
        assert extension.api_key == ""
        assert extension.language == "en"
        assert extension.wiki_domain == ""

    def test_db_tables(self, extension):
        """Test database tables list."""
        assert isinstance(extension.db_tables, list)
        assert len(extension.db_tables) == 0

    @patch("zephyrex.extensions.wiki.EXT_Wiki.logger")
    def test_on_initialize_success(self, mock_logger, extension):
        """Test successful extension initialization."""
        with patch.object(extension, "_create_provider"), patch.object(
            extension, "_register_commands"
        ):
            result = extension.on_initialize()
            assert result is True
            mock_logger.debug.assert_called()

    @patch("zephyrex.extensions.wiki.EXT_Wiki.logger")
    def test_on_initialize_failure(self, mock_logger, extension):
        """Test extension initialization failure handling."""
        with patch.object(
            extension, "_create_provider", side_effect=Exception("Test error")
        ):
            result = extension.on_initialize()
            assert result is False
            mock_logger.error.assert_called()

    def test_create_provider_wikipedia_success(self, extension, mock_wiki_provider):
        """Test successful Wikipedia provider creation."""
        extension.wiki_platform = "wikipedia"

        with patch(
            "zephyrex.extensions.wiki.PRV_Wikipedia.WikipediaProvider",
            return_value=mock_wiki_provider,
        ):
            extension._create_provider()

            assert extension.provider is not None
            assert extension.provider == mock_wiki_provider

    def test_create_provider_fandom_success(self, extension, mock_wiki_provider):
        """Test successful Fandom provider creation."""
        extension.wiki_platform = "fandom"
        extension.wiki_domain = "example"

        with patch(
            "zephyrex.extensions.wiki.PRV_Fandom.FandomProvider",
            return_value=mock_wiki_provider,
        ):
            extension._create_provider()

            assert extension.provider is not None
            assert extension.provider == mock_wiki_provider

    def test_create_provider_kanka_success(self, extension, mock_wiki_provider):
        """Test successful Kanka provider creation."""
        extension.wiki_platform = "kanka"
        extension.api_key = "test-kanka-token"

        with patch(
            "zephyrex.extensions.wiki.PRV_Kanka.KankaProvider",
            return_value=mock_wiki_provider,
        ):
            extension._create_provider()

            assert extension.provider is not None
            assert extension.provider == mock_wiki_provider

    def test_create_provider_unsupported_platform(self, extension):
        """Test provider creation with an unsupported platform."""
        extension.wiki_platform = "unsupported"
        extension._create_provider()

        assert extension.provider is None

    def test_create_provider_import_failure(self, extension):
        """Test provider creation when the Wikipedia provider import/construction fails."""
        with patch(
            "zephyrex.extensions.wiki.PRV_Wikipedia.WikipediaProvider",
            side_effect=ImportError("Mock import error"),
        ):
            extension._create_provider()

            assert extension.provider is None

    def test_wikipedia_provider_creation_parameters(
        self, extension, mock_wiki_provider
    ):
        """Test that the Wikipedia provider is created with correct parameters."""
        extension.wiki_platform = "wikipedia"
        extension.api_key = "test-api-key"
        extension.language = "es"
        extension.conversation_id = "test-conversation-id"

        with patch(
            "zephyrex.extensions.wiki.PRV_Wikipedia.WikipediaProvider",
            return_value=mock_wiki_provider,
        ) as mock_wikipedia_class:
            extension._create_provider()

            mock_wikipedia_class.assert_called_once_with(
                api_key="test-api-key",
                language="es",
                wiki_domain="",
                extension_id="wiki",
                conversation_directory="test-conversation-id",
            )

    def test_register_commands_with_provider(self, extension, mock_wiki_provider):
        """Test command registration with an available provider."""
        extension.provider = mock_wiki_provider
        extension._register_commands()

        assert extension.commands == mock_wiki_provider.commands

    def test_register_commands_without_provider(self, extension):
        """Test command registration without a provider."""
        extension.wiki_platform = "wikipedia"
        extension.provider = None
        extension._register_commands()

        assert len(extension.commands) > 0
        for command_name in extension.commands:
            assert "WIKIPEDIA" in command_name

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
        first = EXT_Wiki()
        second = EXT_Wiki()

        first.register_capability("instance_only_capability")

        assert "instance_only_capability" in first.capabilities
        assert "instance_only_capability" not in second.capabilities

    @pytest.mark.asyncio
    async def test_search_wiki_with_provider(self, extension, mock_wiki_provider):
        """Test searching the wiki with a provider."""
        extension.provider = mock_wiki_provider

        result = await extension.search_wiki("Python programming")

        assert isinstance(result, list)
        assert result[0]["title"] == "Python (programming language)"
        mock_wiki_provider.search.assert_called_once_with("Python programming", 5)

    @pytest.mark.asyncio
    async def test_search_wiki_without_provider(self, extension):
        """Test searching the wiki without a provider."""
        extension.provider = None

        result = await extension.search_wiki("Python programming")

        assert "No wiki provider available" in result

    @pytest.mark.asyncio
    async def test_search_wiki_error(self, extension, mock_wiki_provider):
        """Test searching the wiki when the provider raises."""
        mock_wiki_provider.search.side_effect = Exception("Provider error")
        extension.provider = mock_wiki_provider

        result = await extension.search_wiki("Python programming")

        assert "Error searching wiki" in result

    @pytest.mark.asyncio
    async def test_get_wiki_article_success(self, extension, mock_wiki_provider):
        """Test successful article retrieval."""
        extension.provider = mock_wiki_provider

        result = await extension.get_wiki_article("Python (programming language)")

        assert result["title"] == "Python (programming language)"
        mock_wiki_provider.get_article.assert_called_once_with(
            "Python (programming language)"
        )

    @pytest.mark.asyncio
    async def test_get_wiki_article_without_provider(self, extension):
        """Test article retrieval without a provider."""
        extension.provider = None

        result = await extension.get_wiki_article("Test Page")

        assert "No wiki provider available" in result

    @pytest.mark.asyncio
    async def test_get_wiki_article_error(self, extension, mock_wiki_provider):
        """Test article retrieval when the provider raises."""
        mock_wiki_provider.get_article.side_effect = Exception("Provider error")
        extension.provider = mock_wiki_provider

        result = await extension.get_wiki_article("Test Page")

        assert "Error getting wiki article" in result

    @pytest.mark.asyncio
    async def test_get_wiki_summary_success(self, extension, mock_wiki_provider):
        """Test successful summary retrieval."""
        extension.provider = mock_wiki_provider

        result = await extension.get_wiki_summary("Python (programming language)")

        assert "Python is a programming language" in result
        mock_wiki_provider.get_summary.assert_called_once_with(
            "Python (programming language)"
        )

    @pytest.mark.asyncio
    async def test_get_wiki_summary_without_provider(self, extension):
        """Test summary retrieval without a provider."""
        extension.provider = None

        result = await extension.get_wiki_summary("Test Page")

        assert "No wiki provider available" in result

    @pytest.mark.asyncio
    async def test_get_wiki_summary_error(self, extension, mock_wiki_provider):
        """Test summary retrieval when the provider raises."""
        mock_wiki_provider.get_summary.side_effect = Exception("Provider error")
        extension.provider = mock_wiki_provider

        result = await extension.get_wiki_summary("Test Page")

        assert "Error getting wiki summary" in result

    @pytest.mark.asyncio
    async def test_wiki_operations_without_provider(self, extension):
        """Test wiki operations without a provider."""
        extension.provider = None

        operations = [
            extension.search_wiki("query"),
            extension.get_wiki_article("Test Page"),
            extension.get_wiki_summary("Test Page"),
        ]

        for operation in operations:
            result = await operation
            assert "No wiki provider available" in result

    @pytest.mark.asyncio
    async def test_no_provider_warning(self, extension):
        """Test the no-provider warning message."""
        extension.wiki_platform = "test"

        result = await extension._no_provider_warning()

        assert "No wiki provider available for test" in result

    def test_lifecycle_methods(self, extension):
        """Test extension lifecycle methods."""
        assert extension.on_start() is True

        extension.provider = MagicMock()
        assert extension.on_stop() is True
        assert extension.provider is None

        extension.on_startup()
        extension.on_shutdown()

    def test_provider_cleanup_on_stop(self, extension, mock_wiki_provider):
        """Test that the provider is cleaned up when the extension stops."""
        extension.provider = mock_wiki_provider

        result = extension.on_stop()

        assert result is True
        assert extension.provider is None

    @patch("zephyrex.extensions.wiki.EXT_Wiki.logger")
    def test_extension_startup_shutdown_hooks(self, mock_logger, extension):
        """Test startup and shutdown hooks log the expected messages."""
        extension.on_startup()
        mock_logger.debug.assert_called_with("Wiki extension startup hook called")

        extension.on_shutdown()
        mock_logger.debug.assert_called_with("Wiki extension shutdown hook called")

    def test_validate_config_all_available(self):
        """Test configuration validation when all dependencies are available."""
        with patch("builtins.__import__") as mock_import:
            mock_import.return_value = MagicMock()

            extension = EXT_Wiki(wiki_platform="wikipedia")
            issues = extension.validate_config()

            assert len(issues) == 0

    def test_validate_config_missing_requests(self):
        """Test configuration validation when requests is missing."""

        def mock_import(name, *args, **kwargs):
            if name == "requests":
                raise ImportError("No module named 'requests'")
            return MagicMock()

        with patch("builtins.__import__", side_effect=mock_import):
            extension = EXT_Wiki()
            issues = extension.validate_config()

            assert len(issues) >= 1
            issue_text = " ".join(issues).lower()
            assert "requests" in issue_text

    def test_validate_config_unsupported_platform(self):
        """Test configuration validation with an unsupported platform."""
        with patch("builtins.__import__") as mock_import:
            mock_import.return_value = MagicMock()

            extension = EXT_Wiki(wiki_platform="unsupported")
            issues = extension.validate_config()

            assert len(issues) >= 1
            issue_text = " ".join(issues).lower()
            assert "unsupported" in issue_text

    def test_validate_config_missing_fandom_domain(self):
        """Test configuration validation with a missing Fandom wiki domain."""
        with patch("builtins.__import__") as mock_import:
            mock_import.return_value = MagicMock()

            extension = EXT_Wiki(wiki_platform="fandom")
            issues = extension.validate_config()

            assert len(issues) >= 1
            issue_text = " ".join(issues).lower()
            assert "fandom" in issue_text
            assert "wiki domain" in issue_text

    def test_validate_config_kanka_supported(self):
        """Test configuration validation accepts kanka as a supported platform."""
        with patch("builtins.__import__") as mock_import:
            mock_import.return_value = MagicMock()

            extension = EXT_Wiki(wiki_platform="kanka")
            issues = extension.validate_config()

            assert len(issues) == 0

    def test_get_required_permissions(self, extension):
        """Test getting required permissions."""
        permissions = extension.get_required_permissions()

        assert isinstance(permissions, list)
        assert len(permissions) == 4
        assert "wiki:search" in permissions
        assert "wiki:read" in permissions
        assert "wiki:parse" in permissions
        assert "internet:access" in permissions

    def test_has_capability(self, extension):
        """Test capability checking."""
        for capability in extension.capabilities:
            assert extension.has_capability(capability) is True

        assert extension.has_capability("non_existent_capability") is False

    def test_platform_specific_initialization(self):
        """Test initialization with the supported platforms."""
        for platform in ["wikipedia", "fandom", "kanka"]:
            extension = EXT_Wiki(wiki_platform=platform)
            assert extension.wiki_platform == platform

    def test_platform_is_lowercased(self):
        """Test that the wiki platform is normalized to lower case."""
        extension = EXT_Wiki(wiki_platform="WIKIPEDIA")
        assert extension.wiki_platform == "wikipedia"

    def test_full_initialization_flow(self, extension):
        """Test the complete initialization flow."""
        with patch.object(extension, "_create_provider"), patch.object(
            extension, "_register_commands"
        ):
            result = extension.on_initialize()

            assert result is True

    def test_custom_configuration(self):
        """Test extension with custom configuration via constructor."""
        extension = EXT_Wiki(
            wiki_platform="fandom",
            language="fr",
            wiki_domain="starwars",
        )

        assert extension.wiki_platform == "fandom"
        assert extension.language == "fr"
        assert extension.wiki_domain == "starwars"


class TestKankaProvider:
    """
    Test suite for the Kanka provider.

    Exercises KankaProvider directly (bypassing EXT_Wiki) since the Kanka
    API talks in typed "entities" rather than MediaWiki pages: search hits
    the campaign search endpoint, and article/summary retrieval resolves
    an entity (by numeric id or by name) and renders its rich-text entry.
    All HTTP calls are mocked at the ``requests`` boundary; no live API.
    """

    @pytest.fixture
    def provider(self):
        """Create a KankaProvider instance for testing."""
        return KankaProvider(api_key="test-kanka-token", campaign_id="123")

    @staticmethod
    def _mock_response(json_data, status_code=200):
        response = MagicMock()
        response.status_code = status_code
        response.json.return_value = json_data
        return response

    def test_platform_name(self, provider):
        """Test the platform name reported by the provider."""
        assert provider.get_platform_name() == "Kanka"

    def test_provider_configuration(self, provider):
        """Test that the API token and campaign id are stored as given."""
        assert provider.api_key == "test-kanka-token"
        assert provider.campaign_id == "123"

    def test_config_falls_back_to_environment(self, monkeypatch):
        """Test that a missing token/campaign id falls back to env vars."""
        monkeypatch.setenv("KANKA_API_TOKEN", "env-token")
        monkeypatch.setenv("KANKA_CAMPAIGN_ID", "456")

        provider = KankaProvider()

        assert provider.api_key == "env-token"
        assert provider.campaign_id == "456"

    def test_missing_config_logs_warnings(self, monkeypatch, caplog):
        """Test that missing token/campaign id are logged as warnings."""
        monkeypatch.delenv("KANKA_API_TOKEN", raising=False)
        monkeypatch.delenv("KANKA_CAMPAIGN_ID", raising=False)

        with caplog.at_level("WARNING"):
            provider = KankaProvider()

        assert provider.api_key == ""
        assert provider.campaign_id == ""
        assert "campaign id" in caplog.text.lower()
        assert "api token" in caplog.text.lower()

    @patch("zephyrex.extensions.wiki.PRV_Kanka.requests.get")
    def test_search_success(self, mock_get, provider):
        """Test searching Kanka entities."""
        mock_get.return_value = self._mock_response(
            {
                "data": [
                    {
                        "id": 42,
                        "entity_id": 999,
                        "name": "Solenne",
                        "type": "location",
                        "url": "https://kanka.io/en/campaign/123/entities/999",
                    }
                ]
            }
        )

        results = provider.search("Solenne")

        assert results == [
            {
                "title": "Solenne",
                "snippet": "location",
                "url": "https://kanka.io/en/campaign/123/entities/999",
            }
        ]
        called_url = mock_get.call_args[0][0]
        assert called_url == "https://api.kanka.io/1.0/campaigns/123/search/Solenne"
        assert (
            mock_get.call_args.kwargs["headers"]["Authorization"]
            == "Bearer test-kanka-token"
        )

    @patch("zephyrex.extensions.wiki.PRV_Kanka.requests.get")
    def test_search_respects_limit(self, mock_get, provider):
        """Test that search truncates results to the requested limit."""
        mock_get.return_value = self._mock_response(
            {
                "data": [
                    {"id": 1, "entity_id": 1, "name": "One", "type": "note"},
                    {"id": 2, "entity_id": 2, "name": "Two", "type": "note"},
                    {"id": 3, "entity_id": 3, "name": "Three", "type": "note"},
                ]
            }
        )

        results = provider.search("query", limit=2)

        assert len(results) == 2

    @patch("zephyrex.extensions.wiki.PRV_Kanka.requests.get")
    def test_search_raises_on_http_error(self, mock_get, provider):
        """Test that a Kanka API error propagates from search."""
        error_response = MagicMock()
        error_response.raise_for_status.side_effect = requests.exceptions.HTTPError(
            "500 Server Error"
        )
        mock_get.return_value = error_response

        with pytest.raises(requests.exceptions.HTTPError):
            provider.search("query")

    @patch("zephyrex.extensions.wiki.PRV_Kanka.requests.get")
    def test_get_article_by_name_success(self, mock_get, provider):
        """Test fetching an article by resolving its name via search."""
        search_response = self._mock_response(
            {
                "data": [
                    {
                        "id": 42,
                        "entity_id": 999,
                        "name": "Solenne",
                        "type": "location",
                    }
                ]
            }
        )
        entity_response = self._mock_response(
            {
                "data": {
                    "id": 42,
                    "entity_id": 999,
                    "name": "Solenne",
                    "type": "location",
                    "entry": "<p>Solenne is the free city.</p>\n\n<p>It has many districts.</p>",
                    "url": "https://kanka.io/en/campaign/123/entities/999",
                }
            }
        )
        mock_get.side_effect = [search_response, entity_response]

        article = provider.get_article("Solenne")

        assert article["title"] == "Solenne"
        assert "Solenne is the free city." in article["content"]
        assert "It has many districts." in article["content"]
        assert "<p>" not in article["content"]
        assert article["url"] == "https://kanka.io/en/campaign/123/entities/999"
        assert article["pageid"] == 999
        assert mock_get.call_count == 2

    @patch("zephyrex.extensions.wiki.PRV_Kanka.requests.get")
    def test_get_article_by_id_success(self, mock_get, provider):
        """Test fetching an article directly by numeric entity id."""
        entity_response = self._mock_response(
            {
                "data": {
                    "id": 42,
                    "entity_id": 999,
                    "name": "Solenne",
                    "type": "location",
                    "entry": "<p>Solenne is the free city.</p>",
                    "url": "https://kanka.io/en/campaign/123/entities/999",
                }
            }
        )
        mock_get.return_value = entity_response

        article = provider.get_article("999")

        assert article["title"] == "Solenne"
        assert "Solenne is the free city." in article["content"]
        assert article["pageid"] == 999
        # Resolved directly by id -- no search round-trip needed.
        mock_get.assert_called_once()
        called_url = mock_get.call_args[0][0]
        assert called_url == "https://api.kanka.io/1.0/campaigns/123/entities/999"

    @patch("zephyrex.extensions.wiki.PRV_Kanka.requests.get")
    def test_get_article_not_found(self, mock_get, provider):
        """Test article retrieval when no matching entity exists."""
        mock_get.return_value = self._mock_response({"data": []})

        article = provider.get_article("Nonexistent Entity")

        assert article == {
            "title": "Nonexistent Entity",
            "content": "",
            "url": "",
            "pageid": "",
        }

    @patch("zephyrex.extensions.wiki.PRV_Kanka.requests.get")
    def test_get_summary_success(self, mock_get, provider):
        """Test fetching an entity's entry excerpt as its summary."""
        search_response = self._mock_response(
            {"data": [{"id": 42, "entity_id": 999, "name": "Solenne"}]}
        )
        entity_response = self._mock_response(
            {
                "data": {
                    "id": 42,
                    "entity_id": 999,
                    "name": "Solenne",
                    "entry": "<p>Solenne is the free city.</p>\n\n<p>It has many districts.</p>",
                }
            }
        )
        mock_get.side_effect = [search_response, entity_response]

        summary = provider.get_summary("Solenne")

        assert summary == "Solenne is the free city."

    @patch("zephyrex.extensions.wiki.PRV_Kanka.requests.get")
    def test_get_summary_no_entry(self, mock_get, provider):
        """Test summary retrieval when the resolved entity has no entry text."""
        mock_get.return_value = self._mock_response(
            {"data": {"id": 42, "entity_id": 999, "name": "Solenne", "entry": ""}}
        )

        summary = provider.get_summary("999")

        assert summary == "No summary available"

    @patch("zephyrex.extensions.wiki.PRV_Kanka.requests.get")
    def test_get_summary_not_found(self, mock_get, provider):
        """Test summary retrieval when no matching entity exists."""
        mock_get.return_value = self._mock_response({"data": []})

        summary = provider.get_summary("Nonexistent Entity")

        assert summary == "No summary available"

    @patch("zephyrex.extensions.wiki.PRV_Kanka.requests.get")
    def test_fetch_entity_returns_none_on_404(self, mock_get, provider):
        """Test that a 404 while fetching an entity by id is treated as absent."""
        mock_get.return_value = self._mock_response({}, status_code=404)

        article = provider.get_article("999")

        assert article == {"title": "999", "content": "", "url": "", "pageid": ""}


if __name__ == "__main__":
    pytest.main([__file__])
