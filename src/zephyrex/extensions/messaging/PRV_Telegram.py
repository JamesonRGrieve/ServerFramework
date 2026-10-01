# SPDX-License-Identifier: AGPL-3.0-or-later
"""Telegram, through the Bot API.

The instance's API key is the bot token (else ``TELEGRAM_BOT_TOKEN``); a
recipient is a chat id (or ``@channelusername``). The Bot API keeps no
history a bot can page through: history is the updates Telegram still
holds for the bot (until another consumer acknowledges them), filtered to
the chat.
"""

import re
from typing import Any, ClassVar, Dict, List

from zephyrex.extensions.ExternalErrors import (
    InvalidInputExternalError,
    TransientExternalError,
)
from zephyrex.extensions.messaging.EXT_Messaging import AbstractMessagingProvider
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

TELEGRAM_API = "https://api.telegram.org"
# A bot token is <bot id>:<35 characters>; it is a path segment.
_BOT_TOKEN = re.compile(r"^\d+:[A-Za-z0-9_-]{30,}$")


class PRV_Telegram_Messaging(AbstractMessagingProvider):
    name: ClassVar[str] = "telegram"
    friendly_name: ClassVar[str] = "Telegram"
    description: ClassVar[str] = "Telegram, as a bot"
    _env: ClassVar[Dict[str, Any]] = {"TELEGRAM_BOT_TOKEN": ""}

    @classmethod
    def _bot(cls, instance: ProviderInstanceModel) -> str:
        token = cls.resolve_setting(
            instance, "api_key", "TELEGRAM_BOT_TOKEN", field="api_key"
        )
        if not token:
            raise TransientExternalError(
                "Telegram bot token not configured", provider=cls.name
            )
        if not _BOT_TOKEN.match(token):
            raise InvalidInputExternalError(
                "Telegram bot token is not <bot id>:<secret>", provider=cls.name
            )
        return f"{TELEGRAM_API}/bot{token}"

    @classmethod
    async def _call(
        cls, instance: ProviderInstanceModel, method: str, payload: Dict[str, Any]
    ) -> Any:
        answer = await cls.http().post(f"{cls._bot(instance)}/{method}", json=payload)
        return answer["result"]

    @classmethod
    async def send_message(
        cls, instance: ProviderInstanceModel, recipient: str, text: str
    ) -> Dict[str, Any]:
        sent = await cls._call(
            instance, "sendMessage", {"chat_id": recipient, "text": text}
        )
        return {
            "message_id": str(sent["message_id"]),
            "recipient": str(sent["chat"]["id"]),
            "provider": cls.name,
        }

    @classmethod
    async def get_message_history(
        cls, instance: ProviderInstanceModel, recipient: str, limit: int
    ) -> List[Dict[str, Any]]:
        updates = await cls._call(instance, "getUpdates", {"timeout": 0})
        messages = [
            update.get("message") or update.get("channel_post") or {}
            for update in updates
        ]
        in_chat = [
            message
            for message in messages
            if str(message.get("chat", {}).get("id")) == recipient
            or message.get("chat", {}).get("username") == recipient.lstrip("@")
        ]
        return [
            {
                "id": str(message["message_id"]),
                "author": message.get("from", {}).get("username", ""),
                "text": message.get("text", ""),
                "timestamp": message.get("date", ""),
            }
            for message in reversed(in_chat)
        ][:limit]

    @classmethod
    async def delete_message(
        cls, instance: ProviderInstanceModel, recipient: str, message_id: str
    ) -> Dict[str, Any]:
        if not message_id.isdigit():
            raise InvalidInputExternalError(
                f"{message_id!r} is not a Telegram message id", provider=cls.name
            )
        await cls._call(
            instance,
            "deleteMessage",
            {"chat_id": recipient, "message_id": int(message_id)},
        )
        return {"deleted": True, "message_id": message_id, "provider": cls.name}
