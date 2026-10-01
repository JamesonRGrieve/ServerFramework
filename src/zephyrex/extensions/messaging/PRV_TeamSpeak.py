# SPDX-License-Identifier: AGPL-3.0-or-later
"""TeamSpeak 3, through a server's WebQuery HTTP API.

The instance's ``api_url`` setting (else ``TEAMSPEAK_API_URL``) is the
WebQuery endpoint (``https://host:10443``), its API key (else
``TEAMSPEAK_API_KEY``) a WebQuery key, and its ``server_id`` setting (else
``TEAMSPEAK_SERVER_ID``, default 1) the virtual server. A recipient is
``channel:<id>``, ``client:<id>`` or ``server``. A private address must be
listed in ``EGRESS_ALLOWED_HOSTS``. TeamSpeak keeps no chat history a
query can read and cannot retract a message.
"""

from typing import Any, ClassVar, Dict, List, Tuple

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.ExternalErrors import (
    AuthExternalError,
    InvalidInputExternalError,
    TransientExternalError,
)
from zephyrex.extensions.messaging.EXT_Messaging import AbstractMessagingProvider
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

# sendtextmessage's targetmode: 1 a client, 2 a channel, 3 the server.
_TARGET_MODES = {"client": 1, "channel": 2, "server": 3}
_QUERY_OK = 0
_QUERY_AUTH_ERRORS = {
    2568,  # insufficient client permissions
    5122,  # invalid API key
    5124,  # API key expired
}


class PRV_TeamSpeak_Messaging(AbstractMessagingProvider):
    name: ClassVar[str] = "teamspeak"
    friendly_name: ClassVar[str] = "TeamSpeak"
    description: ClassVar[str] = "TeamSpeak 3, through WebQuery"
    _env: ClassVar[Dict[str, Any]] = {
        "TEAMSPEAK_API_URL": "",
        "TEAMSPEAK_API_KEY": "",
        "TEAMSPEAK_SERVER_ID": "1",
    }
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting(
            "api_key",
            "WebQuery API key",
            env="TEAMSPEAK_API_KEY",
            secret=True,
            field="api_key",
        ),
        InstanceSetting(
            "api_url", "WebQuery base URL (https://host:10443)", env="TEAMSPEAK_API_URL"
        ),
        InstanceSetting(
            "server_id", "Virtual server id", env="TEAMSPEAK_SERVER_ID", default="1"
        ),
    )

    @classmethod
    def _target(cls, recipient: str) -> Tuple[int, str]:
        """``(targetmode, target)`` for ``channel:<id>``, ``client:<id>``
        or ``server``."""
        kind, _, target = recipient.partition(":")
        mode = _TARGET_MODES.get(kind)
        if (
            mode is None
            or (kind == "server") != (target == "")
            or (target and not target.isdigit())
        ):
            raise InvalidInputExternalError(
                f"{recipient!r} is not channel:<id>, client:<id> or server",
                provider=cls.name,
            )
        return mode, target or "0"

    @classmethod
    async def send_message(
        cls, instance: ProviderInstanceModel, recipient: str, text: str
    ) -> Dict[str, Any]:
        mode, target = cls._target(recipient)
        url = cls.setting(instance, "api_url")
        key = cls.setting(instance, "api_key")
        server = cls.setting(instance, "server_id")
        if not (url and key):
            raise TransientExternalError(
                "TeamSpeak api_url and API key not configured", provider=cls.name
            )
        if not (server or "").isdigit():
            raise InvalidInputExternalError(
                "TeamSpeak server_id must be a number", provider=cls.name
            )
        answer = await cls.http().post(
            f"{url.rstrip('/')}/{server}/sendtextmessage",
            json={"targetmode": mode, "target": target, "msg": text},
            headers={"x-api-key": key},
        )
        status = answer.get("status", {})
        code = status.get("code", _QUERY_OK)
        if code in _QUERY_AUTH_ERRORS:
            raise AuthExternalError(
                f"TeamSpeak: {status.get('message')}", provider=cls.name
            )
        if code != _QUERY_OK:
            raise InvalidInputExternalError(
                f"TeamSpeak: {status.get('message')} ({code})", provider=cls.name
            )
        return {"message_id": None, "recipient": recipient, "provider": cls.name}

    @classmethod
    async def get_message_history(
        cls, instance: ProviderInstanceModel, recipient: str, limit: int
    ) -> List[Dict[str, Any]]:
        raise cls.unsupported(
            "read history", "a server keeps no chat history for queries"
        )

    @classmethod
    async def delete_message(
        cls, instance: ProviderInstanceModel, recipient: str, message_id: str
    ) -> Dict[str, Any]:
        raise cls.unsupported("delete messages", "a sent message cannot be retracted")
