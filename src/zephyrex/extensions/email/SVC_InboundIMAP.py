# SPDX-License-Identifier: AGPL-3.0-or-later
"""The poller that reads configured IMAP mailboxes into the inbound hook.

Every :data:`TICK_SECONDS` it looks for mailboxes that are due: root- and
system-scoped, enabled instances of IMAP-reading email providers with
``inbound_enabled`` true (see InboundIMAP). For each, it signs in, selects
the mailbox and reads the messages above its cursor
(:class:`EmailInboundMailboxModel`), at most :data:`MAX_MESSAGES_PER_POLL`
a poll, oldest first. Each message is fetched, claimed by moving the cursor
past it (a compare-and-set, so of two workers one delivers it), parsed and
handed to :func:`receive_inbound_email`, then flagged \\Seen, moved or
deleted as configured.

Nothing is delivered twice: a UID at or below the cursor is never taken
again. When the server's UIDVALIDITY changes, the UIDs read before name
nothing, so the cursor starts over under the new UIDVALIDITY; mail already
delivered is kept out by its \\Seen flag, or by having been moved or
deleted. A message over 25 MiB is passed over, left where it is.

A mailbox whose poll fails (unreachable, refused sign-in, misconfigured)
waits twice as long each time, up to :data:`MAX_BACKOFF_SECONDS`, with the
reason on its cursor; the others are polled as usual and the service keeps
running.
"""

import asyncio
import imaplib
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional, Tuple, Type

from zephyrex.extensions.email.BLL_InboundEmail import root_mailboxes
from zephyrex.extensions.email.InboundEmail import (
    MAX_INBOUND_EMAIL_BYTES,
    InboundEmail,
    receive_inbound_email,
)
from zephyrex.extensions.email.InboundIMAP import (
    DEFAULT_POLL_SECONDS,
    IMAPMailbox,
    InboundConfigError,
    MailboxConfig,
    inbound_enabled,
    mailbox_config,
)
from zephyrex.lib.Environment import env
from zephyrex.lib.Logging import logger
from zephyrex.logic.AbstractService import AbstractService
from zephyrex.logic.BLL_Providers import (
    OPERATOR_SCOPES,
    ProviderInstanceModel,
    ProviderModel,
)

SERVICE_ID = "email_inbound_imap"
# How often the poller looks for due mailboxes; each mailbox's own
# inbound_poll_seconds decides how often it is read.
TICK_SECONDS = 5
# Every IMAP socket operation (connect, TLS handshake, each command) gives
# up after this long.
IMAP_TIMEOUT_SECONDS = 30
MAX_MESSAGES_PER_POLL = 50
MAX_BACKOFF_SECONDS = 3600
# What a failed poll can raise: the network, TLS, the server's answers, and
# settings it can never be read with (InboundConfigError is a ValueError).
POLL_FAILURES = (OSError, imaplib.IMAP4.error, ValueError, EOFError)


@dataclass
class _Schedule:
    next_due: float = 0.0
    failures: int = 0


@dataclass(frozen=True)
class PollResult:
    delivered: int
    more_waiting: bool


def polled_instances(model_registry: Any) -> Iterator[Tuple[Type[Any], Any]]:
    """(provider class, instance) of every mailbox the poller reads."""
    from zephyrex.extensions.email.EXT_EMail import AbstractEmailProvider, EXT_EMail

    root_id = env("ROOT_ID")
    base = model_registry.DB.manager.Base
    provider_db, instance_db = ProviderModel.DB(base), ProviderInstanceModel.DB(base)
    for provider_class in EXT_EMail.providers:
        if not (
            issubclass(provider_class, AbstractEmailProvider)
            and provider_class.polls_imap
        ):
            continue
        records = provider_db.list(
            requester_id=root_id,
            model_registry=model_registry,
            return_type="dto",
            override_dto=ProviderModel,
            name=provider_class.name,
        )
        for record in records:
            instances: List[ProviderInstanceModel] = instance_db.list(
                requester_id=root_id,
                model_registry=model_registry,
                return_type="dto",
                override_dto=ProviderInstanceModel,
                filters=[
                    instance_db.provider_id == record.id,
                    instance_db.deleted_at.is_(None),
                ],
            )
            for instance in instances:
                if (
                    instance.enabled is not False
                    and instance.scope in OPERATOR_SCOPES
                    and inbound_enabled(provider_class, instance)
                ):
                    yield provider_class, instance


class InboundIMAPService(AbstractService):
    """Polls every configured mailbox when it is due, as ROOT."""

    def __init__(
        self,
        requester_id: str,
        model_registry: Any,
        interval_seconds: int = TICK_SECONDS,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            requester_id=requester_id,
            interval_seconds=interval_seconds,
            service_id=SERVICE_ID,
            **kwargs,
        )
        self.model_registry = model_registry
        self._schedules: Dict[str, _Schedule] = {}

    async def update(self) -> None:
        """Poll each due mailbox; one mailbox's failure never stops the
        others, or the service."""
        now = time.monotonic()
        try:
            mailboxes = list(polled_instances(self.model_registry))
        except Exception as exc:  # the database is away: try again next tick
            logger.warning(
                "email: cannot list inbound mailboxes (%s: %s)", type(exc).__name__, exc
            )
            return
        for provider_class, instance in mailboxes:
            instance_id = str(instance.id)
            if self._schedules.get(instance_id, _Schedule()).next_due > now:
                continue
            try:
                await self.poll(provider_class, instance)
            except Exception as exc:  # one mailbox's fault never stops the rest
                self._failed(instance_id, DEFAULT_POLL_SECONDS, exc)

    async def poll(self, provider_class: Type[Any], instance: Any) -> PollResult:
        """Read the instance's mailbox once, and schedule its next poll."""
        instance_id = str(instance.id)
        try:
            config = mailbox_config(provider_class, instance)
        except InboundConfigError as exc:
            self._failed(instance_id, DEFAULT_POLL_SECONDS, exc)
            return PollResult(0, False)
        cursors = root_mailboxes(self.model_registry)
        cursor = cursors.cursor(instance_id, config.mailbox)
        try:
            result = await self._read(config, cursor)
        except POLL_FAILURES as exc:
            self._failed(instance_id, config.poll_seconds, exc)
            cursors.record_poll(cursor.id, f"{type(exc).__name__}: {exc}")
            return PollResult(0, False)
        self._schedules[instance_id] = _Schedule(
            next_due=time.monotonic()
            + (0 if result.more_waiting else config.poll_seconds)
        )
        cursors.record_poll(cursor.id, None)
        return result

    def _failed(self, instance_id: str, poll_seconds: int, error: Exception) -> None:
        schedule = self._schedules.setdefault(instance_id, _Schedule())
        schedule.failures += 1
        delay = min(poll_seconds * 2**schedule.failures, MAX_BACKOFF_SECONDS)
        schedule.next_due = time.monotonic() + delay
        # The error only: a traceback renders frame locals, which hold the
        # mailbox password.
        logger.warning(
            "email: inbound mailbox of instance %s failed (%s: %s); next try in %ss",
            instance_id,
            type(error).__name__,
            error,
            delay,
        )

    async def _read(self, config: MailboxConfig, cursor: Any) -> PollResult:
        mailbox = await asyncio.to_thread(
            IMAPMailbox.open, config, IMAP_TIMEOUT_SECONDS
        )
        try:
            return await self._deliver_new(mailbox, cursor)
        finally:
            await asyncio.to_thread(mailbox.close)

    async def _deliver_new(self, mailbox: IMAPMailbox, cursor: Any) -> PollResult:
        cursors = root_mailboxes(self.model_registry)
        validity: Optional[str] = cursor.uid_validity
        last_uid = cursor.last_uid
        if mailbox.uid_validity != validity:
            if validity is not None:
                logger.warning(
                    "email: UIDVALIDITY of %s on instance %s changed from %s "
                    "to %s; reading it afresh",
                    mailbox.config.mailbox,
                    mailbox.config.provider_instance_id,
                    validity,
                    mailbox.uid_validity,
                )
            if not cursors.advance(
                cursor.id, validity, last_uid, mailbox.uid_validity, "0"
            ):
                return PollResult(0, False)
            validity, last_uid = mailbox.uid_validity, "0"
        waiting = await asyncio.to_thread(mailbox.new_uids, int(last_uid))
        batch = waiting[:MAX_MESSAGES_PER_POLL]
        sizes = await asyncio.to_thread(mailbox.sizes, batch)
        delivered = 0
        for uid in batch:
            oversized = sizes.get(uid, 0) > MAX_INBOUND_EMAIL_BYTES
            raw = None if oversized else await asyncio.to_thread(mailbox.fetch, uid)
            if not cursors.advance(
                cursor.id, validity, last_uid, mailbox.uid_validity, str(uid)
            ):
                # Another worker is reading this mailbox: it delivers the rest.
                return PollResult(delivered, False)
            last_uid = str(uid)
            if oversized or raw is None:
                if oversized:
                    logger.warning(
                        "email: passed over UID %s of %s on instance %s: over %s bytes",
                        uid,
                        mailbox.config.mailbox,
                        mailbox.config.provider_instance_id,
                        MAX_INBOUND_EMAIL_BYTES,
                    )
                continue
            try:
                message = InboundEmail.parse(raw)
            except ValueError as exc:
                logger.warning(
                    "email: passed over UID %s on instance %s: %s",
                    uid,
                    mailbox.config.provider_instance_id,
                    exc,
                )
                continue
            await receive_inbound_email(self.model_registry, message)
            delivered += 1
            await asyncio.to_thread(mailbox.finish, uid)
        return PollResult(delivered, len(waiting) > len(batch))
