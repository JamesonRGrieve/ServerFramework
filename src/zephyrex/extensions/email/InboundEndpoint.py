# SPDX-License-Identifier: AGPL-3.0-or-later
"""Mail a mail server delivers over HTTP: a signed POST of the raw message.

A mail server (a Postfix pipe transport, a provider's inbound route) POSTs
the message, exactly as received, to
``POST /v1/email/inbound/{provider_instance_id}`` with the headers:

- ``X-Zephyrex-Timestamp``: the time of signing, in Unix seconds;
- ``X-Zephyrex-Recipients``: the envelope recipients (``RCPT TO``),
  comma-separated; absent, or empty, when the sender does not know them;
- ``X-Zephyrex-Signature``: ``sha256=`` and the lower-case hex
  HMAC-SHA256, keyed by the instance's ``inbound_signing_secret``, of the
  timestamp, a line feed, the recipients header exactly as sent (empty when
  absent), a line feed, and the raw body.

A header value cannot hold a line feed, so no recipient list and body can be
re-split to sign the same bytes: the recipients are signed with the message.

The delivery is a signed request (``zephyrex.lib.SignedRequests``). A call
is refused with 401, and one message whatever the reason, unless the
instance is an enabled, operator-scoped (root or system) email provider
instance with a signing secret of at least :data:`MIN_SIGNING_SECRET_LENGTH`
characters, the signature is right, the timestamp is within the replay
window of now, and the same signature has not been seen within that
window. A body over :data:`MAX_INBOUND_EMAIL_BYTES` is
refused with 413. No session or token the call carries is ever used.

A message that passes is parsed and handed to the inbound hook point
(:func:`receive_inbound_email`).
"""

from email.utils import parseaddr
from typing import Any, List, Mapping, Optional

from fastapi import HTTPException, Request
from pydantic import BaseModel as RouteModel
from pydantic import Field

from zephyrex.extensions.email.InboundEmail import (
    MAX_INBOUND_EMAIL_BYTES,
    InboundEmail,
    receive_inbound_email,
)
from zephyrex.lib.Environment import env
from zephyrex.lib.Logging import logger
from zephyrex.lib.SignedRequests import (
    SignedRequests,
    lower_case_headers,
    unsigned,
)
from zephyrex.logic.BLL_Providers import OPERATOR_SCOPES, ProviderInstanceModel

RECIPIENTS_HEADER = "X-Zephyrex-Recipients"
SIGNING_SECRET_SETTING = "inbound_signing_secret"
# A shorter secret is open to guessing from one captured delivery.
MIN_SIGNING_SECRET_LENGTH = 32
# More envelope recipients than any one delivery has.
MAX_ENVELOPE_RECIPIENTS = 100
# The longest address SMTP allows (RFC 5321 §4.5.3.1.3).
MAX_ADDRESS_LENGTH = 254


class InboundDelivered(RouteModel):
    """What became of a delivered message."""

    message_id: str = Field(description="Its Message-ID header")
    listeners: int = Field(description="How many inbound listeners took it")


def _signed_content(timestamp: str, headers: Mapping[str, str], body: bytes) -> bytes:
    """What a delivery signs: the timestamp, the recipients header as sent
    (empty when absent) and the body, each after a line feed but the
    first."""
    recipients = headers.get(RECIPIENTS_HEADER.lower(), "")
    return b"\n".join((timestamp.encode(), recipients.encode(), body))


INBOUND_SIGNING = SignedRequests(
    replay_scope="email:inbound", signed_content=_signed_content
)


def inbound_signature(secret: str, timestamp: str, recipients: str, body: bytes) -> str:
    """The ``X-Zephyrex-Signature`` value of a delivery signed with
    ``secret``."""
    return INBOUND_SIGNING.signature(
        secret, timestamp, {RECIPIENTS_HEADER.lower(): recipients}, body
    )


def verified(
    secret: str, headers: Mapping[str, str], body: bytes, now: float
) -> Optional[str]:
    """The delivery's signature when it is right and its timestamp fresh,
    else None. ``headers`` are keyed lower-case."""
    return INBOUND_SIGNING.verified(secret, headers, body, now)


def envelope_recipients(header: str) -> List[str]:
    """The addresses an ``X-Zephyrex-Recipients`` value names. ValueError
    for one that is not a bare address, or for too many."""
    addresses = [part.strip() for part in header.split(",") if part.strip()]
    if len(addresses) > MAX_ENVELOPE_RECIPIENTS:
        raise ValueError(f"at most {MAX_ENVELOPE_RECIPIENTS} envelope recipients")
    for address in addresses:
        local, at, domain = address.rpartition("@")
        if (
            not at
            or not local
            or not domain
            or len(address) > MAX_ADDRESS_LENGTH
            or not address.isascii()
            or any(character.isspace() for character in address)
            or parseaddr(address)[1] != address
        ):
            raise ValueError("an envelope recipient is a bare address")
    return addresses


def too_large() -> HTTPException:
    return HTTPException(
        status_code=413,
        detail=f"An inbound message is at most {MAX_INBOUND_EMAIL_BYTES} bytes",
    )


async def capped_body(request: Request, limit: int = MAX_INBOUND_EMAIL_BYTES) -> bytes:
    """The request body, refused (413) as soon as it is longer than
    ``limit``: by its declared length, or as it streams in."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise too_large()
    chunks: List[bytes] = []
    received = 0
    async for chunk in request.stream():
        received += len(chunk)
        if received > limit:
            raise too_large()
        chunks.append(chunk)
    return b"".join(chunks)


def operator_instance(
    model_registry: Any, provider_instance_id: str
) -> Optional[ProviderInstanceModel]:
    """The live, enabled provider instance ``provider_instance_id`` when it
    speaks for the operator (root or system scope, which only ROOT and
    SYSTEM configure), else None. Read as ROOT: the caller has no session."""
    instance_db = ProviderInstanceModel.DB(model_registry.DB.manager.Base)
    rows: List[ProviderInstanceModel] = instance_db.list(
        requester_id=env("ROOT_ID"),
        model_registry=model_registry,
        return_type="dto",
        override_dto=ProviderInstanceModel,
        filters=[
            instance_db.id == provider_instance_id,
            instance_db.deleted_at.is_(None),
        ],
    )
    if not rows:
        return None
    instance = rows[0]
    if instance.enabled is False or instance.scope not in OPERATOR_SCOPES:
        return None
    return instance


def signing_secret(instance: ProviderInstanceModel) -> Optional[str]:
    """The instance's inbound signing secret when its provider is an email
    provider and the secret is strong enough, else None."""
    from zephyrex.extensions.email.EXT_EMail import EXT_EMail

    try:
        provider = EXT_EMail.provider_class_for(instance)
    except LookupError:
        return None
    secret = provider.setting(instance, SIGNING_SECRET_SETTING)
    if not secret:
        return None
    if len(secret) < MIN_SIGNING_SECRET_LENGTH:
        logger.warning(
            "email: inbound instance %s has a signing secret under %s "
            "characters; deliveries to it are refused",
            instance.id,
            MIN_SIGNING_SECRET_LENGTH,
        )
        return None
    return secret


async def receive_signed_message(
    model_registry: Any, provider_instance_id: str, request: Request
) -> InboundDelivered:
    """Verify a delivery to the instance's endpoint and hand its message to
    the inbound listeners."""
    body = await capped_body(request)
    instance = operator_instance(model_registry, provider_instance_id)
    secret = signing_secret(instance) if instance is not None else None
    if instance is None or secret is None:
        raise unsigned()
    headers = lower_case_headers(request.headers.items())
    INBOUND_SIGNING.accept(secret, instance.id, headers, body)
    if not body:
        raise HTTPException(status_code=400, detail="The body is the raw message")
    try:
        recipients = envelope_recipients(headers.get(RECIPIENTS_HEADER.lower(), ""))
        message = InboundEmail.parse(body, recipients)
    except ValueError as refused:
        raise HTTPException(status_code=400, detail=str(refused)) from None
    taken = await receive_inbound_email(model_registry, message)
    return InboundDelivered(message_id=message.message_id, listeners=taken)
