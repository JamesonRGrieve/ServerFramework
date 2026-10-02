# SPDX-License-Identifier: AGPL-3.0-or-later
"""Brave Search, through the Brave Search API's web search. The
instance's API key is a subscription token (else
``BRAVE_SEARCH_API_KEY``)."""

from typing import Any, ClassVar, Dict, List, Tuple

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.ExternalErrors import (
    AuthExternalError,
    InvalidInputExternalError,
    TransientExternalError,
)
from zephyrex.extensions.websearch.EXT_Websearch import (
    AbstractWebsearchProvider,
    plain,
)
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

API_URL = "https://api.search.brave.com/res/v1/web/search"
# Brave answers a refused token with 422 and this code, not 401.
_TOKEN_REFUSED = "SUBSCRIPTION_TOKEN_INVALID"


class PRV_Brave_Websearch(AbstractWebsearchProvider):
    name: ClassVar[str] = "brave"
    friendly_name: ClassVar[str] = "Brave Search"
    description: ClassVar[str] = "Brave Search API"
    _env: ClassVar[Dict[str, Any]] = {"BRAVE_SEARCH_API_KEY": ""}
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting(
            "api_key",
            "Brave Search API subscription token",
            env="BRAVE_SEARCH_API_KEY",
            secret=True,
            field="api_key",
        ),
    )

    @classmethod
    async def search(
        cls, instance: ProviderInstanceModel, query: str, limit: int
    ) -> List[Dict[str, Any]]:
        token = cls.setting(instance, "api_key")
        if not token:
            raise TransientExternalError(
                "Brave Search API key not configured", provider=cls.name
            )
        try:
            answer = await cls.get_json(
                API_URL,
                {"q": query, "count": limit},
                headers={
                    "Accept": "application/json",
                    "X-Subscription-Token": str(token),
                },
            )
        except InvalidInputExternalError as exc:
            if _TOKEN_REFUSED in str(exc.upstream_payload or ""):
                raise AuthExternalError(
                    "Brave refused the subscription token",
                    provider=cls.name,
                    upstream_status=exc.upstream_status,
                ) from exc
            raise
        return [
            cls.result(
                plain(found.get("title", "")),
                found.get("url", ""),
                plain(found.get("description", "")),
            )
            for found in answer.get("web", {}).get("results", [])
        ][:limit]
