# SPDX-License-Identifier: AGPL-3.0-or-later
"""OpenStreetMap: Nominatim for search and geocoding, OSRM for routing.

The defaults are the public OSM Foundation Nominatim and the FOSSGIS OSRM
servers, which hold one routing graph per travel mode (car, bike, foot);
the public project-osrm.org demo routes only cars whatever profile it is
asked for. Each URL is an instance setting, for a self-hosted server. The
public servers' usage policies allow light use from an identified client
(every request carries this software's User-Agent).
"""

from typing import Any, ClassVar, Dict, List, Mapping, Optional, Tuple

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.ExternalErrors import InvalidInputExternalError
from zephyrex.extensions.maps.EXT_Maps import AbstractMapsProvider, place, step
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

DEFAULT_NOMINATIM_URL = "https://nominatim.openstreetmap.org"
DEFAULT_OSRM_URLS = {
    "driving": "https://routing.openstreetmap.de/routed-car",
    "walking": "https://routing.openstreetmap.de/routed-foot",
    "bicycling": "https://routing.openstreetmap.de/routed-bike",
}


def osrm_instruction(maneuver: Mapping[str, Any], road: str) -> str:
    """Words for an OSRM step, which carries a maneuver but no text."""
    kind = str(maneuver.get("type", ""))
    modifier = str(maneuver.get("modifier", ""))
    onto = f" onto {road}" if road else ""
    direction = f" {modifier}" if modifier else ""
    if kind == "depart":
        return f"Head{direction}{onto}"
    if kind == "arrive":
        return "Arrive at your destination"
    if kind in ("roundabout", "rotary"):
        exit_number = maneuver.get("exit")
        taken = f" and take exit {exit_number}" if exit_number else ""
        return f"Enter the roundabout{taken}{onto}"
    verb = {
        "turn": "Turn",
        "end of road": "Turn",
        "fork": "Keep",
        "merge": "Merge",
        "on ramp": "Take the ramp",
        "off ramp": "Take the exit",
        "new name": "Continue",
        "continue": "Continue",
    }.get(kind, "Continue")
    return f"{verb}{direction}{onto}"


class PRV_OpenStreetMap_Maps(AbstractMapsProvider):
    name: ClassVar[str] = "openstreetmap"
    friendly_name: ClassVar[str] = "OpenStreetMap"
    description: ClassVar[str] = "Nominatim geocoding and OSRM routing"
    _env: ClassVar[Dict[str, Any]] = {
        "OSM_NOMINATIM_URL": DEFAULT_NOMINATIM_URL,
        "OSM_OSRM_DRIVING_URL": DEFAULT_OSRM_URLS["driving"],
        "OSM_OSRM_WALKING_URL": DEFAULT_OSRM_URLS["walking"],
        "OSM_OSRM_BICYCLING_URL": DEFAULT_OSRM_URLS["bicycling"],
    }
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting(
            "nominatim_url",
            "Nominatim server",
            env="OSM_NOMINATIM_URL",
            default=DEFAULT_NOMINATIM_URL,
        ),
        InstanceSetting(
            "osrm_driving_url",
            "OSRM server for driving",
            env="OSM_OSRM_DRIVING_URL",
            default=DEFAULT_OSRM_URLS["driving"],
        ),
        InstanceSetting(
            "osrm_walking_url",
            "OSRM server for walking",
            env="OSM_OSRM_WALKING_URL",
            default=DEFAULT_OSRM_URLS["walking"],
        ),
        InstanceSetting(
            "osrm_bicycling_url",
            "OSRM server for cycling",
            env="OSM_OSRM_BICYCLING_URL",
            default=DEFAULT_OSRM_URLS["bicycling"],
        ),
    )
    # OSRM's profile segment is "driving" on every FOSSGIS graph; the mode
    # picks the server.
    modes: ClassVar[Mapping[str, str]] = {
        "driving": "osrm_driving_url",
        "walking": "osrm_walking_url",
        "bicycling": "osrm_bicycling_url",
    }

    @classmethod
    def _url(cls, instance: ProviderInstanceModel, key: str) -> str:
        return str(cls.setting(instance, key) or "").rstrip("/")

    @classmethod
    def _place(cls, result: Mapping[str, Any]) -> Dict[str, Any]:
        address = str(result.get("display_name", ""))
        return place(
            cls.name,
            name=str(result.get("name") or address.split(",")[0]),
            address=address,
            lat=float(result["lat"]),
            lon=float(result["lon"]),
            place_id=f"{result.get('osm_type', '')}/{result.get('osm_id', '')}",
        )

    @classmethod
    async def _search(
        cls, instance: ProviderInstanceModel, query: str, limit: int
    ) -> List[Dict[str, Any]]:
        results = await cls.get_json(
            f"{cls._url(instance, 'nominatim_url')}/search",
            {"q": query, "format": "jsonv2", "limit": limit},
        )
        return [cls._place(result) for result in results or []]

    @classmethod
    async def search(
        cls, instance: ProviderInstanceModel, query: str, limit: int
    ) -> List[Dict[str, Any]]:
        return await cls._search(instance, query, limit)

    @classmethod
    async def geocode(
        cls, instance: ProviderInstanceModel, address: str
    ) -> Optional[Dict[str, Any]]:
        found = await cls._search(instance, address, 1)
        return found[0] if found else None

    @classmethod
    async def reverse_geocode(
        cls, instance: ProviderInstanceModel, lat: float, lon: float
    ) -> Optional[Dict[str, Any]]:
        result = await cls.get_json(
            f"{cls._url(instance, 'nominatim_url')}/reverse",
            {"lat": lat, "lon": lon, "format": "jsonv2"},
        )
        # Open water and the like: {"error": "Unable to geocode"}.
        if not result or "error" in result:
            return None
        return cls._place(result)

    @classmethod
    async def directions(
        cls,
        instance: ProviderInstanceModel,
        origin: str,
        destination: str,
        mode: str,
    ) -> Dict[str, Any]:
        server = cls._url(instance, cls.mode(mode))
        start = await cls.locate(instance, origin)
        end = await cls.locate(instance, destination)
        # OSRM orders coordinates lon,lat.
        waypoints = f"{start[1]},{start[0]};{end[1]},{end[0]}"
        answer = await cls.get_json(
            f"{server}/route/v1/driving/{waypoints}",
            {"overview": "full", "steps": "true", "geometries": "geojson"},
        )
        routes = answer.get("routes") or []
        if answer.get("code") != "Ok" or not routes:
            raise InvalidInputExternalError(
                f"OSRM found no route: {answer.get('message') or answer.get('code')}",
                provider=cls.name,
            )
        route = routes[0]
        steps = [
            step(
                osrm_instruction(
                    leg_step.get("maneuver", {}), leg_step.get("name", "")
                ),
                leg_step.get("distance", 0),
                leg_step.get("duration", 0),
            )
            for leg in route.get("legs", [])
            for leg_step in leg.get("steps", [])
        ]
        return {
            "distance_m": float(route.get("distance", 0)),
            "duration_s": float(route.get("duration", 0)),
            "steps": steps,
            "path": [
                [lat, lon]
                for lon, lat in route.get("geometry", {}).get("coordinates", [])
            ],
            "mode": mode,
            "provider": cls.name,
        }
