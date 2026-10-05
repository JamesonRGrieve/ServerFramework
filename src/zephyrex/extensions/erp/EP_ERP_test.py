# SPDX-License-Identifier: AGPL-3.0-or-later
"""The ERP routes over HTTP, as a client calls them: JWT-authenticated
document operations on REST and GraphQL, ETag and If-Match on REST, the
instance's scope deciding who is answered, its secrets never answered, and
the public webhook endpoint taking only what the instance signed."""

import json
import uuid
from typing import Any, Dict

from zephyrex.extensions.erp.ERPTestSupport import (
    OPERATOR_KEY,
    OPERATOR_SECRET,
    WEBHOOK_SECRET,
    ERPServerMixin,
    erp_instance,
)
from zephyrex.extensions.erp.FrappeTestServer import webhook_request
from zephyrex.extensions.webhooks.BLL_WebhookDelivery import (
    WebhookDeliveryManager,
    WebhookSubscriptionManager,
)
from zephyrex.lib.Environment import env
from zephyrex.logic.BLL_Providers import ProviderInstanceSettingManager

ERP = "/v1/erp"


def bearer(user: Any) -> Dict[str, str]:
    return {"Authorization": f"Bearer {user.jwt}"}


def customer(server: Any, user: Any, instance: Any, name: str = "Acme") -> Any:
    response = server.post(
        f"{ERP}/document/create",
        json={
            "provider_instance_id": str(instance.id),
            "doctype": "Customer",
            "data": {"customer_name": name},
        },
        headers=bearer(user),
    )
    assert response.status_code == 200, response.text
    return response


def ref(instance: Any, name: str, doctype: str = "Customer") -> Dict[str, Any]:
    return {"provider_instance_id": str(instance.id), "doctype": doctype, "name": name}


class TestDocumentRoutes(ERPServerMixin):
    def test_a_read_answers_with_the_documents_version_as_its_etag(
        self, server, operator, admin_a
    ):
        made = customer(server, admin_a, operator).json()
        read = server.post(
            f"{ERP}/document/get",
            json=ref(operator, made["name"]),
            headers=bearer(admin_a),
        )
        assert read.status_code == 200, read.text
        assert read.headers["etag"] == f'"{made["updated_at"]}"'
        assert read.json()["data"]["customer_name"] == "Acme"

    def test_a_save_names_its_version_in_if_match(self, server, operator, admin_a):
        made = customer(server, admin_a, operator)
        name = made.json()["name"]
        body = {**ref(operator, name), "data": {"credit_limit": 100}}
        missing = server.post(
            f"{ERP}/document/update", json=body, headers=bearer(admin_a)
        )
        assert missing.status_code == 428, missing.text

        saved = server.post(
            f"{ERP}/document/update",
            json=body,
            headers={**bearer(admin_a), "If-Match": made.headers["etag"]},
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["data"]["credit_limit"] == 100

        stale = server.post(
            f"{ERP}/document/update",
            json={**body, "data": {"credit_limit": 1}},
            headers={**bearer(admin_a), "If-Match": made.headers["etag"]},
        )
        assert stale.status_code == 412, stale.text
        assert stale.json()["current"]["data"]["credit_limit"] == 100
        assert stale.headers["etag"] == saved.headers["etag"]

    def test_the_input_names_the_version_where_no_header_can(
        self, server, operator, admin_a
    ):
        made = customer(server, admin_a, operator).json()
        deleted = server.post(
            f"{ERP}/document/delete",
            json={**ref(operator, made["name"]), "if_match": made["updated_at"]},
            headers=bearer(admin_a),
        )
        assert deleted.status_code == 200, deleted.text
        gone = server.post(
            f"{ERP}/document/get",
            json=ref(operator, made["name"]),
            headers=bearer(admin_a),
        )
        assert gone.status_code == 404, gone.text

    def test_every_route_needs_a_signed_in_user(self, server, operator):
        for path in ("/doctypes", "/schema", "/document/list", "/document/get"):
            response = server.post(f"{ERP}{path}", json=ref(operator, "x"), headers={})
            assert response.status_code == 401, (path, response.text)

    def test_a_users_instance_is_not_found_by_anyone_else(
        self, server, model_registry, frappe, admin_a, admin_b
    ):
        mine = erp_instance(
            model_registry, frappe.base_url, owner_id=admin_a.id, scope="user"
        )
        made = customer(server, admin_a, mine).json()
        for path, body in (
            ("/doctypes", {"provider_instance_id": str(mine.id)}),
            ("/document/get", ref(mine, made["name"])),
            (
                "/document/list",
                {"provider_instance_id": str(mine.id), "doctype": "Customer"},
            ),
        ):
            response = server.post(f"{ERP}{path}", json=body, headers=bearer(admin_b))
            assert response.status_code == 404, (path, response.text)
            assert made["name"] not in response.text

    def test_the_lists_and_schemas_answer(self, server, operator, admin_a):
        customer(server, admin_a, operator, "Listed")
        listed = server.post(
            f"{ERP}/document/list",
            json={
                "provider_instance_id": str(operator.id),
                "doctype": "Customer",
                "fields": ["customer_name"],
                "filters": {"customer_name": "Listed"},
            },
            headers=bearer(admin_a),
        )
        assert listed.status_code == 200, listed.text
        assert [d["data"]["customer_name"] for d in listed.json()["documents"]] == [
            "Listed"
        ]
        schema = server.post(
            f"{ERP}/schema",
            json={"provider_instance_id": str(operator.id), "doctype": "Sales Invoice"},
            headers=bearer(admin_a),
        )
        assert schema.status_code == 200, schema.text
        assert schema.json()["tables"] == {"items": "Sales Invoice Item"}

    def test_graphql_reaches_the_same_documents(self, server, operator, admin_a):
        made = customer(server, admin_a, operator, "Graphed").json()
        query = (
            "{ erpGetDocument(input: {"
            f" providerInstanceId: {json.dumps(str(operator.id))},"
            ' doctype: "Customer",'
            f" name: {json.dumps(made['name'])}"
            " }) { name updatedAt data } }"
        )
        answered = server.post(
            "/graphql", json={"query": query}, headers=bearer(admin_a)
        )
        assert answered.status_code == 200, answered.text
        assert "errors" not in answered.json(), answered.text
        got = answered.json()["data"]["erpGetDocument"]
        assert got["name"] == made["name"] and got["updatedAt"] == made["updated_at"]
        # A JSON object travels as JSON text on this GraphQL surface.
        assert json.loads(got["data"])["customer_name"] == "Graphed"

        def update(version: str) -> Any:
            mutation = (
                "mutation { erpUpdateDocument(input: {"
                f" providerInstanceId: {json.dumps(str(operator.id))},"
                ' doctype: "Customer",'
                f" name: {json.dumps(made['name'])},"
                f" data: {json.dumps(json.dumps({'credit_limit': 7}))},"
                f" ifMatch: {json.dumps(version)}"
                " }) { updatedAt } }"
            )
            return server.post(
                "/graphql", json={"query": mutation}, headers=bearer(admin_a)
            ).json()

        saved = update(made["updated_at"])
        assert "errors" not in saved, saved
        assert saved["data"]["erpUpdateDocument"]["updatedAt"] != made["updated_at"]
        stale = update(made["updated_at"])
        assert stale.get("errors") and not (stale.get("data") or {}).get(
            "erpUpdateDocument"
        )


class TestSecrets(ERPServerMixin):
    def test_the_instances_credentials_are_never_answered(
        self, server, model_registry, operator, admin_a
    ):
        rows = ProviderInstanceSettingManager(
            model_registry=model_registry, requester_id=env("ROOT_ID")
        ).list(provider_instance_id=str(operator.id))
        secret_rows = [
            row for row in rows if row.key in ("api_secret", "webhook_secret")
        ]
        assert len(secret_rows) == 2 and all(row.write_only for row in secret_rows)
        for row in secret_rows:
            read = server.get(
                f"/v1/provider/instance/setting/{row.id}", headers=bearer(admin_a)
            )
            assert OPERATOR_SECRET not in read.text and WEBHOOK_SECRET not in read.text
        made = customer(server, admin_a, operator)
        listed = server.post(
            f"{ERP}/doctypes",
            json={"provider_instance_id": str(operator.id)},
            headers=bearer(admin_a),
        )
        for answer in (made, listed):
            assert OPERATOR_SECRET not in answer.text
            assert OPERATOR_KEY not in answer.text
            assert WEBHOOK_SECRET not in answer.text


class TestWebhookRoute(ERPServerMixin):
    def test_only_a_signed_webhook_is_taken_and_passed_on(
        self, server, model_registry, operator, admin_a
    ):
        subscription = WebhookSubscriptionManager(
            requester_id=admin_a.id, model_registry=model_registry
        ).create(
            target_url="https://hooks.example.com/erp",
            event_types="erp.on_update",
            secret=uuid.uuid4().hex,
        )
        event = {
            "event": "on_update",
            "doctype": "Customer",
            "name": f"CUST-{uuid.uuid4().hex[:6]}",
        }
        url = f"/v1/erp/webhook/{operator.id}"
        body, headers = webhook_request(None, event)
        unsigned = server.post(url, content=body, headers=headers)
        assert unsigned.status_code == 401, unsigned.text
        nowhere = server.post(
            f"/v1/erp/webhook/{uuid.uuid4()}",
            content=body,
            headers=webhook_request(WEBHOOK_SECRET, event)[1],
        )
        assert nowhere.status_code == 401, nowhere.text

        body, headers = webhook_request(WEBHOOK_SECRET, event)
        taken = server.post(url, content=body, headers=headers)
        assert taken.status_code == 200, taken.text
        assert taken.json()["event_type"] == "erp.on_update"
        again = server.post(url, content=body, headers=headers)
        assert again.status_code == 401, again.text
        queued = WebhookDeliveryManager(
            requester_id=env("ROOT_ID"), model_registry=model_registry
        ).list(webhook_subscription_id=str(subscription.id))
        assert len(queued) == 1 and event["name"] in queued[0].payload
