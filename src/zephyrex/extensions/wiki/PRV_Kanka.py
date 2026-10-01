import logging
import os
import re
from typing import Any, Dict, List, Optional

import requests

try:
    import kanka  # noqa: F401
except ImportError:  # pragma: no cover - optional convenience SDK, unused
    kanka = None

from zephyrex.extensions.wiki.PRV_Wiki import AbstractWikiProvider

KANKA_REQUEST_TIMEOUT_SECONDS = 10
KANKA_API_BASE_URL = "https://api.kanka.io/1.0"


class KankaProvider(AbstractWikiProvider):
    """
    Wiki provider backed by the Kanka (kanka.io) TTRPG worldbuilding API.

    Kanka organizes campaign knowledge as typed "entities" (characters,
    locations, journals, notes, organisations, etc.) rather than
    MediaWiki-style pages. This provider maps wiki semantics onto that
    model: ``search`` queries the campaign's entity search endpoint,
    ``get_article`` resolves an entity by numeric id or by name and
    renders its rich-text entry as plain text, and ``get_summary`` returns
    that entry's leading excerpt.

    Requires a campaign-scoped Bearer API token and a campaign id. Both
    may be supplied directly (``api_key`` / ``campaign_id``) or, like
    sibling providers reading their own config, fall back to environment
    variables (``KANKA_API_TOKEN`` / ``KANKA_CAMPAIGN_ID``) when omitted.
    An optional ``kanka`` convenience SDK is guarded above but not
    required -- this provider always talks to the API directly via
    ``requests``, matching the pattern used by the other wiki providers.
    """

    def __init__(self, campaign_id: str = "", **kwargs: Any) -> None:
        super().__init__(**kwargs)

        self.campaign_id = campaign_id or os.getenv("KANKA_CAMPAIGN_ID", "")
        if not self.api_key:
            self.api_key = os.getenv("KANKA_API_TOKEN", "")

        if not self.campaign_id:
            logging.warning("No campaign id provided for Kanka provider")
        if not self.api_key:
            logging.warning("No API token provided for Kanka provider")

    def get_platform_name(self) -> str:
        return "Kanka"

    def _api_uri(self, path: str) -> str:
        return f"{KANKA_API_BASE_URL}/campaigns/{self.campaign_id}/{path}"

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }

    def _raw_search(self, query: str, limit: int) -> List[Dict[str, Any]]:
        response = requests.get(
            self._api_uri(f"search/{query}"),
            headers=self._headers(),
            timeout=KANKA_REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()
        matches: List[Dict[str, Any]] = data.get("data", [])
        return matches[:limit]

    def search(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        matches = self._raw_search(query, limit)

        results = []
        for item in matches:
            entity_id = item.get("entity_id", item.get("id", ""))
            results.append(
                {
                    "title": item.get("name", ""),
                    "snippet": item.get("type", ""),
                    "url": item.get(
                        "url",
                        f"https://kanka.io/en/campaign/{self.campaign_id}/entities/{entity_id}",
                    ),
                }
            )
        return results

    def _fetch_entity(self, entity_id: Any) -> Optional[Dict[str, Any]]:
        response = requests.get(
            self._api_uri(f"entities/{entity_id}"),
            headers=self._headers(),
            timeout=KANKA_REQUEST_TIMEOUT_SECONDS,
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        entity: Optional[Dict[str, Any]] = response.json().get("data")
        return entity

    def _resolve_entity(self, title: str) -> Optional[Dict[str, Any]]:
        """Resolve a Kanka entity by numeric id, falling back to a name search."""
        if str(title).isdigit():
            entity = self._fetch_entity(title)
            if entity is not None:
                return entity

        matches = self._raw_search(title, limit=1)
        if not matches:
            return None

        entity_id = matches[0].get("entity_id", matches[0].get("id", ""))
        return self._fetch_entity(entity_id)

    def get_article(self, title: str) -> Dict[str, Any]:
        entity = self._resolve_entity(title)
        if entity is None:
            return {"title": title, "content": "", "url": "", "pageid": ""}

        entry = entity.get("entry", "") or ""
        plain_text = re.sub(r"<.*?>", "", entry)
        entity_id = entity.get("entity_id", entity.get("id", ""))

        return {
            "title": entity.get("name", title),
            "content": plain_text,
            "url": entity.get(
                "url",
                f"https://kanka.io/en/campaign/{self.campaign_id}/entities/{entity_id}",
            ),
            "pageid": entity_id,
        }

    def get_summary(self, title: str) -> str:
        entity = self._resolve_entity(title)
        if entity is None:
            return "No summary available"

        entry = entity.get("entry", "") or ""
        if not entry:
            return "No summary available"

        plain_text = re.sub(r"<.*?>", "", entry)
        paragraphs = [p.strip() for p in plain_text.split("\n\n") if p.strip()]
        return paragraphs[0] if paragraphs else "No summary available"
