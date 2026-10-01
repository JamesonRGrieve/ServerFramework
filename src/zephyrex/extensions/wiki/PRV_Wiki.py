from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class AbstractWikiProvider(ABC):
    """
    Abstract base class for all wiki content providers used by the Wiki
    extension (currently Wikipedia and Fandom, with room for additional
    MediaWiki-based platforms).

    Concrete providers are lightweight, directly-instantiated clients that
    hold their own credentials/config (api key, language, wiki domain) and
    expose synchronous wiki content operations. EXT_Wiki's async abilities
    call into these methods directly (no await) so a provider's return
    values can be plain dicts/lists/strings rather than awaitables.
    """

    def __init__(
        self,
        api_key: str = "",
        language: str = "en",
        wiki_domain: str = "",
        extension_id: Optional[str] = None,
        conversation_directory: str = "",
        **kwargs: Any,
    ) -> None:
        self.api_key = api_key
        self.language = language
        self.wiki_domain = wiki_domain
        self.extension_id = extension_id
        self.conversation_directory = conversation_directory
        self.settings: Dict[str, Any] = kwargs

        self.commands = {
            f"Search {self.get_platform_name()}": self.search,
            f"Get {self.get_platform_name()} Article": self.get_article,
            f"Get {self.get_platform_name()} Summary": self.get_summary,
        }

    @abstractmethod
    def get_platform_name(self) -> str:
        """Get the name of the wiki platform this provider interacts with."""

    @abstractmethod
    def search(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Search for articles matching the query."""

    @abstractmethod
    def get_article(self, title: str) -> Dict[str, Any]:
        """Get the full content of an article by title."""

    @abstractmethod
    def get_summary(self, title: str) -> str:
        """Get a summary of an article by title."""

    @staticmethod
    def services() -> List[str]:
        """Return a list of services provided by this provider."""
        return ["wiki", "knowledge_base"]

    def get_extension_info(self) -> Dict[str, Any]:
        """Get information about the wiki extension."""
        return {
            "name": "Wiki",
            "description": f"Wiki extension for {self.get_platform_name()} content",
        }
