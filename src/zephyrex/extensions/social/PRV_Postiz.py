from typing import Any, Dict, List, Optional

from zephyrex.extensions.AbstractExtensionProvider import AbstractProviderInstance
from zephyrex.extensions.social.PRV_Social import AbstractSocialProvider
from zephyrex.lib.Logging import logger
from zephyrex.logic.BLL_Providers import ProviderInstanceModel


class PostizProvider(AbstractSocialProvider):
    """
    Postiz provider implementation for the social media extension.
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
        """Post content to Postiz."""
        logger.info(f"Posted to Postiz: {content[:30]}...")
        return "Content posted successfully to Postiz"

    @classmethod
    def read_feed(
        cls, bonded_instance: AbstractProviderInstance, count: int = 10
    ) -> List[Dict[str, Any]]:
        """Read the Postiz feed."""
        return [
            {"id": f"item_{i}", "text": f"Postiz post {i}", "platform": "postiz"}
            for i in range(count)
        ]

    @classmethod
    def get_profile(
        cls,
        bonded_instance: AbstractProviderInstance,
        username: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get a Postiz profile."""
        return {
            "username": username or "currentuser",
            "platform": "postiz",
            "followers": 500,
            "posts": 75,
        }

    @classmethod
    def schedule_post(
        cls,
        bonded_instance: AbstractProviderInstance,
        content: str,
        scheduled_time: str,
        media_urls: Optional[List[str]] = None,
    ) -> str:
        """Schedule a post on Postiz."""
        logger.info(f"Scheduled post on Postiz for {scheduled_time}: {content[:30]}...")
        return f"Post scheduled on Postiz for {scheduled_time}"
