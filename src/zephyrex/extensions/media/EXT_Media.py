# SPDX-License-Identifier: AGPL-3.0-or-later
"""Media discovery: search titles and videos, look one up, and find what is
like it, through YouTube (Data API v3) and TMDb (movies and TV, with their
IMDb ids) under the provider rotation.

A media id names the provider that owns it, ``<provider>:<native id>``
(``youtube:dQw4w9WgXcQ``, ``tmdb:movie:603``): a search runs on the first
healthy provider, and a lookup on an id only on instances of its provider.
"""

from abc import abstractmethod
from typing import Any, ClassVar, Dict, List, Optional, Set, Tuple

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractProviderInstance,
    AbstractStaticExtension,
    AbstractStaticProvider,
    ability,
)
from zephyrex.extensions.ExternalErrors import InvalidInputExternalError
from zephyrex.lib.Dependencies import Dependencies
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

MEDIA_REQUEST_TIMEOUT_SECONDS = 10.0
MEDIA_KINDS = ("movie", "tv", "video")


def split_media_id(value: str) -> Tuple[str, str]:
    """``(provider, native id)`` of a media id, refused when it names none."""
    provider, _, native = value.partition(":")
    if not (provider and native):
        raise InvalidInputExternalError(
            f"{value!r} is not a media id (<provider>:<id>, e.g. tmdb:movie:603)"
        )
    return provider, native


class AbstractMediaProvider(AbstractStaticProvider):
    """A media catalogue. Every ability takes the rotated instance; the ids
    it returns are media ids, prefixed with its name."""

    name: ClassVar[str] = ""
    friendly_name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    http_timeout_seconds: ClassVar[float] = MEDIA_REQUEST_TIMEOUT_SECONDS
    _abilities: ClassVar[Set[str]] = {
        "search_media",
        "get_media_info",
        "get_media_recommendations",
    }
    _env: ClassVar[Dict[str, Any]] = {}

    @classmethod
    def bond_instance(cls, instance: ProviderInstanceModel) -> AbstractProviderInstance:
        return AbstractProviderInstance(instance)

    @classmethod
    def media_id(cls, native_id: str) -> str:
        return f"{cls.name}:{native_id}"

    @classmethod
    @abstractmethod
    async def search(
        cls,
        instance: ProviderInstanceModel,
        query: str,
        kind: Optional[str] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Matches for ``query``, each an ``id``, ``title``, ``kind``,
        ``year``, ``overview`` and ``url``. An empty list when ``kind`` is
        one this catalogue does not hold."""

    @classmethod
    @abstractmethod
    async def get_info(
        cls, instance: ProviderInstanceModel, native_id: str
    ) -> Dict[str, Any]:
        """Details of the item ``native_id`` (its id without the prefix)."""

    @classmethod
    @abstractmethod
    async def get_recommendations(
        cls, instance: ProviderInstanceModel, native_id: str, limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Items like ``native_id``, shaped as search results."""

    @classmethod
    def services(cls) -> List[str]:
        return ["media"]


class EXT_Media(AbstractStaticExtension):
    name: ClassVar[str] = "media"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Media search, details and recommendations through YouTube and TMDb"
    )

    _env: ClassVar[Dict[str, Any]] = {}
    dependencies: ClassVar[Dependencies] = Dependencies([])
    _abilities: ClassVar[Set[str]] = {
        "search_media",
        "get_media_info",
        "get_media_recommendations",
    }

    @classmethod
    @ability("search_media")
    async def search_media(
        cls, query: str, kind: Optional[str] = None, limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Search for ``query``; ``kind`` narrows to a movie, tv or video."""
        if kind is not None and kind not in MEDIA_KINDS:
            raise InvalidInputExternalError(
                f"kind must be one of {', '.join(MEDIA_KINDS)}"
            )
        found: List[Dict[str, Any]] = await cls.rotate_provider(
            "search", query, kind, limit
        )
        return found

    @classmethod
    @ability("get_media_info")
    async def get_media_info(cls, media: str) -> Dict[str, Any]:
        """Details of the item ``media`` (a media id), from its own provider."""
        provider, native = split_media_id(media)
        info: Dict[str, Any] = await cls.rotate_provider_for(
            provider, "get_info", native
        )
        return info

    @classmethod
    @ability("get_media_recommendations")
    async def get_media_recommendations(
        cls, media: str, limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Items like ``media`` (a media id), from its own provider."""
        provider, native = split_media_id(media)
        found: List[Dict[str, Any]] = await cls.rotate_provider_for(
            provider, "get_recommendations", native, limit
        )
        return found
