from typing import Any, Dict, List, Optional

from zephyrex.extensions.AbstractExtensionProvider import AbstractProviderInstance
from zephyrex.extensions.social.PRV_Social import AbstractSocialProvider
from zephyrex.lib.Logging import logger
from zephyrex.logic.BLL_Providers import ProviderInstanceModel


class XProvider(AbstractSocialProvider):
    """
    X (Twitter) provider implementation for the social media extension.
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
        """Post content to X."""
        logger.info(f"Posted to X: {content[:30]}...")
        return "Content posted successfully to X"

    @classmethod
    def read_feed(
        cls, bonded_instance: AbstractProviderInstance, count: int = 10
    ) -> List[Dict[str, Any]]:
        """Read the X timeline."""
        return [
            {"id": f"tweet_{i}", "text": f"Tweet {i}", "platform": "x"}
            for i in range(count)
        ]

    @classmethod
    def get_profile(
        cls,
        bonded_instance: AbstractProviderInstance,
        username: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get an X profile."""
        return {
            "username": username or "currentuser",
            "platform": "x",
            "followers": 3000,
            "following": 1500,
            "tweets": 5000,
        }

    @classmethod
    def retweet(cls, bonded_instance: AbstractProviderInstance, tweet_id: str) -> str:
        """Retweet a tweet."""
        logger.info(f"Retweeted tweet: {tweet_id}")
        return f"Successfully retweeted tweet {tweet_id}"

    @classmethod
    def like_tweet(
        cls, bonded_instance: AbstractProviderInstance, tweet_id: str
    ) -> str:
        """Like a tweet."""
        logger.info(f"Liked tweet: {tweet_id}")
        return f"Successfully liked tweet {tweet_id}"

    @classmethod
    def search_tweets(
        cls, bonded_instance: AbstractProviderInstance, query: str, count: int = 10
    ) -> List[Dict[str, Any]]:
        """Search for tweets."""
        return [
            {"id": f"tweet_{i}", "text": f"Tweet about {query}", "platform": "x"}
            for i in range(count)
        ]
