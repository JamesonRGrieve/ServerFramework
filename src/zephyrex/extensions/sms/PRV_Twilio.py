# SPDX-License-Identifier: AGPL-3.0-or-later
"""SMS through Twilio Programmable Messaging.

The instance's API key is the auth token (else ``TWILIO_AUTH_TOKEN``); its
``account_sid`` and ``from_number`` settings fall back to
``TWILIO_ACCOUNT_SID`` and ``TWILIO_FROM_NUMBER``.
"""

import asyncio
from typing import Any, ClassVar, Dict, Tuple

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.ExternalErrors import (
    AuthExternalError,
    InvalidInputExternalError,
    TransientExternalError,
)
from zephyrex.extensions.sms.EXT_SMS import AbstractSMSProvider, e164
from zephyrex.lib.Dependencies import Dependencies, PIP_Dependency, importable
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

# Twilio's HTTP statuses for a bad request and for refused credentials.
_CLIENT_ERROR = 400
_UNAUTHORIZED = 401


class PRV_Twilio_SMS(AbstractSMSProvider):
    name: ClassVar[str] = "twilio"
    friendly_name: ClassVar[str] = "Twilio"
    description: ClassVar[str] = "Twilio Programmable Messaging"
    _env: ClassVar[Dict[str, Any]] = {
        "TWILIO_ACCOUNT_SID": "",
        "TWILIO_AUTH_TOKEN": "",
        "TWILIO_FROM_NUMBER": "",
    }
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting(
            "account_sid", "Twilio account SID (AC…)", env="TWILIO_ACCOUNT_SID"
        ),
        InstanceSetting(
            "api_key",
            "Twilio auth token",
            env="TWILIO_AUTH_TOKEN",
            secret=True,
            field="api_key",
        ),
        InstanceSetting(
            "from_number", "Sending number, E.164", env="TWILIO_FROM_NUMBER"
        ),
    )
    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            PIP_Dependency(
                name="twilio",
                friendly_name="Twilio Python SDK",
                semver=">=9.0.0",
                reason="Twilio SMS provider",
            )
        ]
    )

    @classmethod
    def _client(cls, instance: ProviderInstanceModel) -> Any:
        if not importable("twilio"):
            raise TransientExternalError(
                "twilio package not installed", provider=cls.name
            )
        account_sid = cls.setting(instance, "account_sid")
        token = cls.setting(instance, "api_key")
        if not (account_sid and token):
            raise TransientExternalError(
                "Twilio account_sid and auth token not configured", provider=cls.name
            )
        from twilio.rest import Client

        return Client(account_sid, token)

    @classmethod
    async def _call(cls, call: Any) -> Any:
        """A blocking Twilio call, off the event loop, its failures typed."""
        from twilio.base.exceptions import TwilioRestException

        try:
            return await asyncio.to_thread(call)
        except TwilioRestException as exc:
            if exc.status == _UNAUTHORIZED:
                raise AuthExternalError(
                    exc.msg, provider=cls.name, upstream_status=exc.status
                ) from exc
            if _CLIENT_ERROR <= exc.status < 500:
                raise InvalidInputExternalError(
                    exc.msg, provider=cls.name, upstream_status=exc.status
                ) from exc
            raise TransientExternalError(
                exc.msg, provider=cls.name, upstream_status=exc.status
            ) from exc

    @classmethod
    async def send_sms(
        cls, instance: ProviderInstanceModel, phone_number: str, message: str
    ) -> Dict[str, Any]:
        sender = cls.setting(instance, "from_number")
        if not sender:
            raise TransientExternalError(
                "Twilio from_number not configured", provider=cls.name
            )
        client = cls._client(instance)
        sent = await cls._call(
            lambda: client.messages.create(
                body=message, from_=e164(sender), to=phone_number
            )
        )
        return {"message_id": sent.sid, "status": sent.status, "provider": cls.name}

    @classmethod
    async def get_sms_status(
        cls, instance: ProviderInstanceModel, message_id: str
    ) -> Dict[str, Any]:
        client = cls._client(instance)
        found = await cls._call(lambda: client.messages(message_id).fetch())
        return {
            "message_id": found.sid,
            "status": found.status,
            "error_code": found.error_code,
            "provider": cls.name,
        }
