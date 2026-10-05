# SPDX-License-Identifier: AGPL-3.0-or-later
"""Webhooks extension: inbound webhook router and handler registry, and
outbound delivery to endpoints users subscribe."""

from typing import Any, ClassVar, Dict, List, Set

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension
from zephyrex.lib.Logging import logger


class EXT_Webhooks(AbstractStaticExtension):
    """Webhooks in both directions.

    Inbound: other extensions register handlers via
    ``zephyrex.extensions.webhooks.BLL_Webhooks.webhook_handler``; the router
    from ``zephyrex.extensions.webhooks.EP_Webhooks.create_webhook_router`` is
    mounted by the host application at ``/webhook`` and verifies every
    signature.

    Outbound: users manage their endpoints at ``/v1/webhook-subscription``
    and read what was sent at ``/v1/webhook-delivery``; code raises events
    with ``BLL_WebhookDelivery.dispatch_webhook_event``, naming the record
    each is about, and ``SVC_WebhookDelivery`` sends them signed.
    """

    name: ClassVar[str] = "webhooks"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Inbound webhooks with mandatory signature verification, and signed "
        "outbound delivery to endpoints users subscribe"
    )

    _env: ClassVar[Dict[str, Any]] = {}
    _abilities: ClassVar[Set[str]] = set()
    _providers: ClassVar[List] = []

    @classmethod
    def on_initialize(cls) -> bool:
        logger.debug("Initializing webhooks extension")
        return True

    @classmethod
    def register_services(cls, model_registry: Any, requester_id: str) -> List[Any]:
        """The worker that sends due outbound deliveries, started by the
        framework's background services."""
        from zephyrex.extensions.webhooks.SVC_WebhookDelivery import (
            WebhookDeliveryService,
        )

        return [
            WebhookDeliveryService(
                requester_id=requester_id, model_registry=model_registry
            )
        ]
