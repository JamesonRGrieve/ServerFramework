# SPDX-License-Identifier: AGPL-3.0-or-later
"""SearXNG, the open-source metasearch engine, through a server's JSON
API. The server (``base_url``, else ``SEARXNG_URL``) must list ``json``
among its ``search.formats``; a server on a private network must also be
named in ``EGRESS_ALLOWED_HOSTS``. No key is needed."""

from typing import Any, ClassVar, Dict, List, Mapping, Tuple

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.ExternalErrors import TransientExternalError
from zephyrex.extensions.websearch.EXT_Websearch import (
    AbstractWebsearchProvider,
    plain,
)
from zephyrex.logic.BLL_Providers import ProviderInstanceModel


class PRV_SearXNG_Websearch(AbstractWebsearchProvider):
    name: ClassVar[str] = "searxng"
    friendly_name: ClassVar[str] = "SearXNG"
    description: ClassVar[str] = "A SearXNG metasearch server"
    _env: ClassVar[Dict[str, Any]] = {"SEARXNG_URL": ""}
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting(
            "base_url",
            "SearXNG server address (JSON output enabled)",
            env="SEARXNG_URL",
        ),
    )

    @classmethod
    def results(cls, answer: Mapping[str, Any], limit: int) -> List[Dict[str, Any]]:
        return [
            cls.result(
                plain(found.get("title", "")),
                found.get("url", ""),
                plain(found.get("content", "")),
            )
            for found in answer.get("results", [])
            if found.get("url")
        ][:limit]

    @classmethod
    async def search(
        cls, instance: ProviderInstanceModel, query: str, limit: int
    ) -> List[Dict[str, Any]]:
        base = str(cls.setting(instance, "base_url") or "").rstrip("/")
        if not base:
            raise TransientExternalError(
                "SearXNG server address not configured", provider=cls.name
            )
        answer = await cls.get_json(f"{base}/search", {"q": query, "format": "json"})
        return cls.results(answer, limit)
