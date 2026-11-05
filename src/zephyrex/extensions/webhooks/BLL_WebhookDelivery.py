# SPDX-License-Identifier: AGPL-3.0-or-later
"""Outbound webhook delivery (issue #203): events sent to endpoints users
subscribe.

A **subscription** is a user's: a target URL, the event types it wants
(``*`` for all) and an HMAC secret. The secret is stored encrypted and never
returned; it signs every delivery (``X-Webhook-Signature: sha256=<hex>`` over
the exact body sent), mirroring the inbound ``verify_signature`` convention.
Subscriptions are managed over ``/v1/webhook-subscription`` (owner-scoped,
held to If-Match like every save).

An event reaches a subscription only when its owner can see the record the
event is about (:func:`dispatch_webhook_event` names it), so no one learns
of records they could not read. Each match is queued as a **delivery** row,
readable by the subscription's owner at ``/v1/webhook-delivery`` and written
only by the server. :func:`deliver_due` (run by ``SVC_WebhookDelivery``)
claims due deliveries with a compare-and-set, so of two workers one sends
each, and POSTs them through the shared client (SSRF guard, TLS policy). A
failure waits twice as long each time, up to :data:`MAX_DELAY_SECONDS`;
after :data:`MAX_ATTEMPTS` the delivery is dead-lettered.
"""

import hashlib
import hmac
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, ClassVar, Dict, List, Optional, Type
from urllib.parse import urlsplit

from fastapi import HTTPException
from pydantic import Field
from sqlalchemy import update

from zephyrex.lib.Environment import env
from zephyrex.lib.Logging import logger
from zephyrex.lib.SecretEncryption import decrypt_secret, encrypt_secret
from zephyrex.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    ModelMeta,
    StringSearchModel,
    UpdateMixinModel,
)
from zephyrex.logic.AbstractLogicManager.ownership import (
    OWNERSHIP_FIELDS,
    each_created,
    owned_by,
    server_side,
    without,
)
from zephyrex.logic.BLL_Auth import UserModel
from zephyrex.pydantic2.fastapi import AuthType, RouterMixin, RouteType
from zephyrex.pydantic2.registry import BaseModel

SIGNATURE_HEADER = "X-Webhook-Signature"
EVENT_HEADER = "X-Webhook-Event"
DELIVERY_ID_HEADER = "X-Webhook-Delivery"

ALL_EVENTS = "*"
PENDING, DELIVERED, DEAD = "pending", "delivered", "dead"
MAX_ATTEMPTS = 5
BASE_DELAY_SECONDS = 2.0
MAX_DELAY_SECONDS = 3600.0
# A claimed delivery is not due again until this lease ends, so a worker
# that dies mid-send leaves it to be retried rather than lost.
CLAIM_LEASE_SECONDS = 120.0
DELIVERY_TIMEOUT_SECONDS = 15.0
DELIVERIES_PER_RUN = 50
MAX_RECORDED_ERROR_CHARACTERS = 500
MIN_SECRET_CHARACTERS = 16
MAX_EVENT_TYPES = 50
_EVENT_TYPE = re.compile(r"^(\*|[A-Za-z0-9][A-Za-z0-9_.:-]{0,99})$")
_TARGET_SCHEMES = ("https", "http")

HttpPost = Callable[..., Awaitable[Any]]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def sign_payload(secret: str, body: bytes) -> str:
    """``sha256=<hex>`` HMAC-SHA256 of ``body`` keyed by ``secret``: the
    signature receivers verify (mirrors the inbound convention)."""
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise HTTPException(status_code=422, detail=f"{name} must be a string")
    return value


def checked_target_url(url: Any) -> str:
    """An absolute http(s) URL with a host and no credentials, or 422.
    Where it may point is the SSRF guard's call, at every delivery."""
    parts = urlsplit(_text(url, "target_url").strip())
    if parts.scheme not in _TARGET_SCHEMES or not parts.hostname:
        raise HTTPException(
            status_code=422, detail="target_url must be an absolute http(s) URL"
        )
    if parts.username or parts.password:
        raise HTTPException(
            status_code=422, detail="target_url must not carry credentials"
        )
    return parts.geturl()


def checked_event_types(value: Any) -> str:
    """Space- or comma-separated event types (``*`` for all), normalised."""
    names = [
        name
        for name in re.split(r"[\s,]+", _text(value, "event_types").strip())
        if name
    ]
    if not names or len(names) > MAX_EVENT_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"event_types names 1 to {MAX_EVENT_TYPES} event types",
        )
    for name in names:
        if not _EVENT_TYPE.fullmatch(name):
            raise HTTPException(
                status_code=422, detail=f"{name!r} is not an event type"
            )
    return " ".join(dict.fromkeys(names))


def checked_secret(secret: Any) -> str:
    text = _text(secret, "secret")
    if len(text) < MIN_SECRET_CHARACTERS:
        raise HTTPException(
            status_code=422,
            detail=f"secret must be at least {MIN_SECRET_CHARACTERS} characters",
        )
    return text


class WebhookSubscriptionModel(
    ApplicationModel,
    UpdateMixinModel,
    UserModel.Reference,
    metaclass=ModelMeta,
):
    """An endpoint a user has events delivered to. The secret is
    encrypted, and never serialized."""

    target_url: str = Field(..., description="Where deliveries are POSTed")
    event_types: str = Field(
        ALL_EVENTS, description="Space-separated event types delivered; * for all"
    )
    # Optional here only because a response never carries it; Create
    # requires one.
    secret: Optional[str] = Field(
        None,
        exclude=True,
        description="The HMAC key deliveries are signed with (encrypted)",
    )
    active: bool = Field(True, description="Whether events are delivered")

    table_comment: ClassVar[str] = (
        "An endpoint a user has outbound webhook events delivered to"
    )
    is_system_entity: ClassVar[bool] = False

    def matches(self, event_type: str) -> bool:
        wanted = self.event_types.split()
        return bool(self.active) and (ALL_EVENTS in wanted or event_type in wanted)

    class Create(BaseModel, UserModel.Reference.ID.Optional):
        target_url: str
        event_types: str = ALL_EVENTS
        secret: str = Field(..., description="The HMAC key; never returned")
        active: bool = True

    class Update(BaseModel):
        target_url: Optional[str] = None
        event_types: Optional[str] = None
        secret: Optional[str] = Field(None, description="A new HMAC key")
        active: Optional[bool] = None

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
        UserModel.Reference.ID.Search,
    ):
        target_url: Optional[StringSearchModel] = None
        event_types: Optional[StringSearchModel] = None
        active: Optional[bool] = None


def _checked_fields(fields: Dict[str, Any]) -> Dict[str, Any]:
    """A subscription's fields validated, the secret encrypted."""
    if fields.get("target_url") is not None:
        fields["target_url"] = checked_target_url(fields["target_url"])
    if fields.get("event_types") is not None:
        fields["event_types"] = checked_event_types(fields["event_types"])
    if fields.get("secret") is not None:
        fields["secret"] = encrypt_secret(checked_secret(fields["secret"]))
    return fields


class WebhookSubscriptionManager(AbstractBLLManager, RouterMixin):
    _model = WebhookSubscriptionModel
    prefix: ClassVar[Optional[str]] = "/v1/webhook-subscription"
    tags: ClassVar[Optional[List[str]]] = ["Webhook Delivery"]
    auth_type: ClassVar[AuthType] = AuthType.JWT

    def create(self, **kwargs: Any) -> Any:
        """Subscriptions are the requester's (ROOT and SYSTEM may name the
        owner)."""
        owned = owned_by(self.requester.id)
        return super().create(
            **each_created(kwargs, lambda fields: _checked_fields(owned(fields)))
        )

    def update(self, id: str, **kwargs: Any) -> Any:
        """An update never moves a subscription to another owner."""
        return super().update(id, **_checked_fields(without(kwargs, OWNERSHIP_FIELDS)))


class WebhookDeliveryModel(
    ApplicationModel,
    UpdateMixinModel,
    WebhookSubscriptionModel.Reference.ID,
    metaclass=ModelMeta,
):
    """One event queued for, or sent to, a subscription. Readable as its
    subscription is; written only by the server."""

    event_type: str = Field(..., description="The event delivered")
    payload: str = Field(..., description="The JSON body POSTed, exactly as signed")
    status: str = Field(PENDING, description="pending, delivered or dead")
    attempts: int = Field(0, description="Sends tried so far")
    next_attempt_at: datetime = Field(..., description="When it is next due")
    last_error: Optional[str] = Field(None, description="Why the last send failed")
    delivered_at: Optional[datetime] = Field(None, description="When it was accepted")

    table_comment: ClassVar[str] = (
        "Outbound webhook deliveries: queued, sent, or dead-lettered"
    )
    is_system_entity: ClassVar[bool] = False
    permission_references: ClassVar[List[str]] = ["webhook_subscription"]

    class Create(BaseModel, WebhookSubscriptionModel.Reference.ID):
        event_type: str
        payload: str
        status: str = PENDING
        attempts: int = 0
        next_attempt_at: datetime

    class Update(BaseModel):
        status: Optional[str] = None
        attempts: Optional[int] = None
        next_attempt_at: Optional[datetime] = None
        last_error: Optional[str] = None
        delivered_at: Optional[datetime] = None

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
        WebhookSubscriptionModel.Reference.ID.Search,
    ):
        event_type: Optional[StringSearchModel] = None
        status: Optional[StringSearchModel] = None


class WebhookDeliveryManager(AbstractBLLManager, RouterMixin):
    _model = WebhookDeliveryModel
    prefix: ClassVar[Optional[str]] = "/v1/webhook-delivery"
    tags: ClassVar[Optional[List[str]]] = ["Webhook Delivery"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    routes_to_register: ClassVar[Optional[List[RouteType]]] = [
        RouteType.GET,
        RouteType.LIST,
        RouteType.SEARCH,
    ]

    def create(self, **kwargs: Any) -> Any:
        """Only the server queues deliveries."""
        if not server_side(self.requester.id):
            raise HTTPException(status_code=403, detail="Deliveries are the server's")
        return super().create(**kwargs)

    def update(self, id: str, **kwargs: Any) -> Any:
        """Only the server records a delivery's progress."""
        if not server_side(self.requester.id):
            raise HTTPException(status_code=403, detail="Deliveries are the server's")
        return super().update(id, **kwargs)


def _may_see(
    model_registry: Any, user_id: str, about_model: Type[Any], about_id: str
) -> bool:
    """Whether ``user_id`` can read the record ``about_id`` of ``about_model``."""
    record_db = about_model.DB(model_registry.DB.manager.Base)
    try:
        found = record_db.get(
            requester_id=user_id, model_registry=model_registry, id=about_id
        )
    except HTTPException as exc:
        if exc.status_code in (403, 404):
            return False
        raise
    return found is not None


def _live_subscriptions(model_registry: Any, **criteria: Any) -> List[Any]:
    """Subscriptions not deleted, as ROOT (whose reads include deleted
    rows unless told otherwise)."""
    SubscriptionDB = WebhookSubscriptionModel.DB(model_registry.DB.manager.Base)
    return WebhookSubscriptionManager(
        requester_id=env("ROOT_ID"), model_registry=model_registry
    ).list(filters=[SubscriptionDB.deleted_at.is_(None)], **criteria)


def _audience(
    model_registry: Any,
    about_model: Optional[Type[Any]],
    about_id: Optional[str],
    may_see: Optional[Callable[[str], bool]],
) -> Callable[[str], bool]:
    """Who may hear of an event: those who can read the local record it is
    about, or, for an event about something no local table holds (a record
    in an upstream system), those ``may_see`` admits. Exactly one of the two
    is named."""
    if may_see is not None:
        if about_model is not None or about_id is not None:
            raise ValueError("Name the record an event is about, or may_see, not both")
        return may_see
    if about_model is None or about_id is None:
        raise ValueError("An event names the record it is about, or may_see")
    model: Type[Any] = about_model
    record_id: str = about_id
    return lambda user_id: _may_see(model_registry, user_id, model, record_id)


def dispatch_webhook_event(
    model_registry: Any,
    event_type: str,
    body: Dict[str, Any],
    *,
    about_model: Optional[Type[Any]] = None,
    about_id: Optional[str] = None,
    may_see: Optional[Callable[[str], bool]] = None,
) -> List[str]:
    """Queue ``body`` for every active subscription that wants
    ``event_type`` and whose owner may hear of it: who can read the record
    it is about (a record of ``about_model`` with id ``about_id``), or, for
    an event about no local record, whom ``may_see(user_id)`` admits. The
    delivery ids."""
    if not _EVENT_TYPE.fullmatch(event_type) or event_type == ALL_EVENTS:
        raise ValueError(f"{event_type!r} is not an event type")
    admitted = _audience(model_registry, about_model, about_id, may_see)
    subscriptions = _live_subscriptions(model_registry, active=True)
    payload = json.dumps(body, separators=(",", ":"), sort_keys=True)
    # SYSTEM writes deliveries throughout: a row ROOT made only ROOT may change.
    deliveries = WebhookDeliveryManager(
        requester_id=env("SYSTEM_ID"), model_registry=model_registry
    )
    queued: List[str] = []
    for subscription in subscriptions:
        if not subscription.matches(event_type) or not admitted(
            str(subscription.user_id)
        ):
            continue
        delivery = deliveries.create(
            webhook_subscription_id=subscription.id,
            event_type=event_type,
            payload=payload,
            next_attempt_at=_now(),
        )
        queued.append(str(delivery.id))
    return queued


def backoff_seconds(attempts: int) -> float:
    """How long a delivery waits after its ``attempts``-th failed send."""
    return min(MAX_DELAY_SECONDS, BASE_DELAY_SECONDS * 2.0 ** max(0, attempts - 1))


async def _default_http_post(
    url: str, *, content: bytes, headers: Dict[str, str]
) -> Any:
    """POST via the shared client (SSRF guard, TLS policy, trace, redaction)."""
    from zephyrex.lib.ProviderHTTPClient import ClientPolicy, get_async_client

    client = get_async_client(ClientPolicy(timeout=DELIVERY_TIMEOUT_SECONDS))
    return await client.post(url, content=content, headers=headers)


def _claim(model_registry: Any, delivery: Any, now: datetime) -> bool:
    """Take ``delivery`` for one send: count the attempt and lease it, if no
    other worker has since. False when another worker holds it."""
    DeliveryDB = WebhookDeliveryModel.DB(model_registry.DB.manager.Base)
    session = model_registry.DB.session()
    try:
        claimed = session.execute(
            update(DeliveryDB)
            .where(
                DeliveryDB.id == delivery.id,
                DeliveryDB.status == PENDING,
                DeliveryDB.attempts == delivery.attempts,
            )
            .values(
                attempts=delivery.attempts + 1,
                next_attempt_at=now + timedelta(seconds=CLAIM_LEASE_SECONDS),
                updated_at=now,
                updated_by_user_id=env("SYSTEM_ID"),
            )
        )
        session.commit()
        return bool(claimed.rowcount == 1)
    finally:
        session.close()


def _record(model_registry: Any, delivery_id: str, **fields: Any) -> None:
    WebhookDeliveryManager(
        requester_id=env("SYSTEM_ID"), model_registry=model_registry
    ).update(delivery_id, **fields)


def _failed(model_registry: Any, delivery_id: str, attempts: int, error: str) -> None:
    reason = error[:MAX_RECORDED_ERROR_CHARACTERS]
    if attempts >= MAX_ATTEMPTS:
        _record(model_registry, delivery_id, status=DEAD, last_error=reason)
        return
    _record(
        model_registry,
        delivery_id,
        last_error=reason,
        next_attempt_at=_now() + timedelta(seconds=backoff_seconds(attempts)),
    )


async def deliver(
    model_registry: Any, delivery: Any, http_post: Optional[HttpPost] = None
) -> None:
    """Send one claimed delivery and record how it went."""
    attempts = delivery.attempts + 1
    found = _live_subscriptions(model_registry, id=delivery.webhook_subscription_id)
    subscription = found[0] if found else None
    if subscription is None or not subscription.active or not subscription.secret:
        _record(
            model_registry,
            delivery.id,
            status=DEAD,
            last_error="The subscription is gone, inactive or has no secret",
        )
        return
    body = delivery.payload.encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        SIGNATURE_HEADER: sign_payload(decrypt_secret(subscription.secret), body),
        EVENT_HEADER: delivery.event_type,
        DELIVERY_ID_HEADER: str(delivery.id),
    }
    try:
        response = await (http_post or _default_http_post)(
            subscription.target_url, content=body, headers=headers
        )
    except Exception as exc:  # the network, TLS, or the SSRF guard: retried
        _failed(model_registry, delivery.id, attempts, f"{type(exc).__name__}: {exc}")
        return
    status = int(getattr(response, "status_code", 0))
    if 200 <= status < 300:
        _record(
            model_registry,
            delivery.id,
            status=DELIVERED,
            delivered_at=_now(),
            last_error=None,
        )
        return
    _failed(model_registry, delivery.id, attempts, f"The endpoint answered {status}")


async def deliver_due(model_registry: Any, http_post: Optional[HttpPost] = None) -> int:
    """Send every delivery that is due (at most :data:`DELIVERIES_PER_RUN`),
    each by the one worker that claims it. The number this run sent."""
    now = _now()
    DeliveryDB = WebhookDeliveryModel.DB(model_registry.DB.manager.Base)
    due = WebhookDeliveryManager(
        requester_id=env("ROOT_ID"), model_registry=model_registry
    ).list(
        filters=[
            DeliveryDB.status == PENDING,
            DeliveryDB.next_attempt_at <= now,
            DeliveryDB.deleted_at.is_(None),
        ],
        limit=DELIVERIES_PER_RUN,
    )
    sent = 0
    for delivery in due:
        if not _claim(model_registry, delivery, now):
            continue
        await deliver(model_registry, delivery, http_post)
        sent += 1
    if sent:
        logger.debug("webhook delivery: sent %s delivery(ies)", sent)
    return sent


WebhookSubscriptionModel.Manager = WebhookSubscriptionManager
WebhookDeliveryModel.Manager = WebhookDeliveryManager
