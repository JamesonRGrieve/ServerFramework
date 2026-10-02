# SPDX-License-Identifier: AGPL-3.0-or-later
"""Postiz, the open-source social media scheduler, through its public API:
one instance posts to one channel (``integration_id``) connected in a
Postiz workspace, on whichever platform that channel is.

The instance's API key is the workspace's Postiz API key (else
``POSTIZ_API_KEY``); ``base_url`` is Postiz Cloud's API or a self-hosted
server's (``https://<host>/api/public/v1``). Media are fetched by Postiz
from their URLs. Platforms whose posts need settings of their own
(a subreddit, a board, a title) are refused: post to them from Postiz.
Postiz publishes in the background, so a post answers with its Postiz id.
"""

from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar, Dict, List, Optional, Tuple

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.ExternalErrors import InvalidInputExternalError
from zephyrex.extensions.social.EXT_Social import AbstractSocialProvider
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

DEFAULT_BASE_URL = "https://api.postiz.com/public/v1"
FEED_DAYS = 90
# Default settings a platform's post needs; every other key is optional.
PLATFORM_SETTINGS: Dict[str, Dict[str, Any]] = {
    "x": {"who_can_reply_post": "everyone"},
    "instagram": {"post_type": "post"},
    "instagram-standalone": {"post_type": "post"},
}
# Platforms whose posts need settings only their poster can choose.
NEEDS_OWN_SETTINGS = {
    "youtube",
    "tiktok",
    "reddit",
    "lemmy",
    "discord",
    "slack",
    "pinterest",
    "dribbble",
    "medium",
    "devto",
    "hashnode",
    "wordpress",
}


def postiz_post_id(answer: Any) -> str:
    """The id of the post a create answered with (a list per channel)."""
    entry = answer[0] if isinstance(answer, list) and answer else answer
    if isinstance(entry, dict):
        return str(entry.get("postId") or entry.get("id") or "")
    return ""


class PRV_Postiz_Social(AbstractSocialProvider):
    name: ClassVar[str] = "postiz"
    friendly_name: ClassVar[str] = "Postiz"
    description: ClassVar[str] = "A channel connected in a Postiz workspace"
    _env: ClassVar[Dict[str, Any]] = {
        "POSTIZ_API_KEY": "",
        "POSTIZ_URL": DEFAULT_BASE_URL,
        "POSTIZ_INTEGRATION_ID": "",
    }
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting(
            "api_key",
            "Postiz API key",
            env="POSTIZ_API_KEY",
            secret=True,
            field="api_key",
        ),
        InstanceSetting(
            "base_url",
            "Postiz API (self-hosted: https://<host>/api/public/v1)",
            env="POSTIZ_URL",
            default=DEFAULT_BASE_URL,
        ),
        InstanceSetting(
            "integration_id",
            "The channel's Postiz integration id",
            env="POSTIZ_INTEGRATION_ID",
        ),
    )

    @classmethod
    async def _call(
        cls,
        instance: ProviderInstanceModel,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
    ) -> Any:
        base = str(cls.setting(instance, "base_url") or DEFAULT_BASE_URL).rstrip("/")
        return await cls.http().request(
            method,
            f"{base}{path}",
            params=params,
            json=json_body,
            headers={"Authorization": cls.token(instance)},
        )

    @classmethod
    async def _channel(cls, instance: ProviderInstanceModel) -> Dict[str, Any]:
        wanted = cls.required(instance, "integration_id")
        for channel in await cls._call(instance, "GET", "/integrations") or []:
            if channel.get("id") == wanted:
                if channel.get("disabled"):
                    raise cls.refuse(f"channel {wanted} is disabled in Postiz")
                found: Dict[str, Any] = channel
                return found
        raise InvalidInputExternalError(
            f"Postiz has no channel {wanted!r}", provider=cls.name
        )

    @classmethod
    async def publish(
        cls, instance: ProviderInstanceModel, content: str, media_urls: List[str]
    ) -> Dict[str, Any]:
        channel = await cls._channel(instance)
        platform = str(channel.get("identifier", ""))
        if platform in NEEDS_OWN_SETTINGS:
            raise cls.refuse(
                f"a {platform} post needs its own settings; post it from Postiz"
            )
        images = []
        for url in media_urls:
            uploaded = await cls._call(
                instance, "POST", "/upload-from-url", json_body={"url": url}
            )
            images.append({"id": uploaded["id"], "path": uploaded["path"]})
        answer = await cls._call(
            instance,
            "POST",
            "/posts",
            json_body={
                "type": "now",
                "date": datetime.now(UTC).isoformat(),
                "shortLink": False,
                "tags": [],
                "posts": [
                    {
                        "integration": {"id": channel["id"]},
                        "value": [{"content": content, "image": images}],
                        "settings": {
                            "__type": platform,
                            **PLATFORM_SETTINGS.get(platform, {}),
                        },
                    }
                ],
            },
        )
        return cls.published(instance, postiz_post_id(answer), None)

    @classmethod
    async def feed(
        cls, instance: ProviderInstanceModel, limit: int
    ) -> List[Dict[str, Any]]:
        wanted = cls.required(instance, "integration_id")
        now = datetime.now(UTC)
        found = await cls._call(
            instance,
            "GET",
            "/posts",
            params={
                "startDate": (now - timedelta(days=FEED_DAYS)).isoformat(),
                "endDate": now.isoformat(),
            },
        )
        posts = [
            post
            for post in found.get("posts", [])
            if (post.get("integration") or {}).get("id") == wanted
        ]
        posts.sort(key=lambda post: str(post.get("publishDate", "")), reverse=True)
        return [
            {
                "id": post.get("id", ""),
                "text": post.get("content", ""),
                "url": post.get("releaseURL") or "",
                "created_at": post.get("publishDate"),
            }
            for post in posts[:limit]
        ]

    @classmethod
    async def profile(cls, instance: ProviderInstanceModel) -> Dict[str, Any]:
        channel = await cls._channel(instance)
        return {
            "username": channel.get("profile", ""),
            "name": channel.get("name", ""),
            "url": "",
            "followers": None,
            "following": None,
            "posts": None,
            "platform": channel.get("identifier", ""),
            "provider": cls.name,
        }
