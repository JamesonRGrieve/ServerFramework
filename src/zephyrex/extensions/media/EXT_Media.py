from typing import Any, Dict, List, Optional, Set

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractStaticExtension,
    ability,
)
from zephyrex.lib.Dependencies import EXT_Dependency, PIP_Dependency
from zephyrex.lib.Logging import logger

SUPPORTED_MEDIA_PLATFORMS = ("amazon", "imdb", "netflix", "youtube")


class EXT_Media(AbstractStaticExtension):
    """
    Media extension for AGInfrastructure.

    Provides media streaming and content-platform abilities across multiple
    providers:
    - Netflix (default): streaming search, info, recommendations, watch
      history, and "My List" management
    - Amazon Prime Video: streaming search/info/recommendations plus rental
      and purchase history
    - IMDB: catalog search/info/recommendations plus ratings and reviews
    - YouTube: video search/info/recommendations plus channel, playlist,
      and comment lookups

    Component loading (DB, BLL, EP) is handled automatically by the import
    system based on file naming conventions.
    """

    # Extension metadata
    name = "media"
    version = "1.0.0"
    description = (
        "Media extension providing media streaming and content platform abilities"
    )

    # Define dependencies
    ext_dependencies = [
        EXT_Dependency(
            name="labels",
            friendly_name="Labels Extension",
            optional=True,
            reason="Optional labels for media content categorization",
        ),
    ]

    pip_dependencies = [
        PIP_Dependency(
            name="requests",
            friendly_name="HTTP Requests Library",
            optional=False,
            semver=">=2.28.0",
            reason="HTTP requests for media provider APIs",
        ),
    ]

    sys_dependencies: List[Any] = []

    # Define database tables (none for this extension)
    db_tables: List[Any] = []

    # Define what capabilities this extension provides
    capabilities = [
        "media_search",
        "media_info",
        "media_recommendations",
        "content_discovery",
        "streaming_integration",
    ]

    def __init__(
        self,
        media_platform: str = "netflix",
        api_key: str = "",
        agent_name: str = "",
        conversation_name: str = "",
        conversation_id: str = "",
        user: str = "",
        **kwargs: Any,
    ):
        super().__init__()

        self.media_platform = media_platform.lower()
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
        """Initialize the Media extension with the appropriate provider."""
        logger.debug("Initializing Media Extension...")

        try:
            self._create_provider()
            self._register_commands()

            for capability in self.capabilities:
                self.register_capability(capability)

            logger.debug("Media extension initialized successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to initialize Media extension: {str(e)}")
            return False

    def _create_provider(self) -> None:
        """Create the appropriate media provider based on media_platform."""
        provider_mapping = {
            "amazon": ("zephyrex.extensions.media.PRV_Amazon", "AmazonProvider"),
            "imdb": ("zephyrex.extensions.media.PRV_IMDB", "IMDBProvider"),
            "netflix": ("zephyrex.extensions.media.PRV_Netflix", "NetflixProvider"),
            "youtube": ("zephyrex.extensions.media.PRV_YouTube", "YouTubeProvider"),
        }

        if self.media_platform not in provider_mapping:
            logger.error(f"Unsupported media platform: {self.media_platform}")
            self.provider = None
            return

        module_name, class_name = provider_mapping[self.media_platform]

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
                f"Media provider for {self.media_platform} created successfully"
            )

        except ImportError as e:
            logger.warning(
                f"Could not import media provider for {self.media_platform}: {e}"
            )
            self.provider = None
        except Exception as e:
            logger.error(f"Error creating media provider: {str(e)}")
            self.provider = None

    def _register_commands(self) -> None:
        """Register commands based on available provider."""
        if self.provider and hasattr(self.provider, "commands"):
            self.commands = self.provider.commands
        else:
            platform_name = self.media_platform.upper()
            self.commands = {
                f"Search {platform_name}": self._no_provider_warning,
            }

    async def _no_provider_warning(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        """Return a warning message when no provider is configured."""
        return {
            "error": f"No media provider available for {self.media_platform}. Please check your configuration."
        }

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

    @ability("search_media")
    async def search_media(
        self, query: str, service: Optional[str] = None
    ) -> Dict[str, Any]:
        """Search the configured media platform's catalog."""
        if not self.provider:
            return await self._no_provider_warning()

        try:
            return self.provider.search(query, service)
        except Exception as e:
            logger.error(f"Error searching media: {e}")
            return {"error": f"Failed to search media: {str(e)}"}

    @ability("get_media_info")
    async def get_media_info(self, media_id: str) -> Dict[str, Any]:
        """Get metadata for a single piece of media content."""
        if not self.provider:
            return await self._no_provider_warning()

        try:
            return self.provider.get_info(media_id)
        except Exception as e:
            logger.error(f"Error getting media info: {e}")
            return {"error": f"Failed to get media info: {str(e)}"}

    @ability("get_media_recommendations")
    async def get_media_recommendations(
        self, user_id: str, genre: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get personalized media recommendations for a user."""
        if not self.provider:
            return await self._no_provider_warning()

        try:
            return self.provider.get_recommendations(user_id, genre)
        except Exception as e:
            logger.error(f"Error getting media recommendations: {e}")
            return {"error": f"Failed to get media recommendations: {str(e)}"}

    def on_start(self) -> bool:
        """Start the Media extension."""
        try:
            logger.debug("Media extension started successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to start Media extension: {e}")
            return False

    def on_stop(self) -> bool:
        """Stop the Media extension."""
        try:
            if self.provider:
                # Clean up provider resources if needed
                self.provider = None

            logger.debug("Media extension stopped successfully")
            return True
        except Exception as e:
            logger.error(f"Error stopping Media extension: {e}")
            return False

    def validate_config(self) -> List[str]:
        """Validate the extension configuration."""
        issues = []

        try:
            import requests  # noqa: F401
        except ImportError:
            issues.append(
                "Requests library not installed - media provider APIs will not work"
            )

        if not self.media_platform:
            issues.append("Media platform not specified")
        elif self.media_platform not in SUPPORTED_MEDIA_PLATFORMS:
            issues.append(f"Unsupported media platform: {self.media_platform}")

        return issues

    def get_required_permissions(self) -> List[str]:
        """Return the list of permissions required by this extension."""
        return [
            "media:search",
            "media:info",
            "media:recommend",
            "content:access",
        ]

    def on_startup(self) -> None:
        """Called during application startup."""
        logger.debug("Media extension startup hook called")

    def on_shutdown(self) -> None:
        """Called during application shutdown."""
        logger.debug("Media extension shutdown hook called")

    def has_capability(self, capability: str) -> bool:
        """Check if this extension has a specific capability."""
        return capability in self.capabilities
