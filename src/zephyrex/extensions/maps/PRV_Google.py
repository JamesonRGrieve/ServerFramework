# SPDX-License-Identifier: AGPL-3.0-or-later
"""Google Maps Platform: the Geocoding API, Places API (New) text search
and the Routes API. The instance's API key (else ``GOOGLE_MAPS_API_KEY``)
must have those three APIs enabled.
"""

from typing import Any, Awaitable, ClassVar, Dict, List, Mapping, Optional, Tuple

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.ExternalErrors import (
    AuthExternalError,
    InvalidInputExternalError,
    RateLimitExternalError,
    TransientExternalError,
)
from zephyrex.extensions.maps.EXT_Maps import (
    AbstractMapsProvider,
    parse_coordinates,
    place,
    step,
)
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
PLACES_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
PLACES_FIELDS = "places.id,places.displayName,places.formattedAddress,places.location"
ROUTES_FIELDS = ",".join(
    (
        "routes.distanceMeters",
        "routes.duration",
        "routes.polyline.geoJsonLinestring",
        "routes.legs.steps.distanceMeters",
        "routes.legs.steps.staticDuration",
        "routes.legs.steps.navigationInstruction.instructions",
    )
)
# The Geocoding API answers 200 and puts its verdict in "status".
_GEOCODE_AUTH = {"REQUEST_DENIED"}
_GEOCODE_INPUT = {"INVALID_REQUEST"}
_GEOCODE_QUOTA = {"OVER_QUERY_LIMIT", "OVER_DAILY_LIMIT"}
# The newer APIs refuse a bad key with a 400, not a 401.
_KEY_REFUSED = "API_KEY_INVALID"


def seconds(duration: str) -> float:
    """A protobuf Duration as Google's JSON writes it (``"123s"``)."""
    return float(duration.rstrip("s") or 0)


class PRV_Google_Maps(AbstractMapsProvider):
    name: ClassVar[str] = "google_maps"
    friendly_name: ClassVar[str] = "Google Maps"
    description: ClassVar[str] = "Google Maps Platform geocoding, places and routes"
    _env: ClassVar[Dict[str, Any]] = {"GOOGLE_MAPS_API_KEY": ""}
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting(
            "api_key",
            "Maps Platform API key (Geocoding, Places (New) and Routes enabled)",
            env="GOOGLE_MAPS_API_KEY",
            secret=True,
            field="api_key",
        ),
    )
    modes: ClassVar[Mapping[str, str]] = {
        "driving": "DRIVE",
        "walking": "WALK",
        "bicycling": "BICYCLE",
        "transit": "TRANSIT",
    }

    @classmethod
    def _key(cls, instance: ProviderInstanceModel) -> str:
        key = cls.setting(instance, "api_key")
        if not key:
            raise TransientExternalError(
                "Google Maps API key not configured", provider=cls.name
            )
        return str(key)

    @classmethod
    async def _typed(cls, call: Awaitable[Any]) -> Any:
        """``call``'s answer, a refused key typed as one."""
        try:
            return await call
        except InvalidInputExternalError as exc:
            if _KEY_REFUSED in str(exc.upstream_payload or ""):
                raise AuthExternalError(
                    "Google refused the API key", provider=cls.name
                ) from exc
            raise

    @classmethod
    async def _geocode(
        cls, instance: ProviderInstanceModel, params: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        answer = await cls.get_json(
            GEOCODE_URL, {**params, "key": cls._key(instance), "language": "en"}
        )
        status = answer.get("status", "")
        detail = answer.get("error_message") or status
        if status in _GEOCODE_AUTH:
            raise AuthExternalError(f"Google: {detail}", provider=cls.name)
        if status in _GEOCODE_INPUT:
            raise InvalidInputExternalError(f"Google: {detail}", provider=cls.name)
        if status in _GEOCODE_QUOTA:
            raise RateLimitExternalError(f"Google: {detail}", provider=cls.name)
        if status not in ("OK", "ZERO_RESULTS"):
            raise TransientExternalError(f"Google: {detail}", provider=cls.name)
        return [
            place(
                cls.name,
                name=result.get("formatted_address", "").split(",")[0],
                address=result.get("formatted_address", ""),
                lat=result["geometry"]["location"]["lat"],
                lon=result["geometry"]["location"]["lng"],
                place_id=result.get("place_id", ""),
            )
            for result in answer.get("results", [])
        ]

    @classmethod
    async def search(
        cls, instance: ProviderInstanceModel, query: str, limit: int
    ) -> List[Dict[str, Any]]:
        answer = await cls._typed(
            cls.http().post(
                PLACES_SEARCH_URL,
                json={"textQuery": query, "pageSize": limit},
                headers={
                    "X-Goog-Api-Key": cls._key(instance),
                    "X-Goog-FieldMask": PLACES_FIELDS,
                },
            )
        )
        return [
            place(
                cls.name,
                name=found.get("displayName", {}).get("text", ""),
                address=found.get("formattedAddress", ""),
                lat=found["location"]["latitude"],
                lon=found["location"]["longitude"],
                place_id=found.get("id", ""),
            )
            for found in answer.get("places", [])
        ]

    @classmethod
    async def geocode(
        cls, instance: ProviderInstanceModel, address: str
    ) -> Optional[Dict[str, Any]]:
        found = await cls._geocode(instance, {"address": address})
        return found[0] if found else None

    @classmethod
    async def reverse_geocode(
        cls, instance: ProviderInstanceModel, lat: float, lon: float
    ) -> Optional[Dict[str, Any]]:
        found = await cls._geocode(instance, {"latlng": f"{lat},{lon}"})
        return found[0] if found else None

    @staticmethod
    def _waypoint(location: str) -> Dict[str, Any]:
        pair = parse_coordinates(location)
        if pair is None:
            return {"address": location}
        return {"location": {"latLng": {"latitude": pair[0], "longitude": pair[1]}}}

    @classmethod
    async def directions(
        cls,
        instance: ProviderInstanceModel,
        origin: str,
        destination: str,
        mode: str,
    ) -> Dict[str, Any]:
        answer = await cls._typed(
            cls.http().post(
                ROUTES_URL,
                json={
                    "origin": cls._waypoint(origin),
                    "destination": cls._waypoint(destination),
                    "travelMode": cls.mode(mode),
                    "polylineEncoding": "GEO_JSON_LINESTRING",
                    "languageCode": "en",
                },
                headers={
                    "X-Goog-Api-Key": cls._key(instance),
                    "X-Goog-FieldMask": ROUTES_FIELDS,
                },
            )
        )
        routes = answer.get("routes") or []
        if not routes:
            raise InvalidInputExternalError(
                f"Google found no {mode} route", provider=cls.name
            )
        route = routes[0]
        line = route.get("polyline", {}).get("geoJsonLinestring", {})
        return {
            "distance_m": float(route.get("distanceMeters", 0)),
            "duration_s": seconds(route.get("duration", "0s")),
            "steps": [
                step(
                    leg_step.get("navigationInstruction", {}).get("instructions", ""),
                    leg_step.get("distanceMeters", 0),
                    seconds(leg_step.get("staticDuration", "0s")),
                )
                for leg in route.get("legs", [])
                for leg_step in leg.get("steps", [])
            ],
            "path": [[lat, lon] for lon, lat in line.get("coordinates", [])],
            "mode": mode,
            "provider": cls.name,
        }
