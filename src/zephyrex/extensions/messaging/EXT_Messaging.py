# SPDX-License-Identifier: AGPL-3.0-or-later
"""Chat messaging across Discord, Slack, Telegram, Microsoft Teams,
WhatsApp, Facebook Messenger, Signal and TeamSpeak.

Recipients and message ids belong to one platform, so every ability names
its platform and runs only on that platform's provider instances (several
instances: several workspaces, bots or numbers, in rotation order). Each
instance carries its own credentials, falling back to the environment.

Platforms differ in what their APIs allow: WhatsApp and Messenger deliver
incoming messages only by webhook, a Teams incoming webhook can only post,
and so on. An operation a platform cannot do is refused with a
``PermanentExternalError`` that says why.
"""

import json
import re
from abc import abstractmethod
from typing import Any, ClassVar, Dict, List, Set, Type

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractProviderInstance,
    AbstractStaticExtension,
    AbstractStaticProvider,
    ability,
)
from zephyrex.extensions.ExternalErrors import (
    AuthExternalError,
    InvalidInputExternalError,
    PermanentExternalError,
)
from zephyrex.lib.Dependencies import Dependencies
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

MESSAGING_REQUEST_TIMEOUT_SECONDS = 15.0
DEFAULT_HISTORY_LIMIT = 20
MAX_HISTORY_LIMIT = 100
_PATH_SAFE = re.compile(r"^[A-Za-z0-9_.:@+=-]+$")


def path_segment(value: str, what: str) -> str:
    """``value`` as one URL path segment (an id), refused otherwise: it is
    interpolated into a path, where ``../x`` would reach another endpoint."""
    if not _PATH_SAFE.match(value):
        raise InvalidInputExternalError(f"{what} {value!r} is not a valid id")
    return value


def history_limit(limit: int) -> int:
    return max(1, min(limit, MAX_HISTORY_LIMIT))


# Meta's Graph API answers a bad token with HTTP 400 and error code 190
# (OAuthException), not 401.
META_GRAPH_API = "https://graph.facebook.com/v21.0"
_META_INVALID_TOKEN = 190


async def meta_graph_post(
    provider: Type["AbstractMessagingProvider"],
    path: str,
    payload: Dict[str, Any],
    token: str,
) -> Dict[str, Any]:
    """POST ``payload`` to Meta's Graph API ``path`` with ``token``; a
    refused token is an ``AuthExternalError`` (so the rotation moves on)."""
    try:
        answer: Dict[str, Any] = await provider.http().post(
            f"{META_GRAPH_API}/{path}",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
    except InvalidInputExternalError as exc:
        try:
            code = json.loads(str(exc.upstream_payload))["error"]["code"]
        except (ValueError, KeyError, TypeError):
            raise exc from None
        if code == _META_INVALID_TOKEN:
            raise AuthExternalError(
                "Meta refused the access token",
                provider=provider.name,
                upstream_status=exc.upstream_status,
            ) from exc
        raise
    return answer


class AbstractMessagingProvider(AbstractStaticProvider):
    """A chat platform. Every ability takes the rotated instance."""

    name: ClassVar[str] = ""
    friendly_name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    http_timeout_seconds: ClassVar[float] = MESSAGING_REQUEST_TIMEOUT_SECONDS
    _abilities: ClassVar[Set[str]] = {
        "send_message",
        "get_message_history",
        "delete_message",
    }
    _env: ClassVar[Dict[str, Any]] = {}

    @classmethod
    def bond_instance(cls, instance: ProviderInstanceModel) -> AbstractProviderInstance:
        return AbstractProviderInstance(instance)

    @classmethod
    def unsupported(cls, operation: str, why: str) -> PermanentExternalError:
        return PermanentExternalError(
            f"{cls.friendly_name} cannot {operation}: {why}", provider=cls.name
        )

    @classmethod
    @abstractmethod
    async def send_message(
        cls, instance: ProviderInstanceModel, recipient: str, text: str
    ) -> Dict[str, Any]:
        """Send ``text`` to ``recipient`` (the platform's channel, chat or
        user id): the platform's ``message_id``."""

    @classmethod
    @abstractmethod
    async def get_message_history(
        cls, instance: ProviderInstanceModel, recipient: str, limit: int
    ) -> List[Dict[str, Any]]:
        """Recent messages of ``recipient``'s conversation, newest first:
        each an ``id``, ``author``, ``text`` and ``timestamp``."""

    @classmethod
    @abstractmethod
    async def delete_message(
        cls, instance: ProviderInstanceModel, recipient: str, message_id: str
    ) -> Dict[str, Any]:
        """Delete ``message_id`` from ``recipient``'s conversation."""

    @classmethod
    def services(cls) -> List[str]:
        return ["messaging"]


class EXT_Messaging(AbstractStaticExtension):
    name: ClassVar[str] = "messaging"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Chat messaging across Discord, Slack, Telegram, Teams, WhatsApp, "
        "Messenger, Signal and TeamSpeak"
    )

    _env: ClassVar[Dict[str, Any]] = {}
    dependencies: ClassVar[Dependencies] = Dependencies([])
    _abilities: ClassVar[Set[str]] = {
        "send_message",
        "get_message_history",
        "delete_message",
    }

    @classmethod
    def get_required_permissions(cls) -> List[str]:
        return ["messaging:send", "messaging:read", "messaging:delete"]

    @classmethod
    @ability("send_message")
    async def send_message(
        cls, platform: str, recipient: str, text: str
    ) -> Dict[str, Any]:
        """Send ``text`` to ``recipient`` on ``platform``."""
        if not text.strip():
            raise InvalidInputExternalError("A message needs text")
        sent: Dict[str, Any] = await cls.rotate_provider_for(
            platform, "send_message", recipient, text
        )
        return sent

    @classmethod
    @ability("get_message_history")
    async def get_message_history(
        cls, platform: str, recipient: str, limit: int = DEFAULT_HISTORY_LIMIT
    ) -> List[Dict[str, Any]]:
        """Recent messages of a conversation on ``platform``."""
        found: List[Dict[str, Any]] = await cls.rotate_provider_for(
            platform, "get_message_history", recipient, history_limit(limit)
        )
        return found

    @classmethod
    @ability("delete_message")
    async def delete_message(
        cls, platform: str, recipient: str, message_id: str
    ) -> Dict[str, Any]:
        """Delete a sent message on ``platform``."""
        deleted: Dict[str, Any] = await cls.rotate_provider_for(
            platform, "delete_message", recipient, message_id
        )
        return deleted
