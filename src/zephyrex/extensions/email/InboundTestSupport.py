# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the inbound mail tests share: operator provider instances with
settings, real RFC 5322 messages, and a listener on the inbound hook."""

import uuid
from contextlib import contextmanager
from email.message import EmailMessage
from typing import Any, Dict, Iterator, List

from zephyrex.extensions.email.InboundEmail import (
    InboundEmail,
    on_inbound_email,
    remove_inbound_email_listener,
)
from zephyrex.lib.Environment import env
from zephyrex.logic.BLL_Providers import (
    ProviderInstanceManager,
    ProviderInstanceModel,
    ProviderInstanceSettingManager,
    ProviderManager,
)


def operator_instance(
    model_registry: Any,
    settings: Dict[str, str],
    *,
    provider_name: str = "imap",
    scope: str = "root",
) -> ProviderInstanceModel:
    """A new instance of an email provider, made by ROOT, with settings."""
    root_id = env("ROOT_ID")
    provider = ProviderManager(model_registry=model_registry, requester_id=root_id).get(
        name=provider_name
    )
    instance = ProviderInstanceModel.model_validate(
        ProviderInstanceManager(
            model_registry=model_registry, requester_id=root_id
        ).create(
            name=f"inbound_{uuid.uuid4().hex}", provider_id=provider.id, scope=scope
        ),
        from_attributes=True,
    )
    rows = ProviderInstanceSettingManager(
        model_registry=model_registry, requester_id=root_id
    )
    for key, value in settings.items():
        rows.create(provider_instance_id=instance.id, key=key, value=value)
    return instance


def raw_message(subject: str = "Hello", to: str = "desk@example.org") -> bytes:
    message = EmailMessage()
    message["From"] = "Sender <sender@example.net>"
    message["To"] = to
    message["Subject"] = subject
    message["Message-ID"] = f"<{uuid.uuid4().hex}@example.net>"
    message.set_content("The body.")
    return message.as_bytes()


@contextmanager
def listening() -> Iterator[List[InboundEmail]]:
    """Every message the inbound hook hands its listeners, while open."""
    seen: List[InboundEmail] = []

    async def listener(model_registry: Any, message: InboundEmail) -> None:
        seen.append(message)

    on_inbound_email(listener)
    try:
        yield seen
    finally:
        remove_inbound_email_listener(listener)
