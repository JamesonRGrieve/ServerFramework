# SPDX-License-Identifier: AGPL-3.0-or-later
"""Kanka (kanka.io), a TTRPG worldbuilding service, read as a wiki.

Kanka holds a campaign's knowledge as typed entities (characters, locations,
journals, …) rather than pages: a search is the campaign's entity search, an
article an entity's rich-text entry as plain text, a summary that entry's
first paragraph. The instance's API key is a campaign-scoped Bearer token
(else ``KANKA_API_TOKEN``) and its ``campaign_id`` setting the campaign
(else ``KANKA_CAMPAIGN_ID``).
"""

from typing import Any, ClassVar, Dict, List, Optional
from urllib.parse import quote

from zephyrex.extensions.ExternalErrors import (
    InvalidInputExternalError,
    TransientExternalError,
)
from zephyrex.extensions.wiki.EXT_Wiki import (
    NO_SUMMARY,
    AbstractWikiProvider,
    first_paragraph,
    plain_text,
)
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

KANKA_API = "https://api.kanka.io/1.0"
KANKA_SITE = "https://app.kanka.io/w"
HTTP_NOT_FOUND = 404


class PRV_Kanka_Wiki(AbstractWikiProvider):
    name: ClassVar[str] = "kanka"
    friendly_name: ClassVar[str] = "Kanka"
    description: ClassVar[str] = "A Kanka campaign's entities"
    _env: ClassVar[Dict[str, Any]] = {"KANKA_API_TOKEN": "", "KANKA_CAMPAIGN_ID": ""}

    @classmethod
    def campaign(cls, instance: Optional[ProviderInstanceModel]) -> str:
        campaign = (
            cls.resolve_setting(instance, "campaign_id", "KANKA_CAMPAIGN_ID") or ""
        )
        if not campaign.isdigit():
            raise TransientExternalError(
                "Kanka campaign_id not configured (a numeric id)", provider=cls.name
            )
        return campaign

    @classmethod
    def headers(cls, instance: Optional[ProviderInstanceModel]) -> Dict[str, str]:
        token = cls.resolve_setting(
            instance, "api_key", "KANKA_API_TOKEN", field="api_key"
        )
        if not token:
            raise TransientExternalError(
                "Kanka API token not configured", provider=cls.name
            )
        return {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    @classmethod
    async def _get(cls, instance: ProviderInstanceModel, path: str) -> Dict[str, Any]:
        data: Dict[str, Any] = await cls.get_json(
            f"{KANKA_API}/campaigns/{cls.campaign(instance)}/{path}",
            headers=cls.headers(instance),
        )
        return data

    @classmethod
    def _entity_url(cls, instance: ProviderInstanceModel, entity_id: Any) -> str:
        return f"{KANKA_SITE}/{cls.campaign(instance)}/entities/{entity_id}"

    @classmethod
    async def _search(
        cls, instance: ProviderInstanceModel, query: str, limit: int
    ) -> List[Dict[str, Any]]:
        if not query.strip():
            raise InvalidInputExternalError(
                "A Kanka search needs a term", provider=cls.name
            )
        # The term is a path segment: quoted whole, so "../x" stays a term.
        data = await cls._get(instance, f"search/{quote(query, safe='')}")
        matches: List[Dict[str, Any]] = data.get("data", [])
        return matches[:limit]

    @classmethod
    async def search(
        cls, instance: ProviderInstanceModel, query: str, limit: int = 5
    ) -> List[Dict[str, Any]]:
        return [
            {
                "title": item.get("name", ""),
                "snippet": item.get("type", ""),
                "url": item.get("url")
                or cls._entity_url(instance, item.get("entity_id", item.get("id", ""))),
            }
            for item in await cls._search(instance, query, limit)
        ]

    @classmethod
    async def _entity(
        cls, instance: ProviderInstanceModel, title: str
    ) -> Optional[Dict[str, Any]]:
        """The entity with id ``title``, else the first named ``title``."""
        if title.isdigit():
            try:
                found = await cls._get(instance, f"entities/{title}")
                return found.get("data")
            except InvalidInputExternalError as exc:
                if exc.upstream_status != HTTP_NOT_FOUND:
                    raise
        matches = await cls._search(instance, title, limit=1)
        if not matches:
            return None
        entity_id = matches[0].get("entity_id", matches[0].get("id", ""))
        found = await cls._get(instance, f"entities/{quote(str(entity_id), safe='')}")
        return found.get("data")

    @classmethod
    async def get_article(
        cls, instance: ProviderInstanceModel, title: str
    ) -> Dict[str, Any]:
        entity = await cls._entity(instance, title)
        if entity is None:
            return {"title": title, "content": "", "url": "", "pageid": ""}
        entity_id = entity.get("entity_id", entity.get("id", ""))
        return {
            "title": entity.get("name", title),
            "content": plain_text(entity.get("entry") or ""),
            "url": entity.get("url") or cls._entity_url(instance, entity_id),
            "pageid": entity_id,
        }

    @classmethod
    async def get_summary(cls, instance: ProviderInstanceModel, title: str) -> str:
        entity = await cls._entity(instance, title)
        entry = plain_text(entity.get("entry") or "") if entity else ""
        return first_paragraph(entry) if entry else NO_SUMMARY
