# SPDX-License-Identifier: AGPL-3.0-or-later
"""Outbound webhook subscription + delivery-log router (issue #203).

Exposes a ``create_webhook_delivery_router`` factory (mounted by the host app
like ``create_webhook_router``): manage subscriptions and read the delivery log.
Subscription secrets are never echoed back in responses.
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from zephyrex.extensions.webhooks.BLL_WebhookDelivery import (
    WebhookSubscription,
    get_delivery_service,
)


class SubscriptionCreate(BaseModel):
    target_url: str
    event_types: List[str] = ["*"]
    secret: str


def _subscription_public(sub: WebhookSubscription) -> Dict[str, Any]:
    # Never echo the HMAC secret.
    return {
        "id": sub.id,
        "target_url": sub.target_url,
        "event_types": sub.event_types,
        "active": sub.active,
    }


def create_webhook_delivery_router() -> APIRouter:
    """Construct the outbound-webhook management + delivery-log ``APIRouter``."""

    router = APIRouter(prefix="/webhook", tags=["webhook-delivery"])

    @router.post("/subscriptions", status_code=201)
    async def create_subscription(payload: SubscriptionCreate) -> Dict[str, Any]:
        sub = get_delivery_service().register(
            WebhookSubscription(
                target_url=payload.target_url,
                event_types=payload.event_types,
                secret=payload.secret,
            )
        )
        return _subscription_public(sub)

    @router.get("/subscriptions")
    async def list_subscriptions() -> List[Dict[str, Any]]:
        return [
            _subscription_public(s) for s in get_delivery_service().list_subscriptions()
        ]

    @router.delete("/subscriptions/{subscription_id}", status_code=204)
    async def delete_subscription(subscription_id: str) -> None:
        if not get_delivery_service().unregister(subscription_id):
            raise HTTPException(status_code=404, detail="subscription not found")

    @router.get("/deliveries")
    async def list_deliveries(status: Optional[str] = None) -> List[Dict[str, Any]]:
        return [
            {
                "id": d.id,
                "subscription_id": d.subscription_id,
                "event_type": d.event_type,
                "target_url": d.target_url,
                "status": d.status,
                "attempts": d.attempts,
                "last_error": d.last_error,
                "created_at": d.created_at.isoformat(),
            }
            for d in get_delivery_service().list_deliveries(status)
        ]

    return router
