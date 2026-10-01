from typing import Any, Dict, List

from zephyrex.extensions.wearable.PRV_Wearable import AbstractWearableProvider

FITBIT_API_BASE_URL = "https://api.fitbit.com/1/user/-"


class FitBitProvider(AbstractWearableProvider):
    """
    Wearable provider backed by the FitBit Web API.

    A lightweight, directly-instantiated client: it holds the api key
    (OAuth access token) and issues synchronous HTTP requests to the FitBit
    Web API. Requires the ``requests`` dependency (already required by the
    extension).
    """

    def get_platform_name(self) -> str:
        return "FitBit"

    @staticmethod
    def services() -> List[str]:
        return ["wearable", "health", "activity", "sleep"]

    def _client(self) -> Any:
        import requests

        session = requests.Session()
        session.headers.update(
            {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
        )
        return session

    def get_health_data(
        self, device_type: str = "all", data_type: str = "steps", period: str = "today"
    ) -> Dict[str, Any]:
        response = self._client().get(
            f"{FITBIT_API_BASE_URL}/activities/{data_type}/date/{period}/1d.json"
        )
        response.raise_for_status()
        result: Dict[str, Any] = response.json()
        return result

    def sync_devices(self) -> str:
        response = self._client().get(f"{FITBIT_API_BASE_URL}/devices.json")
        response.raise_for_status()
        return "FitBit devices synchronized"

    def get_device_status(self, device_id: str = "") -> Dict[str, Any]:
        response = self._client().get(f"{FITBIT_API_BASE_URL}/devices/{device_id}.json")
        response.raise_for_status()
        result: Dict[str, Any] = response.json()
        return result

    def analyze_trends(
        self, metric: str = "steps", period: str = "week"
    ) -> Dict[str, Any]:
        response = self._client().get(
            f"{FITBIT_API_BASE_URL}/activities/{metric}/date/today/{period}.json"
        )
        response.raise_for_status()
        result: Dict[str, Any] = response.json()
        return result
