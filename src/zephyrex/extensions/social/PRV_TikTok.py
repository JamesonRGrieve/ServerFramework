# SPDX-License-Identifier: AGPL-3.0-or-later
"""TikTok, through the Content Posting API and the Display API.

The instance's API key is a user access token with ``video.publish``,
``user.info.basic``, ``user.info.stats`` and ``video.list`` (else
``TIKTOK_ACCESS_TOKEN``). A TikTok post is one video or up to 35 photos,
pulled by TikTok from their URLs (on a domain verified for the app); its
privacy is the ``privacy_level`` setting, which must be one the creator
allows. Apps TikTok has not audited may post only privately
(``SELF_ONLY``, the default). TikTok publishes asynchronously, so a post
answers with its publish id and no address yet.
"""

from typing import Any, ClassVar, Dict, List, Optional, Tuple

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.ExternalErrors import (
    AuthExternalError,
    InvalidInputExternalError,
    TransientExternalError,
)
from zephyrex.extensions.social.EXT_Social import (
    AbstractSocialProvider,
    count,
    media_kind,
)
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

API_URL = "https://open.tiktokapis.com/v2"
DEFAULT_PRIVACY = "SELF_ONLY"
MAX_PHOTOS = 35
MAX_VIDEO_LIST = 20
PROFILE_FIELDS = (
    "open_id,display_name,username,profile_deep_link,"
    "follower_count,following_count,video_count"
)
VIDEO_FIELDS = "id,title,video_description,create_time,share_url"
_AUTH_ERRORS = {"access_token_invalid", "scope_not_authorized", "token_expired"}


class PRV_TikTok_Social(AbstractSocialProvider):
    name: ClassVar[str] = "tiktok"
    friendly_name: ClassVar[str] = "TikTok"
    description: ClassVar[str] = "TikTok"
    _env: ClassVar[Dict[str, Any]] = {
        "TIKTOK_ACCESS_TOKEN": "",
        "TIKTOK_PRIVACY_LEVEL": DEFAULT_PRIVACY,
    }
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting(
            "api_key",
            "User access token (video.publish, user.info.basic, user.info.stats, video.list)",
            env="TIKTOK_ACCESS_TOKEN",
            secret=True,
            field="api_key",
        ),
        InstanceSetting(
            "privacy_level",
            "Who sees a post (SELF_ONLY, MUTUAL_FOLLOW_FRIENDS, FOLLOWER_OF_CREATOR, "
            "PUBLIC_TO_EVERYONE)",
            env="TIKTOK_PRIVACY_LEVEL",
            default=DEFAULT_PRIVACY,
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
    ) -> Dict[str, Any]:
        answer: Dict[str, Any] = await cls.http().request(
            method,
            f"{API_URL}{path}",
            params=params,
            json=json_body,
            headers={"Authorization": f"Bearer {cls.token(instance)}"},
        )
        # TikTok can answer 200 with its verdict in error.code.
        error = answer.get("error") or {}
        code = str(error.get("code", "ok"))
        if code == "ok":
            return answer
        detail = f"TikTok: {code} {error.get('message', '')}".strip()
        if code in _AUTH_ERRORS:
            raise AuthExternalError(detail, provider=cls.name)
        if code.startswith("invalid") or code.endswith("_not_allowed"):
            raise InvalidInputExternalError(detail, provider=cls.name)
        raise TransientExternalError(detail, provider=cls.name)

    @classmethod
    async def _privacy(cls, instance: ProviderInstanceModel) -> str:
        """The configured privacy level, refused when the creator lacks it."""
        level = str(cls.setting(instance, "privacy_level") or DEFAULT_PRIVACY)
        found = await cls._call(instance, "POST", "/post/publish/creator_info/query/")
        allowed = found.get("data", {}).get("privacy_level_options") or []
        if level not in allowed:
            raise cls.refuse(
                f"privacy_level {level} is not allowed for this creator "
                f"(allowed: {', '.join(allowed)})"
            )
        return level

    @classmethod
    async def publish(
        cls, instance: ProviderInstanceModel, content: str, media_urls: List[str]
    ) -> Dict[str, Any]:
        kinds = {media_kind(url) for url in media_urls}
        if not media_urls:
            raise cls.refuse("every post needs one video or photos")
        if kinds == {"video"} and len(media_urls) == 1:
            found = await cls._call(
                instance,
                "POST",
                "/post/publish/video/init/",
                json_body={
                    "post_info": {
                        "title": content,
                        "privacy_level": await cls._privacy(instance),
                    },
                    "source_info": {
                        "source": "PULL_FROM_URL",
                        "video_url": media_urls[0],
                    },
                },
            )
        elif kinds == {"image"} and len(media_urls) <= MAX_PHOTOS:
            found = await cls._call(
                instance,
                "POST",
                "/post/publish/content/init/",
                json_body={
                    "post_info": {
                        "description": content,
                        "privacy_level": await cls._privacy(instance),
                    },
                    "source_info": {
                        "source": "PULL_FROM_URL",
                        "photo_cover_index": 0,
                        "photo_images": media_urls,
                    },
                    "post_mode": "DIRECT_POST",
                    "media_type": "PHOTO",
                },
            )
        else:
            raise cls.refuse(f"a post is one video or 1-{MAX_PHOTOS} photos, not a mix")
        return cls.published(instance, found["data"]["publish_id"], None)

    @classmethod
    async def feed(
        cls, instance: ProviderInstanceModel, limit: int
    ) -> List[Dict[str, Any]]:
        found = await cls._call(
            instance,
            "POST",
            "/video/list/",
            params={"fields": VIDEO_FIELDS},
            json_body={"max_count": min(limit, MAX_VIDEO_LIST)},
        )
        return [
            {
                "id": video["id"],
                "text": video.get("video_description") or video.get("title", ""),
                "url": video.get("share_url", ""),
                "created_at": video.get("create_time"),
            }
            for video in found.get("data", {}).get("videos", [])
        ]

    @classmethod
    async def profile(cls, instance: ProviderInstanceModel) -> Dict[str, Any]:
        found = await cls._call(
            instance, "GET", "/user/info/", params={"fields": PROFILE_FIELDS}
        )
        user = found.get("data", {}).get("user", {})
        return {
            "username": user.get("username", ""),
            "name": user.get("display_name", ""),
            "url": user.get("profile_deep_link", ""),
            "followers": count(user.get("follower_count")),
            "following": count(user.get("following_count")),
            "posts": count(user.get("video_count")),
            "provider": cls.name,
        }
