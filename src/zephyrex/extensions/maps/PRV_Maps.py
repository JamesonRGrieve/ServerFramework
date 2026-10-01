from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Set


class AbstractMapsProvider(ABC):
    """
    Abstract base class for all maps service providers used by the Maps
    extension (OpenStreetMap, Google Maps, Apple Maps).

    Concrete providers are lightweight, directly-instantiated clients that
    hold their own credentials/config (api key, api uri/version, user
    agent) and expose synchronous mapping operations. EXT_Maps's async
    abilities call into these methods directly (no await) so a provider's
    return values can be plain values rather than awaitables.
    """

    def __init__(
        self,
        api_key: str = "",
        api_uri: str = "",
        api_version: str = "latest",
        user_agent: str = "AGInfrastructure/1.0",
        extension_id: Optional[str] = None,
        agent_name: str = "",
        ApiClient: Any = None,
        conversation_name: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        self.api_key = api_key
        self.api_uri = api_uri
        self.api_version = api_version
        self.user_agent = user_agent
        self.extension_id = extension_id
        self.agent_name = agent_name
        self.ApiClient = ApiClient
        self.conversation_name = conversation_name
        self.settings: Dict[str, Any] = kwargs

        self.commands = {
            f"Search Location in {self.get_maps_type()} Maps": self.search_location,
            f"Get Directions from {self.get_maps_type()} Maps": self.get_directions,
            f"Geocode Address with {self.get_maps_type()} Maps": self.geocode,
            f"Reverse Geocode with {self.get_maps_type()} Maps": self.reverse_geocode,
        }

    @abstractmethod
    def get_connection(self) -> Any:
        """
        Get a connection to the maps service.
        """

    @abstractmethod
    def search_location(self, query: str) -> str:
        """
        Search for a location on the map.
        """

    @abstractmethod
    def get_directions(
        self, origin: str, destination: str, mode: str = "driving"
    ) -> str:
        """
        Get directions between two locations.

        Args:
            origin: Origin location (address or latitude,longitude)
            destination: Destination location (address or latitude,longitude)
            mode: Transportation mode (driving, walking, bicycling, transit)

        Returns:
            Directions information in a readable format.
        """

    @abstractmethod
    def geocode(self, address: str) -> str:
        """
        Convert an address to coordinates.

        Args:
            address: The address to geocode

        Returns:
            Geocoding information including coordinates.
        """

    @abstractmethod
    def reverse_geocode(self, lat: float, lng: float) -> str:
        """
        Convert coordinates to an address.

        Args:
            lat: Latitude
            lng: Longitude

        Returns:
            Reverse geocoding information including address.
        """

    @abstractmethod
    def get_maps_type(self) -> str:
        """
        Get the type of maps service this provider interacts with.
        """

    def get_maps_classifications(self) -> Set[str]:
        """
        Get the classifications for this maps type (open_source, commercial, routing, etc.)
        Override this method in specific providers to return more accurate classifications.
        """
        maps_type = self.get_maps_type().lower()
        if "openstreetmap" in maps_type:
            return {"open_source", "routing", "geocoding"}
        elif "google" in maps_type:
            return {"commercial", "routing", "geocoding", "satellite"}
        elif "apple" in maps_type:
            return {"commercial", "geocoding", "satellite"}
        else:
            return set()

    @staticmethod
    def services() -> list:
        """
        Return a list of services provided by this provider.
        """
        return ["maps", "location", "directions", "geocoding"]

    def get_extension_info(self) -> Dict[str, Any]:
        """
        Get information about the extension this provider is associated with.
        """
        return {
            "type": self.get_maps_type(),
            "classification": list(self.get_maps_classifications()),
            "description": f"Maps extension for {self.get_maps_type()}",
        }
