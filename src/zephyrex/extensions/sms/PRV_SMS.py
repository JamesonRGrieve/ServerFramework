from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class AbstractSMS(ABC):
    """
    Abstract base class for all SMS providers used by the SMS extension
    (currently Twilio and Amazon SNS).

    Mirrors AbstractAutomotiveProvider / AbstractMessagingProvider: concrete
    providers are lightweight, directly-instantiated clients that hold their
    own credentials/config (api key, extension id) and expose a synchronous
    ``send_sms`` operation. EXT_SMS's async abilities call into these
    methods directly (no await) so a provider's return values can be plain
    dicts rather than awaitables.
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
            f"Send {self.get_platform_name()} SMS": self.send_sms,
        }

    @abstractmethod
    def send_sms(self, phone_number: str, message: str) -> Dict[str, Any]:
        """Send an SMS message to a phone number."""

    @abstractmethod
    def get_platform_name(self) -> str:
        """Get the name of the SMS platform this provider interacts with."""

    @staticmethod
    def services() -> List[str]:
        """Return a list of services provided by this provider."""
        return ["sms"]

    def get_extension_info(self) -> Dict[str, Any]:
        """Get information about the SMS extension/provider."""
        return {
            "name": "SMS",
            "description": f"SMS extension for {self.get_platform_name()}",
        }
