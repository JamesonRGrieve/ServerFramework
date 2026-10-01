# SPDX-License-Identifier: AGPL-3.0-or-later
"""Discord, as a bot (REST API v10).

The instance's API key is the bot token (else ``DISCORD_BOT_TOKEN``); a
recipient is a channel id the bot can see. Reading history needs the
Message Content intent for the text to be returned.
"""

from typing import Any, ClassVar, Dict, List, Tuple

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.ExternalErrors import TransientExternalError
from zephyrex.extensions.messaging.EXT_Messaging import (
    AbstractMessagingProvider,
    path_segment,
)
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

DISCORD_API = "https://discord.com/api/v10"


class PRV_Discord_Messaging(AbstractMessagingProvider):
    name: ClassVar[str] = "discord"
    friendly_name: ClassVar[str] = "Discord"
    description: ClassVar[str] = "Discord, as a bot"
    _env: ClassVar[Dict[str, Any]] = {"DISCORD_BOT_TOKEN": ""}
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting(
            "api_key",
            "Discord bot token",
            env="DISCORD_BOT_TOKEN",
            secret=True,
            field="api_key",
        ),
    )

    @classmethod
    def _headers(cls, instance: ProviderInstanceModel) -> Dict[str, str]:
        token = cls.setting(instance, "api_key")
        if not token:
            raise TransientExternalError(
                "Discord bot token not configured", provider=cls.name
            )
        return {"Authorization": f"Bot {token}"}

    @classmethod
    def _channel(cls, recipient: str) -> str:
        return f"{DISCORD_API}/channels/{path_segment(recipient, 'Discord channel')}"

    @classmethod
    async def send_message(
        cls, instance: ProviderInstanceModel, recipient: str, text: str
    ) -> Dict[str, Any]:
        sent = await cls.http().post(
            f"{cls._channel(recipient)}/messages",
            json={"content": text},
            headers=cls._headers(instance),
        )
        return {"message_id": sent["id"], "recipient": recipient, "provider": cls.name}

    @classmethod
    async def get_message_history(
        cls, instance: ProviderInstanceModel, recipient: str, limit: int
    ) -> List[Dict[str, Any]]:
        messages = await cls.get_json(
            f"{cls._channel(recipient)}/messages",
            {"limit": limit},
            cls._headers(instance),
        )
        return [
            {
                "id": message["id"],
                "author": message.get("author", {}).get("username", ""),
                "text": message.get("content", ""),
                "timestamp": message.get("timestamp", ""),
            }
            for message in messages
        ]

    @classmethod
    async def delete_message(
        cls, instance: ProviderInstanceModel, recipient: str, message_id: str
    ) -> Dict[str, Any]:
        await cls.http().delete(
            f"{cls._channel(recipient)}/messages/{path_segment(message_id, 'Discord message')}",
            headers=cls._headers(instance),
        )
        return {"deleted": True, "message_id": message_id, "provider": cls.name}
