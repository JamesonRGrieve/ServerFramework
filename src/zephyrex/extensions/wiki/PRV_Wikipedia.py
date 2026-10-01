from typing import Any, Dict, List

import requests

from zephyrex.extensions.wiki.PRV_Wiki import AbstractWikiProvider

WIKIPEDIA_REQUEST_TIMEOUT_SECONDS = 10


class WikipediaProvider(AbstractWikiProvider):
    """
    Wiki provider backed by the Wikipedia MediaWiki Action API.

    A lightweight, directly-instantiated client: it holds the target
    language and issues synchronous HTTP requests to Wikipedia's public API.
    Requires the ``requests`` dependency (already required by the
    extension).
    """

    def get_platform_name(self) -> str:
        return "Wikipedia"

    def _api_uri(self) -> str:
        return f"https://{self.language}.wikipedia.org/w/api.php"

    def search(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        params = {
            "action": "query",
            "format": "json",
            "list": "search",
            "srsearch": query,
            "srlimit": limit,
        }
        response = requests.get(
            self._api_uri(), params=params, timeout=WIKIPEDIA_REQUEST_TIMEOUT_SECONDS
        )
        response.raise_for_status()
        data = response.json()

        results = []
        for item in data.get("query", {}).get("search", []):
            title = item.get("title", "")
            results.append(
                {
                    "title": title,
                    "snippet": item.get("snippet", ""),
                    "url": f"https://{self.language}.wikipedia.org/wiki/{title.replace(' ', '_')}",
                }
            )
        return results

    def get_article(self, title: str) -> Dict[str, Any]:
        params = {
            "action": "query",
            "format": "json",
            "prop": "extracts|info",
            "titles": title,
            "explaintext": 1,
            "inprop": "url",
        }
        response = requests.get(
            self._api_uri(), params=params, timeout=WIKIPEDIA_REQUEST_TIMEOUT_SECONDS
        )
        response.raise_for_status()
        data = response.json()

        pages = data.get("query", {}).get("pages", {})
        if not pages:
            return {"title": title, "content": "", "url": "", "pageid": ""}

        page_id = next(iter(pages))
        page = pages[page_id]
        return {
            "title": page.get("title", title),
            "content": page.get("extract", ""),
            "url": page.get("fullurl", ""),
            "pageid": page_id,
        }

    def get_summary(self, title: str) -> str:
        params = {
            "action": "query",
            "format": "json",
            "prop": "extracts",
            "titles": title,
            "exintro": 1,
            "explaintext": 1,
        }
        response = requests.get(
            self._api_uri(), params=params, timeout=WIKIPEDIA_REQUEST_TIMEOUT_SECONDS
        )
        response.raise_for_status()
        data = response.json()

        pages = data.get("query", {}).get("pages", {})
        if not pages:
            return "No summary available"

        page_id = next(iter(pages))
        return pages[page_id].get("extract", "No summary available")
