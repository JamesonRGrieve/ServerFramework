# SPDX-License-Identifier: AGPL-3.0-or-later
"""The worker that sends outbound webhook deliveries when they are due.

Every :data:`TICK_SECONDS` it runs :func:`deliver_due`: due deliveries are
claimed one by one with a compare-and-set (so of several workers, one sends
each), POSTed signed, and recorded delivered, rescheduled with backoff, or
dead-lettered. A failure to reach the database skips a tick; the service
keeps running.
"""

from typing import Any, Optional

from zephyrex.extensions.webhooks.BLL_WebhookDelivery import HttpPost, deliver_due
from zephyrex.lib.Logging import logger
from zephyrex.logic.AbstractService import AbstractService

SERVICE_ID = "webhook_delivery"
TICK_SECONDS = 5


class WebhookDeliveryService(AbstractService):
    """Sends due outbound webhook deliveries."""

    def __init__(
        self,
        requester_id: str,
        model_registry: Any,
        interval_seconds: int = TICK_SECONDS,
        http_post: Optional[HttpPost] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            requester_id=requester_id,
            interval_seconds=interval_seconds,
            service_id=SERVICE_ID,
            **kwargs,
        )
        self.model_registry = model_registry
        self.http_post = http_post

    async def update(self) -> None:
        try:
            await deliver_due(self.model_registry, self.http_post)
        except Exception as exc:  # the database is away: try again next tick
            logger.warning(
                "webhooks: cannot send due deliveries (%s: %s)",
                type(exc).__name__,
                exc,
            )
