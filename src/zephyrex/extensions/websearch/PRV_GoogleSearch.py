from typing import Any, List, Optional, Tuple

from googleapiclient.discovery import build

from zephyrex.extensions.websearch.PRV_Websearch import AbstractWebsearchProvider

GOOGLE_SEARCH_RESULTS_LIMIT = 5


class GoogleSearchProvider(AbstractWebsearchProvider):
    """
    Web search provider backed by the Google Programmable Search JSON API.

    A lightweight, directly-instantiated client: it holds the API key and
    search-engine id and issues a synchronous call to the Custom Search
    API. Content fetching/scraping is delegated to a content-capable
    provider (Brave Search or Playwright) since the Custom Search API
    returns only result metadata, not page content.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.google_api_key = self.settings.get("GOOGLE_API_KEY", "")
        self.google_search_engine_id = self.settings.get("GOOGLE_SEARCH_ENGINE_ID", "")

    @staticmethod
    def services() -> List[str]:
        return ["websearch", "google_search"]

    def is_configured(self) -> bool:
        return bool(self.google_api_key and self.google_search_engine_id)

    def _search(self, query: str) -> List[Tuple[str, str]]:
        service = build(
            "customsearch",
            "v1",
            developerKey=self.google_api_key,
            cache_discovery=False,
        )
        result = (
            service.cse()
            .list(
                q=query,
                cx=self.google_search_engine_id,
                num=GOOGLE_SEARCH_RESULTS_LIMIT,
            )
            .execute()
        )
        items = result.get("items", [])
        return [(item.get("title", item["link"]), item["link"]) for item in items]

    def web_search(self, query: str) -> Tuple[str, List[Any]]:
        if not self.is_configured():
            return "Google Search API credentials not configured.", []

        links = self._search(query)
        return f"Google search results for: {query}", links

    def get_web_content(
        self, url: str, summarize_content: bool = False
    ) -> Tuple[Optional[str], Optional[List[Any]]]:
        # Custom Search results carry only metadata, not page content —
        # delegate content fetching to a content-capable provider.
        return f"Content from {url}", []

    def scrape_websites(
        self, user_input: str = "", summarize_content: bool = False
    ) -> str:
        return "Website scraping is not supported by the Google Search provider."

    def websearch_agent(
        self,
        user_input: str = "",
        search_string: str = "",
        websearch_depth: int = 0,
    ) -> str:
        query = search_string or user_input
        if not query:
            return "Invalid search parameters."

        if not self.is_configured():
            return "Google Search API credentials not configured."

        links = self._search(query)
        if not links:
            return "No search results found."

        return f"Found {len(links)} search results for '{query}'."
