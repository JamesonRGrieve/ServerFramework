# SPDX-License-Identifier: AGPL-3.0-or-later
"""The maps extension's searches, served to signed-in users on ``/v1/maps``.

- ``GET /categories``: the kinds of place ``nearby`` finds.
- ``GET /search?q=&limit=``: places matching a query.
- ``GET /nearby?near=&category=&radius_m=&limit=``: places of a kind around
  an address or ``"lat,lon"``, nearest first.
- ``POST /midpoint``: the point halfway between several locations, how far
  each is from it, and places of a kind around it.

They run on the operator's map providers (the extension's rotation); a
provider that cannot answer is 502, one rate limiting is 429, and input
the map cannot use (an address it does not know, coordinates off the
globe) is 422.
"""

from __future__ import annotations

import asyncio
from typing import Any, ClassVar, Dict, List, Optional

from fastapi import HTTPException
from pydantic import BaseModel as ResponseModel
from pydantic import Field

from zephyrex.extensions.ExternalErrors import (
    BaseExternalError,
    InvalidInputExternalError,
    RateLimitExternalError,
)
from zephyrex.extensions.maps.EXT_Maps import (
    DEFAULT_RADIUS_M,
    DEFAULT_RESULTS,
    MAX_RADIUS_M,
    MAX_RESULTS,
    MODES,
    PLACE_CATEGORIES,
    EXT_Maps,
    distance_m,
    midpoint,
)
from zephyrex.lib.CustomRoute import ExposeIn, custom_route
from zephyrex.lib.Logging import logger
from zephyrex.logic.AbstractLogicManager import AbstractBLLManager
from zephyrex.pydantic2.fastapi import RouterMixin

MAPS_PREFIX = "/v1/maps"
MAX_QUERY_LENGTH = 200
MAX_MIDPOINT_LOCATIONS = 8


class Place(ResponseModel):
    name: str
    address: str
    lat: float
    lon: float
    id: str = ""
    provider: str = ""
    distance_m: Optional[float] = None
    category: Optional[str] = None
    website: Optional[str] = None
    phone: Optional[str] = None
    opening_hours: Optional[str] = None


class PlaceList(ResponseModel):
    places: List[Place]


class Category(ResponseModel):
    name: str
    title: str


class CategoryList(ResponseModel):
    categories: List[Category]


class MidpointRequest(ResponseModel):
    locations: List[str] = Field(
        ...,
        min_length=2,
        max_length=MAX_MIDPOINT_LOCATIONS,
        description="Addresses or 'lat,lon' pairs",
    )
    mode: str = Field("driving", description="How each travels there")
    category: Optional[str] = Field(None, description="A kind of place to find there")
    radius_m: int = Field(DEFAULT_RADIUS_M, ge=1, le=MAX_RADIUS_M)
    limit: int = Field(DEFAULT_RESULTS, ge=1, le=MAX_RESULTS)


class Leg(ResponseModel):
    location: str
    straight_m: float = Field(..., description="As the crow flies")
    distance_m: Optional[float] = Field(
        None, description="By road or path; none when no route was found"
    )
    duration_s: Optional[float] = None


class Midpoint(ResponseModel):
    lat: float
    lon: float
    legs: List[Leg]
    places: List[Place]


def refusal(err: BaseExternalError) -> HTTPException:
    """A map provider's failure as the HTTP answer a caller can act on."""
    if isinstance(err, InvalidInputExternalError):
        return HTTPException(status_code=422, detail=str(err))
    if isinstance(err, RateLimitExternalError):
        return HTTPException(
            status_code=429, detail="The map is busy; try again shortly"
        )
    logger.warning("maps: the provider failed: %s", err)
    return HTTPException(status_code=502, detail="The map did not answer")


def _places(found: List[Dict[str, Any]]) -> List[Place]:
    return [Place.model_validate(place) for place in found]


class MapsManager(AbstractBLLManager, RouterMixin):
    prefix: ClassVar[Optional[str]] = MAPS_PREFIX
    tags: ClassVar[Optional[List[str]]] = ["Maps"]
    routes_to_register: ClassVar[Optional[List[Any]]] = []

    @custom_route(
        method="GET",
        path="/categories",
        output_model=CategoryList,
        authentication_type="jwt",
        expose_in=(ExposeIn.REST,),
        openapi_tags=("Maps",),
        summary="The kinds of place a nearby search finds",
    )
    def categories_route(self) -> CategoryList:
        return CategoryList(
            categories=[
                Category(name=c.name, title=c.title)
                for c in sorted(PLACE_CATEGORIES.values(), key=lambda c: c.title)
            ]
        )

    @custom_route(
        method="GET",
        path="/search",
        output_model=PlaceList,
        authentication_type="jwt",
        expose_in=(ExposeIn.REST,),
        openapi_tags=("Maps",),
        summary="Places matching a query",
    )
    async def search_route(self, q: str, limit: int = DEFAULT_RESULTS) -> PlaceList:
        if not q.strip() or len(q) > MAX_QUERY_LENGTH:
            raise HTTPException(
                status_code=422, detail=f"Search for 1 to {MAX_QUERY_LENGTH} characters"
            )
        try:
            return PlaceList(places=_places(await EXT_Maps.search_location(q, limit)))
        except BaseExternalError as err:
            raise refusal(err) from err

    @custom_route(
        method="GET",
        path="/nearby",
        output_model=PlaceList,
        authentication_type="jwt",
        expose_in=(ExposeIn.REST,),
        openapi_tags=("Maps",),
        summary="Places of a kind around an address or point, nearest first",
    )
    async def nearby_route(
        self,
        near: str,
        category: str,
        radius_m: int = DEFAULT_RADIUS_M,
        limit: int = DEFAULT_RESULTS,
    ) -> PlaceList:
        if not near.strip() or len(near) > MAX_QUERY_LENGTH:
            raise HTTPException(status_code=422, detail="Say where to search around")
        try:
            return PlaceList(
                places=_places(
                    await EXT_Maps.find_nearby(near, category, radius_m, limit)
                )
            )
        except BaseExternalError as err:
            raise refusal(err) from err

    @custom_route(
        method="POST",
        path="/midpoint",
        input_model=MidpointRequest,
        output_model=Midpoint,
        authentication_type="jwt",
        expose_in=(ExposeIn.REST,),
        openapi_tags=("Maps",),
        summary="The point halfway between several locations, and places around it",
    )
    async def midpoint_route(self, body: MidpointRequest) -> Midpoint:
        return await meet_between(body)


async def _leg(location: str, point: tuple[float, float], there: str, mode: str) -> Leg:
    start = await EXT_Maps.locate(location)
    leg = Leg(location=location, straight_m=round(distance_m(start, point)))
    try:
        route = await EXT_Maps.get_directions(f"{start[0]},{start[1]}", there, mode)
    except BaseExternalError as err:
        # A midpoint in a lake has no route to it; the distance still stands.
        logger.info("maps: no %s route to the midpoint: %s", mode, err)
        return leg
    leg.distance_m = route["distance_m"]
    leg.duration_s = route["duration_s"]
    return leg


async def meet_between(body: MidpointRequest) -> Midpoint:
    """The midpoint of ``body.locations``, each one's way there, and places
    of ``body.category`` around it."""
    if body.mode not in MODES:
        raise HTTPException(
            status_code=422, detail=f"mode must be one of {', '.join(MODES)}"
        )
    try:
        points = await asyncio.gather(
            *(EXT_Maps.locate(location) for location in body.locations)
        )
        point = midpoint(points)
        there = f"{point[0]},{point[1]}"
        legs = await asyncio.gather(
            *(_leg(location, point, there, body.mode) for location in body.locations)
        )
        places = (
            await EXT_Maps.find_nearby(there, body.category, body.radius_m, body.limit)
            if body.category
            else []
        )
    except BaseExternalError as err:
        raise refusal(err) from err
    return Midpoint(lat=point[0], lon=point[1], legs=list(legs), places=_places(places))
