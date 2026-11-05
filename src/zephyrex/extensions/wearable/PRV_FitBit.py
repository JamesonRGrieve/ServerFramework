# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fitbit, through the Fitbit Web API for the token's own user.

The instance's API key is an OAuth 2.0 access token with the activity,
heartrate, sleep and settings scopes (else ``FITBIT_ACCESS_TOKEN``).
"""

from typing import Any, ClassVar, Dict, List, Tuple

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.ExternalErrors import TransientExternalError
from zephyrex.extensions.wearable.EXT_Wearable import AbstractWearableProvider
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

FITBIT_API = "https://api.fitbit.com"


class PRV_FitBit_Wearable(AbstractWearableProvider):
    name: ClassVar[str] = "fitbit"
    friendly_name: ClassVar[str] = "Fitbit"
    description: ClassVar[str] = "Fitbit, through the Fitbit Web API"
    _env: ClassVar[Dict[str, Any]] = {"FITBIT_ACCESS_TOKEN": ""}
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting(
            "api_key",
            "Fitbit OAuth 2.0 access token (activity, heartrate, sleep, settings)",
            env="FITBIT_ACCESS_TOKEN",
            secret=True,
            field="api_key",
        ),
    )

    @classmethod
    async def _get(cls, instance: ProviderInstanceModel, path: str) -> Any:
        token = cls.setting(instance, "api_key")
        if not token:
            raise TransientExternalError(
                "Fitbit access token not configured", provider=cls.name
            )
        return await cls.get_json(
            f"{FITBIT_API}/{path}", headers={"Authorization": f"Bearer {token}"}
        )

    @classmethod
    async def get_health_data(
        cls, instance: ProviderInstanceModel, metric: str, date: str
    ) -> Dict[str, Any]:
        if metric == "sleep":
            sleep = await cls._get(instance, f"1.2/user/-/sleep/date/{date}.json")
            summary = sleep.get("summary", {})
            return {
                "metric": metric,
                "date": date,
                "minutes_asleep": summary.get("totalMinutesAsleep"),
                "time_in_bed": summary.get("totalTimeInBed"),
                "stages": summary.get("stages", {}),
                "provider": cls.name,
            }
        data = await cls._get(
            instance, f"1/user/-/activities/{metric}/date/{date}/1d.json"
        )
        series = data.get(f"activities-{metric}", [])
        value = series[0].get("value") if series else None
        if metric == "heart" and isinstance(value, dict):
            return {
                "metric": metric,
                "date": date,
                "resting_heart_rate": value.get("restingHeartRate"),
                "zones": value.get("heartRateZones", []),
                "provider": cls.name,
            }
        return {"metric": metric, "date": date, "value": value, "provider": cls.name}

    @classmethod
    async def get_devices(cls, instance: ProviderInstanceModel) -> List[Dict[str, Any]]:
        devices = await cls._get(instance, "1/user/-/devices.json")
        return [
            {
                "id": device.get("id"),
                "type": device.get("type"),
                "model": device.get("deviceVersion"),
                "battery": device.get("battery"),
                "battery_level": device.get("batteryLevel"),
                "last_sync": device.get("lastSyncTime"),
            }
            for device in devices
        ]

    @classmethod
    async def get_series(
        cls, instance: ProviderInstanceModel, metric: str, period: str
    ) -> List[Dict[str, Any]]:
        data = await cls._get(
            instance, f"1/user/-/activities/{metric}/date/today/{period}.json"
        )
        return [
            {"date": point.get("dateTime"), "value": float(point.get("value", 0))}
            for point in data.get(f"activities-{metric}", [])
        ]
