import logging
from typing import Any, Dict, Optional

from zephyrex.extensions.sms.PRV_SMS import AbstractSMS

try:
    from twilio.rest import Client
except ImportError:  # pragma: no cover - exercised via TwilioProvider tests
    Client = None


class TwilioProvider(AbstractSMS):
    """
    SMS provider backed by the Twilio Programmable Messaging API.

    Requires the optional ``twilio`` dependency declared on
    ``EXT_SMS.pip_dependencies``. When the library is not installed the
    provider still constructs successfully but ``send_sms`` reports a clear
    error instead of raising an ``ImportError`` at construction time.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)

        account_sid = self.settings.get("account_sid", "")
        self.from_number = self.settings.get("from_number", "")
        self.client: Optional[Any] = None

        if Client is None:
            logging.warning("Twilio library not installed; TwilioProvider is inert")
            return

        try:
            self.client = Client(account_sid, self.api_key)
            logging.info("Twilio client initialized")
        except Exception as e:
            logging.error(f"Failed to initialize Twilio client: {str(e)}")
            self.client = None

    def get_platform_name(self) -> str:
        return "Twilio"

    def send_sms(self, phone_number: str, message: str) -> Dict[str, Any]:
        if not self.client or not self.from_number:
            return {
                "success": False,
                "error": "Twilio client not initialized or from_number not set",
            }

        try:
            message_obj = self.client.messages.create(
                body=message, from_=self.from_number, to=phone_number
            )

            return {
                "success": True,
                "message_id": message_obj.sid,
                "provider": "Twilio",
            }
        except Exception as e:
            return {"success": False, "error": str(e), "provider": "Twilio"}
