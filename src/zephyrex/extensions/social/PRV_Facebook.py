# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Facebook Page, through Meta's Graph API.

The instance's API key is a Page access token with ``pages_manage_posts``
and ``pages_read_engagement`` (else ``FACEBOOK_PAGE_TOKEN``); its
``page_id`` setting names the Page. A post carries text and at most one
photo or video, which Facebook fetches from its URL.
"""

from typing import Any, ClassVar, Dict, List, Tuple

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.social.EXT_Social import (
    AbstractSocialProvider,
    count,
    media_kind,
)
from zephyrex.lib.MetaGraph import meta_graph
from zephyrex.lib.ProviderHTTPClient import path_segment
from zephyrex.logic.BLL_Providers import ProviderInstanceModel


class PRV_Facebook_Social(AbstractSocialProvider):
    name: ClassVar[str] = "facebook"
    friendly_name: ClassVar[str] = "Facebook"
    description: ClassVar[str] = "A Facebook Page"
    _env: ClassVar[Dict[str, Any]] = {"FACEBOOK_PAGE_TOKEN": "", "FACEBOOK_PAGE_ID": ""}
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting(
            "api_key",
            "Page access token (pages_manage_posts, pages_read_engagement)",
            env="FACEBOOK_PAGE_TOKEN",
            secret=True,
            field="api_key",
        ),
        InstanceSetting("page_id", "Page id", env="FACEBOOK_PAGE_ID"),
    )

    @classmethod
    def _page(cls, instance: ProviderInstanceModel) -> str:
        return path_segment(cls.required(instance, "page_id"), "Facebook page_id")

    @classmethod
    async def _permalink(cls, post_id: str, token: str) -> str:
        found = await meta_graph(
            cls,
            "GET",
            path_segment(post_id, "Facebook post id"),
            token,
            params={"fields": "permalink_url"},
        )
        return str(found.get("permalink_url", ""))

    @classmethod
    async def publish(
        cls, instance: ProviderInstanceModel, content: str, media_urls: List[str]
    ) -> Dict[str, Any]:
        if len(media_urls) > 1:
            raise cls.refuse("a Page post here carries at most one photo or video")
        token, page = cls.token(instance), cls._page(instance)
        if not media_urls:
            found = await meta_graph(
                cls, "POST", f"{page}/feed", token, json_body={"message": content}
            )
            post_id = str(found["id"])
        elif media_kind(media_urls[0]) == "image":
            found = await meta_graph(
                cls,
                "POST",
                f"{page}/photos",
                token,
                json_body={"url": media_urls[0], "caption": content},
            )
            post_id = str(found.get("post_id") or found["id"])
        else:
            found = await meta_graph(
                cls,
                "POST",
                f"{page}/videos",
                token,
                json_body={"file_url": media_urls[0], "description": content},
            )
            post_id = str(found["id"])
        return cls.published(instance, post_id, await cls._permalink(post_id, token))

    @classmethod
    async def feed(
        cls, instance: ProviderInstanceModel, limit: int
    ) -> List[Dict[str, Any]]:
        found = await meta_graph(
            cls,
            "GET",
            f"{cls._page(instance)}/posts",
            cls.token(instance),
            params={"fields": "id,message,created_time,permalink_url", "limit": limit},
        )
        return [
            {
                "id": post["id"],
                "text": post.get("message", ""),
                "url": post.get("permalink_url", ""),
                "created_at": post.get("created_time"),
            }
            for post in found.get("data", [])
        ]

    @classmethod
    async def profile(cls, instance: ProviderInstanceModel) -> Dict[str, Any]:
        found = await meta_graph(
            cls,
            "GET",
            cls._page(instance),
            cls.token(instance),
            params={"fields": "name,username,link,followers_count,fan_count"},
        )
        return {
            "username": found.get("username", ""),
            "name": found.get("name", ""),
            "url": found.get("link", ""),
            "followers": count(found.get("followers_count")),
            "following": None,
            "posts": None,
            "provider": cls.name,
        }
