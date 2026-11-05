# SPDX-License-Identifier: AGPL-3.0-or-later
"""Where inbound mail comes in: the signed delivery endpoint, and how far the
IMAP poller has read each mailbox.

* :class:`InboundEmailManager` serves ``POST /v1/email/inbound/{id}``, which
  a mail server delivers raw messages to (see InboundEndpoint). It has no
  table of its own and never acts with a session.
* :class:`EmailInboundMailboxModel` is one polled mailbox's cursor: the
  UIDVALIDITY it was read under and the highest UID taken from it. The poller
  claims each message by moving the cursor past it with a compare-and-set,
  so of two workers (or a restart) reading the same mailbox one delivers it.
  It is server bookkeeping: only ROOT and SYSTEM read or write it.
"""

import re
from datetime import datetime, timezone
from typing import Any, ClassVar, List, Optional

from fastapi import HTTPException, Request
from pydantic import Field
from sqlalchemy import update

from zephyrex.extensions.email.InboundEndpoint import (
    MIN_SIGNING_SECRET_LENGTH,
    RECIPIENTS_HEADER,
    InboundDelivered,
    receive_signed_message,
)
from zephyrex.lib.ContentNegotiation import skip_negotiation
from zephyrex.lib.CustomRoute import ExposeIn, custom_route
from zephyrex.lib.Environment import env
from zephyrex.lib.InboundSecurity import rate_limit
from zephyrex.lib.SessionCookies import accept_cross_site_writes
from zephyrex.lib.SignedRequests import SIGNATURE_HEADER, TIMESTAMP_HEADER
from zephyrex.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    ModelMeta,
    StringSearchModel,
    UpdateMixinModel,
)
from zephyrex.logic.AbstractLogicManager.ownership import server_side
from zephyrex.pydantic2.fastapi import AuthType, RouterMixin, RouteType
from zephyrex.pydantic2.registry import BaseModel

INBOUND_PREFIX = "/v1/email/inbound"
INBOUND_PATH = "/{provider_instance_id}"
# Deliveries one address may make in a minute: a mail server's burst.
INBOUND_RATE_LIMIT = "300/min"
# The longest error kept on a mailbox's cursor.
MAX_RECORDED_ERROR_CHARACTERS = 500


class EmailInboundMailboxModel(ApplicationModel, UpdateMixinModel, metaclass=ModelMeta):
    """How far the IMAP poller has read one mailbox of one provider
    instance. UIDs and UIDVALIDITY are IMAP's 32-bit unsigned numbers, kept
    as text: they overflow a signed INTEGER column."""

    provider_instance_id: str = Field(
        ..., description="The provider instance whose mailbox it is"
    )
    mailbox: str = Field(..., description="The mailbox polled")
    uid_validity: Optional[str] = Field(
        None, description="The UIDVALIDITY the cursor was read under"
    )
    last_uid: str = Field("0", description="The highest UID taken from it")
    last_polled_at: Optional[datetime] = Field(
        None, description="When it was last polled"
    )
    last_error: Optional[str] = Field(
        None, description="Why the last poll failed, if it did"
    )

    table_comment: ClassVar[str] = (
        "How far the inbound IMAP poller has read each mailbox"
    )

    class Create(BaseModel):
        provider_instance_id: str
        mailbox: str
        uid_validity: Optional[str] = None
        last_uid: str = "0"

    class Update(BaseModel):
        last_polled_at: Optional[datetime] = None
        last_error: Optional[str] = None

    class Search(ApplicationModel.Search, UpdateMixinModel.Search):
        provider_instance_id: Optional[StringSearchModel] = None
        mailbox: Optional[StringSearchModel] = None


class EmailInboundMailboxManager(AbstractBLLManager):
    """Mailbox cursors, for the poller (ROOT); anyone else is refused."""

    _model = EmailInboundMailboxModel

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        requester = self.optional_requester
        if requester is None or not server_side(requester.id):
            raise HTTPException(
                status_code=403, detail="Inbound mailbox cursors are server state"
            )

    def cursor(self, provider_instance_id: str, mailbox: str) -> Any:
        """The cursor of ``mailbox`` on the instance, made when missing. Of
        cursors two workers made at once, every reader takes the first."""
        found = self._live(provider_instance_id, mailbox)
        if not found:
            self.create(provider_instance_id=provider_instance_id, mailbox=mailbox)
            found = self._live(provider_instance_id, mailbox)
        return min(found, key=lambda row: (row.created_at, row.id))

    def _live(self, provider_instance_id: str, mailbox: str) -> List[Any]:
        rows: List[Any] = self.list(
            provider_instance_id=provider_instance_id,
            mailbox=mailbox,
            filters=[self.DB.deleted_at.is_(None)],
        )
        return rows

    def advance(
        self,
        cursor_id: str,
        expected_validity: Optional[str],
        expected_uid: str,
        uid_validity: str,
        uid: str,
    ) -> bool:
        """Move the cursor from where the caller read it to
        (``uid_validity``, ``uid``); False when it had moved meanwhile (the
        caller lost the claim)."""
        DB = self.DB
        unchanged = (
            DB.uid_validity.is_(None)
            if expected_validity is None
            else DB.uid_validity == expected_validity
        )
        session = self.model_registry.DB.session()
        try:
            moved = session.execute(
                update(DB)
                .where(DB.id == cursor_id, unchanged, DB.last_uid == expected_uid)
                .values(
                    uid_validity=uid_validity,
                    last_uid=uid,
                    updated_at=datetime.now(timezone.utc),
                    updated_by_user_id=self.requester.id,
                )
            )
            session.commit()
            return bool(moved.rowcount == 1)
        finally:
            session.close()

    def record_poll(self, cursor_id: str, error: Optional[str]) -> None:
        """Note a poll's time, and why it failed when it did."""
        self.update(
            id=cursor_id,
            last_polled_at=datetime.now(timezone.utc),
            last_error=error[:MAX_RECORDED_ERROR_CHARACTERS] if error else None,
        )


def root_mailboxes(model_registry: Any) -> EmailInboundMailboxManager:
    return EmailInboundMailboxManager(
        requester_id=env("ROOT_ID"), model_registry=model_registry
    )


class InboundEmailManager(AbstractBLLManager, RouterMixin):
    """The signed delivery endpoint: no table of its own."""

    prefix: ClassVar[Optional[str]] = INBOUND_PREFIX
    tags: ClassVar[Optional[List[str]]] = ["Email Inbound"]
    auth_type: ClassVar[AuthType] = AuthType.NONE
    routes_to_register: ClassVar[Optional[List[RouteType]]] = []

    @custom_route(
        method="POST",
        path=INBOUND_PATH,
        output_model=InboundDelivered,
        authentication_type="none",
        raw_body=True,
        openapi_tags=("Email Inbound",),
        summary="Deliver a raw RFC 5322 message, signed with the instance's secret",
        description=(
            f"The body is the raw message (Content-Type: message/rfc822). "
            f"{SIGNATURE_HEADER} is sha256=<hex HMAC-SHA256 of "
            f"'<{TIMESTAMP_HEADER}>\\n<{RECIPIENTS_HEADER}>\\n' + the raw "
            f"body>, keyed by the provider instance's inbound_signing_secret "
            f"(at least {MIN_SIGNING_SECRET_LENGTH} characters, on a root- or "
            f"system-scoped instance). {RECIPIENTS_HEADER} lists the envelope "
            f"recipients, comma-separated (empty when unknown); the timestamp "
            f"is Unix seconds and must be recent. 401 for a bad, stale or "
            f"replayed signature; 413 for a message over 25 MiB."
        ),
        expose_in=(ExposeIn.REST,),
    )
    @rate_limit(INBOUND_RATE_LIMIT, scope="(ip, endpoint)")
    async def deliver_route(
        self, provider_instance_id: str, request: Request
    ) -> InboundDelivered:
        return await receive_signed_message(
            self.model_registry, provider_instance_id, request
        )


# A mail server has no session here: a delivery is authenticated by its
# signature alone, never a cookie, and its body is a raw message, not a
# negotiated format.
_DELIVERY_PATH = re.escape(INBOUND_PREFIX) + INBOUND_PATH.replace(
    "{provider_instance_id}", "[^/]+"
)
accept_cross_site_writes(_DELIVERY_PATH)
skip_negotiation(_DELIVERY_PATH)
