# SPDX-License-Identifier: AGPL-3.0-or-later
"""Mail received: the hook point inbound mail enters the app through.

Whatever receives a message (a provider reading a mailbox, an endpoint a
mail server delivers to) parses it with :meth:`InboundEmail.parse` and hands
it to :func:`receive_inbound_email` with the app's model registry. Every
listener registered with :func:`on_inbound_email` is then awaited with it,
in the order registered. One listener's failure is logged and does not keep
the message from the rest.
"""

from email import policy
from email.message import EmailMessage, MIMEPart
from email.parser import BytesParser
from email.utils import getaddresses
from typing import Any, Awaitable, Callable, Dict, List, Sequence

from pydantic import BaseModel, Field

from zephyrex.lib.Logging import logger

# The largest message accepted, as most mail servers cap it.
MAX_INBOUND_EMAIL_BYTES = 25 * 1024 * 1024
# The headers whose addresses a message is for.
RECIPIENT_HEADERS = ("To", "Cc", "Delivered-To", "X-Original-To")

InboundEmailListener = Callable[[Any, "InboundEmail"], Awaitable[None]]


class InboundEmail(BaseModel):
    """One received message, as listeners see it."""

    message_id: str = Field("", description="The Message-ID header")
    sender: str = Field("", description="The From address, lower-cased")
    recipients: List[str] = Field(
        default_factory=list,
        description="Every address it was delivered or addressed to, lower-cased",
    )
    subject: str = Field("", description="The Subject header")
    text: str = Field("", description="The plain-text body, else the HTML body")
    headers: Dict[str, str] = Field(default_factory=dict)

    @classmethod
    def parse(
        cls, raw: bytes, envelope_recipients: Sequence[str] = ()
    ) -> "InboundEmail":
        """A raw RFC 5322 message; ``envelope_recipients`` are the SMTP
        ``RCPT TO`` addresses, when the receiver knows them. ValueError for
        one larger than :data:`MAX_INBOUND_EMAIL_BYTES`."""
        if len(raw) > MAX_INBOUND_EMAIL_BYTES:
            raise ValueError(
                f"an inbound message is at most {MAX_INBOUND_EMAIL_BYTES} bytes"
            )
        message = BytesParser(policy=policy.default).parsebytes(raw)
        if not isinstance(message, EmailMessage):  # policy.default makes one
            raise ValueError("not an RFC 5322 message")
        addressed = getaddresses(
            [
                str(value)
                for name in RECIPIENT_HEADERS
                for value in message.get_all(name, [])
            ]
        )
        recipients: List[str] = []
        for address in [*envelope_recipients, *(email for _, email in addressed)]:
            normalized = address.strip().lower()
            if normalized and normalized not in recipients:
                recipients.append(normalized)
        senders = getaddresses([str(message.get("From", ""))])
        body = message.get_body(preferencelist=("plain", "html"))
        return cls(
            message_id=str(message.get("Message-ID", "")).strip(),
            sender=senders[0][1].strip().lower() if senders else "",
            recipients=recipients,
            subject=str(message.get("Subject", "")),
            text=_text_of(body) if body is not None else "",
            headers={name: str(value) for name, value in message.items()},
        )


def _text_of(part: MIMEPart) -> str:
    """A text part's content. One labelled with a charset Python does not
    know is read as UTF-8, undecodable bytes replaced: mail is whatever its
    sender wrote, and is never refused for it."""
    try:
        return str(part.get_content())
    except LookupError:
        payload = part.get_payload(decode=True)
        return payload.decode("utf-8", "replace") if isinstance(payload, bytes) else ""


_LISTENERS: List[InboundEmailListener] = []


def on_inbound_email(listener: InboundEmailListener) -> InboundEmailListener:
    """Call ``listener(model_registry, message)`` for every message
    received. Registering one twice registers it once."""
    if listener not in _LISTENERS:
        _LISTENERS.append(listener)
    return listener


def remove_inbound_email_listener(listener: InboundEmailListener) -> None:
    if listener in _LISTENERS:
        _LISTENERS.remove(listener)


async def receive_inbound_email(model_registry: Any, message: InboundEmail) -> int:
    """Hand ``message`` to every listener; how many took it without
    failing."""
    taken = 0
    for listener in list(_LISTENERS):
        try:
            await listener(model_registry, message)
        except Exception:  # one listener must not keep mail from the rest
            logger.exception(
                "Inbound email listener %s failed on %s",
                getattr(listener, "__qualname__", listener),
                message.message_id,
            )
            continue
        taken += 1
    return taken
