# SPDX-License-Identifier: AGPL-3.0-or-later
"""Microsoft Teams, through a channel's incoming webhook.

Microsoft Graph lets an app post to channels only as a signed-in user or a
Bot Framework bot; a Workflows (or legacy connector) incoming webhook is
the one way a server can post on its own. A webhook belongs to one channel,
so the instance's ``webhook_url`` setting (else ``TEAMS_WEBHOOK_URL``) is
the destination and a recipient is only a label. It can post, nothing
more: history and deletion need Graph as a user.
"""

from typing import Any, ClassVar, Dict, List, Tuple
from urllib.parse import urlparse

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.ExternalErrors import (
    InvalidInputExternalError,
    TransientExternalError,
)
from zephyrex.extensions.messaging.EXT_Messaging import AbstractMessagingProvider
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

# Where Teams webhooks live: Workflows (Power Automate) and the legacy
# Office 365 connectors.
_WEBHOOK_HOSTS = (".logic.azure.com", ".webhook.office.com", ".powerplatform.com")


class PRV_Microsoft_Messaging(AbstractMessagingProvider):
    name: ClassVar[str] = "msteams"
    friendly_name: ClassVar[str] = "Microsoft Teams"
    description: ClassVar[str] = "Microsoft Teams, through a channel's incoming webhook"
    _env: ClassVar[Dict[str, Any]] = {"TEAMS_WEBHOOK_URL": ""}
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting(
            "webhook_url",
            "The channel's Workflows or connector incoming-webhook URL",
            env="TEAMS_WEBHOOK_URL",
            secret=True,
        ),
    )

    @classmethod
    def _webhook(cls, instance: ProviderInstanceModel) -> str:
        url = cls.setting(instance, "webhook_url")
        if not url:
            raise TransientExternalError(
                "Teams webhook_url not configured", provider=cls.name
            )
        parsed = urlparse(url)
        if parsed.scheme != "https" or not (parsed.hostname or "").endswith(
            _WEBHOOK_HOSTS
        ):
            raise InvalidInputExternalError(
                "Teams webhook_url must be an https Teams Workflows or connector URL",
                provider=cls.name,
            )
        return url

    @classmethod
    async def send_message(
        cls, instance: ProviderInstanceModel, recipient: str, text: str
    ) -> Dict[str, Any]:
        # An Adaptive Card: what Workflows webhooks accept (and connectors too).
        await cls.http().post(
            cls._webhook(instance),
            json={
                "type": "message",
                "attachments": [
                    {
                        "contentType": "application/vnd.microsoft.card.adaptive",
                        "content": {
                            "type": "AdaptiveCard",
                            "version": "1.4",
                            "body": [{"type": "TextBlock", "text": text, "wrap": True}],
                        },
                    }
                ],
            },
        )
        # A webhook answers without an id for what it posted.
        return {"message_id": None, "recipient": recipient, "provider": cls.name}

    @classmethod
    async def get_message_history(
        cls, instance: ProviderInstanceModel, recipient: str, limit: int
    ) -> List[Dict[str, Any]]:
        raise cls.unsupported("read history", "an incoming webhook can only post")

    @classmethod
    async def delete_message(
        cls, instance: ProviderInstanceModel, recipient: str, message_id: str
    ) -> Dict[str, Any]:
        raise cls.unsupported("delete messages", "an incoming webhook can only post")
