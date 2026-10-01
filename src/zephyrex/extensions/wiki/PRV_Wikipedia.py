# SPDX-License-Identifier: AGPL-3.0-or-later
"""Wikipedia, through the MediaWiki Action API of the instance's language
edition (``language`` setting, else ``WIKIPEDIA_LANGUAGE``, else ``en``)."""

from typing import Any, ClassVar, Dict, List, Optional, Tuple

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.wiki.EXT_Wiki import (
    NO_SUMMARY,
    AbstractWikiProvider,
    host_label,
)
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

DEFAULT_LANGUAGE = "en"


class PRV_Wikipedia_Wiki(AbstractWikiProvider):
    name: ClassVar[str] = "wikipedia"
    friendly_name: ClassVar[str] = "Wikipedia"
    description: ClassVar[str] = "Wikipedia, any language edition"
    _env: ClassVar[Dict[str, Any]] = {"WIKIPEDIA_LANGUAGE": DEFAULT_LANGUAGE}
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting(
            "language",
            "Wikipedia language edition (en, de, …)",
            env="WIKIPEDIA_LANGUAGE",
            default=DEFAULT_LANGUAGE,
        ),
    )

    @classmethod
    def language(cls, instance: Optional[ProviderInstanceModel]) -> str:
        value = cls.setting(instance, "language")
        return host_label(value or DEFAULT_LANGUAGE, "language")

    @classmethod
    def site(cls, instance: Optional[ProviderInstanceModel]) -> str:
        return f"https://{cls.language(instance)}.wikipedia.org"

    @classmethod
    async def _query(
        cls, instance: ProviderInstanceModel, **params: Any
    ) -> Dict[str, Any]:
        data: Dict[str, Any] = await cls.get_json(
            f"{cls.site(instance)}/w/api.php",
            {"action": "query", "format": "json", **params},
        )
        return data

    @classmethod
    async def search(
        cls, instance: ProviderInstanceModel, query: str, limit: int = 5
    ) -> List[Dict[str, Any]]:
        data = await cls._query(instance, list="search", srsearch=query, srlimit=limit)
        site = cls.site(instance)
        return [
            {
                "title": item.get("title", ""),
                "snippet": item.get("snippet", ""),
                "url": f"{site}/wiki/{item.get('title', '').replace(' ', '_')}",
            }
            for item in data.get("query", {}).get("search", [])
        ]

    @classmethod
    async def get_article(
        cls, instance: ProviderInstanceModel, title: str
    ) -> Dict[str, Any]:
        data = await cls._query(
            instance, prop="extracts|info", titles=title, explaintext=1, inprop="url"
        )
        pages: Dict[str, Dict[str, Any]] = data.get("query", {}).get("pages", {})
        empty: Dict[str, Any] = {}
        page_id, page = next(iter(pages.items()), ("", empty))
        if not page or "missing" in page:
            return {"title": title, "content": "", "url": "", "pageid": ""}
        return {
            "title": page.get("title", title),
            "content": page.get("extract", ""),
            "url": page.get("fullurl", ""),
            "pageid": page_id,
        }

    @classmethod
    async def get_summary(cls, instance: ProviderInstanceModel, title: str) -> str:
        data = await cls._query(
            instance, prop="extracts", titles=title, exintro=1, explaintext=1
        )
        pages: Dict[str, Dict[str, Any]] = data.get("query", {}).get("pages", {})
        empty: Dict[str, Any] = {}
        page = next(iter(pages.values()), empty)
        return str(page.get("extract") or NO_SUMMARY)
