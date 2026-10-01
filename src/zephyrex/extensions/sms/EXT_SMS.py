# SPDX-License-Identifier: AGPL-3.0-or-later
"""SMS: send a message, send to many, and check a message's delivery status,
through Twilio or Amazon SNS under the provider rotation.

Each provider instance carries its own credentials and sender, falling back
to the environment. A delivery status is Twilio's alone: Amazon SNS reports
delivery only through its CloudWatch delivery-status logs.
"""

import re
from abc import abstractmethod
from typing import Any, ClassVar, Dict, List, Set

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractProviderInstance,
    AbstractStaticExtension,
    AbstractStaticProvider,
    ability,
)
from zephyrex.extensions.ExternalErrors import (
    BaseExternalError,
    InvalidInputExternalError,
)
from zephyrex.lib.Dependencies import Dependencies
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

# E.164: a plus, a non-zero country code digit, then up to 14 more digits.
_E164 = re.compile(r"^\+[1-9]\d{1,14}$")


def e164(phone_number: str) -> str:
    """``phone_number`` in E.164 form, refused otherwise."""
    number = re.sub(r"[\s().-]", "", phone_number)
    if not _E164.match(number):
        raise InvalidInputExternalError(
            f"{phone_number!r} is not an E.164 phone number (e.g. +15551234567)"
        )
    return number


class AbstractSMSProvider(AbstractStaticProvider):
    """An SMS gateway. Every ability takes the rotated instance."""

    name: ClassVar[str] = ""
    friendly_name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    _abilities: ClassVar[Set[str]] = {"send_sms", "get_sms_status"}
    _env: ClassVar[Dict[str, Any]] = {}

    @classmethod
    def bond_instance(cls, instance: ProviderInstanceModel) -> AbstractProviderInstance:
        return AbstractProviderInstance(instance)

    @classmethod
    @abstractmethod
    async def send_sms(
        cls, instance: ProviderInstanceModel, phone_number: str, message: str
    ) -> Dict[str, Any]:
        """Send ``message`` to the E.164 ``phone_number``: the gateway's
        ``message_id`` and this ``provider``."""

    @classmethod
    @abstractmethod
    async def get_sms_status(
        cls, instance: ProviderInstanceModel, message_id: str
    ) -> Dict[str, Any]:
        """The delivery ``status`` of the message ``message_id``."""

    @classmethod
    def services(cls) -> List[str]:
        return ["sms"]


class EXT_SMS(AbstractStaticExtension):
    name: ClassVar[str] = "sms"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "SMS through Twilio or Amazon SNS"

    _env: ClassVar[Dict[str, Any]] = {}
    dependencies: ClassVar[Dependencies] = Dependencies([])
    _abilities: ClassVar[Set[str]] = {"send_sms", "get_sms_status", "send_bulk_sms"}

    @classmethod
    def get_required_permissions(cls) -> List[str]:
        return ["sms:send", "sms:status"]

    @classmethod
    @ability("send_sms")
    async def send_sms(cls, phone_number: str, message: str) -> Dict[str, Any]:
        """Send ``message`` to ``phone_number``, with provider failover."""
        result: Dict[str, Any] = await cls.rotate_provider(
            "send_sms", e164(phone_number), message
        )
        return result

    @classmethod
    @ability("get_sms_status")
    async def get_sms_status(cls, message_id: str) -> Dict[str, Any]:
        """The delivery status of a sent message."""
        result: Dict[str, Any] = await cls.rotate_provider("get_sms_status", message_id)
        return result

    @classmethod
    @ability("send_bulk_sms")
    async def send_bulk_sms(
        cls, phone_numbers: List[str], message: str
    ) -> List[Dict[str, Any]]:
        """Send ``message`` to each number: one result per number, a refused
        or failed number reporting its ``error`` without stopping the rest."""
        results: List[Dict[str, Any]] = []
        for phone_number in phone_numbers:
            try:
                sent = await cls.send_sms(phone_number, message)
                results.append({"phone_number": phone_number, "success": True, **sent})
            except BaseExternalError as exc:
                results.append(
                    {
                        "phone_number": phone_number,
                        "success": False,
                        "error": exc.message,
                    }
                )
        return results
