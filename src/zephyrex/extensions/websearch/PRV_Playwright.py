import re
from typing import Any, List, Optional, Tuple

from zephyrex.extensions.websearch.PRV_Websearch import AbstractWebsearchProvider

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - exercised whenever playwright is absent
    sync_playwright = None  # type: ignore[assignment]

PLAYWRIGHT_NOT_INSTALLED_MESSAGE = (
    "Playwright is not installed. Install the optional 'playwright' dependency "
    "(and run 'playwright install chromium') to enable browser-based web search."
)
URL_PATTERN = re.compile(r"(?P<url>https?://[^\s]+)")


class PlaywrightProvider(AbstractWebsearchProvider):
    """
    Web search provider backed by a real, headless browser via Playwright's
    synchronous API — for search-result and content pages that require
    JavaScript rendering that a plain HTTP GET cannot produce.

    Playwright (plus a Chromium install) is an optional, heavy dependency.
    When it isn't installed, every method returns a clear configuration
    error instead of failing at import time.
    """

    @staticmethod
    def services() -> List[str]:
        return ["websearch", "web_scraping", "browser_automation"]

    def _render(self, url: str) -> str:
        assert sync_playwright is not None  # guarded by callers
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page()
                page.goto(url)
                return page.content()
            finally:
                browser.close()

    def web_search(self, query: str) -> Tuple[str, List[Any]]:
        if sync_playwright is None:
            return PLAYWRIGHT_NOT_INSTALLED_MESSAGE, []

        text_content, links = self.get_web_content(
            f"{self.websearch_endpoint}?q={query}"
        )
        return text_content or "", links or []

    def get_web_content(
        self, url: str, summarize_content: bool = False
    ) -> Tuple[Optional[str], Optional[List[Any]]]:
        if sync_playwright is None:
            return PLAYWRIGHT_NOT_INSTALLED_MESSAGE, None

        html = self._render(url)
        text_content, links = self._extract_text_and_links(html)
        text_content = self._summarize(text_content, summarize_content)

        self.browsed_links.append(url)
        return text_content, links

    def scrape_websites(
        self, user_input: str = "", summarize_content: bool = False
    ) -> str:
        if sync_playwright is None:
            return PLAYWRIGHT_NOT_INSTALLED_MESSAGE

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
        if sync_playwright is None:
            return PLAYWRIGHT_NOT_INSTALLED_MESSAGE

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
