# SPDX-License-Identifier: AGPL-3.0-or-later
"""Maps: place search, geocoding, reverse geocoding and directions over
OpenStreetMap (Nominatim + OSRM), Google Maps Platform and the Apple Maps
Server API, through the provider rotation.

Every provider answers in the same shapes. A place is
``{"name", "address", "lat", "lon", "id", "provider"}``; a route is
``{"distance_m", "duration_s", "steps", "path", "mode", "provider"}`` where
each step is ``{"instruction", "distance_m", "duration_s"}`` and ``path``
is the route's ``[lat, lon]`` points. A location argument is an address or
``"lat,lon"``.
"""

import re
from abc import abstractmethod
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Set, Tuple

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractProviderInstance,
    AbstractStaticExtension,
    AbstractStaticProvider,
    ability,
)
from zephyrex.extensions.ExternalErrors import InvalidInputExternalError
from zephyrex.lib.Dependencies import Dependencies
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

MAPS_REQUEST_TIMEOUT_SECONDS = 15.0
MODES = ("driving", "walking", "bicycling", "transit")
DEFAULT_RESULTS = 5
MAX_RESULTS = 20
MAX_LATITUDE = 90.0
MAX_LONGITUDE = 180.0
_COORDINATES = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")


def coordinates(lat: float, lon: float) -> Tuple[float, float]:
    """``(lat, lon)``, refused when either is off the globe."""
    if not (-MAX_LATITUDE <= lat <= MAX_LATITUDE):
        raise InvalidInputExternalError(f"latitude {lat} is outside -90..90")
    if not (-MAX_LONGITUDE <= lon <= MAX_LONGITUDE):
        raise InvalidInputExternalError(f"longitude {lon} is outside -180..180")
    return lat, lon


def parse_coordinates(location: str) -> Optional[Tuple[float, float]]:
    """``(lat, lon)`` when ``location`` is a ``"lat,lon"`` pair, else None
    (it is an address). A pair off the globe is refused."""
    match = _COORDINATES.match(location)
    if match is None:
        return None
    return coordinates(float(match.group(1)), float(match.group(2)))


def result_limit(limit: int) -> int:
    if not 1 <= limit <= MAX_RESULTS:
        raise InvalidInputExternalError(f"limit must be 1-{MAX_RESULTS}, not {limit}")
    return limit


def place(
    provider: str,
    *,
    name: str,
    address: str,
    lat: float,
    lon: float,
    place_id: str = "",
) -> Dict[str, Any]:
    return {
        "name": name,
        "address": address,
        "lat": float(lat),
        "lon": float(lon),
        "id": place_id,
        "provider": provider,
    }


def step(instruction: str, distance_m: float, duration_s: float) -> Dict[str, Any]:
    return {
        "instruction": instruction,
        "distance_m": float(distance_m),
        "duration_s": float(duration_s),
    }


class AbstractMapsProvider(AbstractStaticProvider):
    """A mapping service. ``modes`` maps each travel mode it can route to
    its own name for it; a mode it lacks is refused."""

    name: ClassVar[str] = ""
    friendly_name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    _abilities: ClassVar[Set[str]] = {
        "search_location",
        "geocode",
        "reverse_geocode",
        "get_directions",
    }
    _env: ClassVar[Dict[str, Any]] = {}
    http_timeout_seconds: ClassVar[float] = MAPS_REQUEST_TIMEOUT_SECONDS
    modes: ClassVar[Mapping[str, str]] = {}

    @classmethod
    def bond_instance(cls, instance: ProviderInstanceModel) -> AbstractProviderInstance:
        return AbstractProviderInstance(instance)

    @classmethod
    def mode(cls, mode: str) -> str:
        """This service's name for travel ``mode``."""
        native = cls.modes.get(mode)
        if native is None:
            offered = ", ".join(cls.modes)
            raise InvalidInputExternalError(
                f"{cls.friendly_name} cannot route {mode!r} (it offers {offered})",
                provider=cls.name,
            )
        return native

    @classmethod
    async def locate(
        cls, instance: ProviderInstanceModel, location: str
    ) -> Tuple[float, float]:
        """``(lat, lon)`` of ``location``, geocoding an address."""
        pair = parse_coordinates(location)
        if pair is not None:
            return pair
        found = await cls.geocode(instance, location)
        if found is None:
            raise InvalidInputExternalError(
                f"No place found for {location!r}", provider=cls.name
            )
        return found["lat"], found["lon"]

    @classmethod
    @abstractmethod
    async def search(
        cls, instance: ProviderInstanceModel, query: str, limit: int
    ) -> List[Dict[str, Any]]:
        """Places matching ``query``."""

    @classmethod
    @abstractmethod
    async def geocode(
        cls, instance: ProviderInstanceModel, address: str
    ) -> Optional[Dict[str, Any]]:
        """The best place for ``address``, None when nothing matches."""

    @classmethod
    @abstractmethod
    async def reverse_geocode(
        cls, instance: ProviderInstanceModel, lat: float, lon: float
    ) -> Optional[Dict[str, Any]]:
        """The place at ``lat, lon``, None when there is none."""

    @classmethod
    @abstractmethod
    async def directions(
        cls,
        instance: ProviderInstanceModel,
        origin: str,
        destination: str,
        mode: str,
    ) -> Dict[str, Any]:
        """The best route from ``origin`` to ``destination``."""

    @classmethod
    def services(cls) -> List[str]:
        return ["maps", "geocoding", "routing"]


class EXT_Maps(AbstractStaticExtension):
    name: ClassVar[str] = "maps"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Place search, geocoding and directions across OpenStreetMap, "
        "Google Maps and Apple Maps"
    )

    _env: ClassVar[Dict[str, Any]] = {}
    dependencies: ClassVar[Dependencies] = Dependencies([])
    _abilities: ClassVar[Set[str]] = {
        "search_location",
        "geocode",
        "reverse_geocode",
        "get_directions",
    }

    @classmethod
    @ability("search_location")
    async def search_location(
        cls, query: str, limit: int = DEFAULT_RESULTS
    ) -> List[Dict[str, Any]]:
        """Places matching ``query``."""
        result: List[Dict[str, Any]] = await cls.rotate_provider(
            "search", query, result_limit(limit)
        )
        return result

    @classmethod
    @ability("geocode")
    async def geocode(cls, address: str) -> Optional[Dict[str, Any]]:
        """The place an address names, None when nothing matches."""
        result: Optional[Dict[str, Any]] = await cls.rotate_provider("geocode", address)
        return result

    @classmethod
    @ability("reverse_geocode")
    async def reverse_geocode(cls, lat: float, lon: float) -> Optional[Dict[str, Any]]:
        """The place at a coordinate, None when there is none."""
        lat, lon = coordinates(lat, lon)
        result: Optional[Dict[str, Any]] = await cls.rotate_provider(
            "reverse_geocode", lat, lon
        )
        return result

    @classmethod
    @ability("get_directions")
    async def get_directions(
        cls, origin: str, destination: str, mode: str = "driving"
    ) -> Dict[str, Any]:
        """A route between two locations (addresses or ``"lat,lon"``)."""
        if mode not in MODES:
            raise InvalidInputExternalError(
                f"mode must be one of {', '.join(MODES)}, not {mode!r}"
            )
        result: Dict[str, Any] = await cls.rotate_provider(
            "directions", origin, destination, mode
        )
        return result
