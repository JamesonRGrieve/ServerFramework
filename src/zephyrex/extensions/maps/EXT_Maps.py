from typing import Any, Dict, Set

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractStaticExtension,
    ability,
)
from zephyrex.lib.Dependencies import EXT_Dependency, PIP_Dependency
from zephyrex.lib.Logging import logger


class EXT_Maps(AbstractStaticExtension):
    """
    Maps extension for AGInfrastructure.

    This extension loads the appropriate maps provider based on configuration and provides
    mapping services including geocoding, routing, and location search capabilities.
    """

    # Extension metadata
    name = "maps"
    version = "1.0.0"
    description = (
        "Maps integration extension for connecting to various mapping services"
    )

    # Define dependencies
    ext_dependencies = [
        EXT_Dependency(
            name="core",
            friendly_name="Core Extension",
            optional=False,
            reason="Required for base mapping functionality",
        )
    ]

    pip_dependencies = [
        PIP_Dependency(
            name="requests",
            friendly_name="HTTP Requests Library",
            optional=False,
            semver=">=2.28.0",
            reason="HTTP requests for mapping APIs",
        ),
        PIP_Dependency(
            name="googlemaps",
            friendly_name="Google Maps Python Client",
            optional=True,
            semver=">=4.7.0",
            reason="Google Maps provider support",
        ),
        PIP_Dependency(
            name="polyline",
            friendly_name="Polyline Encoding Library",
            optional=False,
            semver=">=1.4.0",
            reason="Polyline encoding for route data",
        ),
    ]

    sys_dependencies = []

    # Define what capabilities this extension provides
    capabilities = [
        "location_search",
        "geocoding",
        "reverse_geocoding",
        "routing",
        "mapping",
        "directions",
    ]

    # Define database tables
    db_tables = []

    # Maps service classifications
    MAP_TYPES = {
        "open_source": ["openstreetmap"],
        "commercial": ["google", "apple"],
        "routing": ["openstreetmap", "google"],
        "geocoding": ["openstreetmap", "google", "apple"],
        "satellite": ["google", "apple"],
    }

    def __init__(
        self,
        maps_type: str = "openstreetmap",
        api_key: str = "",
        api_uri: str = "",
        api_version: str = "latest",
        user_agent: str = "AGInfrastructure/1.0",
        **kwargs,
    ):
        super().__init__(**kwargs)

        self.maps_type = maps_type.lower()
        self.api_key = api_key
        self.api_uri = api_uri
        self.api_version = api_version
        self.user_agent = user_agent
        self.settings: Dict[str, Any] = {}

        # Provider instance reference
        self.provider = None

    def on_initialize(self) -> bool:
        """
        Initialize the Maps extension.
        """
        logger.debug("Initializing Maps Extension...")

        try:
            # Create provider instance
            self._create_provider()

            # Register capabilities
            self.register_capability("location_search")
            self.register_capability("geocoding")
            self.register_capability("reverse_geocoding")
            self.register_capability("routing")
            self.register_capability("mapping")
            self.register_capability("directions")

            logger.debug("Maps extension initialized successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to initialize Maps extension: {str(e)}")
            return False

    def _create_provider(self):
        """
        Create the appropriate maps provider instance based on maps_type.
        """
        try:
            if self.maps_type == "openstreetmap":
                from zephyrex.extensions.maps.openstreetmap import OpenStreetMapProvider

                self.provider = OpenStreetMapProvider(
                    api_key=self.api_key,
                    api_uri=self.api_uri,
                    user_agent=self.user_agent,
                    extension_id=getattr(self, "conversation_id", ""),
                    agent_name=getattr(self, "agent_name", ""),
                    ApiClient=getattr(self, "ApiClient", None),
                    conversation_name=getattr(self, "conversation_name", ""),
                    **self.settings,
                )
            elif self.maps_type == "google":
                from zephyrex.extensions.maps.googlemaps import GoogleMapsProvider

                self.provider = GoogleMapsProvider(
                    api_key=self.api_key,
                    api_uri=self.api_uri,
                    api_version=self.api_version,
                    extension_id=getattr(self, "conversation_id", ""),
                    agent_name=getattr(self, "agent_name", ""),
                    ApiClient=getattr(self, "ApiClient", None),
                    conversation_name=getattr(self, "conversation_name", ""),
                    **self.settings,
                )
            elif self.maps_type == "apple":
                from zephyrex.extensions.maps.applemaps import AppleMapsProvider

                self.provider = AppleMapsProvider(
                    api_key=self.api_key,
                    api_uri=self.api_uri,
                    api_version=self.api_version,
                    extension_id=getattr(self, "conversation_id", ""),
                    agent_name=getattr(self, "agent_name", ""),
                    ApiClient=getattr(self, "ApiClient", None),
                    conversation_name=getattr(self, "conversation_name", ""),
                    **self.settings,
                )
            else:
                logger.error(f"Unsupported maps type: {self.maps_type}")
                self.provider = None

            if self.provider:
                logger.debug(f"Maps provider for {self.maps_type} created successfully")
            else:
                logger.warning(f"No maps provider available for {self.maps_type}")

        except Exception as e:
            logger.error(f"Error creating maps provider: {str(e)}")
            self.provider = None

    def get_capabilities(self) -> Set[str]:
        """Return the capabilities this extension provides."""
        return set(self.capabilities)

    def register_capability(self, capability: str):
        """Register a new capability."""
        if capability not in self.capabilities:
            self.capabilities.append(capability)

    def get_registered_capabilities(self) -> Set[str]:
        """Return currently registered capabilities."""
        return set(self.capabilities)

    @ability("search_location")
    async def search_location(self, query: str) -> str:
        """
        Search for a location on the map.
        """
        if not self.provider:
            return await self._no_provider_warning()

        try:
            return self.provider.search_location(query)
        except Exception as e:
            return f"Failed to search location: {str(e)}"

    @ability("get_directions")
    async def get_directions(
        self, origin: str, destination: str, mode: str = "driving"
    ) -> str:
        """
        Get directions between two locations.
        """
        if not self.provider:
            return await self._no_provider_warning()

        try:
            return self.provider.get_directions(origin, destination, mode)
        except Exception as e:
            return f"Failed to get directions: {str(e)}"

    @ability("geocode")
    async def geocode(self, address: str) -> str:
        """
        Convert an address to coordinates.
        """
        if not self.provider:
            return await self._no_provider_warning()

        try:
            return self.provider.geocode(address)
        except Exception as e:
            return f"Failed to geocode address: {str(e)}"

    @ability("reverse_geocode")
    async def reverse_geocode(self, lat: float, lng: float) -> str:
        """
        Convert coordinates to an address.
        """
        if not self.provider:
            return await self._no_provider_warning()

        try:
            return self.provider.reverse_geocode(lat, lng)
        except Exception as e:
            return f"Failed to reverse geocode: {str(e)}"

    async def _no_provider_warning(self, *args, **kwargs) -> str:
        """Return a warning message when no provider is configured."""
        return f"Maps provider not configured for {self.maps_type}. Please check your configuration."

    def get_maps_classifications(self, maps_type: str = None) -> Dict[str, list]:
        """
        Get maps type classifications. If maps_type is provided, returns only the
        classifications for that specific maps type.
        """
        if maps_type:
            maps_type = maps_type.lower()
            result = {}
            for classification, types in self.MAP_TYPES.items():
                if maps_type in types:
                    result[classification] = [maps_type]
            return result
        return self.MAP_TYPES

    def get_required_permissions(self) -> list[str]:
        """Return the list of permissions required by this extension."""
        return [
            "maps:search",
            "maps:geocode",
            "maps:directions",
            "location:access",
        ]

    def on_start(self) -> bool:
        """
        Start the Maps extension.
        """
        try:
            logger.debug("Maps extension started successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to start Maps extension: {e}")
            return False

    def on_stop(self) -> bool:
        """
        Stop the Maps extension.
        """
        try:
            if self.provider:
                # Clean up provider resources if needed
                self.provider = None

            logger.debug("Maps extension stopped successfully")
            return True
        except Exception as e:
            logger.error(f"Error stopping Maps extension: {e}")
            return False

    def validate_config(self) -> list[str]:
        """
        Validate the extension configuration.
        """
        issues = []

        if not self.maps_type:
            issues.append("Maps type not specified")

        if self.maps_type == "google" and not self.api_key:
            issues.append("Google Maps API key not provided")

        return issues

    def on_startup(self):
        """
        Called during application startup.
        """
        logger.debug("Maps extension startup hook called")

    def on_shutdown(self):
        """
        Called during application shutdown.
        """
        logger.debug("Maps extension shutdown hook called")

    def has_capability(self, capability: str) -> bool:
        """Check if this extension has a specific capability."""
        return capability in self.capabilities
