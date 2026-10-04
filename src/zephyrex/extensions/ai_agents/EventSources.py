# SPDX-License-Identifier: AGPL-3.0-or-later
"""What fires event triggers from outside: a signed webhook call, and mail.

A **webhook** trigger has a secret, made by the server, shown once and kept
encrypted. A call to ``POST /v1/invocation-trigger/{id}/webhook`` is a
signed request (``zephyrex.lib.SignedRequests``): ``X-Zephyrex-Timestamp``
(Unix seconds) and ``X-Zephyrex-Signature``: ``sha256=`` and the hex
HMAC-SHA256, keyed by the secret, of the timestamp, a ``.`` and the raw
body. A call is refused (401) unless the signature is right, the timestamp
is within the replay window of now and the same signature has not been
seen within that window. The body, a JSON
object of at most :data:`MAX_WEBHOOK_BODY_BYTES`, is the turn's payload. No session or token
the call carries is ever used: the turn is the agent's owner's.

An **email** trigger has an address the server made (unguessable, at
``AI_AGENTS_EMAIL_DOMAIN``). Mail the email extension receives is handed to
:func:`receive_email`, which fires each enabled email trigger it is
addressed to whose ``event_filter`` (``from``, ``subject``) it matches, the
message as the turn's payload.

Each turn's payload is the trigger's instructions, when it has any, then
the event.
"""

import secrets
from datetime import datetime, timezone
from typing import Any, List, Mapping, Optional

from fastapi import HTTPException, Request

from zephyrex.extensions.ai_agents.AgentTurnExecutor import fire_turn
from zephyrex.extensions.ai_agents.BLL_AI_Agents import (
    EMAIL,
    WEBHOOK,
    InvocationTriggerManager,
    InvocationTriggerModel,
    WebhookFired,
    email_filter,
)
from zephyrex.extensions.email.InboundEmail import InboundEmail
from zephyrex.lib.Environment import env
from zephyrex.lib.Logging import logger
from zephyrex.lib.RequestBody import capped_body
from zephyrex.lib.SecretEncryption import decrypt_secret
from zephyrex.lib.SignedRequests import (
    SignedRequests,
    lower_case_headers,
    unsigned,
)

MAX_WEBHOOK_BODY_BYTES = 64 * 1024
WEBHOOK_TOO_LARGE = f"A webhook body is at most {MAX_WEBHOOK_BODY_BYTES} bytes"
WEBHOOK_SECRET_BYTES = 32
# The most of an email's text a turn is handed.
MAX_EMAIL_TEXT_CHARACTERS = 32 * 1024


def new_webhook_secret() -> str:
    return secrets.token_urlsafe(WEBHOOK_SECRET_BYTES)


def _signed_content(timestamp: str, headers: Mapping[str, str], body: bytes) -> bytes:
    """What a webhook call signs: the timestamp, a ``.`` and the body."""
    return timestamp.encode() + b"." + body


WEBHOOK_SIGNING = SignedRequests(
    replay_scope="ai_agents:webhook", signed_content=_signed_content
)


def webhook_signature(secret: str, timestamp: str, body: bytes) -> str:
    """The ``X-Zephyrex-Signature`` value a call signed with ``secret``
    carries."""
    return WEBHOOK_SIGNING.signature(secret, timestamp, {}, body)


def _stored_trigger(model_registry: Any, trigger_id: str) -> Optional[Any]:
    """The trigger row with its secret: read from the table itself, since
    the secret never leaves the model in a serialization (or a cache)."""
    rows = InvocationTriggerModel.DB(model_registry.DB.manager.Base).list(
        requester_id=env("ROOT_ID"),
        model_registry=model_registry,
        return_type="dto",
        override_dto=InvocationTriggerModel,
        id=trigger_id,
    )
    return rows[0] if rows else None


def _payload(trigger: Any, event: str) -> str:
    """The trigger's instructions, when it has any, then the event."""
    instructions = (trigger.invocation_payload or "").strip()
    return f"{instructions}\n\n{event}" if instructions else event


def count_firing(model_registry: Any, trigger: Any) -> None:
    """Record that an event trigger fired (the server's bookkeeping)."""
    InvocationTriggerManager(
        requester_id=env("ROOT_ID"), model_registry=model_registry
    ).update(
        id=trigger.id,
        last_fired_at=datetime.now(timezone.utc),
        fire_count=(trigger.fire_count or 0) + 1,
    )


async def receive_webhook(
    model_registry: Any, trigger_id: str, request: Request
) -> WebhookFired:
    """Verify a call to the trigger's webhook and fire its turn, as the
    agent's owner, handed the body."""
    body = await capped_body(request, MAX_WEBHOOK_BODY_BYTES, WEBHOOK_TOO_LARGE)
    trigger = _stored_trigger(model_registry, trigger_id)
    if (
        trigger is None
        or trigger.invocation_type != "event"
        or trigger.event_source != WEBHOOK
        or not trigger.webhook_secret
    ):
        raise unsigned()
    WEBHOOK_SIGNING.accept(
        decrypt_secret(trigger.webhook_secret),
        trigger.id,
        lower_case_headers(request.headers.items()),
        body,
    )
    if not trigger.enabled:
        raise HTTPException(status_code=409, detail="The trigger is disabled")
    try:
        event = body.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=400, detail="A webhook body is UTF-8 text"
        ) from None
    turn = await fire_turn(
        model_registry,
        trigger.agent_id,
        _payload(trigger, event),
        trigger_id=trigger.id,
    )
    count_firing(model_registry, trigger)
    return WebhookFired(invocation_instance_id=turn.id, status=turn.status)


def matches(trigger: Any, message: InboundEmail) -> bool:
    """Whether ``message`` is for the email trigger: addressed to it, and
    matching its filter."""
    address = (trigger.email_address or "").lower()
    if not address or address not in message.recipients:
        return False
    criteria = email_filter(trigger.event_filter)
    sender = criteria.get("from", "").lower()
    if sender and not (
        message.sender == sender
        or (sender.startswith("@") and message.sender.endswith(sender))
    ):
        return False
    subject = criteria.get("subject", "").lower()
    return not subject or subject in message.subject.lower()


def email_event(message: InboundEmail) -> str:
    """The message as a turn reads it."""
    text = message.text
    if len(text) > MAX_EMAIL_TEXT_CHARACTERS:
        text = text[:MAX_EMAIL_TEXT_CHARACTERS] + "\n[truncated]"
    return (
        f"An email arrived.\nFrom: {message.sender}\n"
        f"To: {', '.join(message.recipients)}\nSubject: {message.subject}\n"
        f"Message-ID: {message.message_id}\n\n{text}"
    )


async def receive_email(model_registry: Any, message: InboundEmail) -> List[str]:
    """Fire every enabled email trigger the message is for; the turns'
    ids. A trigger whose agent cannot take the turn is skipped."""
    triggers = InvocationTriggerManager(
        requester_id=env("ROOT_ID"), model_registry=model_registry
    )
    candidates = {
        trigger.id: trigger
        for recipient in message.recipients
        for trigger in triggers.list(
            invocation_type="event",
            event_source=EMAIL,
            enabled=True,
            email_address=recipient,
        )
    }
    turns: List[str] = []
    for trigger in candidates.values():
        if not matches(trigger, message):
            continue
        try:
            turn = await fire_turn(
                model_registry,
                trigger.agent_id,
                _payload(trigger, email_event(message)),
                trigger_id=trigger.id,
            )
        except HTTPException as refused:
            logger.warning(
                "Email trigger %s cannot fire: %s", trigger.id, refused.detail
            )
            continue
        count_firing(model_registry, trigger)
        turns.append(turn.id)
    return turns
