# SPDX-License-Identifier: AGPL-3.0-or-later
"""The generic endpoint suite over /v1/webhook-subscription: owner-scoped
CRUD, held to If-Match, and the secret never in a response."""

import secrets
import uuid
from typing import Any, List

from zephyrex.AbstractTest import ParentEntity
from zephyrex.endpoints.AbstractEPTest import AbstractEPTest
from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.webhooks.BLL_WebhookDelivery import (
    ALL_EVENTS,
    WebhookSubscriptionModel,
)
from zephyrex.extensions.webhooks.EXT_Webhooks import EXT_Webhooks


class TestWebhookSubscriptionEP(AbstractEPTest, ExtensionServerMixin):
    extension_class = EXT_Webhooks
    base_endpoint = "webhook-subscription"
    entity_name = "webhook_subscription"
    class_under_test = WebhookSubscriptionModel
    required_fields = ["id", "target_url", "event_types", "active", "created_at"]
    string_field_to_update = "event_types"
    searchable_fields = ["target_url"]
    parent_entities: List[ParentEntity] = []
    system_entity = False

    create_fields = {
        "target_url": lambda: f"https://hooks.example.com/{uuid.uuid4()}",
        "event_types": "*",
        "secret": lambda: secrets.token_hex(16),
    }
    update_fields = {"event_types": "label.attached", "active": False}
    unique_fields: List[str] = []

    def create_payload(
        self,
        name=None,
        parent_ids=None,
        team_id=None,
        minimal=False,
        invalid_data=False,
    ):
        if invalid_data:
            return {"target_url": 12345, "secret": True}
        payload = {
            "target_url": f"https://hooks.example.com/{name or uuid.uuid4()}",
            "secret": secrets.token_hex(16),
        }
        if not minimal:
            payload["event_types"] = ALL_EVENTS
        return payload

    def test_GQL_mutation_create(self, server: Any, admin_a: Any, team_a: Any):
        """Overridden because the abstract test sends only
        string_field_to_update for a parentless entity, and a subscription
        needs its target and secret. The secret goes in, and never out."""
        target = f"https://hooks.example.com/{uuid.uuid4()}"
        mutation = f"""
        mutation {{
            createWebhookSubscription(input: {{
                targetUrl: "{target}",
                secret: "{secrets.token_hex(16)}",
                eventTypes: "team.updated"
            }}) {{
                id
                targetUrl
                eventTypes
                active
            }}
        }}
        """
        response = server.post(
            "/graphql",
            json={"query": mutation},
            headers=self._get_appropriate_headers(admin_a.jwt),
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert not data.get("errors"), data
        created = data["data"]["createWebhookSubscription"]
        assert created["targetUrl"] == target
        assert created["eventTypes"] == "team.updated"
        assert created["active"] is True
