# SPDX-License-Identifier: AGPL-3.0-or-later
"""Social media: publish posts, read an account's recent posts and its
profile on X, Facebook Pages, Instagram, Threads, TikTok, or any channel
connected to a Postiz workspace.

Each provider instance is one account, its token write-only. A post goes
to the platform the caller names (never to whichever platform happens to
be healthy), on that platform's instances in rotation order. Every post a
platform accepts is recorded as a ``SocialPublication``.

Media are given as public HTTPS URLs, which the platform fetches itself;
what a platform accepts differs (Instagram and TikTok need media, X here
takes text only) and is refused up front with the reason.

Providers answer in the same shapes: a post ``{id, text, url,
created_at}``, a profile ``{username, name, url, followers, following,
posts}`` (None where the platform does not say).
"""

import asyncio
from abc import abstractmethod
from datetime import UTC, datetime
from typing import Any, ClassVar, Dict, List, Optional, Set
from urllib.parse import urlparse

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractProviderInstance,
    AbstractStaticExtension,
    AbstractStaticProvider,
    ability,
)
from zephyrex.extensions.ExternalErrors import (
    InvalidInputExternalError,
    PermanentExternalError,
    TransientExternalError,
)
from zephyrex.lib.Dependencies import Dependencies
from zephyrex.lib.Environment import env
from zephyrex.lib.MetaGraph import meta_graph
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

SOCIAL_REQUEST_TIMEOUT_SECONDS = 30.0
DEFAULT_FEED_LIMIT = 10
MAX_FEED_LIMIT = 100
MAX_MEDIA = 10
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".gif")
VIDEO_EXTENSIONS = (".mp4", ".mov", ".m4v", ".webm")
# Meta processes an uploaded media container before it can be published.
CONTAINER_POLL_SECONDS = 2.0
CONTAINER_WAIT_SECONDS = 60.0


def media_kind(url: str) -> str:
    """``image`` or ``video``, from a public HTTPS media URL's file type."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise InvalidInputExternalError(f"{url!r} is not a public https URL")
    path = parsed.path.lower()
    if path.endswith(IMAGE_EXTENSIONS):
        return "image"
    if path.endswith(VIDEO_EXTENSIONS):
        return "video"
    raise InvalidInputExternalError(
        f"{url!r} does not end in an image or video file type"
    )


def checked_media(media_urls: Optional[List[str]]) -> List[str]:
    urls = list(media_urls or [])
    if len(urls) > MAX_MEDIA:
        raise InvalidInputExternalError(f"at most {MAX_MEDIA} media per post")
    for url in urls:
        media_kind(url)
    return urls


def feed_limit(limit: int) -> int:
    if not 1 <= limit <= MAX_FEED_LIMIT:
        raise InvalidInputExternalError(f"limit must be 1-{MAX_FEED_LIMIT}")
    return limit


def count(value: Any) -> Optional[int]:
    """A platform's count, None when it gave none."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


class AbstractSocialProvider(AbstractStaticProvider):
    """A social platform; each instance is one account on it."""

    name: ClassVar[str] = ""
    friendly_name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    _abilities: ClassVar[Set[str]] = {"publish_post", "read_feed", "get_profile"}
    _env: ClassVar[Dict[str, Any]] = {}
    http_timeout_seconds: ClassVar[float] = SOCIAL_REQUEST_TIMEOUT_SECONDS

    @classmethod
    def bond_instance(cls, instance: ProviderInstanceModel) -> AbstractProviderInstance:
        return AbstractProviderInstance(instance)

    @classmethod
    def token(cls, instance: ProviderInstanceModel) -> str:
        token = cls.setting(instance, "api_key")
        if not token:
            raise TransientExternalError(
                f"{cls.friendly_name} access token not configured", provider=cls.name
            )
        return str(token)

    @classmethod
    def required(cls, instance: ProviderInstanceModel, key: str) -> str:
        value = cls.setting(instance, key)
        if not value:
            raise TransientExternalError(
                f"{cls.friendly_name} {key} not configured", provider=cls.name
            )
        return str(value)

    @classmethod
    def published(
        cls, instance: ProviderInstanceModel, post_id: str, url: Optional[str]
    ) -> Dict[str, Any]:
        return {
            "platform_post_id": str(post_id),
            "url": url,
            "provider": cls.name,
            "provider_instance_id": str(instance.id),
        }

    @classmethod
    def refuse(cls, why: str) -> InvalidInputExternalError:
        return InvalidInputExternalError(
            f"{cls.friendly_name}: {why}", provider=cls.name
        )

    @classmethod
    async def await_container(
        cls, container_id: str, token: str, base: str, status_field: str
    ) -> None:
        """Wait for a Meta media container to finish processing."""
        waited = 0.0
        while True:
            found = await meta_graph(
                cls,
                "GET",
                container_id,
                token,
                base=base,
                params={"fields": status_field},
            )
            state = str(found.get(status_field, ""))
            if state in ("FINISHED", "PUBLISHED"):
                return
            if state in ("ERROR", "EXPIRED"):
                raise PermanentExternalError(
                    f"{cls.friendly_name} could not process the media ({state})",
                    provider=cls.name,
                )
            if waited >= CONTAINER_WAIT_SECONDS:
                raise TransientExternalError(
                    f"{cls.friendly_name} is still processing the media",
                    provider=cls.name,
                )
            await asyncio.sleep(CONTAINER_POLL_SECONDS)
            waited += CONTAINER_POLL_SECONDS

    @classmethod
    @abstractmethod
    async def publish(
        cls, instance: ProviderInstanceModel, content: str, media_urls: List[str]
    ) -> Dict[str, Any]:
        """Publish a post: see ``published``."""

    @classmethod
    @abstractmethod
    async def feed(
        cls, instance: ProviderInstanceModel, limit: int
    ) -> List[Dict[str, Any]]:
        """The account's most recent posts, newest first."""

    @classmethod
    @abstractmethod
    async def profile(cls, instance: ProviderInstanceModel) -> Dict[str, Any]:
        """The account's profile and counts."""

    @classmethod
    def services(cls) -> List[str]:
        return ["social_post", "social_read"]


class EXT_Social(AbstractStaticExtension):
    name: ClassVar[str] = "social"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Publish and read posts on X, Facebook, Instagram, Threads, TikTok "
        "and Postiz channels"
    )

    _env: ClassVar[Dict[str, Any]] = {}
    dependencies: ClassVar[Dependencies] = Dependencies([])
    _abilities: ClassVar[Set[str]] = {"publish_post", "read_feed", "get_profile"}

    @classmethod
    def _record(cls, content: str, media_urls: List[str], post: Dict[str, Any]) -> str:
        """Record an accepted post; its publication id."""
        from zephyrex.extensions.social.BLL_Social import SocialPublicationManager

        root = cls.root
        if root is None:
            raise TransientExternalError("No social rotation to record against")
        created = SocialPublicationManager(
            model_registry=root.model_registry, requester_id=env("ROOT_ID")
        ).create(
            provider=post["provider"],
            provider_instance_id=post["provider_instance_id"],
            platform_post_id=post["platform_post_id"],
            url=post["url"],
            content=content,
            media_urls=media_urls or None,
            published_at=datetime.now(UTC),
        )
        return str(created.id)

    @classmethod
    @ability("publish_post")
    async def publish_post(
        cls, provider: str, content: str, media_urls: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """Publish ``content`` (and media, by public HTTPS URL) on the
        named platform."""
        media = checked_media(media_urls)
        if not content.strip() and not media:
            raise InvalidInputExternalError("a post needs text or media")
        post: Dict[str, Any] = await cls.rotate_provider_for(
            provider, "publish", content, media
        )
        return {**post, "publication_id": cls._record(content, media, post)}

    @classmethod
    @ability("read_feed")
    async def read_feed(
        cls, provider: str, limit: int = DEFAULT_FEED_LIMIT
    ) -> List[Dict[str, Any]]:
        """The account's most recent posts on the named platform."""
        result: List[Dict[str, Any]] = await cls.rotate_provider_for(
            provider, "feed", feed_limit(limit)
        )
        return result

    @classmethod
    @ability("get_profile")
    async def get_profile(cls, provider: str) -> Dict[str, Any]:
        """The account's profile and follower counts on the named platform."""
        result: Dict[str, Any] = await cls.rotate_provider_for(provider, "profile")
        return result
