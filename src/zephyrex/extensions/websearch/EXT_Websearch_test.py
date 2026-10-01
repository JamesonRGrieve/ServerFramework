from unittest.mock import MagicMock, patch

import pytest

from zephyrex.extensions.websearch.EXT_Websearch import EXT_Websearch
from zephyrex.extensions.websearch.PRV_BraveSearch import BraveSearchProvider
from zephyrex.extensions.websearch.PRV_GoogleSearch import GoogleSearchProvider
from zephyrex.extensions.websearch.PRV_Playwright import PlaywrightProvider


class TestWebsearchExtension:
    """
    Test suite for the Websearch extension.

    Tests extension metadata/configuration, provider creation for each of
    the three supported backends (Brave Search, Google Custom Search,
    Playwright), the search/scrape/agent-research abilities, capability
    management, and extension lifecycle/config validation.
    """

    @pytest.fixture
    def extension(self):
        """Create an EXT_Websearch instance for testing."""
        return EXT_Websearch()

    @pytest.fixture
    def mock_provider(self):
        """Mock websearch provider."""
        mock_provider = MagicMock()
        mock_provider.web_search.return_value = (
            "Search summary",
            [("Result Title", "https://example.com/result")],
        )
        mock_provider.scrape_websites.return_value = (
            "I have read all of the content from the following links into my memory:\n"
            "https://example.com"
        )
        mock_provider.websearch_agent.return_value = (
            "Completed web search for 'test'. Browsed 1 links."
        )
        return mock_provider

    def test_extension_metadata(self, extension):
        """Test extension metadata is correctly set."""
        assert extension.name == "websearch"
        assert extension.version == "1.0.0"
        assert "web search" in extension.description.lower()
        assert hasattr(extension, "ext_dependencies")
        assert hasattr(extension, "pip_dependencies")
        assert hasattr(extension, "sys_dependencies")

    def test_dependencies(self, extension):
        """Test that dependencies are properly structured."""
        ext_deps = extension.ext_dependencies
        assert len(ext_deps) == 1
        assert ext_deps[0].name == "memories"
        assert ext_deps[0].optional is True

        pip_deps = extension.pip_dependencies
        pip_dep_names = {dep.name for dep in pip_deps}
        assert "beautifulsoup4" in pip_dep_names
        assert "requests" in pip_dep_names
        assert "google-api-python-client" in pip_dep_names
        assert "playwright" in pip_dep_names

        for dep in pip_deps:
            if dep.name in ("beautifulsoup4", "requests"):
                assert dep.optional is False
            elif dep.name in ("google-api-python-client", "playwright"):
                assert dep.optional is True

        assert isinstance(extension.sys_dependencies, list)
        assert len(extension.sys_dependencies) == 0

    def test_capabilities(self, extension):
        """Test extension capabilities are properly defined."""
        expected_capabilities = {
            "web_search",
            "website_scraping",
            "ai_web_research",
            "content_extraction",
            "search_aggregation",
        }
        assert set(extension.capabilities) == expected_capabilities

    def test_initialization(self, extension):
        """Test extension attributes are properly initialized."""
        assert hasattr(extension, "provider_type")
        assert hasattr(extension, "api_key")
        assert hasattr(extension, "provider")
        assert hasattr(extension, "commands")
        assert extension.provider is None

    def test_default_configuration(self, extension):
        """Test default configuration values."""
        assert extension.provider_type == "brave"
        assert extension.api_key == ""

    def test_provider_type_is_lowercased(self):
        """Test that the provider type is normalized to lower case."""
        extension = EXT_Websearch(provider_type="BRAVE")
        assert extension.provider_type == "brave"

    def test_db_tables(self, extension):
        """Test database tables list."""
        assert isinstance(extension.db_tables, list)
        assert len(extension.db_tables) == 0

    def test_extra_kwargs_become_settings(self):
        """Test that unrecognized constructor kwargs are captured as settings."""
        extension = EXT_Websearch(websearch_endpoint="https://example.com/search")
        assert (
            extension.settings.get("websearch_endpoint") == "https://example.com/search"
        )

    @patch("zephyrex.extensions.websearch.EXT_Websearch.logger")
    def test_on_initialize_success(self, mock_logger, extension):
        """Test successful extension initialization."""
        with patch.object(extension, "_create_provider"), patch.object(
            extension, "_register_commands"
        ):
            result = extension.on_initialize()
            assert result is True
            mock_logger.debug.assert_called()

    @patch("zephyrex.extensions.websearch.EXT_Websearch.logger")
    def test_on_initialize_failure(self, mock_logger, extension):
        """Test extension initialization failure handling."""
        with patch.object(
            extension, "_create_provider", side_effect=Exception("Test error")
        ):
            result = extension.on_initialize()
            assert result is False
            mock_logger.error.assert_called()

    def test_create_provider_brave_success(self, extension, mock_provider):
        """Test successful Brave Search provider creation (the default)."""
        extension.provider_type = "brave"

        with patch(
            "zephyrex.extensions.websearch.PRV_BraveSearch.BraveSearchProvider",
            return_value=mock_provider,
        ):
            extension._create_provider()

            assert extension.provider is not None
            assert extension.provider == mock_provider

    def test_create_provider_google_success(self, extension, mock_provider):
        """Test successful Google Search provider creation."""
        extension.provider_type = "google"
        extension.api_key = "test-api-key"

        with patch(
            "zephyrex.extensions.websearch.PRV_GoogleSearch.GoogleSearchProvider",
            return_value=mock_provider,
        ):
            extension._create_provider()

            assert extension.provider is not None
            assert extension.provider == mock_provider

    def test_create_provider_playwright_success(self, extension, mock_provider):
        """Test successful Playwright provider creation."""
        extension.provider_type = "playwright"

        with patch(
            "zephyrex.extensions.websearch.PRV_Playwright.PlaywrightProvider",
            return_value=mock_provider,
        ):
            extension._create_provider()

            assert extension.provider is not None
            assert extension.provider == mock_provider

    def test_create_provider_unsupported_platform(self, extension):
        """Test provider creation with an unsupported provider type."""
        extension.provider_type = "unsupported"
        extension._create_provider()

        assert extension.provider is None

    def test_create_provider_import_failure(self, extension):
        """Test provider creation when the provider import/construction fails."""
        with patch(
            "zephyrex.extensions.websearch.PRV_BraveSearch.BraveSearchProvider",
            side_effect=ImportError("Mock import error"),
        ):
            extension._create_provider()

            assert extension.provider is None

    def test_provider_creation_parameters(self, extension, mock_provider):
        """Test that the provider is created with the correct parameters."""
        extension.provider_type = "brave"
        extension.api_key = "test-api-key"
        extension.agent_name = "Test Agent"
        extension.conversation_name = "Test Conversation"
        extension.conversation_id = "conv-123"
        extension.user = "test-user"

        with patch(
            "zephyrex.extensions.websearch.PRV_BraveSearch.BraveSearchProvider",
            return_value=mock_provider,
        ) as mock_provider_class:
            extension._create_provider()

            mock_provider_class.assert_called_once_with(
                api_key="test-api-key",
                agent_name="Test Agent",
                conversation_name="Test Conversation",
                conversation_id="conv-123",
                user="test-user",
                extension_id="websearch",
            )

    def test_register_commands_with_provider(self, extension, mock_provider):
        """Test command registration with an available provider."""
        extension.provider = mock_provider
        extension._register_commands()

        assert "Search The Web" in extension.commands
        assert "Scrape Websites" in extension.commands
        assert "Agent Web Research" in extension.commands

    def test_register_commands_without_provider(self, extension):
        """Test command registration without a provider."""
        extension.provider_type = "brave"
        extension.provider = None
        extension._register_commands()

        assert len(extension.commands) > 0
        for command_name in extension.commands:
            assert "BRAVE" in command_name

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
        first = EXT_Websearch()
        second = EXT_Websearch()

        first.register_capability("instance_only_capability")

        assert "instance_only_capability" in first.capabilities
        assert "instance_only_capability" not in second.capabilities

    @pytest.mark.asyncio
    async def test_search_the_web_with_provider(self, extension, mock_provider):
        """Test searching the web with a provider returning links."""
        extension.provider = mock_provider

        result = await extension.search_the_web(query="test query")

        assert "Search summary" in result
        assert "Found Links" in result
        assert "Result Title" in result
        mock_provider.web_search.assert_called_once_with("test query")

    @pytest.mark.asyncio
    async def test_search_the_web_without_links(self, extension, mock_provider):
        """Test searching the web when the provider returns no links."""
        mock_provider.web_search.return_value = ("Just text, no links.", [])
        extension.provider = mock_provider

        result = await extension.search_the_web(query="test query")

        assert result == "Just text, no links."

    @pytest.mark.asyncio
    async def test_search_the_web_without_provider(self, extension):
        """Test searching the web without a provider configured."""
        extension.provider = None

        result = await extension.search_the_web(query="test query")

        assert "No websearch provider available" in result

    @pytest.mark.asyncio
    async def test_search_the_web_provider_error(self, extension, mock_provider):
        """Test searching the web when the provider raises."""
        mock_provider.web_search.side_effect = Exception("Search error")
        extension.provider = mock_provider

        result = await extension.search_the_web(query="test query")

        assert "Failed to search the web" in result
        assert "Search error" in result

    @pytest.mark.asyncio
    async def test_scrape_websites_success(self, extension, mock_provider):
        """Test successful website scraping."""
        extension.provider = mock_provider

        result = await extension.scrape_websites(user_input="https://example.com")

        assert "read all of the content" in result
        mock_provider.scrape_websites.assert_called_once_with(
            user_input="https://example.com", summarize_content=False
        )

    @pytest.mark.asyncio
    async def test_scrape_websites_without_provider(self, extension):
        """Test website scraping without a provider configured."""
        extension.provider = None

        result = await extension.scrape_websites(user_input="https://example.com")

        assert "No websearch provider available" in result

    @pytest.mark.asyncio
    async def test_scrape_websites_provider_error(self, extension, mock_provider):
        """Test website scraping when the provider raises."""
        mock_provider.scrape_websites.side_effect = Exception("Scrape error")
        extension.provider = mock_provider

        result = await extension.scrape_websites(user_input="https://example.com")

        assert "Failed to scrape websites" in result
        assert "Scrape error" in result

    @pytest.mark.asyncio
    async def test_agent_websearch_success(self, extension, mock_provider):
        """Test successful agent-driven web research."""
        extension.provider = mock_provider

        result = await extension.agent_websearch(
            user_input="Research AI breakthroughs",
            search_string="AI breakthroughs",
            websearch_depth=3,
        )

        assert "Completed web search" in result
        mock_provider.websearch_agent.assert_called_once_with(
            user_input="Research AI breakthroughs",
            search_string="AI breakthroughs",
            websearch_depth=3,
        )

    @pytest.mark.asyncio
    async def test_agent_websearch_without_provider(self, extension):
        """Test agent-driven web research without a provider configured."""
        extension.provider = None

        result = await extension.agent_websearch()

        assert "No websearch provider available" in result

    @pytest.mark.asyncio
    async def test_agent_websearch_provider_error(self, extension, mock_provider):
        """Test agent-driven web research when the provider raises."""
        mock_provider.websearch_agent.side_effect = Exception("Agent error")
        extension.provider = mock_provider

        result = await extension.agent_websearch()

        assert "Failed to perform agent websearch" in result
        assert "Agent error" in result

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

    @patch("zephyrex.extensions.websearch.EXT_Websearch.logger")
    def test_extension_startup_shutdown_hooks(self, mock_logger, extension):
        """Test startup and shutdown hooks log the expected messages."""
        extension.on_startup()
        mock_logger.debug.assert_called_with("Websearch extension startup hook called")

        extension.on_shutdown()
        mock_logger.debug.assert_called_with("Websearch extension shutdown hook called")

    def test_validate_config_all_available(self):
        """Test configuration validation when all dependencies are available."""
        extension = EXT_Websearch(provider_type="brave")
        issues = extension.validate_config()

        assert len(issues) == 0

    def test_validate_config_unsupported_provider(self):
        """Test configuration validation with an unsupported provider type."""
        extension = EXT_Websearch(provider_type="unsupported")
        issues = extension.validate_config()

        assert len(issues) >= 1
        issue_text = " ".join(issues).lower()
        assert "unsupported" in issue_text

    def test_validate_config_google_missing_api_key(self):
        """Test configuration validation for Google Search without an API key."""
        extension = EXT_Websearch(provider_type="google")
        issues = extension.validate_config()

        assert len(issues) >= 1
        issue_text = " ".join(issues).lower()
        assert "google" in issue_text
        assert "api key" in issue_text

    def test_get_required_permissions(self, extension):
        """Test getting required permissions."""
        permissions = extension.get_required_permissions()

        assert isinstance(permissions, list)
        assert len(permissions) == 4
        assert "web:search" in permissions
        assert "web:scrape" in permissions
        assert "web:browse" in permissions
        assert "internet:access" in permissions

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
        extension = EXT_Websearch(
            provider_type="google",
            api_key="custom-api-key",
            agent_name="Custom Agent",
        )

        assert extension.provider_type == "google"
        assert extension.api_key == "custom-api-key"
        assert extension.agent_name == "Custom Agent"


class TestBraveSearchProvider:
    """Unit tests for the Brave Search provider (HTTP + BeautifulSoup, no browser)."""

    SEARCH_RESULTS_HTML = (
        "<html><body>"
        '<a href="https://example.com/one">Result One</a>'
        '<a href="https://example.com/two">Result Two</a>'
        '<a href="/relative">Not a real link</a>'
        "</body></html>"
    )

    @pytest.fixture
    def provider(self):
        return BraveSearchProvider()

    def test_verify_link(self, provider):
        assert provider.verify_link("https://example.com") is True
        assert provider.verify_link("") is False
        assert provider.verify_link("/relative/path") is False

        provider.browsed_links.append("https://example.com/already-seen")
        assert provider.verify_link("https://example.com/already-seen") is False

    def test_web_search_parses_results(self, provider):
        mock_response = MagicMock()
        mock_response.text = self.SEARCH_RESULTS_HTML
        mock_response.raise_for_status.return_value = None

        with patch("requests.get", return_value=mock_response) as mock_get:
            text_content, links = provider.web_search("python programming")

        mock_get.assert_called_once()
        assert "Result One" in text_content
        assert ("Result One", "https://example.com/one") in links
        # Relative hrefs are not valid search-result links.
        assert all(href.startswith("http") for _, href in links)

    def test_web_search_raises_on_http_error(self, provider):
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = Exception("HTTP 503")

        with patch("requests.get", return_value=mock_response):
            with pytest.raises(Exception, match="HTTP 503"):
                provider.web_search("python programming")

    def test_get_web_content_marks_link_browsed(self, provider):
        mock_response = MagicMock()
        mock_response.text = "<html><body>Page text</body></html>"
        mock_response.raise_for_status.return_value = None

        with patch("requests.get", return_value=mock_response):
            text_content, _ = provider.get_web_content("https://example.com/page")

        assert "Page text" in text_content
        assert "https://example.com/page" in provider.browsed_links

    def test_get_web_content_summarizes_when_requested(self, provider):
        long_text = "word " * 200
        mock_response = MagicMock()
        mock_response.text = f"<html><body>{long_text}</body></html>"
        mock_response.raise_for_status.return_value = None

        with patch("requests.get", return_value=mock_response):
            text_content, _ = provider.get_web_content(
                "https://example.com/page", summarize_content=True
            )

        assert text_content.endswith("...")
        assert len(text_content) <= provider.DEFAULT_SUMMARY_LENGTH + 3

    def test_scrape_websites_no_urls(self, provider):
        result = provider.scrape_websites(user_input="no urls here")
        assert result == "No URLs found in the input."

    def test_scrape_websites_fetches_each_url(self, provider):
        mock_response = MagicMock()
        mock_response.text = "<html><body>Content</body></html>"
        mock_response.raise_for_status.return_value = None

        with patch("requests.get", return_value=mock_response) as mock_get:
            result = provider.scrape_websites(
                user_input="Check https://example.com/a and https://example.com/b"
            )

        assert mock_get.call_count == 2
        assert "https://example.com/a" in result
        assert "https://example.com/b" in result

    def test_websearch_agent_invalid_parameters(self, provider):
        assert provider.websearch_agent(user_input="", websearch_depth=0) == (
            "Invalid search parameters."
        )
        assert provider.websearch_agent(user_input="hello", websearch_depth=0) == (
            "Invalid search parameters."
        )

    def test_websearch_agent_no_results(self, provider):
        with patch.object(provider, "web_search", return_value=("", [])):
            result = provider.websearch_agent(user_input="hello", websearch_depth=2)

        assert result == "No search results found."

    def test_websearch_agent_browses_top_results(self, provider):
        links = [("A", "https://example.com/a"), ("B", "https://example.com/b")]
        with patch.object(provider, "web_search", return_value=("summary", links)):
            with patch.object(provider, "get_web_content") as mock_get_content:
                result = provider.websearch_agent(
                    user_input="hello", search_string="hello", websearch_depth=1
                )

        mock_get_content.assert_called_once_with("https://example.com/a")
        assert "Completed web search for 'hello'" in result
        assert "Browsed 1 links" in result


class TestGoogleSearchProvider:
    """Unit tests for the Google Custom Search provider."""

    @pytest.fixture
    def provider(self):
        return GoogleSearchProvider(
            GOOGLE_API_KEY="test-key", GOOGLE_SEARCH_ENGINE_ID="test-engine"
        )

    def test_is_configured(self, provider):
        assert provider.is_configured() is True
        assert GoogleSearchProvider().is_configured() is False

    def test_web_search_without_credentials(self):
        provider = GoogleSearchProvider()
        text_content, links = provider.web_search("python")

        assert "credentials not configured" in text_content
        assert links == []

    def test_web_search_with_results(self, provider):
        mock_service = MagicMock()
        mock_service.cse.return_value.list.return_value.execute.return_value = {
            "items": [
                {"title": "Python", "link": "https://python.org"},
                {"title": "Docs", "link": "https://docs.python.org"},
            ]
        }

        with patch(
            "zephyrex.extensions.websearch.PRV_GoogleSearch.build",
            return_value=mock_service,
        ):
            text_content, links = provider.web_search("python")

        assert "python" in text_content.lower()
        assert ("Python", "https://python.org") in links
        assert len(links) == 2

    def test_get_web_content_returns_placeholder(self, provider):
        text_content, links = provider.get_web_content("https://example.com")

        assert "https://example.com" in text_content
        assert links == []

    def test_scrape_websites_not_supported(self, provider):
        result = provider.scrape_websites(user_input="https://example.com")
        assert "not supported" in result

    def test_websearch_agent_invalid_query(self, provider):
        result = provider.websearch_agent(user_input="", search_string="")
        assert result == "Invalid search parameters."

    def test_websearch_agent_without_credentials(self):
        provider = GoogleSearchProvider()
        result = provider.websearch_agent(search_string="python")
        assert "credentials not configured" in result

    def test_websearch_agent_with_results(self, provider):
        mock_service = MagicMock()
        mock_service.cse.return_value.list.return_value.execute.return_value = {
            "items": [{"title": "Python", "link": "https://python.org"}]
        }

        with patch(
            "zephyrex.extensions.websearch.PRV_GoogleSearch.build",
            return_value=mock_service,
        ):
            result = provider.websearch_agent(search_string="python")

        assert "Found 1 search results" in result


class TestPlaywrightProvider:
    """
    Unit tests for the Playwright provider.

    Playwright is an optional, heavy dependency and is NOT installed in this
    environment — `sync_playwright` is therefore genuinely `None` here,
    exercising the real "not installed" guard path without any mocking.
    Rendering logic itself is covered by mocking `sync_playwright` directly,
    so these tests never require an actual browser install.
    """

    @pytest.fixture
    def provider(self):
        return PlaywrightProvider()

    def test_playwright_not_installed_in_this_environment(self):
        from zephyrex.extensions.websearch import PRV_Playwright

        assert PRV_Playwright.sync_playwright is None

    def test_web_search_without_playwright_installed(self, provider):
        text_content, links = provider.web_search("python")

        assert "Playwright is not installed" in text_content
        assert links == []

    def test_get_web_content_without_playwright_installed(self, provider):
        text_content, links = provider.get_web_content("https://example.com")

        assert text_content is not None
        assert "Playwright is not installed" in text_content
        assert links is None

    def test_scrape_websites_without_playwright_installed(self, provider):
        result = provider.scrape_websites(user_input="https://example.com")
        assert "Playwright is not installed" in result

    def test_websearch_agent_without_playwright_installed(self, provider):
        result = provider.websearch_agent(user_input="hello", websearch_depth=2)
        assert "Playwright is not installed" in result

    def test_get_web_content_renders_with_mocked_browser(self, provider):
        mock_page = MagicMock()
        mock_page.content.return_value = "<html><body>Rendered content</body></html>"
        mock_browser = MagicMock()
        mock_browser.new_page.return_value = mock_page
        mock_chromium = MagicMock()
        mock_chromium.launch.return_value = mock_browser
        mock_playwright_context = MagicMock()
        mock_playwright_context.chromium = mock_chromium
        mock_sync_playwright = MagicMock()
        mock_sync_playwright.return_value.__enter__.return_value = (
            mock_playwright_context
        )

        with patch(
            "zephyrex.extensions.websearch.PRV_Playwright.sync_playwright",
            mock_sync_playwright,
        ):
            text_content, _links = provider.get_web_content("https://example.com")

        mock_page.goto.assert_called_once_with("https://example.com")
        mock_browser.close.assert_called_once()
        assert "Rendered content" in text_content
        assert "https://example.com" in provider.browsed_links
