from typing import Any

from zephyrex.extensions.automotive.PRV_Automotive import AbstractAutomotiveProvider

TESLA_API_BASE_URL = "https://owner-api.teslamotors.com/api/1"


class TeslaProvider(AbstractAutomotiveProvider):
    """
    Automotive provider backed by the Tesla Owner API.

    A lightweight, directly-instantiated client: it holds the vehicle id and
    OAuth access token and issues synchronous HTTP requests to the Tesla
    Owner API. Requires the ``requests`` dependency (already required by the
    extension) and, for token refresh/storage, the optional ``cryptography``
    dependency.
    """

    def get_platform_name(self) -> str:
        return "Tesla"

    def _client(self) -> Any:
        import requests

        session = requests.Session()
        session.headers.update(
            {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
            }
        )
        return session

    def _vehicle_url(self, path: str) -> str:
        return f"{TESLA_API_BASE_URL}/vehicles/{self.vehicle_id}/{path}"

    def get_vehicle_info(self) -> str:
        response = self._client().get(self._vehicle_url("vehicle_data"))
        response.raise_for_status()
        data = response.json().get("response", {}) or {}

        charge_state = data.get("charge_state", {}) or {}
        display_name = data.get("display_name", "Tesla vehicle")
        battery_level = charge_state.get("battery_level")
        battery_range = charge_state.get("battery_range")

        details = [display_name]
        if battery_level is not None:
            details.append(f"Battery: {battery_level}%")
        if battery_range is not None:
            details.append(f"Range: {battery_range} miles")

        return " - ".join(details) if len(details) > 1 else details[0]

    def lock_doors(self) -> str:
        response = self._client().post(self._vehicle_url("command/door_lock"))
        response.raise_for_status()
        return "Vehicle doors locked"

    def unlock_doors(self) -> str:
        response = self._client().post(self._vehicle_url("command/door_unlock"))
        response.raise_for_status()
        return "Vehicle doors unlocked"

    def set_climate(self, temperature: float, enabled: bool = True) -> str:
        client = self._client()
        command = "auto_conditioning_start" if enabled else "auto_conditioning_stop"
        response = client.post(self._vehicle_url(f"command/{command}"))
        response.raise_for_status()
        response = client.post(
            self._vehicle_url("command/set_temps"),
            json={"driver_temp": temperature, "passenger_temp": temperature},
        )
        response.raise_for_status()
        return f"Climate set to {temperature}°C"

    def start_charging(self) -> str:
        response = self._client().post(self._vehicle_url("command/charge_start"))
        response.raise_for_status()
        return "Charging started"

    def stop_charging(self) -> str:
        response = self._client().post(self._vehicle_url("command/charge_stop"))
        response.raise_for_status()
        return "Charging stopped"

    def navigate_to(self, address: str) -> str:
        response = self._client().post(
            self._vehicle_url("command/navigation_request"),
            json={"type": "share_ext_content_raw", "locale": "en-US", "value": {"android.intent.extra.TEXT": address}},
        )
        response.raise_for_status()
        return f"Navigation set to {address}"
