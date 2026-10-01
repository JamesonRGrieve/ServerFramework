from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class AbstractMessagingProvider(ABC):
    """
    Abstract base class for all messaging-platform providers used by the
    Messaging extension (Discord, Slack, Telegram, WhatsApp, Signal,
    Facebook Messenger, Microsoft Teams, TeamSpeak).

    Mirrors AbstractAutomotiveProvider: concrete providers are lightweight,
    directly-instantiated clients that hold their own credentials/config
    (api key, extension id) and expose messaging operations. EXT_Messaging's
    abilities call into these methods directly (no await — the same
    synchronous-call convention automotive/ecommerce providers use) so a
    provider's return values can be plain strings/lists rather than
    awaitables.
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
            f"Send {self.get_platform_name()} Message": self.send_message,
            f"Receive {self.get_platform_name()} Messages": self.receive_messages,
        }

    @abstractmethod
    def send_message(
        self, recipient: str, message: str, platform: str = "default"
    ) -> str:
        """Send a message to a recipient on this platform."""

    @abstractmethod
    def receive_messages(self, platform: str = "default") -> List[Dict[str, Any]]:
        """Receive recent messages from this platform."""

    @abstractmethod
    def get_platform_name(self) -> str:
        """Get the name of the messaging platform this provider interacts with."""

    @staticmethod
    def services() -> List[str]:
        """Return a list of services provided by this provider."""
        return ["messaging"]

    def get_extension_info(self) -> Dict[str, Any]:
        """Get information about the messaging extension/provider."""
        return {
            "name": "Messaging",
            "description": f"Messaging extension for {self.get_platform_name()}",
        }
