from typing import Any, Dict, List, Set

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension, ability
from zephyrex.lib.Dependencies import EXT_Dependency, PIP_Dependency
from zephyrex.lib.Logging import logger


class EXT_Wiki(AbstractStaticExtension):
    """
    Wiki extension for AGInfrastructure.

    Provides wiki content access abilities such as search, article
    retrieval, and summarization, currently supporting Wikipedia and Fandom
    wikis with plans for additional MediaWiki-based platforms.

    Component loading (DB, BLL, EP) is handled automatically by the import
    system based on file naming conventions.
    """

    # Extension metadata
    name = "wiki"
    version = "1.0.0"
    description = "Wiki content access extension for AGInfrastructure"

    # Define dependencies
    ext_dependencies = [
        EXT_Dependency(
            name="labels",
            friendly_name="Labels Extension",
            optional=True,
            reason="Optional labels for wiki content categorization",
        ),
    ]

    pip_dependencies = [
        PIP_Dependency(
            name="requests",
            friendly_name="HTTP Requests Library",
            optional=False,
            reason="Required for wiki API communications",
            semver=">=2.28.0",
        ),
        PIP_Dependency(
            name="wikipedia",
            friendly_name="Wikipedia API Library",
            optional=True,
            reason="Optional convenience client for Wikipedia article lookups",
            semver=">=1.4.0",
        ),
    ]

    sys_dependencies: List[Any] = []

    # Define database tables (none for this extension)
    db_tables: List[Any] = []

    # Define what capabilities this extension provides
    capabilities = [
        "wiki_search",
        "page_retrieval",
        "content_parsing",
        "multi_wiki",
    ]

    def __init__(
        self,
        wiki_platform: str = "wikipedia",
        api_key: str = "",
        language: str = "en",
        wiki_domain: str = "",
        **kwargs: Any,
    ):
        """
        Initialize the wiki extension.
        """
        super().__init__(**kwargs)

        self.wiki_platform = wiki_platform.lower()
        self.api_key = api_key
        self.language = language
        self.wiki_domain = wiki_domain
        self.provider = None
        self.commands: Dict[str, Any] = {}

        # Give each instance its own copy of the mutable class-level
        # capability list so register_capability() on one instance can never
        # leak into the class default (and therefore into sibling instances).
        self.capabilities = list(type(self).capabilities)

    def on_initialize(self) -> bool:
        """Initialize the wiki extension with the appropriate provider."""
        logger.debug("Initializing Wiki Extension...")

        try:
            self._create_provider()
            self._register_commands()

            # Register capabilities
            for capability in self.capabilities:
                self.register_capability(capability)

            logger.debug("Wiki extension initialized successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to initialize Wiki extension: {str(e)}")
            return False

    def _create_provider(self) -> None:
        """Create the appropriate wiki provider based on wiki_platform."""
        try:
            provider_mapping = {
                "wikipedia": (
                    "zephyrex.extensions.wiki.PRV_Wikipedia",
                    "WikipediaProvider",
                ),
                "fandom": ("zephyrex.extensions.wiki.PRV_Fandom", "FandomProvider"),
                "kanka": ("zephyrex.extensions.wiki.PRV_Kanka", "KankaProvider"),
            }

            if self.wiki_platform not in provider_mapping:
                logger.error(f"Unsupported wiki platform: {self.wiki_platform}")
                self.provider = None
                return

            module_name, class_name = provider_mapping[self.wiki_platform]

            try:
                module = __import__(module_name, fromlist=[class_name])
                provider_class = getattr(module, class_name)

                self.provider = provider_class(
                    api_key=self.api_key,
                    language=self.language,
                    wiki_domain=self.wiki_domain,
                    extension_id=self.name,
                    conversation_directory=getattr(self, "conversation_id", ""),
                )
                logger.debug(
                    f"Wiki provider for {self.wiki_platform} created successfully"
                )

            except ImportError as e:
                logger.warning(
                    f"Could not import wiki provider for {self.wiki_platform}: {e}"
                )
                self.provider = None
            except Exception as e:
                logger.error(f"Error creating wiki provider: {str(e)}")
                self.provider = None

        except Exception as e:
            logger.error(f"Error in provider creation: {str(e)}")
            self.provider = None

    def _register_commands(self) -> None:
        """Register commands based on available provider."""
        if self.provider and hasattr(self.provider, "commands"):
            self.commands = self.provider.commands
        else:
            # Provide placeholder commands that warn about missing provider
            platform_name = self.wiki_platform.upper()
            self.commands = {
                f"Search {platform_name}": self._no_provider_warning,
                f"Get {platform_name} Article": self._no_provider_warning,
            }

    async def _no_provider_warning(self, *args: Any, **kwargs: Any) -> str:
        """Warning message when a provider is not available."""
        return f"No wiki provider available for {self.wiki_platform}. Please check your configuration."

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

    @ability("search_wiki")
    async def search_wiki(self, query: str, limit: int = 5) -> Any:
        """Search the configured wiki for articles matching the query."""
        if not self.provider:
            return await self._no_provider_warning()

        try:
            return self.provider.search(query, limit)
        except Exception as e:
            logger.error(f"Error searching wiki: {e}")
            return f"Error searching wiki: {str(e)}"

    @ability("get_wiki_article")
    async def get_wiki_article(self, title: str) -> Any:
        """Get the full content of a wiki article by title."""
        if not self.provider:
            return await self._no_provider_warning()

        try:
            return self.provider.get_article(title)
        except Exception as e:
            logger.error(f"Error getting wiki article: {e}")
            return f"Error getting wiki article: {str(e)}"

    @ability("get_wiki_summary")
    async def get_wiki_summary(self, title: str) -> str:
        """Get a summary of a wiki article by title."""
        if not self.provider:
            return await self._no_provider_warning()

        try:
            return self.provider.get_summary(title)
        except Exception as e:
            logger.error(f"Error getting wiki summary: {e}")
            return f"Error getting wiki summary: {str(e)}"

    def on_start(self) -> bool:
        """Start the Wiki extension."""
        try:
            logger.debug("Wiki extension started successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to start Wiki extension: {e}")
            return False

    def on_stop(self) -> bool:
        """Stop the Wiki extension."""
        try:
            if self.provider:
                # Clean up provider resources if needed
                self.provider = None

            logger.debug("Wiki extension stopped successfully")
            return True
        except Exception as e:
            logger.error(f"Error stopping Wiki extension: {e}")
            return False

    def validate_config(self) -> List[str]:
        """Validate the extension configuration."""
        issues = []

        # Check for required Python packages
        try:
            import requests  # noqa: F401
        except ImportError:
            issues.append(
                "Requests library not installed - wiki API communications will not work"
            )

        # Platform-specific validation
        if not self.wiki_platform:
            issues.append("Wiki platform not specified")
        elif self.wiki_platform not in ["wikipedia", "fandom", "kanka"]:
            issues.append(f"Unsupported wiki platform: {self.wiki_platform}")

        # Fandom requires a wiki domain to target
        if self.wiki_platform == "fandom" and not self.wiki_domain:
            issues.append("Fandom integration requires a wiki domain")

        return issues

    def get_required_permissions(self) -> List[str]:
        """Return the list of permissions required by this extension."""
        return [
            "wiki:search",
            "wiki:read",
            "wiki:parse",
            "internet:access",
        ]

    def on_startup(self) -> None:
        """Called during application startup."""
        logger.debug("Wiki extension startup hook called")

    def on_shutdown(self) -> None:
        """Called during application shutdown."""
        logger.debug("Wiki extension shutdown hook called")

    def has_capability(self, capability: str) -> bool:
        """Check if this extension has a specific capability."""
        return capability in self.capabilities
