# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tesla, through the Tesla Fleet API (the Owner API is retired).

The instance's API key is a Fleet API OAuth access token (else
``TESLA_ACCESS_TOKEN``). Its ``api_url`` setting (else ``TESLA_API_URL``)
is the account's regional endpoint, North America by default; vehicles
that require signed commands (most built since 2021) take commands only
through Tesla's vehicle-command proxy (tesla-http-proxy), so point
``api_url`` at that proxy for them (a private address goes in
``EGRESS_ALLOWED_HOSTS``). A sleeping vehicle answers 408: it is woken and
the call fails over (or the caller retries) while it wakes.
"""

from typing import Any, ClassVar, Dict, Tuple

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.automotive.EXT_Automotive import AbstractAutomotiveProvider
from zephyrex.extensions.ExternalErrors import (
    InvalidInputExternalError,
    TransientExternalError,
)
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

TESLA_FLEET_API = "https://fleet-api.prd.na.vn.cloud.tesla.com"
HTTP_VEHICLE_ASLEEP = 408
# The extension's commands, as Fleet API command endpoints.
_COMMANDS = {
    "door_lock": "door_lock",
    "door_unlock": "door_unlock",
    "climate_on": "auto_conditioning_start",
    "climate_off": "auto_conditioning_stop",
    "charge_start": "charge_start",
    "charge_stop": "charge_stop",
    "navigate": "share",
}


class PRV_Tesla_Automotive(AbstractAutomotiveProvider):
    name: ClassVar[str] = "tesla"
    friendly_name: ClassVar[str] = "Tesla"
    description: ClassVar[str] = "Tesla vehicles, through the Fleet API"
    _env: ClassVar[Dict[str, Any]] = {
        "TESLA_ACCESS_TOKEN": "",
        "TESLA_API_URL": TESLA_FLEET_API,
    }
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting(
            "api_key",
            "Tesla Fleet API OAuth access token",
            env="TESLA_ACCESS_TOKEN",
            secret=True,
            field="api_key",
        ),
        InstanceSetting(
            "api_url",
            "Fleet API regional endpoint, or the vehicle-command proxy",
            env="TESLA_API_URL",
            default=TESLA_FLEET_API,
        ),
    )

    @classmethod
    def _vehicle(
        cls, instance: ProviderInstanceModel, vehicle_id: str
    ) -> Tuple[str, Dict[str, str]]:
        """``(vehicle url, headers)``."""
        if not vehicle_id.isdigit():
            raise InvalidInputExternalError(
                f"{vehicle_id!r} is not a Tesla vehicle id", provider=cls.name
            )
        token = cls.setting(instance, "api_key")
        if not token:
            raise TransientExternalError(
                "Tesla access token not configured", provider=cls.name
            )
        base = cls.setting(instance, "api_url")
        return (
            f"{(base or TESLA_FLEET_API).rstrip('/')}/api/1/vehicles/{vehicle_id}",
            {"Authorization": f"Bearer {token}"},
        )

    @classmethod
    async def _awake(cls, url: str, headers: Dict[str, str], call: Any) -> Any:
        """``call()``; a sleeping vehicle is woken and the call fails over."""
        try:
            return await call()
        except InvalidInputExternalError as exc:
            if exc.upstream_status != HTTP_VEHICLE_ASLEEP:
                raise
            await cls.http().post(f"{url}/wake_up", headers=headers)
            raise TransientExternalError(
                "The vehicle was asleep and is waking; try again shortly",
                provider=cls.name,
            ) from exc

    @classmethod
    async def get_vehicle_info(
        cls, instance: ProviderInstanceModel, vehicle_id: str
    ) -> Dict[str, Any]:
        url, headers = cls._vehicle(instance, vehicle_id)
        answer = await cls._awake(
            url, headers, lambda: cls.get_json(f"{url}/vehicle_data", headers=headers)
        )
        data = answer.get("response") or {}
        charge = data.get("charge_state") or {}
        return {
            "vehicle_id": vehicle_id,
            "name": data.get("display_name", ""),
            "state": data.get("state", ""),
            "battery_level": charge.get("battery_level"),
            "range_miles": charge.get("battery_range"),
            "charging_state": charge.get("charging_state"),
            "locked": (data.get("vehicle_state") or {}).get("locked"),
            "provider": cls.name,
        }

    @classmethod
    async def command(
        cls,
        instance: ProviderInstanceModel,
        vehicle_id: str,
        command: str,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        endpoint = _COMMANDS.get(command)
        if endpoint is None:
            raise InvalidInputExternalError(
                f"Unknown command {command!r}", provider=cls.name
            )
        url, headers = cls._vehicle(instance, vehicle_id)

        async def post(name: str, body: Dict[str, Any]) -> Dict[str, Any]:
            answer: Dict[str, Any] = await cls.http().post(
                f"{url}/command/{name}", json=body, headers=headers
            )
            result = answer.get("response") or {}
            if not result.get("result", False):
                raise InvalidInputExternalError(
                    f"Tesla refused {name}: {result.get('reason', 'no reason given')}",
                    provider=cls.name,
                )
            return result

        if command == "climate_on":
            temperature = payload["temperature"]
            await cls._awake(
                url,
                headers,
                lambda: post(
                    "set_temps",
                    {"driver_temp": temperature, "passenger_temp": temperature},
                ),
            )
        body: Dict[str, Any] = {}
        if command == "navigate":
            body = {
                "type": "share_ext_content_raw",
                "locale": "en-US",
                "timestamp_ms": "0",
                "value": {"android.intent.extra.TEXT": payload["address"]},
            }
        await cls._awake(url, headers, lambda: post(endpoint, body))
        return {
            "vehicle_id": vehicle_id,
            "command": command,
            "done": True,
            "provider": cls.name,
        }
