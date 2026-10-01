# SPDX-License-Identifier: AGPL-3.0-or-later
"""Signal, through a self-hosted signal-cli REST API
(github.com/bbernhard/signal-cli-rest-api); Signal has no official one.

The instance's ``api_url`` setting (else ``SIGNAL_API_URL``) is that
service and its ``number`` setting (else ``SIGNAL_NUMBER``) the registered
account that sends; a recipient is a phone number or a group id. A private
address must be listed in ``EGRESS_ALLOWED_HOSTS`` for the outbound guard
to allow it. Signal keeps no history a client can fetch: history is what
the account has received since it was last read, and reading consumes it.
A message id is the message's timestamp, which remote deletion takes.
"""

from typing import Any, ClassVar, Dict, List, Tuple
from urllib.parse import quote

from zephyrex.extensions.ExternalErrors import (
    InvalidInputExternalError,
    TransientExternalError,
)
from zephyrex.extensions.messaging.EXT_Messaging import AbstractMessagingProvider
from zephyrex.logic.BLL_Providers import ProviderInstanceModel


class PRV_Signal_Messaging(AbstractMessagingProvider):
    name: ClassVar[str] = "signal"
    friendly_name: ClassVar[str] = "Signal"
    description: ClassVar[str] = "Signal, through a signal-cli REST API"
    _env: ClassVar[Dict[str, Any]] = {"SIGNAL_API_URL": "", "SIGNAL_NUMBER": ""}

    @classmethod
    def _service(cls, instance: ProviderInstanceModel) -> Tuple[str, str]:
        """``(api url, sending number)``."""
        url = cls.resolve_setting(instance, "api_url", "SIGNAL_API_URL")
        number = cls.resolve_setting(instance, "number", "SIGNAL_NUMBER")
        if not (url and number):
            raise TransientExternalError(
                "Signal api_url and number not configured", provider=cls.name
            )
        return url.rstrip("/"), number

    @classmethod
    async def send_message(
        cls, instance: ProviderInstanceModel, recipient: str, text: str
    ) -> Dict[str, Any]:
        url, number = cls._service(instance)
        sent = await cls.http().post(
            f"{url}/v2/send",
            json={"message": text, "number": number, "recipients": [recipient]},
        )
        return {
            "message_id": str(sent["timestamp"]),
            "recipient": recipient,
            "provider": cls.name,
        }

    @classmethod
    async def get_message_history(
        cls, instance: ProviderInstanceModel, recipient: str, limit: int
    ) -> List[Dict[str, Any]]:
        url, number = cls._service(instance)
        received = await cls.get_json(f"{url}/v1/receive/{quote(number, safe='')}")
        messages = []
        for item in received:
            envelope = item.get("envelope", {})
            data = envelope.get("dataMessage") or {}
            group = (data.get("groupInfo") or {}).get("groupId")
            source = envelope.get("sourceNumber") or envelope.get("source")
            if "message" in data and recipient in (source, group):
                messages.append(
                    {
                        "id": str(envelope.get("timestamp", "")),
                        "author": source or "",
                        "text": data.get("message") or "",
                        "timestamp": envelope.get("timestamp", ""),
                    }
                )
        return list(reversed(messages))[:limit]

    @classmethod
    async def delete_message(
        cls, instance: ProviderInstanceModel, recipient: str, message_id: str
    ) -> Dict[str, Any]:
        if not message_id.isdigit():
            raise InvalidInputExternalError(
                f"{message_id!r} is not a Signal message timestamp", provider=cls.name
            )
        url, number = cls._service(instance)
        await cls.http().post(
            f"{url}/v1/remote-delete/{quote(number, safe='')}",
            json={"recipient": recipient, "timestamp": int(message_id)},
        )
        return {"deleted": True, "message_id": message_id, "provider": cls.name}
