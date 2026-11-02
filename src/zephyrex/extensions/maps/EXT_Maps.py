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

``nearby`` finds places of a category (``PLACE_CATEGORIES``, which an app
can add to with ``register_place_category``) around a point: each is a
place with ``distance_m``, ``category`` and, when the map knows them,
``website``, ``phone`` and ``opening_hours``. Only providers declaring
``nearby_search`` answer it (OpenStreetMap, through Overpass).
``midpoint`` is the point halfway between several locations on the globe.
The same searches are served to signed-in users on ``/v1/maps``
(``BLL_Maps``).
"""

import math
import re
from abc import abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Sequence, Set, Tuple

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
EARTH_RADIUS_M = 6_371_008.8
DEFAULT_RADIUS_M = 5_000
MAX_RADIUS_M = 50_000
NEARBY_SEARCH = "nearby_search"
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


def search_radius(radius_m: int) -> int:
    if not 1 <= radius_m <= MAX_RADIUS_M:
        raise InvalidInputExternalError(
            f"radius_m must be 1-{MAX_RADIUS_M}, not {radius_m}"
        )
    return radius_m


def distance_m(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    """The great-circle distance between two ``(lat, lon)`` points, metres."""
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(min(1.0, h)))


def midpoint(points: Sequence[Tuple[float, float]]) -> Tuple[float, float]:
    """The point halfway between ``points`` on the globe: the mean of their
    positions on the unit sphere, so it holds across the antimeridian."""
    if not points:
        raise InvalidInputExternalError("A midpoint needs at least one point")
    x = y = z = 0.0
    for lat, lon in points:
        phi, lam = math.radians(lat), math.radians(lon)
        x += math.cos(phi) * math.cos(lam)
        y += math.cos(phi) * math.sin(lam)
        z += math.sin(phi)
    if math.isclose(math.hypot(x, y, z), 0.0, abs_tol=1e-12):
        raise InvalidInputExternalError(
            "Those points have no midpoint (they are opposite)"
        )
    lat = math.degrees(math.atan2(z, math.hypot(x, y)))
    lon = math.degrees(math.atan2(y, x))
    return round(lat, 6), round(lon, 6)


@dataclass(frozen=True)
class PlaceCategory:
    """A kind of place ``nearby`` can find. ``osm`` holds Overpass tag
    filters (any one matches), e.g. ``'["amenity"="cafe"]'``."""

    name: str
    title: str
    osm: Tuple[str, ...]


# A healthcare:speciality value is a ;-separated list.
_SPECIALITY = "(^|;) *({}) *(;|$)"

PLACE_CATEGORIES: Dict[str, PlaceCategory] = {}


def register_place_category(category: PlaceCategory) -> None:
    """Make ``category`` searchable by ``nearby`` (an app's own kinds of place)."""
    PLACE_CATEGORIES[category.name] = category


def place_category(name: str) -> PlaceCategory:
    category = PLACE_CATEGORIES.get(name)
    if category is None:
        offered = ", ".join(sorted(PLACE_CATEGORIES))
        raise InvalidInputExternalError(
            f"No kind of place called {name!r} (there are {offered})"
        )
    return category


for _category in (
    PlaceCategory(
        "sexual_health",
        "Sexual-health clinics and testing",
        (
            '["healthcare:speciality"~"'
            + _SPECIALITY.format("sexual_health|std|sti|hiv|venereology")
            + '",i]',
            '["healthcare"="sexual_health"]',
            '["healthcare:counselling"~"sexual",i]',
        ),
    ),
    PlaceCategory("restaurant", "Restaurants", ('["amenity"="restaurant"]',)),
    PlaceCategory("cafe", "Cafés", ('["amenity"="cafe"]',)),
    PlaceCategory("bar", "Bars and pubs", ('["amenity"~"^(bar|pub)$"]',)),
    PlaceCategory("cinema", "Cinemas", ('["amenity"="cinema"]',)),
    PlaceCategory("theatre", "Theatres", ('["amenity"="theatre"]',)),
    PlaceCategory(
        "museum", "Museums and galleries", ('["tourism"~"^(museum|gallery)$"]',)
    ),
    PlaceCategory("park", "Parks and gardens", ('["leisure"~"^(park|garden)$"]',)),
):
    register_place_category(_category)


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
    async def nearby(
        cls,
        instance: ProviderInstanceModel,
        lat: float,
        lon: float,
        category: PlaceCategory,
        radius_m: int,
        limit: int,
    ) -> List[Dict[str, Any]]:
        """Places of ``category`` within ``radius_m`` of ``lat, lon``,
        nearest first. Offered by providers declaring ``nearby_search``."""
        raise InvalidInputExternalError(
            f"{cls.friendly_name} cannot search nearby", provider=cls.name
        )

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
        "find_nearby",
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

    @classmethod
    @ability("find_nearby")
    async def find_nearby(
        cls,
        near: str,
        category: str,
        radius_m: int = DEFAULT_RADIUS_M,
        limit: int = DEFAULT_RESULTS,
    ) -> List[Dict[str, Any]]:
        """Places of a kind (``PLACE_CATEGORIES``) around a location (an
        address or ``"lat,lon"``), nearest first."""
        kind = place_category(category)
        radius, count = search_radius(radius_m), result_limit(limit)
        lat, lon = await cls.locate(near)
        result: List[Dict[str, Any]] = await cls.rotate_capable(
            NEARBY_SEARCH, "nearby", lat, lon, kind, radius, count
        )
        return result

    @classmethod
    async def locate(cls, location: str) -> Tuple[float, float]:
        """``(lat, lon)`` of an address or ``"lat,lon"``."""
        pair = parse_coordinates(location)
        if pair is not None:
            return pair
        found = await cls.geocode(location)
        if found is None:
            raise InvalidInputExternalError(f"No place found for {location!r}")
        return float(found["lat"]), float(found["lon"])
