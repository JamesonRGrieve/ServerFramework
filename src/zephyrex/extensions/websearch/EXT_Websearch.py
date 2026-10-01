from typing import Any, Dict, List, Set

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractStaticExtension,
    ability,
)
from zephyrex.lib.Dependencies import EXT_Dependency, PIP_Dependency
from zephyrex.lib.Logging import logger

SUPPORTED_PROVIDER_TYPES = ("brave", "google", "playwright")


class EXT_Websearch(AbstractStaticExtension):
    """
    Web search extension for AGInfrastructure.

    Provides web search, website scraping, and AI-powered web research
    across multiple providers:
    - Brave Search (default): HTTP + BeautifulSoup, no browser required
    - Google Custom Search: JSON API, result metadata only
    - Playwright: headless browser for JavaScript-rendered pages

    Component loading (DB, BLL, EP) is handled automatically by the import
    system based on file naming conventions.
    """

    # Extension metadata
    name = "websearch"
    version = "1.0.0"
    description = "Provides web search capabilities using various search engines"

    # Define dependencies
    ext_dependencies = [
        EXT_Dependency(
            name="memories",
            friendly_name="Memories Extension",
            optional=True,
            reason="Optional memory storage for search results",
        ),
    ]

    pip_dependencies = [
        PIP_Dependency(
            name="beautifulsoup4",
            friendly_name="Beautiful Soup 4",
            optional=False,
            semver=">=4.11.0",
            reason="HTML parsing for web search and scraping",
        ),
        PIP_Dependency(
            name="requests",
            friendly_name="HTTP Requests Library",
            optional=False,
            semver=">=2.28.0",
            reason="HTTP requests for web search and scraping",
        ),
        PIP_Dependency(
            name="google-api-python-client",
            friendly_name="Google API Client",
            optional=True,
            semver=">=2.0.0",
            reason="Google Custom Search integration",
        ),
        PIP_Dependency(
            name="playwright",
            friendly_name="Playwright",
            optional=True,
            semver=">=1.28.0",
            reason="Headless-browser search/scraping of JavaScript-rendered pages",
        ),
    ]

    sys_dependencies: List[Any] = []

    # Define database tables (none for this extension)
    db_tables: List[Any] = []

    # Define what capabilities this extension provides
    capabilities = [
        "web_search",
        "website_scraping",
        "ai_web_research",
        "content_extraction",
        "search_aggregation",
    ]

    def __init__(
        self,
        provider_type: str = "brave",
        api_key: str = "",
        agent_name: str = "",
        conversation_name: str = "",
        conversation_id: str = "",
        user: str = "",
        **kwargs: Any,
    ):
        super().__init__()

        self.provider_type = provider_type.lower()
        self.api_key = api_key
        self.agent_name = agent_name
        self.conversation_name = conversation_name
        self.conversation_id = conversation_id
        self.user = user
        self.settings: Dict[str, Any] = kwargs
        self.provider = None
        self.commands: Dict[str, Any] = {}

        # Give each instance its own copy of the mutable class-level
        # capability list so register_capability() on one instance can never
        # leak into the class default (and therefore into sibling instances).
        self.capabilities = list(type(self).capabilities)

    def on_initialize(self) -> bool:
        """Initialize the Websearch extension with the appropriate provider."""
        logger.debug("Initializing Websearch Extension...")

        try:
            self._create_provider()
            self._register_commands()

            for capability in self.capabilities:
                self.register_capability(capability)

            logger.debug("Websearch extension initialized successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to initialize Websearch extension: {str(e)}")
            return False

    def _create_provider(self) -> None:
        """Create the appropriate websearch provider based on provider_type."""
        provider_mapping = {
            "brave": (
                "zephyrex.extensions.websearch.PRV_BraveSearch",
                "BraveSearchProvider",
            ),
            "google": (
                "zephyrex.extensions.websearch.PRV_GoogleSearch",
                "GoogleSearchProvider",
            ),
            "playwright": (
                "zephyrex.extensions.websearch.PRV_Playwright",
                "PlaywrightProvider",
            ),
        }

        if self.provider_type not in provider_mapping:
            logger.error(f"Unsupported websearch provider: {self.provider_type}")
            self.provider = None
            return

        module_name, class_name = provider_mapping[self.provider_type]

        try:
            module = __import__(module_name, fromlist=[class_name])
            provider_class = getattr(module, class_name)

            self.provider = provider_class(
                api_key=self.api_key,
                agent_name=self.agent_name,
                conversation_name=self.conversation_name,
                conversation_id=self.conversation_id,
                user=self.user,
                extension_id=self.name,
                **self.settings,
            )
            logger.debug(
                f"Websearch provider for {self.provider_type} created successfully"
            )

        except ImportError as e:
            logger.warning(
                f"Could not import websearch provider for {self.provider_type}: {e}"
            )
            self.provider = None
        except Exception as e:
            logger.error(f"Error creating websearch provider: {str(e)}")
            self.provider = None

    def _register_commands(self) -> None:
        """Register commands based on available provider."""
        if self.provider:
            self.commands = {
                "Search The Web": self.search_the_web,
                "Scrape Websites": self.scrape_websites,
                "Agent Web Research": self.agent_websearch,
            }
        else:
            platform_name = self.provider_type.upper()
            self.commands = {
                f"Search The Web ({platform_name})": self._no_provider_warning,
            }

    async def _no_provider_warning(self, *args: Any, **kwargs: Any) -> str:
        """Return a warning message when no provider is configured."""
        return f"No websearch provider available for {self.provider_type}. Please check your configuration."

    def register_capability(self, capability: str) -> None:
        """Register a new capability."""
        if capability not in self.capabilities:
            self.capabilities.append(capability)

    def get_registered_capabilities(self) -> Set[str]:
        """Return currently registered capabilities."""
        return set(self.capabilities)

    def get_capabilities(self) -> Set[str]:
        """Return the capabilities this extension provides."""
        return set(self.capabilities)

    @ability("search_the_web")
    async def search_the_web(self, query: str) -> str:
        """
        Search the web for information.
        """
        if not self.provider:
            return await self._no_provider_warning()

        try:
            text_content, link_list = self.provider.web_search(query)

            if isinstance(link_list, list) and link_list:
                link_md = "\n".join(
                    (
                        f"- [{link[0]}]({link[1]})"
                        if isinstance(link, tuple)
                        else f"- {link}"
                    )
                    for link in link_list[:10]
                )
                return f"{text_content}\n\n### Found Links\n{link_md}"
            return text_content
        except Exception as e:
            return f"Failed to search the web: {str(e)}"

    @ability("scrape_websites")
    async def scrape_websites(
        self, user_input: str = "", summarize_content: bool = False
    ) -> str:
        """
        Scrape content from websites.
        """
        if not self.provider:
            return await self._no_provider_warning()

        try:
            return self.provider.scrape_websites(
                user_input=user_input,
                summarize_content=summarize_content,
            )
        except Exception as e:
            return f"Failed to scrape websites: {str(e)}"

    @ability("agent_websearch")
    async def agent_websearch(
        self,
        user_input: str = "What are the latest breakthroughs in AI?",
        search_string: str = "",
        websearch_depth: int = 0,
    ) -> str:
        """
        Perform AI-powered web research.
        """
        if not self.provider:
            return await self._no_provider_warning()

        try:
            return self.provider.websearch_agent(
                user_input=user_input,
                search_string=search_string,
                websearch_depth=websearch_depth,
            )
        except Exception as e:
            return f"Failed to perform agent websearch: {str(e)}"

    def on_start(self) -> bool:
        """
        Start the Websearch extension.
        """
        try:
            logger.debug("Websearch extension started successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to start Websearch extension: {e}")
            return False

    def on_stop(self) -> bool:
        """
        Stop the Websearch extension.
        """
        try:
            if self.provider:
                # Clean up provider resources if needed
                self.provider = None

            logger.debug("Websearch extension stopped successfully")
            return True
        except Exception as e:
            logger.error(f"Error stopping Websearch extension: {e}")
            return False

    def validate_config(self) -> List[str]:
        """
        Validate the extension configuration.
        """
        issues = []

        try:
            import bs4  # noqa: F401
        except ImportError:
            issues.append("BeautifulSoup4 not installed - HTML parsing will not work")

        try:
            import requests  # noqa: F401
        except ImportError:
            issues.append(
                "Requests library not installed - web search/scraping will not work"
            )

        if not self.provider_type:
            issues.append("Websearch provider type not specified")
        elif self.provider_type not in SUPPORTED_PROVIDER_TYPES:
            issues.append(f"Unsupported websearch provider: {self.provider_type}")

        if self.provider_type == "google" and not self.api_key:
            issues.append("Google Search integration requires an API key")

        return issues

    def get_required_permissions(self) -> List[str]:
        """Return the list of permissions required by this extension."""
        return [
            "web:search",
            "web:scrape",
            "web:browse",
            "internet:access",
        ]

    def on_startup(self) -> None:
        """
        Called during application startup.
        """
        logger.debug("Websearch extension startup hook called")

    def on_shutdown(self) -> None:
        """
        Called during application shutdown.
        """
        logger.debug("Websearch extension shutdown hook called")

    def has_capability(self, capability: str) -> bool:
        """Check if this extension has a specific capability."""
        return capability in self.capabilities
