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

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, ClassVar, Dict, FrozenSet, List, Optional

from pydantic import BaseModel, Field

from zephyrex.extensions.AbstractExtensionProvider import AbstractProviderInstance
from zephyrex.extensions.email.EmailErrors import NotSupportedError


__all__ = [
    "SentMessage",
    "AbstractEmailProviderInstance",
    "EmailValidationResult",
    "BulkSendResult",
    "BulkSendRow",
    "SuppressionEntry",
    "SuppressionListPage",
    "EmailStats",
    "MessageListPage",
    "MessageSummary",
]


# ----------------------------------------------------------------------
# Item 95 — typed result models for the capability ladder.
# ----------------------------------------------------------------------


class EmailValidationResult(BaseModel):
    """Outcome of a pre-flight email-address validation call.

    `verdict` is one of `"valid"`, `"risky"`, `"invalid"` per the
    SendGrid-style three-tier verdict; `score` is 0.0-1.0 when the
    upstream returns a probabilistic confidence (None otherwise).
    """

    email: str = Field(..., description="The address that was validated.")
    verdict: str = Field(..., description="`valid` / `risky` / `invalid`.")
    score: Optional[float] = Field(
        None, ge=0.0, le=1.0, description="Optional 0-1 confidence."
    )
    checks: Dict[str, Any] = Field(
        default_factory=dict,
        description="Provider-specific check breakdown (mx, smtp, etc.).",
    )
    raw: Dict[str, Any] = Field(
        default_factory=dict, description="Raw upstream response for diagnostics."
    )


class BulkSendRow(BaseModel):
    """One per-recipient row inside a `BulkSendResult`."""

    recipient: str
    success: bool
    message_id: Optional[str] = None
    error: Optional[str] = None
    error_type: Optional[str] = None


class BulkSendResult(BaseModel):
    """Aggregate result of a templated bulk send."""

    succeeded: int = 0
    failed: int = 0
    rows: List[BulkSendRow] = Field(default_factory=list)
    template_id: Optional[str] = None


class SuppressionEntry(BaseModel):
    """A single suppression-list row."""

    email: str
    suppression_type: str = Field(
        ..., description="One of `bounce`, `block`, `spam_report`, `unsubscribe`, `invalid`."
    )
    reason: Optional[str] = None
    created_at: Optional[datetime] = None
    raw: Dict[str, Any] = Field(default_factory=dict)


class SuppressionListPage(BaseModel):
    """Cursor-paginated page of suppression entries."""

    items: List[SuppressionEntry] = Field(default_factory=list)
    next_token: Optional[str] = None


class EmailStats(BaseModel):
    """Aggregate send-history counters across a window."""

    since: Optional[datetime] = None
    until: Optional[datetime] = None
    delivered: int = 0
    opens: int = 0
    clicks: int = 0
    bounces: int = 0
    spam_reports: int = 0
    unsubscribes: int = 0
    requests: int = 0
    raw: Dict[str, Any] = Field(default_factory=dict)


class MessageSummary(BaseModel):
    """Per-message row used by `list_messages`."""

    message_id: str
    recipient: str
    subject: Optional[str] = None
    status: Optional[str] = None
    sent_at: Optional[datetime] = None
    raw: Dict[str, Any] = Field(default_factory=dict)


class MessageListPage(BaseModel):
    """Cursor-paginated page of `MessageSummary` rows."""

    items: List[MessageSummary] = Field(default_factory=list)
    next_token: Optional[str] = None


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
    async def send(self, message: "EmailMessage") -> "SentMessage":  # type: ignore[name-defined]
        """Send a single typed `EmailMessage`. Raises typed errors from
        `EmailErrors` on failure."""

    @abstractmethod
    async def send_bulk(
        self, messages: List["EmailMessage"]  # type: ignore[name-defined]
    ) -> "BatchResult":  # type: ignore[name-defined]
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

    # ------------------------------------------------------------------
    # Item 95 — capability ladder.
    #
    # These are concrete defaults rather than @abstractmethod so providers
    # opt in to a subset rather than being forced to stub each one. The
    # default raises NotSupportedError; callers should branch on
    # `capability in bonded.capabilities` before invoking.
    # ------------------------------------------------------------------

    def _provider_name(self) -> str:
        """Return the bonded provider's short name for error messages."""
        owner = type(self).__name__
        return owner.replace("ProviderInstance", "").replace("Provider", "").lower() or owner

    async def validate_address(self, email: str) -> EmailValidationResult:
        """Pre-flight an address against the upstream's validation API.

        Default: raise `NotSupportedError`. Providers declaring
        `Capability.VALIDATE_ADDRESS` override this with a real call.
        """
        raise NotSupportedError(
            provider=self._provider_name(), capability="validate_address"
        )

    async def send_with_template(
        self,
        template_id: str,
        recipients: List[str],
        substitutions: Optional[Dict[str, Any]] = None,
    ) -> BulkSendResult:
        """Render and send a server-side template to N recipients.

        Default: raise `NotSupportedError`. Providers declaring
        `Capability.TEMPLATES` override this.
        """
        raise NotSupportedError(
            provider=self._provider_name(), capability="send_with_template"
        )

    async def list_suppressions(
        self,
        suppression_type: str,
        *,
        limit: int = 50,
        cursor: Optional[str] = None,
    ) -> SuppressionListPage:
        """Page through one of the suppression lists (`bounce`, `block`,
        `spam_report`, `unsubscribe`, `invalid`).

        Default: raise `NotSupportedError`.
        """
        raise NotSupportedError(
            provider=self._provider_name(), capability="list_suppressions"
        )

    async def add_suppression(
        self,
        email: str,
        suppression_type: str,
        reason: Optional[str] = None,
    ) -> None:
        """Add an address to a suppression list.

        Default: raise `NotSupportedError`.
        """
        raise NotSupportedError(
            provider=self._provider_name(), capability="add_suppression"
        )

    async def remove_suppression(self, email: str, suppression_type: str) -> None:
        """Remove an address from a suppression list.

        Default: raise `NotSupportedError`.
        """
        raise NotSupportedError(
            provider=self._provider_name(), capability="remove_suppression"
        )

    async def get_stats(
        self,
        *,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
    ) -> EmailStats:
        """Fetch aggregate counters across a window.

        Default: raise `NotSupportedError`.
        """
        raise NotSupportedError(
            provider=self._provider_name(), capability="get_stats"
        )

    async def list_messages(
        self,
        *,
        since: Optional[datetime] = None,
        status: Optional[str] = None,
        limit: int = 50,
        cursor: Optional[str] = None,
    ) -> MessageListPage:
        """Cursor-paginated history of sent messages.

        Default: raise `NotSupportedError`.
        """
        raise NotSupportedError(
            provider=self._provider_name(), capability="list_messages"
        )
