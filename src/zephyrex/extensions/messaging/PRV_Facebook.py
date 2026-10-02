# SPDX-License-Identifier: AGPL-3.0-or-later
"""Facebook Messenger, through the Messenger Platform Send API as a Page.

The instance's API key is the Page access token (else
``MESSENGER_PAGE_TOKEN``); a recipient is the user's page-scoped id
(PSID). A message outside the 24-hour window after the user's last message
needs a message tag, which this provider does not send. Incoming messages
arrive only by webhook, and sent messages cannot be deleted.
"""

from typing import Any, ClassVar, Dict, List, Tuple

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.ExternalErrors import TransientExternalError
from zephyrex.extensions.messaging.EXT_Messaging import AbstractMessagingProvider
from zephyrex.lib.MetaGraph import meta_graph
from zephyrex.logic.BLL_Providers import ProviderInstanceModel


class PRV_Facebook_Messaging(AbstractMessagingProvider):
    name: ClassVar[str] = "messenger"
    friendly_name: ClassVar[str] = "Facebook Messenger"
    description: ClassVar[str] = "Facebook Messenger, as a Page"
    _env: ClassVar[Dict[str, Any]] = {"MESSENGER_PAGE_TOKEN": ""}
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting(
            "api_key",
            "Facebook Page access token",
            env="MESSENGER_PAGE_TOKEN",
            secret=True,
            field="api_key",
        ),
    )

    @classmethod
    async def send_message(
        cls, instance: ProviderInstanceModel, recipient: str, text: str
    ) -> Dict[str, Any]:
        token = cls.setting(instance, "api_key")
        if not token:
            raise TransientExternalError(
                "Messenger Page token not configured", provider=cls.name
            )
        sent = await meta_graph(
            cls,
            "POST",
            "me/messages",
            token,
            json_body={
                "recipient": {"id": recipient},
                "messaging_type": "RESPONSE",
                "message": {"text": text},
            },
        )
        return {
            "message_id": sent["message_id"],
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
        raise cls.unsupported("delete messages", "the Send API has no deletion")
