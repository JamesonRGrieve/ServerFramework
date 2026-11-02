# SPDX-License-Identifier: AGPL-3.0-or-later
"""Nearby search and the midpoint: the globe's arithmetic, the Overpass
query and how its answer becomes places (from an answer shaped as Overpass
sends it), and the /v1/maps routes against a real server, with real calls
to OpenStreetMap's public servers when they answer (xfail when not)."""

from typing import Any, Dict

import httpx
import pytest

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.ExternalErrors import InvalidInputExternalError
from zephyrex.extensions.maps.EXT_Maps import (
    PLACE_CATEGORIES,
    EXT_Maps,
    PlaceCategory,
    distance_m,
    midpoint,
    place_category,
    register_place_category,
    search_radius,
)
from zephyrex.extensions.maps.PRV_OpenStreetMap import (
    PRV_OpenStreetMap_Maps,
    overpass_places,
    overpass_query,
)
from zephyrex.testing.factories import create_user

EDMONTON = (53.5461, -113.4938)
CALGARY = (51.0447, -114.0719)

# Part of an Overpass answer: a node, a way with its centre, an unnamed node
# and a node seen twice (two filters matched it).
OVERPASS_ANSWER: Dict[str, Any] = {
    "elements": [
        {
            "type": "node",
            "id": 101,
            "lat": 53.5444,
            "lon": -113.4909,
            "tags": {
                "name": "Downtown Sexual Health Clinic",
                "healthcare": "clinic",
                "healthcare:speciality": "sexual_health;family_planning",
                "addr:housenumber": "10101",
                "addr:street": "Jasper Avenue",
                "addr:city": "Edmonton",
                "website": "https://clinic.example.org",
                "opening_hours": "Mo-Fr 09:00-17:00",
            },
        },
        {
            "type": "way",
            "id": 202,
            "center": {"lat": 53.5232, "lon": -113.5263},
            "tags": {
                "name": "University Health Centre",
                "contact:phone": "+1 780 555 0100",
            },
        },
        {"type": "node", "id": 303, "lat": 53.55, "lon": -113.49, "tags": {}},
        {
            "type": "node",
            "id": 101,
            "lat": 53.5444,
            "lon": -113.4909,
            "tags": {"name": "Downtown Sexual Health Clinic"},
        },
    ]
}


def _online(url: str) -> bool:
    try:
        httpx.head(url, timeout=5)
        return True
    except httpx.HTTPError:
        return False


osm_online = pytest.mark.xfail(
    not _online("https://nominatim.openstreetmap.org"),
    reason="OpenStreetMap's servers are unreachable",
)


class TestGlobe:
    def test_distance_between_two_cities(self):
        assert distance_m(EDMONTON, CALGARY) == pytest.approx(281_000, rel=0.01)

    def test_a_point_is_no_distance_from_itself(self):
        assert distance_m(EDMONTON, EDMONTON) == 0

    def test_the_midpoint_of_two_cities_is_between_them(self):
        lat, lon = midpoint([EDMONTON, CALGARY])
        assert distance_m((lat, lon), EDMONTON) == pytest.approx(
            distance_m((lat, lon), CALGARY), rel=0.001
        )

    def test_the_midpoint_holds_across_the_antimeridian(self):
        """An average of longitudes would put it at 0°, the far side of the world."""
        lat, lon = midpoint([(0.0, 179.0), (0.0, -179.0)])
        assert lat == pytest.approx(0.0, abs=1e-6)
        assert abs(lon) == pytest.approx(180.0, abs=1e-6)

    def test_opposite_points_have_no_midpoint(self):
        with pytest.raises(InvalidInputExternalError):
            midpoint([(0.0, 0.0), (0.0, 180.0)])

    def test_no_points_have_no_midpoint(self):
        with pytest.raises(InvalidInputExternalError):
            midpoint([])

    @pytest.mark.parametrize("radius", [0, -5, 50_001])
    def test_a_radius_out_of_range_is_refused(self, radius):
        with pytest.raises(InvalidInputExternalError):
            search_radius(radius)


class TestCategories:
    def test_clinics_and_date_ideas_are_offered(self):
        assert {"sexual_health", "restaurant", "cafe", "park", "cinema"} <= set(
            PLACE_CATEGORIES
        )

    def test_an_unknown_kind_of_place_is_refused(self):
        with pytest.raises(InvalidInputExternalError, match="No kind of place"):
            place_category("volcano")

    def test_an_app_adds_its_own_kind(self):
        register_place_category(
            PlaceCategory("florist", "Florists", ('["shop"="florist"]',))
        )
        try:
            assert place_category("florist").title == "Florists"
        finally:
            del PLACE_CATEGORIES["florist"]

    def test_the_sexual_health_filter_matches_whole_specialities_only(self):
        """'sti' is inside 'plastic_surgery': the filter must not find a plastic surgeon."""
        import re

        speciality = place_category("sexual_health").osm[0]
        pattern = re.search(r'~"(.*)",i\]', speciality)
        assert pattern is not None
        regex = re.compile(pattern.group(1), re.IGNORECASE)
        assert regex.search("sexual_health")
        assert regex.search("family_planning;STI")
        assert not regex.search("plastic_surgery")
        assert not regex.search("diagnostic_radiology")


class TestOverpass:
    def test_the_query_asks_for_named_places_around_the_point(self):
        query = overpass_query(place_category("cafe"), 53.5, -113.5, 2_000, 5)
        assert query.startswith("[out:json][timeout:")
        assert 'nwr["amenity"="cafe"]["name"](around:2000,53.5,-113.5);' in query
        assert query.endswith("out center 20;")

    def test_each_filter_is_its_own_clause(self):
        query = overpass_query(place_category("sexual_health"), 1.0, 2.0, 100, 1)
        assert query.count("nwr[") == len(place_category("sexual_health").osm)

    def test_the_answer_becomes_places_nearest_first(self):
        found = overpass_places(
            OVERPASS_ANSWER,
            "openstreetmap",
            place_category("sexual_health"),
            EDMONTON,
            5,
        )
        assert [p["name"] for p in found] == [
            "Downtown Sexual Health Clinic",
            "University Health Centre",
        ]
        clinic, centre = found
        assert clinic["address"] == "10101 Jasper Avenue, Edmonton"
        assert clinic["website"] == "https://clinic.example.org"
        assert clinic["opening_hours"] == "Mo-Fr 09:00-17:00"
        assert clinic["category"] == "sexual_health"
        assert clinic["id"] == "node/101"
        assert clinic["distance_m"] < centre["distance_m"]
        assert centre["phone"] == "+1 780 555 0100"
        assert centre["lat"] == pytest.approx(53.5232)

    def test_the_answer_is_cut_to_the_limit(self):
        found = overpass_places(
            OVERPASS_ANSWER, "openstreetmap", place_category("cafe"), EDMONTON, 1
        )
        assert len(found) == 1

    def test_only_openstreetmap_searches_nearby(self):
        assert "nearby_search" in PRV_OpenStreetMap_Maps._abilities
        assert {
            p.name for p in EXT_Maps.providers if "nearby_search" in p._abilities
        } == {"openstreetmap"}


class TestRoutes(ExtensionServerMixin):
    extension_class = EXT_Maps

    @pytest.fixture(scope="class")
    def headers(self, server) -> Dict[str, str]:
        return {"Authorization": f"Bearer {create_user(server).jwt}"}

    def test_signing_in_is_required(self, server):
        assert server.get("/v1/maps/categories").status_code == 401

    def test_the_kinds_of_place(self, server, headers):
        response = server.get("/v1/maps/categories", headers=headers)
        assert response.status_code == 200, response.text
        names = {c["name"] for c in response.json()["categories"]}
        assert {"sexual_health", "restaurant"} <= names

    def test_an_unknown_kind_is_refused(self, server, headers):
        response = server.get(
            "/v1/maps/nearby",
            params={"near": "53.5,-113.5", "category": "volcano"},
            headers=headers,
        )
        assert response.status_code == 422
        assert "No kind of place" in response.json()["detail"]["message"]

    def test_coordinates_off_the_globe_are_refused(self, server, headers):
        response = server.get(
            "/v1/maps/nearby",
            params={"near": "95,0", "category": "cafe"},
            headers=headers,
        )
        assert response.status_code == 422

    def test_a_midpoint_needs_two_places(self, server, headers):
        response = server.post(
            "/v1/maps/midpoint", json={"locations": ["53.5,-113.5"]}, headers=headers
        )
        assert response.status_code == 422

    def test_an_unknown_travel_mode_is_refused(self, server, headers):
        response = server.post(
            "/v1/maps/midpoint",
            json={"locations": ["53.5,-113.5", "51.0,-114.0"], "mode": "teleport"},
            headers=headers,
        )
        assert response.status_code == 422

    @osm_online
    def test_the_midpoint_of_two_cities(
        self, server, headers, provider_instance, rotation_over, monkeypatch
    ):
        osm = provider_instance(PRV_OpenStreetMap_Maps)
        monkeypatch.setattr(EXT_Maps, "_root_rotation_cache", rotation_over(osm))
        response = server.post(
            "/v1/maps/midpoint",
            json={"locations": ["53.5461,-113.4938", "51.0447,-114.0719"]},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["lat"] == pytest.approx(52.3, abs=0.1)
        assert [leg["straight_m"] for leg in body["legs"]] == pytest.approx(
            [140_500, 140_500], rel=0.01
        )
        assert all(leg["duration_s"] for leg in body["legs"])
        assert body["places"] == []


class TestSeeding(ExtensionServerMixin):
    """A fresh deployment searches with OpenStreetMap before anything is configured: its seeded
    instance joins the maps rotation though no OSM variable is set, while Google, which needs a
    key, stays out until the operator gives it one (on Cule, the clinic finder answered 503).
    """

    extension_class = EXT_Maps

    def test_openstreetmap_joins_the_rotation_on_its_defaults(
        self, server, monkeypatch
    ):
        from zephyrex.logic.BLL_Providers import (
            RotationProviderInstanceModel,
            root_instance_name,
        )

        for name in (*PRV_OpenStreetMap_Maps._env, "GOOGLE_MAPS_API_KEY"):
            monkeypatch.delenv(name, raising=False)
        links = RotationProviderInstanceModel.seed_data(server.app.state.model_registry)
        linked = {
            link["_provider_instance_name"]
            for link in links
            if link["_rotation_name"] == "Root_Maps"
        }
        assert root_instance_name(PRV_OpenStreetMap_Maps.name) in linked
        assert root_instance_name("google_maps") not in linked
