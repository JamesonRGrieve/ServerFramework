from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class AbstractAutomotiveProvider(ABC):
    """
    Abstract base class for all automotive/smart-car providers used by the
    Automotive extension (currently Tesla, with room for additional
    manufacturers).

    Concrete providers are lightweight, directly-instantiated clients that
    hold their own credentials/config (api key, vehicle id, access token)
    and expose synchronous vehicle-control operations. EXT_Automotive's
    async abilities call into these methods directly (no await) so a
    provider's return values can be plain strings rather than awaitables.
    """

    def __init__(
        self,
        api_key: str = "",
        vehicle_id: str = "",
        access_token: str = "",
        extension_id: Optional[str] = None,
        conversation_directory: str = "",
        **kwargs: Any,
    ) -> None:
        self.api_key = api_key
        self.vehicle_id = vehicle_id
        self.access_token = access_token
        self.extension_id = extension_id
        self.conversation_directory = conversation_directory
        self.settings: Dict[str, Any] = kwargs

        self.commands = {
            f"Get {self.get_platform_name()} Vehicle Info": self.get_vehicle_info,
            f"Lock {self.get_platform_name()} Vehicle": self.lock_doors,
            f"Unlock {self.get_platform_name()} Vehicle": self.unlock_doors,
            f"Set {self.get_platform_name()} Climate": self.set_climate,
            f"Start {self.get_platform_name()} Charging": self.start_charging,
            f"Navigate {self.get_platform_name()} Vehicle To": self.navigate_to,
        }

    @abstractmethod
    def get_vehicle_info(self) -> str:
        """Get information about the vehicle."""

    @abstractmethod
    def lock_doors(self) -> str:
        """Lock the vehicle doors."""

    @abstractmethod
    def unlock_doors(self) -> str:
        """Unlock the vehicle doors."""

    @abstractmethod
    def set_climate(self, temperature: float, enabled: bool = True) -> str:
        """Set the climate temperature and state."""

    @abstractmethod
    def start_charging(self) -> str:
        """Start vehicle charging."""

    @abstractmethod
    def stop_charging(self) -> str:
        """Stop vehicle charging."""

    @abstractmethod
    def navigate_to(self, address: str) -> str:
        """Navigate to an address."""

    @abstractmethod
    def get_platform_name(self) -> str:
        """Get the name of the vehicle platform this provider interacts with."""

    @staticmethod
    def services() -> List[str]:
        """Return a list of services provided by this provider."""
        return ["automotive", "vehicle", "smart_car"]

    def get_extension_info(self) -> Dict[str, Any]:
        """Get information about the automotive extension."""
        return {
            "name": "Automotive",
            "description": f"Automotive extension for {self.get_platform_name()} vehicles",
            "vehicle_id": self.vehicle_id,
        }
