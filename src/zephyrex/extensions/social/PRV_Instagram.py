# SPDX-License-Identifier: AGPL-3.0-or-later
"""An Instagram professional account, through the Instagram Graph API's
content publishing.

The instance's API key is an access token with
``instagram_content_publish`` (else ``INSTAGRAM_ACCESS_TOKEN``) and its
``ig_user_id`` the account's Instagram user id. The token's login decides
the host: Facebook Login tokens use graph.facebook.com (the default),
Instagram Login tokens graph.instagram.com (``graph_url``).

Every Instagram post needs media: one photo, one video (published as a
Reel), or two to ten (a carousel). Instagram fetches each from its URL
and processes it before the post is published.
"""

from typing import Any, ClassVar, Dict, List, Tuple

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.social.EXT_Social import (
    AbstractSocialProvider,
    count,
    media_kind,
)
from zephyrex.lib.MetaGraph import FACEBOOK_GRAPH, meta_graph
from zephyrex.lib.ProviderHTTPClient import path_segment
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

STATUS_FIELD = "status_code"


class PRV_Instagram_Social(AbstractSocialProvider):
    name: ClassVar[str] = "instagram"
    friendly_name: ClassVar[str] = "Instagram"
    description: ClassVar[str] = "An Instagram professional account"
    _env: ClassVar[Dict[str, Any]] = {
        "INSTAGRAM_ACCESS_TOKEN": "",
        "INSTAGRAM_USER_ID": "",
        "INSTAGRAM_GRAPH_URL": FACEBOOK_GRAPH,
    }
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting(
            "api_key",
            "Access token (instagram_content_publish)",
            env="INSTAGRAM_ACCESS_TOKEN",
            secret=True,
            field="api_key",
        ),
        InstanceSetting("ig_user_id", "Instagram user id", env="INSTAGRAM_USER_ID"),
        InstanceSetting(
            "graph_url",
            "Graph host (Instagram Login tokens: https://graph.instagram.com/v26.0)",
            env="INSTAGRAM_GRAPH_URL",
            default=FACEBOOK_GRAPH,
        ),
    )

    @classmethod
    def _base(cls, instance: ProviderInstanceModel) -> str:
        return str(cls.setting(instance, "graph_url") or FACEBOOK_GRAPH).rstrip("/")

    @classmethod
    def _user(cls, instance: ProviderInstanceModel) -> str:
        return path_segment(
            cls.required(instance, "ig_user_id"), "Instagram ig_user_id"
        )

    @classmethod
    async def _container(
        cls, instance: ProviderInstanceModel, fields: Dict[str, Any]
    ) -> str:
        """A processed media container's id."""
        token, base = cls.token(instance), cls._base(instance)
        found = await meta_graph(
            cls,
            "POST",
            f"{cls._user(instance)}/media",
            token,
            base=base,
            json_body=fields,
        )
        container = path_segment(str(found["id"]), "Instagram container id")
        await cls.await_container(container, token, base, STATUS_FIELD)
        return container

    @staticmethod
    def _media_fields(url: str) -> Dict[str, Any]:
        if media_kind(url) == "image":
            return {"image_url": url}
        return {"media_type": "VIDEO", "video_url": url}

    @classmethod
    async def publish(
        cls, instance: ProviderInstanceModel, content: str, media_urls: List[str]
    ) -> Dict[str, Any]:
        if not media_urls:
            raise cls.refuse("every post needs a photo or video")
        if len(media_urls) == 1:
            fields = cls._media_fields(media_urls[0])
            if fields.get("media_type") == "VIDEO":
                fields["media_type"] = "REELS"
            container = await cls._container(instance, {**fields, "caption": content})
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
                    "caption": content,
                },
            )
        token, base = cls.token(instance), cls._base(instance)
        found = await meta_graph(
            cls,
            "POST",
            f"{cls._user(instance)}/media_publish",
            token,
            base=base,
            json_body={"creation_id": container},
        )
        post_id = path_segment(str(found["id"]), "Instagram media id")
        detail = await meta_graph(
            cls, "GET", post_id, token, base=base, params={"fields": "permalink"}
        )
        return cls.published(instance, post_id, detail.get("permalink"))

    @classmethod
    async def feed(
        cls, instance: ProviderInstanceModel, limit: int
    ) -> List[Dict[str, Any]]:
        found = await meta_graph(
            cls,
            "GET",
            f"{cls._user(instance)}/media",
            cls.token(instance),
            base=cls._base(instance),
            params={"fields": "id,caption,permalink,timestamp", "limit": limit},
        )
        return [
            {
                "id": post["id"],
                "text": post.get("caption", ""),
                "url": post.get("permalink", ""),
                "created_at": post.get("timestamp"),
            }
            for post in found.get("data", [])
        ]

    @classmethod
    async def profile(cls, instance: ProviderInstanceModel) -> Dict[str, Any]:
        found = await meta_graph(
            cls,
            "GET",
            cls._user(instance),
            cls.token(instance),
            base=cls._base(instance),
            params={
                "fields": "username,name,followers_count,follows_count,media_count"
            },
        )
        username = found.get("username", "")
        return {
            "username": username,
            "name": found.get("name", ""),
            "url": f"https://www.instagram.com/{username}/" if username else "",
            "followers": count(found.get("followers_count")),
            "following": count(found.get("follows_count")),
            "posts": count(found.get("media_count")),
            "provider": cls.name,
        }
