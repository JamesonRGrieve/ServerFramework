import logging
from typing import Any, Dict, Optional

from zephyrex.extensions.media.PRV_Media import AbstractMediaProvider

YOUTUBE_API_BASE_URL = "https://www.googleapis.com/youtube/v3"


class YouTubeProvider(AbstractMediaProvider):
    """
    Media provider backed by YouTube.

    A lightweight, directly-instantiated client. Search, info, and
    recommendation lookups are mocked pending a real YouTube Data API
    integration; channel info, playlists, and comments follow the same
    shape.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.api_uri = self.settings.get("youtube_api_uri", YOUTUBE_API_BASE_URL)
        self.commands.update(
            {
                "Get YouTube Channel Info": self.get_channel_info,
                "Get YouTube Playlists": self.get_playlists,
                "Get YouTube Comments": self.get_comments,
            }
        )

    def get_platform_name(self) -> str:
        return "YouTube"

    def search(self, query: str, service: Optional[str] = None) -> Dict[str, Any]:
        logging.info(f"Searching YouTube for: {query}")

        return {
            "service": "YouTube",
            "query": query,
            "results": [
                {
                    "id": "yt123abc",
                    "title": f"Video about {query}",
                    "type": "video",
                    "channel": "SampleChannel",
                },
                {
                    "id": "yt456def",
                    "title": f"Tutorial on {query}",
                    "type": "video",
                    "channel": "TutorialChannel",
                },
            ],
        }

    def get_info(self, media_id: str) -> Dict[str, Any]:
        logging.info(f"Getting info for YouTube video: {media_id}")

        return {
            "id": media_id,
            "title": f"YouTube Video {media_id}",
            "description": "This is a sample description for a YouTube video.",
            "published_at": "2023-01-15T12:30:45Z",
            "channel": "SampleChannel",
            "channel_id": "UC123456",
            "views": 12345,
            "likes": 1000,
            "duration": "PT10M30S",
        }

    def get_recommendations(
        self, user_id: str, genre: Optional[str] = None
    ) -> Dict[str, Any]:
        logging.info(f"Getting YouTube recommendations for user: {user_id}")

        filter_text = f" in {genre} category" if genre else ""
        return {
            "service": "YouTube",
            "user_id": user_id,
            "category": genre,
            "recommendations": [
                {
                    "id": "yt789ghi",
                    "title": f"Recommended Video{filter_text}",
                    "channel": "PopularChannel",
                },
                {
                    "id": "yt012jkl",
                    "title": f"Trending Video{filter_text}",
                    "channel": "TrendingChannel",
                },
            ],
        }

    def get_channel_info(self, channel_id: str) -> Dict[str, Any]:
        logging.info(f"Getting channel info for: {channel_id}")

        return {
            "id": channel_id,
            "title": f"Channel {channel_id}",
            "description": "This is a sample YouTube channel.",
            "subscribers": 100000,
            "video_count": 456,
            "created_at": "2020-05-15T00:00:00Z",
        }

    def get_playlists(self, channel_id: str) -> Dict[str, Any]:
        logging.info(f"Getting playlists for channel: {channel_id}")

        return {
            "channel_id": channel_id,
            "playlists": [
                {"id": "PL123", "title": "Tutorial Series", "video_count": 15},
                {"id": "PL456", "title": "Vlogs", "video_count": 32},
            ],
        }

    def get_comments(self, video_id: str, limit: int = 20) -> Dict[str, Any]:
        logging.info(f"Getting comments for video: {video_id}")

        return {
            "video_id": video_id,
            "comment_count": 256,
            "comments": [
                {
                    "id": "comment123",
                    "author": "User1",
                    "text": "Great video!",
                    "likes": 45,
                    "published_at": "2023-02-10T15:30:00Z",
                },
                {
                    "id": "comment456",
                    "author": "User2",
                    "text": "Thanks for the information.",
                    "likes": 12,
                    "published_at": "2023-02-11T08:45:00Z",
                },
            ][:limit],
        }
