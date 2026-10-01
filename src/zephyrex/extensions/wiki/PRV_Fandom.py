# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Fandom wiki, through its MediaWiki Action API (``wiki_domain`` setting,
else ``FANDOM_WIKI_DOMAIN``, else ``community``: ``<domain>.fandom.com``)."""

from typing import Any, ClassVar, Dict, List, Optional

from zephyrex.extensions.ExternalErrors import InvalidInputExternalError
from zephyrex.extensions.wiki.EXT_Wiki import (
    NO_SUMMARY,
    AbstractWikiProvider,
    first_paragraph,
    host_label,
    plain_text,
)
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

DEFAULT_WIKI_DOMAIN = "community"


class PRV_Fandom_Wiki(AbstractWikiProvider):
    name: ClassVar[str] = "fandom"
    friendly_name: ClassVar[str] = "Fandom"
    description: ClassVar[str] = "A Fandom (MediaWiki) wiki"
    _env: ClassVar[Dict[str, Any]] = {"FANDOM_WIKI_DOMAIN": DEFAULT_WIKI_DOMAIN}

    @classmethod
    def site(cls, instance: Optional[ProviderInstanceModel]) -> str:
        domain = cls.resolve_setting(
            instance, "wiki_domain", "FANDOM_WIKI_DOMAIN", default=DEFAULT_WIKI_DOMAIN
        )
        return f"https://{host_label(domain or DEFAULT_WIKI_DOMAIN, 'wiki_domain')}.fandom.com"

    @classmethod
    async def _api(cls, instance: ProviderInstanceModel, **params: Any) -> Any:
        return await cls.get_json(
            f"{cls.site(instance)}/api.php", {"format": "json", **params}
        )

    @classmethod
    async def search(
        cls, instance: ProviderInstanceModel, query: str, limit: int = 5
    ) -> List[Dict[str, Any]]:
        # OpenSearch answers [query, titles, descriptions, urls].
        data = await cls._api(instance, action="opensearch", search=query, limit=limit)
        titles, descriptions, urls = (
            list(data[i]) if len(data) > i else [] for i in (1, 2, 3)
        )
        return [
            {
                "title": title,
                "snippet": descriptions[i] if i < len(descriptions) else "",
                "url": urls[i] if i < len(urls) else "",
            }
            for i, title in enumerate(titles)
        ]

    @classmethod
    async def get_article(
        cls, instance: ProviderInstanceModel, title: str
    ) -> Dict[str, Any]:
        data = await cls._api(
            instance,
            action="parse",
            page=title,
            prop="text|displaytitle",
            formatversion="2",
        )
        if "error" in data:
            if data["error"].get("code") == "missingtitle":
                return {"title": title, "content": "", "url": "", "pageid": ""}
            raise InvalidInputExternalError(
                f"Fandom refused the request: {data['error'].get('info', '')}",
                provider=cls.name,
            )
        parsed = data.get("parse", {})
        return {
            "title": plain_text(parsed.get("displaytitle", title)),
            "content": plain_text(parsed.get("text", "")),
            "url": f"{cls.site(instance)}/wiki/{title.replace(' ', '_')}",
            "pageid": parsed.get("pageid", ""),
        }

    @classmethod
    async def get_summary(cls, instance: ProviderInstanceModel, title: str) -> str:
        data = await cls._api(
            instance,
            action="query",
            titles=title,
            prop="extracts",
            exintro=1,
            explaintext=1,
        )
        pages: Dict[str, Dict[str, Any]] = data.get("query", {}).get("pages", {})
        empty: Dict[str, Any] = {}
        extract = next(iter(pages.values()), empty).get("extract", "")
        if extract:
            return str(extract)
        # Fandom wikis often lack the TextExtracts extension: the summary is
        # then the article's first paragraph.
        article = await cls.get_article(instance, title)
        return first_paragraph(article["content"]) if article["content"] else NO_SUMMARY
