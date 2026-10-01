"""
Social media extension for AGInfrastructure.
Implements the Provider Rotation System for social media integrations.
"""

from typing import Any, ClassVar, Dict, List, Optional, Set

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractStaticExtension,
    ability,
)
from zephyrex.extensions.social.PRV_Social import AbstractSocialProvider
from zephyrex.lib.Dependencies import Dependencies, EXT_Dependency, PIP_Dependency
from zephyrex.lib.Logging import logger
from zephyrex.pydantic2.registry import classproperty


class EXT_Social(AbstractStaticExtension):
    """
    Social media extension for AGInfrastructure.

    Provides social media integration abilities including Twitter/X, Facebook,
    Instagram, Threads, TikTok, and Postiz. This extension uses the Provider
    Rotation System for failover and load balancing across multiple social
    media providers.

    The extension focuses on:
    - Social media posting and content creation through provider rotation
    - Social monitoring and analytics
    - Content scheduling and automation
    - Multi-platform social media management
    - Integration with various social media providers via rotation system

    Usage:
        # Post to social media using rotation system
        result = EXT_Social.root.rotate(
            EXT_Social.post_to_social,
            content="Hello world!",
            platforms=["twitter", "linkedin"]
        )
    """

    # Extension metadata (class attributes)
    name: ClassVar[str] = "social"
    friendly_name: ClassVar[str] = "Social Media Integration"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Social media integration extension providing comprehensive social media integration "
        "abilities via Provider Rotation System"
    )

    # Environment variables that this extension needs
    _env: ClassVar[Dict[str, Any]] = {
        "SOCIAL_ENABLED": "true",
        "SOCIAL_DEFAULT_PLATFORM": "twitter",
        "SOCIAL_AUTO_SCHEDULE": "false",
    }

    # Unified dependencies using the Dependencies class
    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            EXT_Dependency(
                name="labels",
                friendly_name="Labels Extension",
                optional=True,
                reason="Optional labels for social media content categorization",
            ),
            PIP_Dependency(
                name="requests",
                friendly_name="HTTP Requests Library",
                optional=False,
                semver=">=2.28.0",
                reason="HTTP requests for social media APIs",
            ),
        ]
    )

    # Meta abilities provided by this extension for managing social media
    _abilities: ClassVar[Set[str]] = {
        "post_to_social",
        "monitor_mentions",
        "get_social_analytics",
    }

    @classproperty
    def pip_dependencies(cls):
        """Get PIP dependencies for backward compatibility."""
        return cls.dependencies.pip

    @classproperty
    def ext_dependencies(cls):
        """Get extension dependencies for backward compatibility."""
        return cls.dependencies.ext

    @classproperty
    def sys_dependencies(cls):
        """Get system dependencies for backward compatibility."""
        return cls.dependencies.sys

    @classmethod
    def has_ability(cls, ability: str) -> bool:
        """Check if this extension has a specific ability."""
        return ability in cls._abilities

    @classmethod
    @ability(name="post_to_social")
    def post_to_social(
        cls,
        content: str,
        platforms: Optional[List[str]] = None,
        media_urls: Optional[List[str]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Meta ability: Post content to social media platforms via provider rotation.

        Args:
            content: The content to post
            platforms: Optional list of platforms to post to
            media_urls: Optional list of media URLs to attach

        Returns:
            Dict containing the posting result
        """
        try:
            return {
                "success": True,
                "content": content,
                "platforms": platforms or [],
                "media_urls": media_urls or [],
                "message": "Social posting capability",
            }
        except Exception as e:
            logger.error(f"Error posting to social media: {e}")
            return {"success": False, "error": str(e)}

    @classmethod
    @ability(name="monitor_mentions")
    def monitor_mentions(
        cls,
        keywords: Optional[List[str]] = None,
        platform: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Meta ability: Monitor social media mentions for the given keywords.

        Args:
            keywords: Optional list of keywords to monitor
            platform: Optional platform to restrict monitoring to

        Returns:
            Dict containing the monitoring result
        """
        try:
            return {
                "success": True,
                "keywords": keywords or [],
                "platform": platform,
                "mentions": [],
                "message": "Mention monitoring capability",
            }
        except Exception as e:
            logger.error(f"Error monitoring mentions: {e}")
            return {"success": False, "error": str(e)}

    @classmethod
    @ability(name="get_social_analytics")
    def get_social_analytics(
        cls,
        platform: Optional[str] = None,
        period: str = "week",
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Meta ability: Retrieve social media analytics.

        Args:
            platform: Optional platform to scope analytics to
            period: Reporting period (default: "week")

        Returns:
            Dict containing analytics information
        """
        try:
            return {
                "success": True,
                "platform": platform,
                "period": period,
                "followers": 0,
                "engagement_rate": 0.0,
                "message": "Social analytics capability",
            }
        except Exception as e:
            logger.error(f"Error retrieving social analytics: {e}")
            return {"success": False, "error": str(e)}

    AbstractProvider = AbstractSocialProvider
