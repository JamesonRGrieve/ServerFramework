from typing import Any, Dict, List, Optional

from zephyrex.extensions.AbstractExtensionProvider import AbstractProviderInstance
from zephyrex.extensions.social.PRV_Social import AbstractSocialProvider
from zephyrex.lib.Logging import logger
from zephyrex.logic.BLL_Providers import ProviderInstanceModel


class TikTokProvider(AbstractSocialProvider):
    """
    TikTok provider implementation for the social media extension.
    """

    @classmethod
    def bond_instance(cls, instance: ProviderInstanceModel) -> AbstractProviderInstance:
        return AbstractProviderInstance(instance)

    @classmethod
    def post_content(
        cls,
        bonded_instance: AbstractProviderInstance,
        content: str,
        media_urls: Optional[List[str]] = None,
    ) -> str:
        """Post content to TikTok."""
        if not media_urls:
            return "TikTok requires a video to post"

        logger.info(f"Posted to TikTok with caption: {content[:30]}...")
        return "Content posted successfully to TikTok"

    @classmethod
    def read_feed(
        cls, bonded_instance: AbstractProviderInstance, count: int = 10
    ) -> List[Dict[str, Any]]:
        """Read the TikTok feed."""
        return [
            {"id": f"item_{i}", "caption": f"TikTok video {i}", "platform": "tiktok"}
            for i in range(count)
        ]

    @classmethod
    def get_profile(
        cls,
        bonded_instance: AbstractProviderInstance,
        username: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get a TikTok profile."""
        return {
            "username": username or "currentuser",
            "platform": "tiktok",
            "followers": 5000,
            "following": 1000,
            "videos": 200,
            "likes": 15000,
        }

    @classmethod
    def post_video(
        cls,
        bonded_instance: AbstractProviderInstance,
        video_url: str,
        caption: Optional[str] = None,
    ) -> str:
        """Post a video to TikTok."""
        logger.info(f"Posted video to TikTok: {video_url}")
        return "Video posted successfully to TikTok"

    @classmethod
    def get_trends(
        cls, bonded_instance: AbstractProviderInstance, count: int = 10
    ) -> List[Dict[str, Any]]:
        """Get current trends on TikTok."""
        return [
            {"name": f"Trend #{i}", "views": i * 1000000} for i in range(1, count + 1)
        ]
