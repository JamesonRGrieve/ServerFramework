# SPDX-License-Identifier: AGPL-3.0-or-later
"""The wearable extension: inputs held to what Fitbit accepts, the trend
summary, configuration, and real calls to Fitbit (a refused token needs
only the network; data needs a test account's token)."""

import httpx
import pytest

from zephyrex.extensions.ExternalErrors import (
    AuthExternalError,
    InvalidInputExternalError,
    TransientExternalError,
)
from zephyrex.extensions.wearable.EXT_Wearable import (
    EXT_Wearable,
    checked_date,
    checked_metric,
    checked_period,
    trend,
)
from zephyrex.extensions.wearable.PRV_FitBit import PRV_FitBit_Wearable


def _online(url: str) -> bool:
    try:
        httpx.head(url, timeout=5)
        return True
    except httpx.HTTPError:
        return False


class TestInputs:
    @pytest.mark.parametrize("date", ["today", "2026-09-30"])
    def test_dates(self, date):
        assert checked_date(date) == date

    @pytest.mark.parametrize("date", ["yesterday", "2026/09/30", "../x", ""])
    def test_a_date_fitbit_cannot_take_is_refused(self, date):
        with pytest.raises(InvalidInputExternalError):
            checked_date(date)

    def test_metrics_and_periods(self):
        assert checked_metric("steps") == "steps"
        assert checked_period("30d") == "30d"
        with pytest.raises(InvalidInputExternalError):
            checked_metric("steps/../../profile")
        with pytest.raises(InvalidInputExternalError):
            checked_period("1y")

    async def test_heart_and_sleep_are_not_trended(self):
        with pytest.raises(InvalidInputExternalError, match="read it by day"):
            await EXT_Wearable.analyze_trends("sleep")


class TestTrend:
    def test_summary(self):
        summary = trend([1.0, 2.0, 3.0, 6.0])
        assert summary == {
            "count": 4,
            "min": 1.0,
            "max": 6.0,
            "mean": 3.0,
            "change": 3.0,  # (3+6)/2 - (1+2)/2
        }

    def test_an_empty_series(self):
        assert trend([]) == {"count": 0}

    def test_a_single_point(self):
        assert trend([5.0])["change"] == 0.0


class TestFitbit:
    def test_providers(self):
        assert {p.name for p in EXT_Wearable.providers} == {"fitbit"}

    async def test_without_a_token_it_fails_over(self, provider_instance, set_env):
        set_env("FITBIT_ACCESS_TOKEN", "")
        with pytest.raises(TransientExternalError, match="access token"):
            await PRV_FitBit_Wearable.get_devices(
                provider_instance(PRV_FitBit_Wearable)
            )

    @pytest.mark.xfail(
        not _online("https://api.fitbit.com"), reason="Fitbit unreachable"
    )
    async def test_a_refused_token_is_an_auth_error(self, provider_instance):
        instance = provider_instance(PRV_FitBit_Wearable, api_key="not-a-real-token")
        with pytest.raises(AuthExternalError):
            await PRV_FitBit_Wearable.get_devices(instance)

    @pytest.mark.external_api(provider="fitbit")
    async def test_live(self, provider_instance, sandbox_credentials_for):
        token = sandbox_credentials_for("fitbit")["FITBIT_ACCESS_TOKEN"]
        instance = provider_instance(PRV_FitBit_Wearable, api_key=token)
        steps = await PRV_FitBit_Wearable.get_health_data(instance, "steps", "today")
        assert steps["metric"] == "steps"
        series = await PRV_FitBit_Wearable.get_series(instance, "steps", "7d")
        assert len(series) == 7
        assert isinstance(await PRV_FitBit_Wearable.get_devices(instance), list)
