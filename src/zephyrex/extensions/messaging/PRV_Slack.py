# SPDX-License-Identifier: AGPL-3.0-or-later
"""Slack, through the Web API with a bot token.

The instance's API key is the bot token, ``xoxb-…`` (else
``SLACK_BOT_TOKEN``); a recipient is a channel id the bot is in, and a
message id is the message's ``ts``. Slack answers 200 with ``ok: false``
on failure, so its error codes are mapped here.
"""

from typing import Any, ClassVar, Dict, List, Tuple

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.ExternalErrors import (
    AuthExternalError,
    BaseExternalError,
    InvalidInputExternalError,
    RateLimitExternalError,
    TransientExternalError,
)
from zephyrex.extensions.messaging.EXT_Messaging import AbstractMessagingProvider
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

SLACK_API = "https://slack.com/api"
_AUTH_ERRORS = {
    "invalid_auth",
    "not_authed",
    "account_inactive",
    "token_revoked",
    "token_expired",
    "no_permission",
    "missing_scope",
}
_TRANSIENT_ERRORS = {
    "internal_error",
    "fatal_error",
    "service_unavailable",
    "request_timeout",
}


class PRV_Slack_Messaging(AbstractMessagingProvider):
    name: ClassVar[str] = "slack"
    friendly_name: ClassVar[str] = "Slack"
    description: ClassVar[str] = "Slack, as a bot"
    _env: ClassVar[Dict[str, Any]] = {"SLACK_BOT_TOKEN": ""}
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting(
            "api_key",
            "Slack bot token (xoxb-…)",
            env="SLACK_BOT_TOKEN",
            secret=True,
            field="api_key",
        ),
    )

    @classmethod
    def _headers(cls, instance: ProviderInstanceModel) -> Dict[str, str]:
        token = cls.setting(instance, "api_key")
        if not token:
            raise TransientExternalError(
                "Slack bot token not configured", provider=cls.name
            )
        return {"Authorization": f"Bearer {token}"}

    @classmethod
    def _checked(cls, answer: Dict[str, Any]) -> Dict[str, Any]:
        """``answer``, or the typed error its ``ok: false`` stands for."""
        if answer.get("ok"):
            return answer
        code = str(answer.get("error", "unknown_error"))
        error: BaseExternalError
        if code in _AUTH_ERRORS:
            error = AuthExternalError(f"Slack: {code}", provider=cls.name)
        elif code == "ratelimited":
            error = RateLimitExternalError(f"Slack: {code}", provider=cls.name)
        elif code in _TRANSIENT_ERRORS:
            error = TransientExternalError(f"Slack: {code}", provider=cls.name)
        else:
            error = InvalidInputExternalError(f"Slack: {code}", provider=cls.name)
        raise error

    @classmethod
    async def _call(
        cls, instance: ProviderInstanceModel, method: str, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        answer = await cls.http().post(
            f"{SLACK_API}/{method}", json=payload, headers=cls._headers(instance)
        )
        return cls._checked(answer)

    @classmethod
    async def send_message(
        cls, instance: ProviderInstanceModel, recipient: str, text: str
    ) -> Dict[str, Any]:
        sent = await cls._call(
            instance, "chat.postMessage", {"channel": recipient, "text": text}
        )
        return {
            "message_id": sent["ts"],
            "recipient": sent["channel"],
            "provider": cls.name,
        }

    @classmethod
    async def get_message_history(
        cls, instance: ProviderInstanceModel, recipient: str, limit: int
    ) -> List[Dict[str, Any]]:
        answer = cls._checked(
            await cls.get_json(
                f"{SLACK_API}/conversations.history",
                {"channel": recipient, "limit": limit},
                cls._headers(instance),
            )
        )
        return [
            {
                "id": message["ts"],
                "author": message.get("user") or message.get("bot_id", ""),
                "text": message.get("text", ""),
                "timestamp": message["ts"],
            }
            for message in answer.get("messages", [])
        ]

    @classmethod
    async def delete_message(
        cls, instance: ProviderInstanceModel, recipient: str, message_id: str
    ) -> Dict[str, Any]:
        await cls._call(
            instance, "chat.delete", {"channel": recipient, "ts": message_id}
        )
        return {"deleted": True, "message_id": message_id, "provider": cls.name}
