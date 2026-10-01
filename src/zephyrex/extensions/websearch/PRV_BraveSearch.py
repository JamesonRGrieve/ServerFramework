import re
from typing import Any, List, Optional, Tuple

import requests

from zephyrex.extensions.websearch.PRV_Websearch import AbstractWebsearchProvider

REQUEST_TIMEOUT_SECONDS = 15
URL_PATTERN = re.compile(r"(?P<url>https?://[^\s]+)")


class BraveSearchProvider(AbstractWebsearchProvider):
    """
    Web search provider backed by Brave Search's public results page.

    A lightweight, directly-instantiated client: it issues synchronous HTTP
    GET requests and parses the returned HTML with BeautifulSoup. Requires
    only the `requests` and `beautifulsoup4` dependencies already required
    by the extension — no browser engine needed.
    """

    @staticmethod
    def services() -> List[str]:
        return ["websearch", "web_scraping", "brave_search"]

    def web_search(self, query: str) -> Tuple[str, List[Any]]:
        response = requests.get(
            self.websearch_endpoint,
            params={"q": query},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()

        text_content, links = self._extract_text_and_links(response.text)
        valid_links = [
            (title, href) for title, href in links if href.startswith("http")
        ]

        return text_content, valid_links

    def get_web_content(
        self, url: str, summarize_content: bool = False
    ) -> Tuple[Optional[str], Optional[List[Any]]]:
        response = requests.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()

        text_content, links = self._extract_text_and_links(response.text)
        text_content = self._summarize(text_content, summarize_content)

        self.browsed_links.append(url)
        return text_content, links

    def scrape_websites(
        self, user_input: str = "", summarize_content: bool = False
    ) -> str:
        links = [
            link for link in URL_PATTERN.findall(user_input) if self.verify_link(link)
        ]
        if not links:
            return "No URLs found in the input."

        for link in links:
            self.get_web_content(link, summarize_content)

        str_links = "\n".join(links)
        return f"I have read all of the content from the following links into my memory:\n{str_links}"

    def websearch_agent(
        self,
        user_input: str = "",
        search_string: str = "",
        websearch_depth: int = 0,
    ) -> str:
        query = search_string or user_input
        if websearch_depth <= 0 or not query:
            return "Invalid search parameters."

        _, links = self.web_search(query)
        if not links:
            return "No search results found."

        links = links[:websearch_depth]
        for _, url in links:
            if self.verify_link(url):
                self.get_web_content(url)

        return f"Completed web search for '{query}'. Browsed {len(links)} links."
