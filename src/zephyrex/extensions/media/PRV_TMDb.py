# SPDX-License-Identifier: AGPL-3.0-or-later
"""Movies and TV, through The Movie Database (TMDb) API v3.

The instance's API key (else ``TMDB_API_KEY``) is either a v3 API key or a
v4 read access token (a JWT, sent as a Bearer token). A native id is
``movie:<id>`` or ``tv:<id>``; details carry the title's IMDb id.
"""

from typing import Any, ClassVar, Dict, List, Optional, Tuple

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.ExternalErrors import (
    InvalidInputExternalError,
    TransientExternalError,
)
from zephyrex.extensions.media.EXT_Media import AbstractMediaProvider
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

TMDB_API = "https://api.themoviedb.org/3"
TMDB_SITE = "https://www.themoviedb.org"
TMDB_KINDS = ("movie", "tv")
# A v4 read access token is a JWT; a v3 key is 32 hex digits.
_JWT_PREFIX = "eyJ"


class PRV_TMDb_Media(AbstractMediaProvider):
    name: ClassVar[str] = "tmdb"
    friendly_name: ClassVar[str] = "TMDb"
    description: ClassVar[str] = "The Movie Database: movies and TV"
    _env: ClassVar[Dict[str, Any]] = {"TMDB_API_KEY": ""}
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting(
            "api_key",
            "TMDb v3 API key or v4 read access token",
            env="TMDB_API_KEY",
            secret=True,
            field="api_key",
        ),
    )

    @classmethod
    def _auth(
        cls, instance: ProviderInstanceModel
    ) -> Tuple[Dict[str, str], Dict[str, str]]:
        """``(params, headers)`` carrying the credential."""
        key = cls.setting(instance, "api_key")
        if not key:
            raise TransientExternalError(
                "TMDb API key not configured", provider=cls.name
            )
        if key.startswith(_JWT_PREFIX):
            return {}, {"Authorization": f"Bearer {key}"}
        return {"api_key": key}, {}

    @classmethod
    async def _api(
        cls, instance: ProviderInstanceModel, path: str, **params: Any
    ) -> Dict[str, Any]:
        auth_params, headers = cls._auth(instance)
        data: Dict[str, Any] = await cls.get_json(
            f"{TMDB_API}/{path}", {**params, **auth_params}, headers
        )
        return data

    @classmethod
    def _item(cls, kind: str, item: Dict[str, Any]) -> Dict[str, Any]:
        date = item.get("release_date") or item.get("first_air_date") or ""
        return {
            "id": cls.media_id(f"{kind}:{item['id']}"),
            "title": item.get("title") or item.get("name") or "",
            "kind": kind,
            "year": int(date[:4]) if date[:4].isdigit() else None,
            "overview": item.get("overview", ""),
            "url": f"{TMDB_SITE}/{kind}/{item['id']}",
        }

    @classmethod
    async def search(
        cls,
        instance: ProviderInstanceModel,
        query: str,
        kind: Optional[str] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        if kind == "video":
            return []
        data = await cls._api(instance, f"search/{kind or 'multi'}", query=query)
        found = []
        for item in data.get("results", []):
            item_kind = kind or item.get("media_type")
            if item_kind in TMDB_KINDS:
                found.append(cls._item(item_kind, item))
        return found[:limit]

    @classmethod
    def _native(cls, native_id: str) -> Tuple[str, int]:
        kind, _, number = native_id.partition(":")
        if kind not in TMDB_KINDS or not number.isdigit():
            raise InvalidInputExternalError(
                f"{native_id!r} is not a TMDb id (movie:<id> or tv:<id>)",
                provider=cls.name,
            )
        return kind, int(number)

    @classmethod
    async def get_info(
        cls, instance: ProviderInstanceModel, native_id: str
    ) -> Dict[str, Any]:
        kind, number = cls._native(native_id)
        data = await cls._api(
            instance, f"{kind}/{number}", append_to_response="external_ids"
        )
        return {
            **cls._item(kind, data),
            "imdb_id": data.get("external_ids", {}).get("imdb_id")
            or data.get("imdb_id"),
            "genres": [genre["name"] for genre in data.get("genres", [])],
            "rating": data.get("vote_average"),
            "runtime": data.get("runtime"),
            "seasons": data.get("number_of_seasons"),
            "status": data.get("status"),
        }

    @classmethod
    async def get_recommendations(
        cls, instance: ProviderInstanceModel, native_id: str, limit: int = 10
    ) -> List[Dict[str, Any]]:
        kind, number = cls._native(native_id)
        data = await cls._api(instance, f"{kind}/{number}/recommendations")
        return [cls._item(kind, item) for item in data.get("results", [])][:limit]
