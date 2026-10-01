from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class AbstractMediaProvider(ABC):
    """
    Abstract base class for all media-streaming providers used by the Media
    extension (currently Amazon Prime Video, IMDB, Netflix, and YouTube, with
    room for additional platforms).

    Concrete providers are lightweight, directly-instantiated clients that
    hold their own credentials/config (api key, api uri) and expose
    synchronous media operations. EXT_Media's async abilities call into
    these methods directly (no await) so a provider's return values can be
    plain values rather than awaitables.
    """

    def __init__(
        self,
        api_key: str = "",
        extension_id: Optional[str] = None,
        agent_name: str = "",
        conversation_name: str = "",
        conversation_id: str = "",
        user: str = "",
        **kwargs: Any,
    ) -> None:
        self.api_key = api_key
        self.extension_id = extension_id
        self.agent_name = agent_name
        self.conversation_name = conversation_name
        self.conversation_id = conversation_id
        self.user = user
        self.settings: Dict[str, Any] = kwargs

        platform = self.get_platform_name()
        self.commands: Dict[str, Any] = {
            f"Search {platform}": self.search,
            f"Get {platform} Info": self.get_info,
            f"Get {platform} Recommendations": self.get_recommendations,
        }

    @abstractmethod
    def get_platform_name(self) -> str:
        """Get the name of the media platform this provider interacts with."""

    @abstractmethod
    def search(self, query: str, service: Optional[str] = None) -> Dict[str, Any]:
        """Search this platform's media catalog for `query`."""

    @abstractmethod
    def get_info(self, media_id: str) -> Dict[str, Any]:
        """Get metadata for a single piece of media content."""

    @abstractmethod
    def get_recommendations(
        self, user_id: str, genre: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get personalized media recommendations for a user."""

    @staticmethod
    def services() -> List[str]:
        """Return a list of services provided by this provider."""
        return ["media", "streaming"]

    def get_extension_info(self) -> Dict[str, Any]:
        """Get information about the media extension."""
        return {
            "name": "Media",
            "description": f"Media streaming extension for {self.get_platform_name()}",
        }
