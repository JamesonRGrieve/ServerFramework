# SPDX-License-Identifier: AGPL-3.0-or-later
"""YouTube videos, through the YouTube Data API v3.

The instance's API key (else ``YOUTUBE_API_KEY``) is a Google Cloud key
with the YouTube Data API enabled. YouTube's API no longer offers related
videos (``relatedToVideoId`` was retired in 2023), so recommendations are
the other videos of the same channel, most viewed first.
"""

import re
from typing import Any, ClassVar, Dict, List, Optional, Tuple

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.ExternalErrors import (
    InvalidInputExternalError,
    TransientExternalError,
)
from zephyrex.extensions.media.EXT_Media import AbstractMediaProvider
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

YOUTUBE_API = "https://www.googleapis.com/youtube/v3"
# YouTube caps a page at 50 results.
YOUTUBE_MAX_RESULTS = 50
_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


class PRV_YouTube_Media(AbstractMediaProvider):
    name: ClassVar[str] = "youtube"
    friendly_name: ClassVar[str] = "YouTube"
    description: ClassVar[str] = "YouTube videos (Data API v3)"
    _env: ClassVar[Dict[str, Any]] = {"YOUTUBE_API_KEY": ""}
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting(
            "api_key",
            "Google API key with the YouTube Data API enabled",
            env="YOUTUBE_API_KEY",
            secret=True,
            field="api_key",
        ),
    )

    @classmethod
    def _key(cls, instance: ProviderInstanceModel) -> str:
        key = cls.setting(instance, "api_key")
        if not key:
            raise TransientExternalError(
                "YouTube API key not configured", provider=cls.name
            )
        return key

    @classmethod
    async def _api(
        cls, instance: ProviderInstanceModel, resource: str, **params: Any
    ) -> Dict[str, Any]:
        # The key travels as a parameter, never in the URL a failure names.
        data: Dict[str, Any] = await cls.get_json(
            f"{YOUTUBE_API}/{resource}", {**params, "key": cls._key(instance)}
        )
        return data

    @classmethod
    def _video(cls, video_id: str, snippet: Dict[str, Any]) -> Dict[str, Any]:
        published = snippet.get("publishedAt", "")
        return {
            "id": cls.media_id(video_id),
            "title": snippet.get("title", ""),
            "kind": "video",
            "year": int(published[:4]) if published[:4].isdigit() else None,
            "overview": snippet.get("description", ""),
            "channel": snippet.get("channelTitle", ""),
            "url": f"https://www.youtube.com/watch?v={video_id}",
        }

    @classmethod
    def _limit(cls, limit: int) -> int:
        return max(1, min(limit, YOUTUBE_MAX_RESULTS))

    @classmethod
    async def search(
        cls,
        instance: ProviderInstanceModel,
        query: str,
        kind: Optional[str] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        if kind not in (None, "video"):
            return []
        data = await cls._api(
            instance,
            "search",
            part="snippet",
            q=query,
            type="video",
            maxResults=cls._limit(limit),
        )
        return [
            cls._video(item["id"]["videoId"], item.get("snippet", {}))
            for item in data.get("items", [])
            if item.get("id", {}).get("videoId")
        ]

    @classmethod
    def _video_id(cls, native_id: str) -> str:
        if not _VIDEO_ID.match(native_id):
            raise InvalidInputExternalError(
                f"{native_id!r} is not a YouTube video id", provider=cls.name
            )
        return native_id

    @classmethod
    async def get_info(
        cls, instance: ProviderInstanceModel, native_id: str
    ) -> Dict[str, Any]:
        video_id = cls._video_id(native_id)
        data = await cls._api(
            instance, "videos", part="snippet,contentDetails,statistics", id=video_id
        )
        items = data.get("items", [])
        if not items:
            raise InvalidInputExternalError(
                f"No YouTube video {video_id}", provider=cls.name, upstream_status=404
            )
        item = items[0]
        statistics = item.get("statistics", {})
        return {
            **cls._video(video_id, item.get("snippet", {})),
            "channel_id": item.get("snippet", {}).get("channelId", ""),
            "duration": item.get("contentDetails", {}).get("duration", ""),
            "views": int(statistics.get("viewCount", 0)),
            "likes": int(statistics.get("likeCount", 0)),
        }

    @classmethod
    async def get_recommendations(
        cls, instance: ProviderInstanceModel, native_id: str, limit: int = 10
    ) -> List[Dict[str, Any]]:
        info = await cls.get_info(instance, native_id)
        data = await cls._api(
            instance,
            "search",
            part="snippet",
            channelId=info["channel_id"],
            type="video",
            order="viewCount",
            maxResults=cls._limit(limit + 1),
        )
        return [
            cls._video(item["id"]["videoId"], item.get("snippet", {}))
            for item in data.get("items", [])
            if item.get("id", {}).get("videoId") not in (None, native_id)
        ][:limit]
