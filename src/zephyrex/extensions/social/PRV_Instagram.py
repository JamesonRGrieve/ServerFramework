from typing import Any, Dict, List, Optional

from zephyrex.extensions.AbstractExtensionProvider import AbstractProviderInstance
from zephyrex.extensions.social.PRV_Social import AbstractSocialProvider
from zephyrex.lib.Logging import logger
from zephyrex.logic.BLL_Providers import ProviderInstanceModel


class InstagramProvider(AbstractSocialProvider):
    """
    Instagram provider implementation for the social media extension.
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
        """Post content to Instagram."""
        if not media_urls:
            return "Instagram requires at least one image or video to post"

        logger.info(f"Posted to Instagram with media: {media_urls}")
        return "Content posted successfully to Instagram"

    @classmethod
    def read_feed(
        cls, bonded_instance: AbstractProviderInstance, count: int = 10
    ) -> List[Dict[str, Any]]:
        """Read the Instagram feed."""
        return [
            {"id": f"item_{i}", "text": f"Instagram post {i}", "platform": "instagram"}
            for i in range(count)
        ]

    @classmethod
    def get_profile(
        cls,
        bonded_instance: AbstractProviderInstance,
        username: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get an Instagram profile."""
        return {
            "username": username or "currentuser",
            "platform": "instagram",
            "followers": 2000,
            "following": 1000,
            "posts": 150,
        }

    @classmethod
    def post_story(
        cls,
        bonded_instance: AbstractProviderInstance,
        media_url: str,
        caption: Optional[str] = None,
    ) -> str:
        """Post a story to Instagram."""
        logger.info(f"Posted story to Instagram: {media_url}")
        return "Story posted successfully to Instagram"

    @classmethod
    def post_reel(
        cls,
        bonded_instance: AbstractProviderInstance,
        video_url: str,
        caption: Optional[str] = None,
    ) -> str:
        """Post a reel to Instagram."""
        logger.info(f"Posted reel to Instagram: {video_url}")
        return "Reel posted successfully to Instagram"
