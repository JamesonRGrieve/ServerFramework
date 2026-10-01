# SPDX-License-Identifier: AGPL-3.0-or-later
"""The automotive extension: every action names its vehicle, inputs are
held to what a vehicle accepts, and Tesla's Fleet API is called for real
(refused credentials need only the network; a vehicle needs a test
account)."""

import httpx
import pytest

from zephyrex.extensions.automotive.EXT_Automotive import (
    EXT_Automotive,
    climate_temperature,
)
from zephyrex.extensions.automotive.PRV_Tesla import PRV_Tesla_Automotive
from zephyrex.extensions.ExternalErrors import (
    AuthExternalError,
    InvalidInputExternalError,
    TransientExternalError,
)


def _online(url: str) -> bool:
    try:
        httpx.head(url, timeout=5)
        return True
    except httpx.HTTPError:
        return False


class TestExtension:
    def test_providers(self):
        assert {p.name for p in EXT_Automotive.providers} == {"tesla"}

    @pytest.mark.parametrize("celsius", [14.9, 28.5, -5])
    def test_a_cabin_temperature_out_of_range_is_refused(self, celsius):
        with pytest.raises(InvalidInputExternalError):
            climate_temperature(celsius)

    async def test_a_destination_needs_an_address(self):
        with pytest.raises(InvalidInputExternalError):
            await EXT_Automotive.navigate_to("1", "  ")

    async def test_set_climate_checks_the_temperature_first(self):
        with pytest.raises(InvalidInputExternalError, match="°C"):
            await EXT_Automotive.set_climate("1", 40.0)


class TestTesla:
    @pytest.mark.parametrize("vehicle", ["abc", "1/../2", ""])
    async def test_a_vehicle_id_is_a_number(self, provider_instance, vehicle):
        instance = provider_instance(PRV_Tesla_Automotive, api_key="token")
        with pytest.raises(InvalidInputExternalError, match="vehicle id"):
            await PRV_Tesla_Automotive.get_vehicle_info(instance, vehicle)

    async def test_without_a_token_it_fails_over(self, provider_instance, set_env):
        set_env("TESLA_ACCESS_TOKEN", "")
        with pytest.raises(TransientExternalError, match="access token"):
            await PRV_Tesla_Automotive.get_vehicle_info(
                provider_instance(PRV_Tesla_Automotive), "1"
            )

    async def test_an_unknown_command_is_refused(self, provider_instance):
        instance = provider_instance(PRV_Tesla_Automotive, api_key="token")
        with pytest.raises(InvalidInputExternalError, match="Unknown command"):
            await PRV_Tesla_Automotive.command(instance, "1", "self_destruct", {})

    @pytest.mark.xfail(
        not _online("https://fleet-api.prd.na.vn.cloud.tesla.com"),
        reason="Tesla Fleet API unreachable",
    )
    async def test_a_refused_token_is_an_auth_error(self, provider_instance):
        instance = provider_instance(PRV_Tesla_Automotive, api_key="not-a-real-token")
        with pytest.raises(AuthExternalError):
            await PRV_Tesla_Automotive.get_vehicle_info(instance, "1")
