from abc import ABC, abstractmethod
from typing import Any, ClassVar, Dict, List, Optional, Tuple


class AbstractWebsearchProvider(ABC):
    """
    Abstract base class for all web-search providers used by the Websearch
    extension (currently Brave Search, Google Custom Search, and Playwright,
    with room for additional search backends).

    Concrete providers are lightweight, directly-instantiated clients that
    hold their own credentials/config (API key, search endpoint, etc.) and
    expose synchronous search/scrape operations. EXT_Websearch's async
    abilities call into these methods directly (no await) so a provider's
    return values can be plain values rather than awaitables.
    """

    DEFAULT_SUMMARY_LENGTH: ClassVar[int] = 500

    def __init__(
        self,
        api_key: str = "",
        agent_name: str = "",
        conversation_name: str = "",
        conversation_id: str = "",
        user: str = "",
        extension_id: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        self.api_key = api_key
        self.agent_name = agent_name
        self.conversation_name = conversation_name
        self.conversation_id = conversation_id
        self.user = user
        self.extension_id = extension_id
        self.settings: Dict[str, Any] = kwargs

        self.websearch_endpoint = self.settings.get(
            "websearch_endpoint", "https://search.brave.com/search"
        )
        self.browsed_links: List[str] = []

    def verify_link(self, link: str = "") -> bool:
        """A link is worth browsing when it's a real, unvisited HTTP(S) URL."""
        return bool(link) and link not in self.browsed_links and link.startswith("http")

    @staticmethod
    def _extract_text_and_links(html: str) -> Tuple[str, List[Tuple[str, str]]]:
        """Shared HTML -> (plain text, [(link text, href), ...]) extraction."""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        text_content = " ".join(soup.get_text().split())
        links = [
            (anchor.get_text(strip=True), str(anchor["href"]))
            for anchor in soup.find_all("a", href=True)
        ]
        return text_content, links

    @classmethod
    def _summarize(cls, text: str, summarize_content: bool) -> str:
        """Truncate `text` to `DEFAULT_SUMMARY_LENGTH` when requested."""
        if summarize_content and len(text) > cls.DEFAULT_SUMMARY_LENGTH:
            return text[: cls.DEFAULT_SUMMARY_LENGTH] + "..."
        return text

    @abstractmethod
    def web_search(self, query: str) -> Tuple[str, List[Any]]:
        """Search the web and return (summary text, [(title, url), ...])."""

    @abstractmethod
    def get_web_content(
        self, url: str, summarize_content: bool = False
    ) -> Tuple[Optional[str], Optional[List[Any]]]:
        """Fetch a URL and return (text content, [(title, url), ...])."""

    @abstractmethod
    def scrape_websites(
        self, user_input: str = "", summarize_content: bool = False
    ) -> str:
        """Scrape every URL found in free-form user input."""

    @abstractmethod
    def websearch_agent(
        self,
        user_input: str = "",
        search_string: str = "",
        websearch_depth: int = 0,
    ) -> str:
        """Search the web, then browse the top results for `user_input`."""

    @staticmethod
    def services() -> List[str]:
        """Return a list of services provided by this provider."""
        return ["websearch", "web_scraping"]

    def get_extension_info(self) -> Dict[str, Any]:
        """Get information about the websearch extension."""
        return {
            "name": "Websearch",
            "description": "Web search provider",
        }
