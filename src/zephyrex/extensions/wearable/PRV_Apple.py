from typing import Any, Dict, List

from zephyrex.extensions.wearable.PRV_Wearable import AbstractWearableProvider

APPLE_HEALTH_API_BASE_URL = "https://api.apple.com/health"


class AppleHealthProvider(AbstractWearableProvider):
    """
    Wearable provider backed by Apple Health / Apple Watch.

    A lightweight, directly-instantiated client: it holds the api key and
    issues synchronous HTTP requests to the Apple Health API. Requires the
    ``requests`` dependency (already required by the extension).
    """

    def get_platform_name(self) -> str:
        return "Apple Health"

    @staticmethod
    def services() -> List[str]:
        return ["wearable", "health", "activity"]

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
            f"{APPLE_HEALTH_API_BASE_URL}/data",
            params={"device_type": device_type, "data_type": data_type, "period": period},
        )
        response.raise_for_status()
        result: Dict[str, Any] = response.json()
        return result

    def sync_devices(self) -> str:
        response = self._client().post(f"{APPLE_HEALTH_API_BASE_URL}/sync")
        response.raise_for_status()
        return "Apple Health devices synchronized"

    def get_device_status(self, device_id: str = "") -> Dict[str, Any]:
        response = self._client().get(f"{APPLE_HEALTH_API_BASE_URL}/devices/{device_id}")
        response.raise_for_status()
        result: Dict[str, Any] = response.json()
        return result

    def analyze_trends(
        self, metric: str = "steps", period: str = "week"
    ) -> Dict[str, Any]:
        response = self._client().get(
            f"{APPLE_HEALTH_API_BASE_URL}/trends",
            params={"metric": metric, "period": period},
        )
        response.raise_for_status()
        result: Dict[str, Any] = response.json()
        return result
