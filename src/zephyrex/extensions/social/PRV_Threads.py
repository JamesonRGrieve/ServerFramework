# SPDX-License-Identifier: AGPL-3.0-or-later
"""Threads, through Meta's Threads API.

The instance's API key is a Threads user access token with
``threads_basic`` and ``threads_content_publish`` (else
``THREADS_ACCESS_TOKEN``; ``threads_manage_insights`` adds the follower
count). A post is text, a photo or video, or a carousel of two to ten;
Threads fetches media from their URLs and processes them before the post
is published.
"""

from typing import Any, ClassVar, Dict, List, Optional, Tuple

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.ExternalErrors import InvalidInputExternalError
from zephyrex.extensions.social.EXT_Social import (
    AbstractSocialProvider,
    count,
    media_kind,
)
from zephyrex.lib.MetaGraph import THREADS_GRAPH, meta_graph
from zephyrex.lib.ProviderHTTPClient import path_segment
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

STATUS_FIELD = "status"


class PRV_Threads_Social(AbstractSocialProvider):
    name: ClassVar[str] = "threads"
    friendly_name: ClassVar[str] = "Threads"
    description: ClassVar[str] = "Threads"
    _env: ClassVar[Dict[str, Any]] = {"THREADS_ACCESS_TOKEN": ""}
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting(
            "api_key",
            "Threads user access token (threads_basic, threads_content_publish)",
            env="THREADS_ACCESS_TOKEN",
            secret=True,
            field="api_key",
        ),
    )

    @classmethod
    async def _graph(
        cls, instance: ProviderInstanceModel, method: str, path: str, **kwargs: Any
    ) -> Dict[str, Any]:
        return await meta_graph(
            cls, method, path, cls.token(instance), base=THREADS_GRAPH, **kwargs
        )

    @classmethod
    async def _container(
        cls, instance: ProviderInstanceModel, fields: Dict[str, Any]
    ) -> str:
        found = await cls._graph(instance, "POST", "me/threads", json_body=fields)
        container = path_segment(str(found["id"]), "Threads container id")
        if fields.get("media_type") != "TEXT":
            await cls.await_container(
                container, cls.token(instance), THREADS_GRAPH, STATUS_FIELD
            )
        return container

    @staticmethod
    def _media_fields(url: str) -> Dict[str, Any]:
        if media_kind(url) == "image":
            return {"media_type": "IMAGE", "image_url": url}
        return {"media_type": "VIDEO", "video_url": url}

    @classmethod
    async def publish(
        cls, instance: ProviderInstanceModel, content: str, media_urls: List[str]
    ) -> Dict[str, Any]:
        if not media_urls:
            container = await cls._container(
                instance, {"media_type": "TEXT", "text": content}
            )
        elif len(media_urls) == 1:
            container = await cls._container(
                instance, {**cls._media_fields(media_urls[0]), "text": content}
            )
        else:
            children = [
                await cls._container(
                    instance, {**cls._media_fields(url), "is_carousel_item": True}
                )
                for url in media_urls
            ]
            container = await cls._container(
                instance,
                {
                    "media_type": "CAROUSEL",
                    "children": ",".join(children),
                    "text": content,
                },
            )
        found = await cls._graph(
            instance, "POST", "me/threads_publish", json_body={"creation_id": container}
        )
        post_id = path_segment(str(found["id"]), "Threads post id")
        detail = await cls._graph(
            instance, "GET", post_id, params={"fields": "permalink"}
        )
        return cls.published(instance, post_id, detail.get("permalink"))

    @classmethod
    async def feed(
        cls, instance: ProviderInstanceModel, limit: int
    ) -> List[Dict[str, Any]]:
        found = await cls._graph(
            instance,
            "GET",
            "me/threads",
            params={"fields": "id,text,permalink,timestamp", "limit": limit},
        )
        return [
            {
                "id": post["id"],
                "text": post.get("text", ""),
                "url": post.get("permalink", ""),
                "created_at": post.get("timestamp"),
            }
            for post in found.get("data", [])
        ]

    @classmethod
    async def _followers(cls, instance: ProviderInstanceModel) -> Optional[int]:
        """The follower count, None without threads_manage_insights."""
        try:
            found = await cls._graph(
                instance,
                "GET",
                "me/threads_insights",
                params={"metric": "followers_count"},
            )
        except InvalidInputExternalError:
            return None
        for metric in found.get("data", []):
            if metric.get("name") == "followers_count":
                return count((metric.get("total_value") or {}).get("value"))
        return None

    @classmethod
    async def profile(cls, instance: ProviderInstanceModel) -> Dict[str, Any]:
        found = await cls._graph(
            instance, "GET", "me", params={"fields": "id,username,name"}
        )
        username = found.get("username", "")
        return {
            "username": username,
            "name": found.get("name", ""),
            "url": f"https://www.threads.net/@{username}" if username else "",
            "followers": await cls._followers(instance),
            "following": None,
            "posts": None,
            "provider": cls.name,
        }
