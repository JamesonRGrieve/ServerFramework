# SPDX-License-Identifier: AGPL-3.0-or-later
"""ERP documents end to end: the real app and database, and a Frappe site
served in process (FrappeTestServer) answering as Frappe does. Every DocType
is lifted from the site's own metadata; access follows the instance's
scope; a save is held to its version; the site's validation and permissions
decide every write; and its signed webhooks reach only subscribers who may
use the instance they came from."""

import json
import uuid
from typing import Any, Dict, List

import pytest
from fastapi import HTTPException

from zephyrex.extensions.erp.BLL_ERP import (
    MAX_PAGE_LENGTH,
    ERPDocuments,
    ListQuery,
    can_use,
    checked_doctype,
    checked_name,
    lift_doctype,
    receive_webhook,
)
from zephyrex.extensions.erp.ERPTestSupport import (
    CLERK_KEY,
    CLERK_SECRET,
    CUSTOMER,
    DELIVERY_ROUTE,
    SALES_INVOICE,
    SALES_INVOICE_ITEM,
    WEBHOOK_SECRET,
    erp_instance,
)
from zephyrex.extensions.erp.ERPServer_test import ERPServerMixin
from zephyrex.extensions.erp.EXT_ERP import EXT_ERP
from zephyrex.extensions.erp.FrappeTestServer import webhook_request
from zephyrex.extensions.webhooks.BLL_WebhookDelivery import (
    WebhookDeliveryManager,
    WebhookSubscriptionManager,
)
from zephyrex.extensions.erp.PRV_ERPNext import PRV_ERPNext
from zephyrex.lib.Environment import env
from zephyrex.lib.Preconditions import PreconditionFailed, PreconditionRequired
from zephyrex.lib.ReplayCache import get_replay_cache
from zephyrex.logic.BLL_Providers import (
    ProviderInstanceManager,
    reads_environment_settings,
    root_instance_name,
)

INVOICE = {
    "customer": "Acme",
    "posting_date": "2026-01-02",
    "items": [{"item_code": "WIDGET", "qty": 2, "rate": 10.5}],
}


class _Refused:
    """``with _Refused(404):`` the block raises an HTTPException of that
    status."""

    def __init__(self, status: int) -> None:
        self.status = status
        self.raised: Any = None

    def __enter__(self) -> "_Refused":
        return self

    def __exit__(self, kind: Any, exc: Any, traceback: Any) -> bool:
        assert isinstance(exc, HTTPException), f"expected {self.status}, got {exc!r}"
        assert exc.status_code == self.status, exc.detail
        self.raised = exc
        return True


class TestLift:
    """A DocType's metadata becomes a model by way of the federation's
    OpenAPI importer: nothing is written per DocType."""

    def test_a_submittable_doctype_nests_its_child_table(self):
        schema = lift_doctype([SALES_INVOICE, SALES_INVOICE_ITEM])
        assert schema.is_submittable and not schema.istable
        assert set(schema.tables) == {"items"}
        child = schema.tables["items"]
        assert child.doctype == "Sales Invoice Item" and child.istable
        made = schema.model.model_validate(
            {"customer": "Acme", "items": [{"item_code": "W", "qty": 1}]}
        )
        assert type(getattr(made, "items")[0]) is child.model
        properties = schema.model.model_json_schema()["properties"]
        assert {"customer", "posting_date", "remarks", "items", "modified"} <= set(
            properties
        )

    def test_layout_fields_are_not_fields_and_types_follow_frappe(self):
        schema = lift_doctype([CUSTOMER])
        properties = schema.model.model_json_schema()["properties"]
        assert "details" not in properties
        assert [f.fieldname for f in schema.fields] == [
            "customer_name",
            "customer_group",
            "credit_limit",
            "disabled",
        ]
        written = schema.checked_write({"credit_limit": "12.5", "disabled": 1})
        assert written == {"credit_limit": 12.5, "disabled": 1}

    def test_a_custom_doctype_lifts_with_a_field_named_like_a_model_attribute(self):
        schema = lift_doctype([DELIVERY_ROUTE])
        assert schema.custom
        written = schema.checked_write({"route_code": "R1", "json": "x"})
        assert written == {"route_code": "R1", "json": "x"}

    @pytest.mark.parametrize(
        "data, problem",
        [
            ({"no_such_field": 1}, "no_such_field"),
            ({"docstatus": 1}, "docstatus"),
            ({"modified": "2026-01-01"}, "modified"),
            ({"owner": "someone@example.com"}, "owner"),
            ({"items": [{"item_code": "W", "parent": "X"}]}, "parent"),
            ({"items": "not rows"}, "list of rows"),
            ({"items": [{"qty": "many"}]}, "Invalid field values"),
        ],
    )
    def test_a_write_is_only_the_doctypes_fields_each_of_its_type(self, data, problem):
        schema = lift_doctype([SALES_INVOICE, SALES_INVOICE_ITEM])
        with _Refused(422) as refusal:
            schema.checked_write(data)
        assert problem in str(refusal.raised.detail)

    def test_existing_child_rows_are_addressed_by_name(self):
        schema = lift_doctype([SALES_INVOICE, SALES_INVOICE_ITEM])
        written = schema.checked_write(
            {"items": [{"name": "row1", "idx": 1, "item_code": "W", "qty": "3"}]}
        )
        assert written == {
            "items": [{"name": "row1", "idx": 1, "item_code": "W", "qty": 3.0}]
        }


class TestChecks:
    @pytest.mark.parametrize("value", ["", "  ", "..", "a/b", "x\n", None, "x" * 141])
    def test_doctype_names(self, value):
        with _Refused(422):
            checked_doctype(value)

    @pytest.mark.parametrize("value", ["", ".", "..", "a\x00", 7, "x" * 141])
    def test_document_names(self, value):
        with _Refused(422):
            checked_name(value)

    def test_a_document_name_may_hold_a_slash_and_spaces(self):
        assert checked_name("ACME / West") == "ACME / West"

    def test_a_list_carries_what_identifies_each_row(self):
        query = ListQuery.checked("Customer", ["customer_name"])
        assert query.fields == ("customer_name", "name", "modified", "docstatus")
        assert ListQuery.checked("Customer").fields == ("*",)

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"fields": ["name; drop table"]},
            {"filters": {"a b": 1}},
            {"filters": [["name", "=", "x"]]},
            {"order_by": "name; delete"},
            {"start": -1},
            {"page_length": MAX_PAGE_LENGTH + 1},
            {"page_length": 0},
        ],
    )
    def test_refused_list_queries(self, kwargs):
        with _Refused(422):
            ListQuery.checked("Customer", **kwargs)


class TestSeeding(ERPServerMixin):
    def test_no_root_instance_is_seeded_for_erpnext(self, model_registry):
        """ERPNext takes nothing from the environment, and the operator's
        (root-scoped) instance serves every user: a seeded Root_Erpnext would
        be an instance shared with everyone that no operator chose (operator
        decision). Operators create ERP instances in the scope they mean."""
        assert not reads_environment_settings(PRV_ERPNext)
        seeded = ProviderInstanceManager(
            model_registry=model_registry, requester_id=env("ROOT_ID")
        ).list(name=root_instance_name(PRV_ERPNext.name))
        assert seeded == []


class TestAccess(ERPServerMixin):
    """An instance serves whoever may use it, by its scope: everyone for the
    operator's, its user or team otherwise; anyone else is told it does not
    exist."""

    async def test_the_operators_instance_serves_everyone(
        self, model_registry, operator, admin_a, user_b
    ):
        for user in (admin_a, user_b):
            documents = ERPDocuments.for_requester(
                model_registry, user.id, str(operator.id)
            )
            assert {d.name for d in await documents.doctypes()} >= {
                "Customer",
                "Sales Invoice",
                "Delivery Route",
            }

    async def test_a_users_instance_serves_only_that_user(
        self, model_registry, frappe, admin_a, admin_b
    ):
        mine = erp_instance(
            model_registry, frappe.base_url, owner_id=admin_a.id, scope="user"
        )
        documents = ERPDocuments.for_requester(model_registry, admin_a.id, str(mine.id))
        await documents.create("Customer", {"customer_name": "Mine"})
        with _Refused(404):
            ERPDocuments.for_requester(model_registry, admin_b.id, str(mine.id))
        assert not can_use(model_registry, admin_b.id, mine)

    async def test_a_teams_instance_serves_its_members_only(
        self, model_registry, frappe, admin_a, admin_b, user_b, team_b
    ):
        shared = erp_instance(
            model_registry,
            frappe.base_url,
            owner_id=admin_b.id,
            scope="team",
            team_id=str(team_b.id),
        )
        documents = ERPDocuments.for_requester(
            model_registry, user_b.id, str(shared.id)
        )
        assert await documents.doctypes()
        with _Refused(404):
            ERPDocuments.for_requester(model_registry, admin_a.id, str(shared.id))

    def test_a_disabled_instance_or_another_extensions_serves_no_one(
        self, model_registry, frappe, operator, admin_a
    ):
        ProviderInstanceManager(
            model_registry=model_registry, requester_id=env("ROOT_ID")
        ).update(str(operator.id), enabled=False)
        with _Refused(404):
            ERPDocuments.for_requester(model_registry, admin_a.id, str(operator.id))
        with _Refused(404):
            ERPDocuments.for_requester(model_registry, admin_a.id, str(uuid.uuid4()))

    async def test_the_sites_permissions_apply_for_the_instances_account(
        self, model_registry, frappe, admin_a
    ):
        clerk = erp_instance(
            model_registry, frappe.base_url, api_key=CLERK_KEY, api_secret=CLERK_SECRET
        )
        documents = ERPDocuments.for_requester(
            model_registry, admin_a.id, str(clerk.id)
        )
        with _Refused(403):
            await documents.create("Customer", {"customer_name": "Nope"})
        with _Refused(403):
            await documents.doctypes()
        assert await documents.list(ListQuery.checked("Customer")) == []

    async def test_wrong_credentials_are_the_instances_problem_not_the_callers(
        self, model_registry, frappe, admin_a
    ):
        broken = erp_instance(
            model_registry, frappe.base_url, api_key=CLERK_KEY, api_secret="wrong"
        )
        documents = ERPDocuments.for_requester(
            model_registry, admin_a.id, str(broken.id)
        )
        with _Refused(502) as refusal:
            await documents.get("Customer", "x")
        assert "credentials" in refusal.raised.detail

    async def test_an_unconfigured_instance_says_what_it_lacks(
        self, model_registry, admin_a
    ):
        bare = erp_instance(model_registry, "", webhook_secret=None)
        documents = ERPDocuments.for_requester(model_registry, admin_a.id, str(bare.id))
        with _Refused(503) as refusal:
            await documents.get("Customer", "x")
        assert "base_url" in refusal.raised.detail

    async def test_the_ssrf_guard_holds_for_an_instance_address(
        self, model_registry, frappe, admin_a, monkeypatch
    ):
        monkeypatch.delenv("EGRESS_ALLOWED_HOSTS")
        documents = ERPDocuments.for_requester(
            model_registry,
            admin_a.id,
            str(erp_instance(model_registry, frappe.base_url).id),
        )
        with _Refused(502):
            await documents.get("Customer", "x")
        assert frappe.requests == []


class TestDocuments(ERPServerMixin):
    """Every read is the site's answer; every write goes through the site's
    validation."""

    async def test_create_read_list_with_child_tables_nested(
        self, model_registry, frappe, operator, admin_a
    ):
        documents = ERPDocuments.for_requester(
            model_registry, admin_a.id, str(operator.id)
        )
        made = await documents.create("Sales Invoice", INVOICE)
        assert made.name.startswith("ACC-SINV-") and made.docstatus == 0
        assert made.updated_at == made.data["modified"]
        [row] = made.data["items"]
        assert row["parent"] == made.name and row["parenttype"] == "Sales Invoice"

        read = await documents.get("Sales Invoice", made.name)
        assert read.data["items"][0]["item_code"] == "WIDGET"
        listed = await documents.list(
            ListQuery.checked(
                "Sales Invoice", ["customer"], filters={"customer": "Acme"}
            )
        )
        assert [d.name for d in listed] == [made.name]
        assert listed[0].updated_at == made.updated_at
        assert "items" not in listed[0].data

    async def test_names_are_sent_as_one_path_segment(
        self, model_registry, frappe, operator, admin_a
    ):
        documents = ERPDocuments.for_requester(
            model_registry, admin_a.id, str(operator.id)
        )
        await documents.create(
            "Customer", {"name": "ACME / West", "customer_name": "ACME"}
        )
        read = await documents.get("Customer", "ACME / West")
        assert read.name == "ACME / West"
        assert frappe.requests[-1]["path"] == "/api/resource/Customer/ACME / West"

    async def test_the_sites_validation_decides_and_its_reason_is_passed_on(
        self, model_registry, frappe, operator, admin_a
    ):
        documents = ERPDocuments.for_requester(
            model_registry, admin_a.id, str(operator.id)
        )
        with _Refused(422) as refusal:
            await documents.create("Customer", {"credit_limit": 5})
        assert "Value missing for Customer: Customer Name" in refusal.raised.detail
        with _Refused(404):
            await documents.get("Customer", "nobody")
        with _Refused(404):
            await documents.schema("No Such DocType")

    async def test_a_child_table_is_written_inside_its_parent(
        self, model_registry, frappe, operator, admin_a
    ):
        documents = ERPDocuments.for_requester(
            model_registry, admin_a.id, str(operator.id)
        )
        with _Refused(400):
            await documents.create("Sales Invoice Item", {"item_code": "W", "qty": 1})

    async def test_the_schema_is_the_sites_description_now(
        self, model_registry, frappe, operator, admin_a
    ):
        documents = ERPDocuments.for_requester(
            model_registry, admin_a.id, str(operator.id)
        )
        described = (await documents.schema("Sales Invoice")).described()
        assert described["tables"] == {"items": "Sales Invoice Item"}
        assert described["is_submittable"] is True
        assert "items" in described["json_schema"]["properties"]
        frappe.site.doctypes["Customer"]["fields"].append(
            {"fieldname": "custom_tier", "fieldtype": "Data", "label": "Tier"}
        )
        fresh = ERPDocuments.for_requester(model_registry, admin_a.id, str(operator.id))
        made = await fresh.create(
            "Customer", {"customer_name": "T", "custom_tier": "A"}
        )
        assert made.data["custom_tier"] == "A"

    async def test_an_ability_acts_for_its_requester(
        self, model_registry, frappe, operator, admin_a, admin_b
    ):
        made = await EXT_ERP.erp_create(
            admin_a.id, str(operator.id), "Customer", {"customer_name": "Via ability"}
        )
        read = await EXT_ERP.erp_get(
            admin_a.id, str(operator.id), "Customer", made["name"]
        )
        assert read["data"]["customer_name"] == "Via ability"
        mine = erp_instance(
            model_registry, frappe.base_url, owner_id=admin_a.id, scope="user"
        )
        with _Refused(404):
            await EXT_ERP.erp_doctypes(admin_b.id, str(mine.id))


class TestVersions(ERPServerMixin):
    """A save names the version it was read at; one the document has moved
    on from is refused (412) and changes nothing."""

    async def made(self, model_registry, operator, user) -> Any:
        documents = ERPDocuments.for_requester(
            model_registry, user.id, str(operator.id)
        )
        return documents, await documents.create("Sales Invoice", INVOICE)

    async def test_an_update_at_the_current_version_saves(
        self, model_registry, frappe, operator, admin_a
    ):
        documents, made = await self.made(model_registry, operator, admin_a)
        saved = await documents.update(
            "Sales Invoice", made.name, {"remarks": "first"}, f'"{made.updated_at}"'
        )
        assert saved.data["remarks"] == "first"
        assert saved.updated_at != made.updated_at
        # The version travels with the change, for the site's own check.
        put = [r for r in frappe.requests if r["method"] == "PUT"][-1]
        assert json.loads(put["body"]) == {
            "remarks": "first",
            "modified": made.updated_at,
        }

    async def test_a_stale_update_is_412_and_overwrites_nothing(
        self, model_registry, frappe, operator, admin_a
    ):
        documents, made = await self.made(model_registry, operator, admin_a)
        other = await documents.update(
            "Sales Invoice", made.name, {"remarks": "theirs"}, made.updated_at
        )
        with pytest.raises(PreconditionFailed) as stale:
            await documents.update(
                "Sales Invoice", made.name, {"remarks": "mine"}, made.updated_at
            )
        assert stale.value.status_code == 412
        assert stale.value.extra["current"].updated_at == other.updated_at
        assert stale.value.headers == {"ETag": f'"{other.updated_at}"'}
        stored = frappe.site.documents["Sales Invoice"][made.name]
        assert stored["remarks"] == "theirs"

    async def test_a_save_without_a_version_is_428_and_never_sent(
        self, model_registry, frappe, operator, admin_a
    ):
        documents, made = await self.made(model_registry, operator, admin_a)
        sent = len(frappe.requests)
        for save in (
            documents.update("Sales Invoice", made.name, {"remarks": "x"}, None),
            documents.delete("Sales Invoice", made.name, None),
            documents.submit("Sales Invoice", made.name, ""),
            documents.cancel("Sales Invoice", made.name, None),
        ):
            with pytest.raises(PreconditionRequired):
                await save
        assert not [
            r
            for r in frappe.requests[sent:]
            if r["method"] in ("PUT", "DELETE", "POST")
        ]

    async def test_without_the_requirement_a_save_may_name_no_version(
        self, model_registry, frappe, operator, admin_a, set_env
    ):
        set_env("IF_MATCH_REQUIRED", "false")
        documents, made = await self.made(model_registry, operator, admin_a)
        saved = await documents.update(
            "Sales Invoice", made.name, {"remarks": "x"}, None
        )
        assert saved.data["remarks"] == "x"

    async def test_any_version_saves_over_whatever_is_there(
        self, model_registry, frappe, operator, admin_a
    ):
        documents, made = await self.made(model_registry, operator, admin_a)
        await documents.update(
            "Sales Invoice", made.name, {"remarks": "a"}, made.updated_at
        )
        saved = await documents.update(
            "Sales Invoice", made.name, {"remarks": "b"}, "*"
        )
        assert saved.data["remarks"] == "b"

    async def test_submit_then_cancel_each_at_its_version(
        self, model_registry, frappe, operator, admin_a
    ):
        documents, made = await self.made(model_registry, operator, admin_a)
        submitted = await documents.submit("Sales Invoice", made.name, made.updated_at)
        assert submitted.docstatus == 1
        noted = await documents.update(
            "Sales Invoice", made.name, {"remarks": "paid"}, submitted.updated_at
        )
        with _Refused(422) as refusal:
            await documents.update(
                "Sales Invoice", made.name, {"customer": "Other"}, noted.updated_at
            )
        assert "after submission" in refusal.raised.detail
        with _Refused(422) as refusal:
            await documents.delete("Sales Invoice", made.name, noted.updated_at)
        assert "Cancel it first" in refusal.raised.detail
        cancelled = await documents.cancel("Sales Invoice", made.name, noted.updated_at)
        assert cancelled.docstatus == 2

    async def test_stale_submit_cancel_and_delete_are_412_and_change_nothing(
        self, model_registry, frappe, operator, admin_a
    ):
        documents, made = await self.made(model_registry, operator, admin_a)
        moved = await documents.update(
            "Sales Invoice", made.name, {"remarks": "moved"}, made.updated_at
        )
        with pytest.raises(PreconditionFailed):
            await documents.submit("Sales Invoice", made.name, made.updated_at)
        with pytest.raises(PreconditionFailed):
            await documents.delete("Sales Invoice", made.name, made.updated_at)
        assert frappe.site.documents["Sales Invoice"][made.name]["docstatus"] == 0
        submitted = await documents.submit("Sales Invoice", made.name, moved.updated_at)
        with pytest.raises(PreconditionFailed):
            await documents.cancel("Sales Invoice", made.name, moved.updated_at)
        assert frappe.site.documents["Sales Invoice"][made.name]["docstatus"] == 1
        await documents.cancel("Sales Invoice", made.name, submitted.updated_at)

    async def test_a_submission_raced_by_another_save_is_412(
        self, model_registry, frappe, operator, admin_a
    ):
        """The document changes between the read the submission is checked
        against and the submission: the site's own timestamp check refuses
        it, and the caller is told the document moved on."""
        documents, made = await self.made(model_registry, operator, admin_a)

        def another_save(site: Any) -> None:
            site.documents["Sales Invoice"][made.name]["modified"] = site.now()

        frappe.before("POST", "/api/method/frappe.client.submit", another_save)
        with pytest.raises(PreconditionFailed):
            await documents.submit("Sales Invoice", made.name, made.updated_at)
        assert frappe.site.documents["Sales Invoice"][made.name]["docstatus"] == 0

    async def test_only_a_submittable_doctype_is_submitted(
        self, model_registry, frappe, operator, admin_a
    ):
        documents = ERPDocuments.for_requester(
            model_registry, admin_a.id, str(operator.id)
        )
        customer = await documents.create("Customer", {"customer_name": "C"})
        with _Refused(400):
            await documents.submit("Customer", customer.name, customer.updated_at)


def subscribe(server: Any, user: Any, event_types: str) -> str:
    return str(
        WebhookSubscriptionManager(
            requester_id=user.id, model_registry=server.app.state.model_registry
        )
        .create(
            target_url="https://hooks.example.com/erp",
            event_types=event_types,
            secret=uuid.uuid4().hex,
        )
        .id
    )


def deliveries_for(server: Any, subscription_id: str) -> List[Any]:
    found: List[Any] = WebhookDeliveryManager(
        requester_id=env("ROOT_ID"), model_registry=server.app.state.model_registry
    ).list(webhook_subscription_id=subscription_id)
    return found


def event(**fields: Any) -> Dict[str, Any]:
    return {
        "event": "on_submit",
        "doctype": "Sales Invoice",
        "name": f"ACC-SINV-{uuid.uuid4().hex[:6]}",
        "modified": "2026-01-01 09:00:00.001001",
        **fields,
    }


class TestWebhooks(ERPServerMixin):
    """A webhook the instance signed reaches the subscribers who may use the
    instance, once; anything else is refused and reaches no one."""

    def test_a_signed_event_reaches_only_subscribers_who_may_use_the_instance(
        self, server, model_registry, frappe, admin_a, admin_b
    ):
        mine = erp_instance(
            model_registry, frappe.base_url, owner_id=admin_a.id, scope="user"
        )
        wanted = subscribe(server, admin_a, "erp.on_submit")
        outsider = subscribe(server, admin_b, "*")
        body, headers = webhook_request(WEBHOOK_SECRET, event())
        accepted = receive_webhook(
            model_registry,
            str(mine.id),
            {k.lower(): v for k, v in headers.items()},
            body,
        )
        assert accepted.event_type == "erp.on_submit" and accepted.queued >= 1
        [delivery] = deliveries_for(server, wanted)
        assert '"doctype":"Sales Invoice"' in delivery.payload
        assert f'"provider_instance_id":"{mine.id}"' in delivery.payload
        assert deliveries_for(server, outsider) == []

    def test_an_operators_instance_reaches_every_subscriber(
        self, server, model_registry, operator, admin_b
    ):
        theirs = subscribe(server, admin_b, "erp.on_cancel")
        body, headers = webhook_request(WEBHOOK_SECRET, event(event="on_cancel"))
        receive_webhook(
            model_registry,
            str(operator.id),
            {k.lower(): v for k, v in headers.items()},
            body,
        )
        assert len(deliveries_for(server, theirs)) == 1

    @pytest.mark.parametrize(
        "case", ["unsigned", "forged", "tampered", "replayed", "other_instance"]
    )
    def test_an_unsigned_forged_or_replayed_webhook_is_refused_and_reaches_no_one(
        self, server, model_registry, frappe, operator, admin_a, case
    ):
        subscription = subscribe(server, admin_a, "*")
        body, headers = webhook_request(
            None if case == "unsigned" else WEBHOOK_SECRET, event()
        )
        target = str(operator.id)
        if case == "forged":
            body, headers = webhook_request("a-guessed-secret-xxxxxxxx", event())
        if case == "tampered":
            body = body.replace(b"on_submit", b"on_trash")
        if case == "replayed":
            receive_webhook(
                model_registry, target, {k.lower(): v for k, v in headers.items()}, body
            )
        if case == "other_instance":
            target = str(
                erp_instance(
                    model_registry,
                    frappe.base_url,
                    webhook_secret="another-secret-0987654321",
                ).id
            )
        before = len(deliveries_for(server, subscription))
        with _Refused(401):
            receive_webhook(
                model_registry, target, {k.lower(): v for k, v in headers.items()}, body
            )
        assert len(deliveries_for(server, subscription)) == before

    @pytest.mark.parametrize("secret", [None, "short"])
    def test_an_instance_without_a_strong_secret_takes_no_webhooks(
        self, model_registry, frappe, secret
    ):
        instance = erp_instance(model_registry, frappe.base_url, webhook_secret=secret)
        body, headers = webhook_request(secret or WEBHOOK_SECRET, event())
        with _Refused(401):
            receive_webhook(
                model_registry,
                str(instance.id),
                {k.lower(): v for k, v in headers.items()},
                body,
            )

    @pytest.mark.parametrize(
        "fields", [{"event": "on_rename"}, {"doctype": ""}, {"name": None}]
    )
    def test_a_signed_body_must_name_its_event_and_document(
        self, model_registry, operator, fields
    ):
        body, headers = webhook_request(WEBHOOK_SECRET, event(**fields))
        with _Refused(400):
            receive_webhook(
                model_registry,
                str(operator.id),
                {k.lower(): v for k, v in headers.items()},
                body,
            )

    def test_the_replay_mark_is_the_instances_and_the_signatures(
        self, model_registry, operator
    ):
        body, headers = webhook_request(WEBHOOK_SECRET, event())
        receive_webhook(
            model_registry,
            str(operator.id),
            {k.lower(): v for k, v in headers.items()},
            body,
        )
        signature = headers["X-Frappe-Webhook-Signature"]
        assert get_replay_cache().is_used(f"erp:webhook:{operator.id}:{signature}")
