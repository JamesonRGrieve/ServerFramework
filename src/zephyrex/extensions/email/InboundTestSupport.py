# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the inbound mail tests share: operator provider instances with
settings, real RFC 5322 messages, and a listener on the inbound hook."""

import uuid
from contextlib import contextmanager
from email.message import EmailMessage
from typing import Any, Dict, Iterator, List

from zephyrex.extensions.email.EmailTestSupport import email_instance
from zephyrex.extensions.email.InboundEmail import (
    InboundEmail,
    on_inbound_email,
    remove_inbound_email_listener,
)
from zephyrex.logic.BLL_Providers import ProviderInstanceModel


def operator_instance(
    model_registry: Any,
    settings: Dict[str, str],
    *,
    provider_name: str = "imap",
    scope: str = "root",
) -> ProviderInstanceModel:
    """A new instance of an email provider, made by ROOT, with settings."""
    return email_instance(model_registry, provider_name, settings, scope=scope)


def raw_message(subject: str = "Hello", to: str = "desk@example.org") -> bytes:
    message = EmailMessage()
    message["From"] = "Sender <sender@example.net>"
    message["To"] = to
    message["Subject"] = subject
    message["Message-ID"] = f"<{uuid.uuid4().hex}@example.net>"
    message.set_content("The body.")
    return message.as_bytes()


@contextmanager
def listening(extension_name: str = "email") -> Iterator[List[InboundEmail]]:
    """Every message the inbound hook hands a listener of
    ``extension_name``'s, while open."""
    seen: List[InboundEmail] = []

    async def listener(model_registry: Any, message: InboundEmail) -> None:
        seen.append(message)

    on_inbound_email(extension_name, listener)
    try:
        yield seen
    finally:
        remove_inbound_email_listener(listener)
