import logging
from typing import Any, Dict, Optional

from zephyrex.extensions.sms.PRV_SMS import AbstractSMS

try:
    import boto3
except ImportError:  # pragma: no cover - exercised via AmazonSNSProvider tests
    boto3 = None


class AmazonSNSProvider(AbstractSMS):
    """
    SMS provider backed by Amazon SNS.

    Requires the optional ``boto3`` dependency declared on
    ``EXT_SMS.pip_dependencies``. When the library is not installed the
    provider still constructs successfully but ``send_sms`` reports a clear
    error instead of raising an ``ImportError`` at construction time.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)

        aws_region = self.settings.get("aws_region", "us-east-1")
        aws_access_key = self.settings.get("aws_access_key", self.api_key)
        aws_secret_key = self.settings.get("aws_secret_key", "")
        self.sender_id = self.settings.get("sender_id", "NOTIFY")
        self.sns_client: Optional[Any] = None

        if boto3 is None:
            logging.warning("boto3 library not installed; AmazonSNSProvider is inert")
            return

        try:
            self.sns_client = boto3.client(
                "sns",
                region_name=aws_region,
                aws_access_key_id=aws_access_key,
                aws_secret_access_key=aws_secret_key,
            )
            logging.info("Amazon SNS client initialized")
        except Exception as e:
            logging.error(f"Failed to initialize Amazon SNS client: {str(e)}")
            self.sns_client = None

    def get_platform_name(self) -> str:
        return "Amazon SNS"

    def send_sms(self, phone_number: str, message: str) -> Dict[str, Any]:
        if not self.sns_client:
            return {"success": False, "error": "SNS client not initialized"}

        try:
            response = self.sns_client.publish(
                PhoneNumber=phone_number,
                Message=message,
                MessageAttributes={
                    "AWS.SNS.SMS.SenderID": {
                        "DataType": "String",
                        "StringValue": self.sender_id,
                    }
                },
            )

            return {
                "success": True,
                "message_id": response.get("MessageId", ""),
                "provider": "Amazon SNS",
            }
        except Exception as e:
            return {"success": False, "error": str(e), "provider": "Amazon SNS"}
