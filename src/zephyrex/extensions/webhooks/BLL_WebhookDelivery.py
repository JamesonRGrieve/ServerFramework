# SPDX-License-Identifier: AGPL-3.0-or-later
"""Outbound webhook delivery for the webhooks extension (Item — issue #203).

Inbound webhooks (``BLL_Webhooks``) handle events *from* providers; this adds
delivery *to* consumer-registered endpoints:

* a **subscription registry** — target URL + event-type filter + HMAC secret;
* a **delivery queue** with per-delivery exponential backoff and a dead-letter
  tier after ``max_attempts``;
* **HMAC-SHA256-signed** POSTs (``X-Webhook-Signature: sha256=...``) so receivers
  can authenticate the payload, mirroring the inbound ``verify_signature``
  convention.

The queue is in-process (thread-safe registry, async delivery). A durable /
cross-process store is a follow-on; this delivers the subscription + signed
delivery + backoff/dead-letter + delivery-log surface #203 asks for.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

from pydantic import BaseModel, Field

from zephyrex.lib.Logging import logger

SIGNATURE_HEADER = "X-Webhook-Signature"
EVENT_HEADER = "X-Webhook-Event"
DELIVERY_ID_HEADER = "X-Webhook-Delivery"

HttpPost = Callable[..., Awaitable[Any]]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def sign_payload(secret: str, body: bytes) -> str:
    """``sha256=<hex>`` HMAC-SHA256 of ``body`` keyed by ``secret`` — the
    signature receivers verify (mirrors the inbound convention)."""
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


class WebhookSubscription(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    target_url: str
    event_types: List[str] = Field(default_factory=lambda: ["*"])  # "*" = all events
    secret: str
    active: bool = True

    def matches(self, event_type: str) -> bool:
        return self.active and (
            "*" in self.event_types or event_type in self.event_types
        )


class WebhookDelivery(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    subscription_id: str
    target_url: str
    event_type: str
    body: Dict[str, Any]
    status: str = "pending"  # pending | delivered | dead
    attempts: int = 0
    next_attempt_at: datetime = Field(default_factory=_utcnow)
    last_error: Optional[str] = None
    created_at: datetime = Field(default_factory=_utcnow)


async def _default_http_post(
    url: str, *, content: bytes, headers: Dict[str, str]
) -> Any:
    """POST via the shared client (SSRF guard, TLS policy, trace, redaction)."""
    from zephyrex.lib.ProviderHTTPClient import ClientPolicy, get_async_client

    client = get_async_client(ClientPolicy(timeout=15.0))
    return await client.post(url, content=content, headers=headers)


class WebhookDeliveryService:
    """Subscription registry + delivery queue with exponential backoff and a
    dead-letter tier. The registry is thread-safe; delivery is async and
    injectable (``http_post``) for testing."""

    def __init__(
        self,
        *,
        max_attempts: int = 5,
        base_delay_seconds: float = 2.0,
        max_delay_seconds: float = 3600.0,
        now: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self._lock = threading.Lock()
        self._subscriptions: Dict[str, WebhookSubscription] = {}
        self._deliveries: Dict[str, WebhookDelivery] = {}
        self.max_attempts = max_attempts
        self.base_delay = base_delay_seconds
        self.max_delay = max_delay_seconds
        self._now = now or _utcnow

    # --------------------------------------------------------- subscriptions
    def register(self, subscription: WebhookSubscription) -> WebhookSubscription:
        with self._lock:
            self._subscriptions[subscription.id] = subscription
        return subscription

    def unregister(self, subscription_id: str) -> bool:
        with self._lock:
            return self._subscriptions.pop(subscription_id, None) is not None

    def list_subscriptions(self) -> List[WebhookSubscription]:
        with self._lock:
            return list(self._subscriptions.values())

    def get_subscription(self, subscription_id: str) -> Optional[WebhookSubscription]:
        with self._lock:
            return self._subscriptions.get(subscription_id)

    # --------------------------------------------------------------- dispatch
    def dispatch(self, event_type: str, body: Dict[str, Any]) -> List[str]:
        """Enqueue a delivery for every active subscription matching
        ``event_type``. Returns the created delivery ids."""
        with self._lock:
            subs = [s for s in self._subscriptions.values() if s.matches(event_type)]
        ids: List[str] = []
        for sub in subs:
            delivery = WebhookDelivery(
                subscription_id=sub.id,
                target_url=sub.target_url,
                event_type=event_type,
                body=body,
                next_attempt_at=self._now(),
            )
            with self._lock:
                self._deliveries[delivery.id] = delivery
            ids.append(delivery.id)
        return ids

    # --------------------------------------------------------------- delivery
    def _due(self) -> List[WebhookDelivery]:
        now = self._now()
        with self._lock:
            return [
                d
                for d in self._deliveries.values()
                if d.status == "pending" and d.next_attempt_at <= now
            ]

    def _backoff_seconds(self, attempts: int) -> float:
        return min(self.max_delay, self.base_delay * (2.0 ** max(0, attempts - 1)))

    def _mark_delivered(self, delivery_id: str) -> None:
        with self._lock:
            d = self._deliveries.get(delivery_id)
            if d is not None:
                d.attempts += 1
                d.status = "delivered"
                d.last_error = None

    def _mark_failed(self, delivery_id: str, error: str) -> None:
        with self._lock:
            d = self._deliveries.get(delivery_id)
            if d is None:
                return
            d.attempts += 1
            d.last_error = error
            if d.attempts >= self.max_attempts:
                d.status = "dead"  # dead-letter — no further attempts
            else:
                delay = self._backoff_seconds(d.attempts)
                d.next_attempt_at = self._now() + timedelta(seconds=delay)

    async def deliver_one(
        self, delivery: WebhookDelivery, http_post: Optional[HttpPost] = None
    ) -> None:
        post = http_post or _default_http_post
        sub = self.get_subscription(delivery.subscription_id)
        secret = sub.secret if sub is not None else ""
        payload = json.dumps(
            delivery.body, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            SIGNATURE_HEADER: sign_payload(secret, payload),
            EVENT_HEADER: delivery.event_type,
            DELIVERY_ID_HEADER: delivery.id,
        }
        try:
            response = await post(delivery.target_url, content=payload, headers=headers)
            status = int(getattr(response, "status_code", 0))
            if 200 <= status < 300:
                self._mark_delivered(delivery.id)
                return
            raise RuntimeError(f"non-2xx response: {status}")
        except Exception as exc:  # noqa: BLE001 — record + backoff/dead-letter
            self._mark_failed(delivery.id, str(exc))

    async def run_once(self, http_post: Optional[HttpPost] = None) -> int:
        """Attempt delivery of every currently-due delivery once. Returns the
        number attempted. A background service calls this on an interval."""
        due = self._due()
        for delivery in due:
            await self.deliver_one(delivery, http_post)
        if due:
            logger.debug(f"webhook delivery: attempted {len(due)} delivery(ies)")
        return len(due)

    def list_deliveries(self, status: Optional[str] = None) -> List[WebhookDelivery]:
        with self._lock:
            items = list(self._deliveries.values())
        if status is not None:
            items = [d for d in items if d.status == status]
        return sorted(items, key=lambda d: d.created_at, reverse=True)


_SERVICE: Optional[WebhookDeliveryService] = None


def get_delivery_service() -> WebhookDeliveryService:
    """Process-wide outbound delivery service (lazy singleton)."""
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = WebhookDeliveryService()
    return _SERVICE


def reset_delivery_service_for_test() -> None:
    """Drop the singleton so a test starts from an empty registry/queue."""
    global _SERVICE
    _SERVICE = None
