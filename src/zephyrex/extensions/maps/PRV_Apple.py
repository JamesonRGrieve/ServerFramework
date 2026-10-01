# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Apple Maps Server API: search, geocoding, reverse geocoding and
directions (driving, walking, cycling; it has no transit routing).

An instance holds a Maps private key (``.p8`` PEM), its key id and the
team id, each falling back to the environment. A request signs a short
Maps token (ES256, scope ``server_api``) and trades it at ``/v1/token``
for the access token the other endpoints take; that access token is
reused until shortly before it expires.
"""

import time
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Tuple

import jwt

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.ExternalErrors import (
    AuthExternalError,
    InvalidInputExternalError,
    TransientExternalError,
)
from zephyrex.extensions.maps.EXT_Maps import AbstractMapsProvider, place, step
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

API_URL = "https://maps-api.apple.com"
MAPS_TOKEN_LIFETIME_SECONDS = 600
# An access token is refreshed this long before Apple says it expires.
ACCESS_TOKEN_MARGIN_SECONDS = 60


def maps_token(team_id: str, key_id: str, private_key: str, now: float) -> str:
    """The signed Maps token ``/v1/token`` trades for an access token."""
    issued = int(now)
    try:
        return jwt.encode(
            {
                "iss": team_id,
                "iat": issued,
                "exp": issued + MAPS_TOKEN_LIFETIME_SECONDS,
                "scope": "server_api",
            },
            private_key,
            algorithm="ES256",
            headers={"kid": key_id, "typ": "JWT"},
        )
    except (ValueError, TypeError, jwt.PyJWTError) as exc:
        raise AuthExternalError(
            "Apple Maps private key is not a usable ES256 key", provider="apple_maps"
        ) from exc


class PRV_Apple_Maps(AbstractMapsProvider):
    name: ClassVar[str] = "apple_maps"
    friendly_name: ClassVar[str] = "Apple Maps"
    description: ClassVar[str] = "Apple Maps Server API"
    _env: ClassVar[Dict[str, Any]] = {
        "APPLE_MAPS_TEAM_ID": "",
        "APPLE_MAPS_KEY_ID": "",
        "APPLE_MAPS_PRIVATE_KEY": "",
    }
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting("team_id", "Apple Developer team id", env="APPLE_MAPS_TEAM_ID"),
        InstanceSetting("key_id", "Maps private key id", env="APPLE_MAPS_KEY_ID"),
        InstanceSetting(
            "private_key",
            "Maps private key (.p8 PEM contents)",
            env="APPLE_MAPS_PRIVATE_KEY",
            secret=True,
        ),
    )
    modes: ClassVar[Mapping[str, str]] = {
        "driving": "Automobile",
        "walking": "Walking",
        "bicycling": "Cycling",
    }
    # instance id -> (credentials it was issued for, access token, expiry).
    _access_tokens: ClassVar[Dict[str, Tuple[Tuple[str, ...], str, float]]] = {}

    @classmethod
    def _credentials(cls, instance: ProviderInstanceModel) -> Tuple[str, str, str]:
        team_id = cls.setting(instance, "team_id")
        key_id = cls.setting(instance, "key_id")
        private_key = cls.setting(instance, "private_key")
        if not (team_id and key_id and private_key):
            raise TransientExternalError(
                "Apple Maps team id, key id and private key not configured",
                provider=cls.name,
            )
        return str(team_id), str(key_id), str(private_key)

    @classmethod
    async def _access_token(cls, instance: ProviderInstanceModel) -> str:
        credentials = cls._credentials(instance)
        cached = cls._access_tokens.get(str(instance.id))
        now = time.time()
        if cached and cached[0] == credentials and cached[2] > now:
            return cached[1]
        answer = await cls.get_json(
            f"{API_URL}/v1/token",
            headers={"Authorization": f"Bearer {maps_token(*credentials, now)}"},
        )
        token = str(answer["accessToken"])
        expiry = now + int(answer["expiresInSeconds"]) - ACCESS_TOKEN_MARGIN_SECONDS
        cls._access_tokens[str(instance.id)] = (credentials, token, expiry)
        return token

    @classmethod
    async def _get(
        cls, instance: ProviderInstanceModel, path: str, params: Dict[str, Any]
    ) -> Any:
        token = await cls._access_token(instance)
        try:
            return await cls.get_json(
                f"{API_URL}{path}",
                {**params, "lang": "en-US"},
                headers={"Authorization": f"Bearer {token}"},
            )
        except AuthExternalError:
            # A revoked key: the next call trades for a fresh token.
            cls._access_tokens.pop(str(instance.id), None)
            raise

    @classmethod
    def _place(cls, found: Mapping[str, Any]) -> Dict[str, Any]:
        lines = found.get("formattedAddressLines") or []
        return place(
            cls.name,
            name=str(found.get("name") or (lines[0] if lines else "")),
            address=", ".join(lines),
            lat=found["coordinate"]["latitude"],
            lon=found["coordinate"]["longitude"],
            place_id=str(found.get("id", "")),
        )

    @classmethod
    async def search(
        cls, instance: ProviderInstanceModel, query: str, limit: int
    ) -> List[Dict[str, Any]]:
        answer = await cls._get(instance, "/v1/search", {"q": query})
        return [cls._place(found) for found in answer.get("results", [])[:limit]]

    @classmethod
    async def geocode(
        cls, instance: ProviderInstanceModel, address: str
    ) -> Optional[Dict[str, Any]]:
        answer = await cls._get(instance, "/v1/geocode", {"q": address})
        results = answer.get("results") or []
        return cls._place(results[0]) if results else None

    @classmethod
    async def reverse_geocode(
        cls, instance: ProviderInstanceModel, lat: float, lon: float
    ) -> Optional[Dict[str, Any]]:
        answer = await cls._get(instance, "/v1/reverseGeocode", {"loc": f"{lat},{lon}"})
        results = answer.get("results") or []
        return cls._place(results[0]) if results else None

    @classmethod
    async def directions(
        cls,
        instance: ProviderInstanceModel,
        origin: str,
        destination: str,
        mode: str,
    ) -> Dict[str, Any]:
        answer = await cls._get(
            instance,
            "/v1/directions",
            {
                "origin": origin,
                "destination": destination,
                "transportType": cls.mode(mode),
            },
        )
        routes = answer.get("routes") or []
        if not routes:
            raise InvalidInputExternalError(
                f"Apple Maps found no {mode} route", provider=cls.name
            )
        route = routes[0]
        all_steps: List[Dict[str, Any]] = answer.get("steps", [])
        step_paths: List[List[Dict[str, float]]] = answer.get("stepPaths", [])
        taken = [all_steps[index] for index in route.get("stepIndexes", [])]
        path: List[List[float]] = []
        for route_step in taken:
            index = route_step.get("stepPathIndex")
            if index is not None and index < len(step_paths):
                path.extend(
                    [point["latitude"], point["longitude"]]
                    for point in step_paths[index]
                )
        return {
            "distance_m": float(route.get("distanceMeters", 0)),
            "duration_s": float(route.get("durationSeconds", 0)),
            "steps": [
                step(
                    route_step.get("instructions", ""),
                    route_step.get("distanceMeters", 0),
                    route_step.get("durationSeconds", 0),
                )
                for route_step in taken
            ],
            "path": path,
            "mode": mode,
            "provider": cls.name,
        }
