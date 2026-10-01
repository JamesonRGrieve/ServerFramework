# SPDX-License-Identifier: AGPL-3.0-or-later
"""SMS through Amazon SNS.

The instance's API key is the AWS access key id (else
``AWS_ACCESS_KEY_ID``); its ``aws_secret_key``, ``aws_region`` and
``sender_id`` settings fall back to ``AWS_SECRET_ACCESS_KEY``,
``AWS_REGION`` (``us-east-1``) and ``SNS_SMS_SENDER_ID``. SNS has no
per-message status: delivery is reported through its CloudWatch
delivery-status logs.
"""

import asyncio
from typing import Any, ClassVar, Dict, Tuple

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.ExternalErrors import (
    AuthExternalError,
    InvalidInputExternalError,
    PermanentExternalError,
    TransientExternalError,
)
from zephyrex.extensions.sms.EXT_SMS import AbstractSMSProvider
from zephyrex.lib.Dependencies import Dependencies, PIP_Dependency, importable
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

DEFAULT_REGION = "us-east-1"
CONNECT_TIMEOUT_SECONDS = 10
# A sender id is 1-11 alphanumeric characters (AWS's limit).
SENDER_ID_MAX = 11
_AUTH_ERRORS = {
    "InvalidClientTokenId",
    "SignatureDoesNotMatch",
    "AuthorizationError",
    "UnrecognizedClientException",
}
_INPUT_ERRORS = {"InvalidParameter", "InvalidParameterValue", "OptedOut"}


class PRV_Amazon_SMS(AbstractSMSProvider):
    name: ClassVar[str] = "amazon_sns"
    friendly_name: ClassVar[str] = "Amazon SNS"
    description: ClassVar[str] = "Amazon Simple Notification Service SMS"
    _env: ClassVar[Dict[str, Any]] = {
        "AWS_ACCESS_KEY_ID": "",
        "AWS_SECRET_ACCESS_KEY": "",
        "AWS_REGION": DEFAULT_REGION,
        "SNS_SMS_SENDER_ID": "",
    }
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting(
            "api_key",
            "AWS access key id",
            env="AWS_ACCESS_KEY_ID",
            secret=True,
            field="api_key",
        ),
        InstanceSetting(
            "aws_region", "AWS region", env="AWS_REGION", default=DEFAULT_REGION
        ),
        InstanceSetting(
            "aws_secret_key",
            "AWS secret access key",
            env="AWS_SECRET_ACCESS_KEY",
            secret=True,
        ),
        InstanceSetting(
            "sender_id",
            "SMS sender id (1-11 letters or digits)",
            env="SNS_SMS_SENDER_ID",
        ),
    )
    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            PIP_Dependency(
                name="boto3",
                friendly_name="AWS SDK for Python",
                semver=">=1.26.0",
                reason="Amazon SNS SMS provider",
            ),
            PIP_Dependency(
                name="botocore",
                friendly_name="AWS SDK core",
                semver=">=1.29.0",
                reason="The AWS client errors the provider types",
            ),
        ]
    )

    @classmethod
    def _client(cls, instance: ProviderInstanceModel) -> Any:
        if not importable("boto3"):
            raise TransientExternalError(
                "boto3 package not installed", provider=cls.name
            )
        key_id = cls.setting(instance, "api_key")
        secret = cls.setting(instance, "aws_secret_key")
        if not (key_id and secret):
            raise TransientExternalError(
                "AWS credentials not configured", provider=cls.name
            )
        import boto3
        from botocore.config import Config

        return boto3.client(
            "sns",
            region_name=cls.setting(instance, "aws_region"),
            aws_access_key_id=key_id,
            aws_secret_access_key=secret,
            # The rotation retries and fails over, not the SDK.
            config=Config(
                retries={"max_attempts": 1}, connect_timeout=CONNECT_TIMEOUT_SECONDS
            ),
        )

    @classmethod
    async def send_sms(
        cls, instance: ProviderInstanceModel, phone_number: str, message: str
    ) -> Dict[str, Any]:
        from botocore.exceptions import BotoCoreError, ClientError

        attributes: Dict[str, Any] = {}
        sender = cls.setting(instance, "sender_id")
        if sender:
            if not (sender.isalnum() and len(sender) <= SENDER_ID_MAX):
                raise InvalidInputExternalError(
                    f"SNS sender_id {sender!r} must be 1-{SENDER_ID_MAX} letters or digits",
                    provider=cls.name,
                )
            attributes["AWS.SNS.SMS.SenderID"] = {
                "DataType": "String",
                "StringValue": sender,
            }
        client = cls._client(instance)
        try:
            response = await asyncio.to_thread(
                client.publish,
                PhoneNumber=phone_number,
                Message=message,
                MessageAttributes=attributes,
            )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            detail = exc.response.get("Error", {}).get("Message", code)
            if code in _AUTH_ERRORS:
                raise AuthExternalError(detail, provider=cls.name) from exc
            if code in _INPUT_ERRORS:
                raise InvalidInputExternalError(detail, provider=cls.name) from exc
            raise TransientExternalError(detail, provider=cls.name) from exc
        except BotoCoreError as exc:
            raise TransientExternalError(str(exc), provider=cls.name) from exc
        return {"message_id": response.get("MessageId", ""), "provider": cls.name}

    @classmethod
    async def get_sms_status(
        cls, instance: ProviderInstanceModel, message_id: str
    ) -> Dict[str, Any]:
        raise PermanentExternalError(
            "Amazon SNS has no per-message status; enable SNS SMS delivery-status "
            "logging to CloudWatch",
            provider=cls.name,
        )
