# SPDX-License-Identifier: AGPL-3.0-or-later
"""WhatsApp, through Meta's WhatsApp Business Cloud API.

The instance's API key is a system-user access token (else
``WHATSAPP_ACCESS_TOKEN``) and its ``phone_number_id`` setting (else
``WHATSAPP_PHONE_NUMBER_ID``) the business number that sends; a recipient
is a phone number in international form. A free-form text reaches only a
user who wrote within the last 24 hours (outside it Meta requires a
template). Incoming messages arrive only by webhook, and sent messages
cannot be deleted through the API.
"""

from typing import Any, ClassVar, Dict, List

from zephyrex.extensions.ExternalErrors import TransientExternalError
from zephyrex.extensions.messaging.EXT_Messaging import (
    AbstractMessagingProvider,
    meta_graph_post,
    path_segment,
)
from zephyrex.logic.BLL_Providers import ProviderInstanceModel


class PRV_WhatsApp_Messaging(AbstractMessagingProvider):
    name: ClassVar[str] = "whatsapp"
    friendly_name: ClassVar[str] = "WhatsApp"
    description: ClassVar[str] = "WhatsApp Business Cloud API"
    _env: ClassVar[Dict[str, Any]] = {
        "WHATSAPP_ACCESS_TOKEN": "",
        "WHATSAPP_PHONE_NUMBER_ID": "",
    }

    @classmethod
    async def send_message(
        cls, instance: ProviderInstanceModel, recipient: str, text: str
    ) -> Dict[str, Any]:
        token = cls.resolve_setting(
            instance, "api_key", "WHATSAPP_ACCESS_TOKEN", field="api_key"
        )
        number = cls.resolve_setting(
            instance, "phone_number_id", "WHATSAPP_PHONE_NUMBER_ID"
        )
        if not (token and number):
            raise TransientExternalError(
                "WhatsApp access token and phone_number_id not configured",
                provider=cls.name,
            )
        sent = await meta_graph_post(
            cls,
            f"{path_segment(number, 'WhatsApp phone_number_id')}/messages",
            {
                "messaging_product": "whatsapp",
                "to": recipient.lstrip("+"),
                "type": "text",
                "text": {"body": text},
            },
            token,
        )
        return {
            "message_id": sent["messages"][0]["id"],
            "recipient": recipient,
            "provider": cls.name,
        }

    @classmethod
    async def get_message_history(
        cls, instance: ProviderInstanceModel, recipient: str, limit: int
    ) -> List[Dict[str, Any]]:
        raise cls.unsupported(
            "read history", "incoming messages arrive only by webhook"
        )

    @classmethod
    async def delete_message(
        cls, instance: ProviderInstanceModel, recipient: str, message_id: str
    ) -> Dict[str, Any]:
        raise cls.unsupported("delete messages", "the Cloud API has no deletion")
