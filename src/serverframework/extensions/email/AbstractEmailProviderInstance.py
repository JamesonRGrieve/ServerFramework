"""
Typed `AbstractEmailProviderInstance` ABC (Item 89).

Phase-1 introduced friendly classmethods on `AbstractEmailProvider`
(`send`, `update_email`, `list_emails`) that take `provider_instance` as
a positional argument. Item 26 (Batch B) promotes the bonded-instance
contract to a real ABC; this module defines the email-extension's typed
instance shape so call sites become `bonded.send(message)` instead of
`Provider.send(provider_instance, message)`.

The eight typed abilities map 1:1 to the legacy `AbstractEmailProvider`
abstracts but use the typed `EmailMessage` / `SentMessage` / `BatchResult`
shapes from Item 88 / Item 12.

TODO (Batch B): once `AbstractProviderInstance` carries the strengthened
contract from Item 26, switch the base class import to that canonical
location. The current import path (`extensions.AbstractExtensionProvider`)
is the existing class to keep this module loadable in isolation.
"""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, ClassVar, Dict, FrozenSet, List, Optional

from serverframework.extensions.AbstractExtensionProvider import AbstractProviderInstance


__all__ = [
    "SentMessage",
    "AbstractEmailProviderInstance",
]


@dataclass
class SentMessage:
    """Successful-send return shape from `AbstractEmailProviderInstance.send`.

    Replaces the legacy ``"Email sent successfully to ..."`` string that
    callers had to substring-match on. Failures raise typed errors from
    `EmailErrors` instead of returning a different string.

    Attributes:
        message_id: Provider-assigned message identifier (SendGrid's
            `X-Message-Id`, SMTP server response, etc.).
        provider: Name of the provider that accepted the message.
        accepted_at: Timestamp the upstream confirmed receipt (UTC).
        recipient: The primary recipient address (first `to`).
        upstream_response: Optional raw upstream payload for diagnostics.
    """

    message_id: str
    provider: str
    accepted_at: datetime
    recipient: str = ""
    upstream_response: Dict[str, Any] = field(default_factory=dict)


class AbstractEmailProviderInstance(AbstractProviderInstance):
    """Bonded email-provider instance with eight typed abilities.

    Concrete providers' `bond_instance` returns an instance of (a subclass
    of) this ABC; call sites then invoke the typed methods directly:

        bonded = SendgridProvider.bond_instance(model)
        sent = await bonded.send(message)

    Capability flags signal which abilities a given concrete bonded
    instance actually implements; callers should branch on
    ``capability in instance.capabilities`` rather than catching
    `NotImplementedError`.
    """

    capabilities: ClassVar[FrozenSet[str]] = frozenset()

    # The eight typed abilities --------------------------------------------

    @abstractmethod
    async def send(self, message: "EmailMessage") -> SentMessage:  # noqa: F821
        """Send a single typed `EmailMessage`. Raises typed errors from
        `EmailErrors` on failure."""

    @abstractmethod
    async def send_bulk(
        self, messages: List["EmailMessage"]  # noqa: F821
    ) -> "BatchResult":  # noqa: F821
        """Send up to N messages in one upstream call; returns
        `BatchResult` with per-item success/failure rows."""

    @abstractmethod
    async def list_emails(
        self,
        *,
        folder: Optional[str] = None,
        query: Optional[str] = None,
        limit: int = 10,
        cursor: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Page through a folder; optional `query` switches to search."""

    @abstractmethod
    async def get_email(self, message_id: str) -> Dict[str, Any]:
        """Retrieve a single message by id."""

    @abstractmethod
    async def update_email(
        self,
        message_id: str,
        *,
        read: Optional[bool] = None,
        flagged: Optional[bool] = None,
        folder: Optional[str] = None,
        deleted: bool = False,
    ) -> Dict[str, Any]:
        """Apply state changes (read/flag/move/delete) to a single message."""

    @abstractmethod
    async def reply(
        self,
        message_id: str,
        body: str,
        attachments: Optional[List[str]] = None,
    ) -> SentMessage:
        """Reply to an existing message; returns the new `SentMessage`."""

    @abstractmethod
    async def download_attachment(
        self, message_id: str, attachment_id: str
    ) -> bytes:
        """Return the raw bytes of an attachment."""

    @abstractmethod
    async def list_threads(
        self, folder: Optional[str] = None, limit: int = 10
    ) -> List[Dict[str, Any]]:
        """List conversation threads in a folder."""
