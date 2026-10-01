import logging
import re
from typing import Any, Dict, List

import requests

from zephyrex.extensions.wiki.PRV_Wiki import AbstractWikiProvider

FANDOM_REQUEST_TIMEOUT_SECONDS = 10
DEFAULT_FANDOM_WIKI_DOMAIN = "community"


class FandomProvider(AbstractWikiProvider):
    """
    Wiki provider backed by a Fandom wiki's MediaWiki Action API.

    A lightweight, directly-instantiated client: it holds the target wiki
    domain and issues synchronous HTTP requests to that Fandom wiki's public
    API. Requires the ``requests`` dependency (already required by the
    extension).
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)

        if not self.wiki_domain:
            logging.warning("No wiki domain provided for Fandom provider")
            self.wiki_domain = DEFAULT_FANDOM_WIKI_DOMAIN

    def get_platform_name(self) -> str:
        return "Fandom"

    def _api_uri(self) -> str:
        return f"https://{self.wiki_domain}.fandom.com/api.php"

    def search(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        params = {
            "action": "opensearch",
            "format": "json",
            "search": query,
            "limit": limit,
        }
        response = requests.get(
            self._api_uri(), params=params, timeout=FANDOM_REQUEST_TIMEOUT_SECONDS
        )
        response.raise_for_status()
        data = response.json()

        # OpenSearch returns [query, titles, descriptions, urls]
        titles = data[1] if len(data) > 1 else []
        descriptions = data[2] if len(data) > 2 else []
        urls = data[3] if len(data) > 3 else []

        results = []
        for i, title in enumerate(titles):
            results.append(
                {
                    "title": title,
                    "snippet": descriptions[i] if i < len(descriptions) else "",
                    "url": urls[i] if i < len(urls) else "",
                }
            )
        return results

    def get_article(self, title: str) -> Dict[str, Any]:
        params = {
            "action": "parse",
            "format": "json",
            "page": title,
            "prop": "text|displaytitle",
            "formatversion": "2",
        }
        response = requests.get(
            self._api_uri(), params=params, timeout=FANDOM_REQUEST_TIMEOUT_SECONDS
        )
        response.raise_for_status()
        data = response.json()

        if "error" in data:
            logging.error(f"Fandom API error: {data['error'].get('info', '')}")
            return {"title": title, "content": "", "url": "", "pageid": ""}

        parse_data = data.get("parse", {})
        html_content = parse_data.get("text", "")
        plain_text = re.sub(r"<.*?>", "", html_content)

        return {
            "title": parse_data.get("displaytitle", title),
            "content": plain_text,
            "url": f"https://{self.wiki_domain}.fandom.com/wiki/{title.replace(' ', '_')}",
            "pageid": parse_data.get("pageid", ""),
        }

    def get_summary(self, title: str) -> str:
        params = {
            "action": "query",
            "format": "json",
            "titles": title,
            "prop": "extracts",
            "exintro": 1,
            "explaintext": 1,
        }
        response = requests.get(
            self._api_uri(), params=params, timeout=FANDOM_REQUEST_TIMEOUT_SECONDS
        )
        response.raise_for_status()
        data = response.json()

        pages = data.get("query", {}).get("pages", {})
        if not pages:
            return "No summary available"

        page_id = next(iter(pages))
        summary = pages[page_id].get("extract", "")

        # If the API doesn't return an extract, fall back to the first
        # paragraph of the full article.
        if not summary:
            article = self.get_article(title)
            content = article.get("content", "")
            paragraphs = content.split("\n\n")
            summary = paragraphs[0] if paragraphs else "No summary available"

        return summary
