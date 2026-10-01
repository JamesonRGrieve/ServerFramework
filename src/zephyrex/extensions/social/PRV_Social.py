from abc import abstractmethod
from typing import Any, Dict, List, Optional

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractProviderInstance,
    AbstractStaticProvider,
    ability,
)
from zephyrex.logic.BLL_Providers import ProviderInstanceModel


class AbstractSocialProvider(AbstractStaticProvider):
    """
    Abstract base class for social media service providers.

    All social media providers are static/abstract classes with no
    instantiation required, integrating with the Provider Rotation System
    for failover and load balancing. Concrete providers implement
    ``bond_instance`` to attach a ``ProviderInstanceModel``'s credentials
    and expose synchronous platform operations against the bonded instance.
    """

    @staticmethod
    def services() -> List[str]:
        """Return the services provided by social media providers."""
        return ["social_post", "social_read", "social_engage"]

    @classmethod
    @abstractmethod
    def bond_instance(cls, instance: ProviderInstanceModel) -> AbstractProviderInstance:
        """Bond a provider instance with the platform credentials."""
        pass

    @classmethod
    @abstractmethod
    @ability(name="post_content")
    def post_content(
        cls,
        bonded_instance: AbstractProviderInstance,
        content: str,
        media_urls: Optional[List[str]] = None,
    ) -> str:
        """
        Post content to the social media platform.

        Args:
            bonded_instance: The bonded provider instance carrying credentials
            content: Text content to post
            media_urls: Optional list of media URLs to attach

        Returns:
            Status message indicating success or failure
        """
        pass

    @classmethod
    @abstractmethod
    @ability(name="read_feed")
    def read_feed(
        cls, bonded_instance: AbstractProviderInstance, count: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Read the latest feed items.

        Args:
            bonded_instance: The bonded provider instance carrying credentials
            count: Number of items to retrieve

        Returns:
            List of feed items
        """
        pass

    @classmethod
    @abstractmethod
    @ability(name="get_profile")
    def get_profile(
        cls,
        bonded_instance: AbstractProviderInstance,
        username: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Get profile information.

        Args:
            bonded_instance: The bonded provider instance carrying credentials
            username: Optional username to get profile for (default: authenticated user)

        Returns:
            Profile information
        """
        pass
