# SPDX-License-Identifier: AGPL-3.0-or-later
"""The maps extension: coordinate and mode validation, OSRM's step
wording, Apple's Maps token, each provider's configuration, real calls to
OpenStreetMap's public servers, Google and Apple refusing credentials,
and a refused provider failing over to the next (the network calls xfail
when the service is unreachable)."""

import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from zephyrex.extensions.ExternalErrors import (
    AuthExternalError,
    InvalidInputExternalError,
    TransientExternalError,
)
from zephyrex.extensions.maps.EXT_Maps import (
    MAX_RESULTS,
    EXT_Maps,
    parse_coordinates,
    result_limit,
)
from zephyrex.extensions.maps.PRV_Apple import PRV_Apple_Maps, maps_token
from zephyrex.extensions.maps.PRV_Google import PRV_Google_Maps, seconds
from zephyrex.extensions.maps.PRV_OpenStreetMap import (
    PRV_OpenStreetMap_Maps,
    osrm_instruction,
)

# The Louvre and the Eiffel Tower.
LOUVRE = "48.8606,2.3376"
EIFFEL = "48.8584,2.2945"


def _online(url: str) -> bool:
    try:
        httpx.head(url, timeout=5)
        return True
    except httpx.HTTPError:
        return False


def _pem(key: ec.EllipticCurvePrivateKey) -> str:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


osm_online = pytest.mark.xfail(
    not _online("https://nominatim.openstreetmap.org"),
    reason="OpenStreetMap's servers are unreachable",
)


class TestExtension:
    def test_providers(self):
        assert {p.name for p in EXT_Maps.providers} == {
            "openstreetmap",
            "google_maps",
            "apple_maps",
        }

    def test_abilities(self):
        assert {
            "search_location",
            "geocode",
            "reverse_geocode",
            "get_directions",
        } <= set(EXT_Maps.get_abilities())

    async def test_an_unknown_mode_is_refused_before_any_provider(self):
        with pytest.raises(InvalidInputExternalError):
            await EXT_Maps.get_directions(LOUVRE, EIFFEL, mode="teleport")

    async def test_coordinates_off_the_globe_are_refused(self):
        with pytest.raises(InvalidInputExternalError):
            await EXT_Maps.reverse_geocode(91.0, 0.0)


class TestHelpers:
    @pytest.mark.parametrize(
        "text, pair",
        [
            ("48.8606,2.3376", (48.8606, 2.3376)),
            (" -33.86 , 151.21 ", (-33.86, 151.21)),
            ("0,0", (0.0, 0.0)),
        ],
    )
    def test_coordinates(self, text, pair):
        assert parse_coordinates(text) == pair

    @pytest.mark.parametrize("text", ["Paris, France", "1600 Pennsylvania Ave", ""])
    def test_an_address_is_not_coordinates(self, text):
        assert parse_coordinates(text) is None

    @pytest.mark.parametrize("text", ["91,0", "0,181", "-90.5,10"])
    def test_coordinates_off_the_globe(self, text):
        with pytest.raises(InvalidInputExternalError):
            parse_coordinates(text)

    @pytest.mark.parametrize("limit", [0, -1, MAX_RESULTS + 1])
    def test_a_limit_out_of_range_is_refused(self, limit):
        with pytest.raises(InvalidInputExternalError):
            result_limit(limit)

    def test_google_durations(self):
        assert seconds("125s") == 125.0
        assert seconds("0.5s") == 0.5
        assert seconds("s") == 0.0

    @pytest.mark.parametrize(
        "maneuver, road, words",
        [
            ({"type": "depart", "modifier": "north"}, "Rue X", "Head north onto Rue X"),
            ({"type": "depart"}, "", "Head"),
            ({"type": "turn", "modifier": "left"}, "Main St", "Turn left onto Main St"),
            (
                {"type": "roundabout", "exit": 2},
                "A1",
                "Enter the roundabout and take exit 2 onto A1",
            ),
            ({"type": "arrive", "modifier": "right"}, "", "Arrive at your destination"),
            (
                {"type": "new name", "modifier": "straight"},
                "B",
                "Continue straight onto B",
            ),
        ],
    )
    def test_osrm_instruction(self, maneuver, road, words):
        assert osrm_instruction(maneuver, road) == words

    def test_a_mode_the_provider_lacks_is_refused(self):
        with pytest.raises(InvalidInputExternalError):
            PRV_OpenStreetMap_Maps.mode("transit")
        with pytest.raises(InvalidInputExternalError):
            PRV_Apple_Maps.mode("transit")
        assert PRV_Google_Maps.mode("transit") == "TRANSIT"


class TestAppleToken:
    def test_the_maps_token_is_signed_for_the_server_api(self):
        key = ec.generate_private_key(ec.SECP256R1())
        now = time.time()
        token = maps_token("TEAM123456", "KEY1234567", _pem(key), now)
        assert jwt.get_unverified_header(token) == {
            "alg": "ES256",
            "kid": "KEY1234567",
            "typ": "JWT",
        }
        claims = jwt.decode(token, key.public_key(), algorithms=["ES256"])
        assert claims["iss"] == "TEAM123456"
        assert claims["scope"] == "server_api"
        assert claims["iat"] == int(now) and claims["exp"] > claims["iat"]

    def test_an_unusable_key_is_an_auth_failure(self):
        """It fails over to the next provider rather than surfacing."""
        with pytest.raises(AuthExternalError):
            maps_token("TEAM123456", "KEY1234567", "not a key", time.time())


class TestConfiguration:
    def test_osm_defaults_route_each_mode_on_its_own_graph(self, provider_instance):
        instance = provider_instance(PRV_OpenStreetMap_Maps)
        urls = {
            mode: PRV_OpenStreetMap_Maps._url(
                instance, PRV_OpenStreetMap_Maps.mode(mode)
            )
            for mode in ("driving", "walking", "bicycling")
        }
        assert len(set(urls.values())) == 3

    def test_osm_servers_from_the_instance(self, provider_instance):
        instance = provider_instance(
            PRV_OpenStreetMap_Maps,
            settings={"nominatim_url": "https://nominatim.example.org/"},
        )
        assert (
            PRV_OpenStreetMap_Maps._url(instance, "nominatim_url")
            == "https://nominatim.example.org"
        )

    async def test_google_without_a_key(self, provider_instance, set_env):
        set_env("GOOGLE_MAPS_API_KEY", "")
        with pytest.raises(TransientExternalError):
            await PRV_Google_Maps.geocode(provider_instance(PRV_Google_Maps), "Paris")

    async def test_apple_without_credentials(self, provider_instance, set_env):
        for name in (
            "APPLE_MAPS_TEAM_ID",
            "APPLE_MAPS_KEY_ID",
            "APPLE_MAPS_PRIVATE_KEY",
        ):
            set_env(name, "")
        with pytest.raises(TransientExternalError):
            await PRV_Apple_Maps.geocode(provider_instance(PRV_Apple_Maps), "Paris")

    def test_secrets_are_declared_write_only(self):
        assert PRV_Google_Maps.instance_setting("api_key").secret
        assert PRV_Apple_Maps.instance_setting("private_key").secret
        assert not PRV_Apple_Maps.instance_setting("team_id").secret


@osm_online
class TestOpenStreetMap:
    async def test_geocode(self, provider_instance):
        found = await PRV_OpenStreetMap_Maps.geocode(
            provider_instance(PRV_OpenStreetMap_Maps), "Eiffel Tower, Paris"
        )
        assert found is not None
        assert found["lat"] == pytest.approx(48.858, abs=0.01)
        assert found["lon"] == pytest.approx(2.294, abs=0.01)
        assert found["provider"] == "openstreetmap"

    async def test_reverse_geocode(self, provider_instance):
        found = await PRV_OpenStreetMap_Maps.reverse_geocode(
            provider_instance(PRV_OpenStreetMap_Maps), 48.8584, 2.2945
        )
        assert found is not None and "Paris" in found["address"]

    async def test_open_ocean_has_no_address(self, provider_instance):
        assert (
            await PRV_OpenStreetMap_Maps.reverse_geocode(
                provider_instance(PRV_OpenStreetMap_Maps), 0.0, -30.0
            )
            is None
        )

    @pytest.mark.parametrize("mode", ["driving", "walking"])
    async def test_directions(self, provider_instance, mode):
        route = await PRV_OpenStreetMap_Maps.directions(
            provider_instance(PRV_OpenStreetMap_Maps), LOUVRE, EIFFEL, mode
        )
        # About 4 km apart; walking takes far longer than driving.
        assert 2_000 < route["distance_m"] < 10_000
        assert route["steps"] and route["steps"][-1]["instruction"].startswith("Arrive")
        assert route["path"][0] == pytest.approx([48.8606, 2.3376], abs=0.01)
        if mode == "walking":
            assert route["duration_s"] > 30 * 60


class TestRefusedCredentials:
    @pytest.mark.xfail(
        not _online("https://maps.googleapis.com"), reason="Google is unreachable"
    )
    async def test_google_geocoding_refuses_a_bad_key(self, provider_instance):
        instance = provider_instance(PRV_Google_Maps, api_key="not-a-real-key")
        with pytest.raises(AuthExternalError):
            await PRV_Google_Maps.geocode(instance, "Paris")

    @pytest.mark.xfail(
        not _online("https://routes.googleapis.com"), reason="Google is unreachable"
    )
    async def test_google_routes_refuses_a_bad_key(self, provider_instance):
        instance = provider_instance(PRV_Google_Maps, api_key="not-a-real-key")
        with pytest.raises(AuthExternalError):
            await PRV_Google_Maps.directions(instance, LOUVRE, EIFFEL, "driving")

    @pytest.mark.xfail(
        not _online("https://maps-api.apple.com"), reason="Apple is unreachable"
    )
    async def test_apple_refuses_an_unknown_key(self, provider_instance):
        instance = provider_instance(
            PRV_Apple_Maps,
            settings={
                "team_id": "TEAM123456",
                "key_id": "KEY1234567",
                "private_key": _pem(ec.generate_private_key(ec.SECP256R1())),
            },
        )
        with pytest.raises(AuthExternalError):
            await PRV_Apple_Maps.geocode(instance, "Paris")


@osm_online
class TestRotation:
    async def test_a_refused_provider_fails_over(
        self, provider_instance, rotation_over, monkeypatch
    ):
        refused = provider_instance(PRV_Google_Maps, api_key="not-a-real-key")
        osm = provider_instance(PRV_OpenStreetMap_Maps)
        monkeypatch.setattr(
            EXT_Maps, "_root_rotation_cache", rotation_over(refused, osm)
        )
        found = await EXT_Maps.geocode("Eiffel Tower, Paris")
        assert found is not None and found["provider"] == "openstreetmap"
