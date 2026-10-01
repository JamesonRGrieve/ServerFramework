from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class AbstractWearableProvider(ABC):
    """
    Abstract base class for all wearable-device providers used by the
    Wearable extension (currently Apple Health and FitBit).

    Mirrors AbstractAutomotiveProvider / AbstractSMS: concrete providers are
    lightweight, directly-instantiated clients that hold their own
    credentials/config (api key, extension id) and expose synchronous
    health-data operations. EXT_Wearable's async abilities call into these
    methods directly (no await) so a provider's return values can be plain
    dicts/strings rather than awaitables.
    """

    def __init__(
        self,
        api_key: str = "",
        extension_id: Optional[str] = None,
        conversation_directory: str = "",
        **kwargs: Any,
    ) -> None:
        self.api_key = api_key
        self.extension_id = extension_id
        self.conversation_directory = conversation_directory
        self.settings: Dict[str, Any] = kwargs

        self.commands = {
            f"Get {self.get_platform_name()} Health Data": self.get_health_data,
            f"Sync {self.get_platform_name()} Devices": self.sync_devices,
            f"Get {self.get_platform_name()} Device Status": self.get_device_status,
            f"Analyze {self.get_platform_name()} Fitness Trends": self.analyze_trends,
        }

    @abstractmethod
    def get_health_data(
        self, device_type: str = "all", data_type: str = "steps", period: str = "today"
    ) -> Dict[str, Any]:
        """Fetch health data from the wearable device."""

    @abstractmethod
    def sync_devices(self) -> str:
        """Synchronize data from all connected wearable devices."""

    @abstractmethod
    def get_device_status(self, device_id: str = "") -> Dict[str, Any]:
        """Get status and information about a connected wearable device."""

    @abstractmethod
    def analyze_trends(
        self, metric: str = "steps", period: str = "week"
    ) -> Dict[str, Any]:
        """Analyze fitness trends from wearable data."""

    @abstractmethod
    def get_platform_name(self) -> str:
        """Get the name of the wearable platform this provider interacts with."""

    @staticmethod
    def services() -> List[str]:
        """Return a list of services provided by this provider."""
        return ["wearable"]

    def get_extension_info(self) -> Dict[str, Any]:
        """Get information about the wearable extension/provider."""
        return {
            "name": "Wearable",
            "description": f"Wearable device integration provider for {self.get_platform_name()}",
        }
