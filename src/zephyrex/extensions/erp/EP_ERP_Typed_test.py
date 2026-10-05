# SPDX-License-Identifier: AGPL-3.0-or-later
"""The operator's DocTypes as typed models, over HTTP, after a restart.

Instances are made on a first boot; the second boot, on the same database,
reads the operator's instances' catalogues and serves each parent DocType
as a typed, table-less model on REST and GraphQL beside the generic
document API. A user's instance, a disabled one, one whose site cannot be
reached and one whose account may not list DocTypes are left out (the last
two logged with the reason); every typed route holds to the generic API's
access, version and secret rules."""

import json
import os
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect as sql_inspect

from conftest import CORE_COMPANION_EXTENSIONS
from zephyrex.extensions.erp.BLL_ERP_Typed import instance_namespace
from zephyrex.extensions.erp.ERPTestSupport import (
    ALL_RIGHTS,
    CLERK_KEY,
    CLERK_SECRET,
    OPERATOR_KEY,
    OPERATOR_SECRET,
    WEBHOOK_SECRET,
    erp_instance,
    standard_site,
)
from zephyrex.extensions.erp.FrappeTestServer import FrappeServer, doctype_of, field_of
from zephyrex.extensions.federation.BLL_Federation_Typed import model_stem
from zephyrex.lib.Environment import env
from zephyrex.lib.Logging import logger
from zephyrex.logic.BLL_Providers import ProviderInstanceManager
from zephyrex.testing.factories import make_admin_a, make_admin_b

PROVIDER = "erpnext"
# A custom DocType whose name holds spaces, brackets and a dash.
ODD_DOCTYPE = "Route Plan (EU) - 2026"
EXTENSIONS = ",".join(
    ["erp", "federation", "webhooks"]
    + [
        name
        for name in CORE_COMPANION_EXTENSIONS
        if name not in ("erp", "federation", "webhooks")
    ]
)


@dataclass
class Booted:
    """The restarted app, and what was made before it booted."""

    server: TestClient
    frappe: FrappeServer
    admin: Any
    other: Any
    namespaces: Dict[str, str]
    operator_id: str
    warnings: List[str]
    # The hosts the test sites listen on, for EGRESS_ALLOWED_HOSTS.
    egress: str

    @property
    def operator(self) -> str:
        return self.namespaces["operator"]

    def typed(self, slug: str, operation: str) -> str:
        return f"/v1/federated/{self.operator}/{slug}/{operation}"


def bearer(user: Any) -> Dict[str, str]:
    return {"Authorization": f"Bearer {user.jwt}"}


def graphql_field(namespace: str, slug: str, operation: str) -> str:
    """A typed route's GraphQL field, camelCased as the schema names it."""
    first, *rest = f"{namespace}_{slug}_{operation}".split("_")
    return first + "".join(part.capitalize() for part in rest)


def _build(prefix: str, frappe: FrappeServer, unreachable: str, egress: str) -> Booted:
    """Boot an app, make the instances, then boot again on the same database
    so the typed models are read from them."""
    from zephyrex.app import instance
    from zephyrex.pydantic2.sqlalchemy import prepare_test_registry

    prepare_test_registry()
    first_app = instance(db_prefix=prefix, extensions=EXTENSIONS)
    first = TestClient(first_app)
    registry = first_app.state.model_registry
    admin, other = make_admin_a(first), make_admin_b(first)
    made = {
        "operator": erp_instance(registry, frappe.base_url),
        "user": erp_instance(
            registry, frappe.base_url, owner_id=admin.id, scope="user"
        ),
        "disabled": erp_instance(registry, frappe.base_url, scope="system"),
        "unreachable": erp_instance(registry, unreachable),
        "clerk": erp_instance(
            registry, frappe.base_url, api_key=CLERK_KEY, api_secret=CLERK_SECRET
        ),
    }
    ProviderInstanceManager(
        model_registry=registry, requester_id=env("ROOT_ID")
    ).update(str(made["disabled"].id), enabled=False)

    prepare_test_registry()
    warnings: List[str] = []
    sink = logger.add(warnings.append, level="WARNING", format="{message}")
    try:
        restarted = TestClient(instance(db_prefix=prefix, extensions=EXTENSIONS))
    finally:
        logger.remove(sink)
    return Booted(
        server=restarted,
        frappe=frappe,
        admin=admin,
        other=other,
        namespaces={
            role: instance_namespace(PROVIDER, str(row.id))
            for role, row in made.items()
        },
        operator_id=str(made["operator"].id),
        warnings=warnings,
        egress=egress,
    )


@pytest.fixture(scope="module")
def booted() -> Iterator[Booted]:
    from zephyrex.pydantic2.sqlalchemy import prepare_test_registry

    worker = os.environ.get("PYTEST_XDIST_WORKER", "")
    prefix = f"test.erp_typed.{worker}" if worker else "test.erp_typed"
    with FrappeServer(standard_site()) as gone:
        unreachable = gone.base_url
    site = standard_site()
    site.add_doctype(doctype_of(ODD_DOCTYPE, [field_of("plan_code", reqd=1)], custom=1))
    site.accounts[OPERATOR_KEY].rights[ODD_DOCTYPE] = set(ALL_RIGHTS)
    with FrappeServer(site) as frappe:
        egress = f"{frappe.host},{unreachable[len('http://'):]}"
        # The allowance is patched only while the apps are built (boot reads
        # the catalogue); each test sets it again for itself (``allowed``),
        # so no other module on this worker inherits it.
        with pytest.MonkeyPatch.context() as patch:
            patch.setenv("EGRESS_ALLOWED_HOSTS", egress)
            built = _build(prefix, frappe, unreachable, egress)
        yield built
        prepare_test_registry()


@pytest.fixture(autouse=True)
def allowed(booted: Booted, monkeypatch: pytest.MonkeyPatch) -> None:
    """The test sites, reachable through the SSRF guard for this test only."""
    monkeypatch.setenv("EGRESS_ALLOWED_HOSTS", booted.egress)


def create(booted: Booted, slug: str, data: Dict[str, Any]) -> Any:
    made = booted.server.post(
        booted.typed(slug, "create"), json={"data": data}, headers=bearer(booted.admin)
    )
    assert made.status_code == 200, made.text
    return made


class TestCatalogue:
    def test_the_operators_parent_doctypes_are_typed_routes(self, booted):
        paths = set(booted.server.app.openapi()["paths"])
        for slug in ("customer", "sales_invoice", "delivery_route"):
            for operation in ("list", "get", "create", "update", "delete"):
                assert booted.typed(slug, operation) in paths
        # A child table is its parents' nested type, not a type of its own.
        assert not [p for p in paths if "sales_invoice_item" in p]
        # The generic document API is as it was.
        assert "/v1/erp/document/get" in paths

    def test_only_the_operators_enabled_instances_are_in_the_shared_schema(
        self, booted
    ):
        paths = " ".join(booted.server.app.openapi()["paths"])
        schema = booted.server.app.state.model_registry.gql.as_str()
        for role in ("user", "disabled"):
            namespace = booted.namespaces[role]
            assert namespace not in paths, role
            assert namespace.split("_")[1] not in schema.lower(), role
        assert graphql_field(booted.operator, "customer", "get") in schema

    def test_a_site_left_out_at_boot_is_logged_and_the_app_serves_the_rest(
        self, booted
    ):
        errors = booted.server.app.state.federation_report.errors
        unreachable, clerk = (
            booted.namespaces["unreachable"],
            booted.namespaces["clerk"],
        )
        assert "502" in errors[unreachable], errors
        assert "403" in errors[clerk], errors
        for namespace in (unreachable, clerk):
            assert [w for w in booted.warnings if namespace in w and "not served" in w]
        paths = " ".join(booted.server.app.openapi()["paths"])
        assert unreachable not in paths and clerk not in paths
        generic = booted.server.post(
            "/v1/erp/document/create",
            json={
                "provider_instance_id": booted.operator_id,
                "doctype": "Customer",
                "data": {"customer_name": "Generic"},
            },
            headers=bearer(booted.admin),
        )
        assert generic.status_code == 200, generic.text

    def test_no_table_is_made_for_a_doctype(self, booted):
        registry = booted.server.app.state.model_registry
        names = {
            model_stem(booted.operator, slug)
            for slug in ("customer", "sales_invoice", "delivery_route")
        }
        records = [
            model
            for name, (model, _) in registry.external_models.items()
            if name in names
        ]
        assert {model.__name__ for model in records} == names
        assert not set(records) & set(registry.bound_models)
        assert not set(records) & set(registry.db_models)
        engine = registry.database_manager.get_setup_engine()
        assert not [
            table
            for table in sql_inspect(engine).get_table_names()
            if booted.operator.split("_")[1] in table.lower()
        ]


class TestTypedDocuments:
    def test_a_document_with_child_rows_is_typed_and_versioned(self, booted):
        customer = create(booted, "customer", {"customer_name": "Typed Ltd"}).json()
        made = create(
            booted,
            "sales_invoice",
            {
                "customer": customer["name"],
                "posting_date": "2026-10-05",
                "items": [{"item_code": "WIDGET", "qty": 2, "rate": 9.5}],
            },
        )
        invoice = made.json()
        assert made.headers["etag"] == f'"{invoice["updated_at"]}"'
        assert invoice["updated_at"] == invoice["modified"]
        assert (
            invoice["items"][0]["qty"] == 2
            and invoice["items"][0]["parent"] == invoice["name"]
        )
        assert invoice["docstatus"] == 0

        read = booted.server.post(
            booted.typed("sales_invoice", "get"),
            json={"name": invoice["name"]},
            headers=bearer(booted.admin),
        )
        assert read.status_code == 200, read.text
        assert read.headers["etag"] == made.headers["etag"]
        assert read.json()["items"][0]["item_code"] == "WIDGET"
        listed = booted.server.post(
            booted.typed("customer", "list"),
            json={"filters": {"customer_name": "Typed Ltd"}},
            headers=bearer(booted.admin),
        )
        assert listed.status_code == 200, listed.text
        assert [row["name"] for row in listed.json()["items"]] == [customer["name"]]

    def test_a_save_is_held_to_the_documents_modified(self, booted):
        made = create(booted, "customer", {"customer_name": "Versioned"})
        name = made.json()["name"]
        change = {"name": name, "data": {"credit_limit": 100}}
        update = booted.typed("customer", "update")

        missing = booted.server.post(update, json=change, headers=bearer(booted.admin))
        assert missing.status_code == 428, missing.text
        saved = booted.server.post(
            update,
            json=change,
            headers={**bearer(booted.admin), "If-Match": made.headers["etag"]},
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["credit_limit"] == 100
        stale = booted.server.post(
            update,
            json={"name": name, "data": {"credit_limit": 1}},
            headers={**bearer(booted.admin), "If-Match": made.headers["etag"]},
        )
        assert stale.status_code == 412, stale.text
        assert stale.json()["current"]["credit_limit"] == 100
        assert stale.headers["etag"] == saved.headers["etag"]
        assert booted.frappe.site.stored("Customer", name)["credit_limit"] == 100

        delete = booted.typed("customer", "delete")
        stale_delete = booted.server.post(
            delete,
            json={"name": name, "if_match": made.json()["updated_at"]},
            headers=bearer(booted.admin),
        )
        assert stale_delete.status_code == 412, stale_delete.text
        deleted = booted.server.post(
            delete,
            json={"name": name, "if_match": saved.json()["updated_at"]},
            headers=bearer(booted.admin),
        )
        assert deleted.status_code == 200, deleted.text
        gone = booted.server.post(
            booted.typed("customer", "get"),
            json={"name": name},
            headers=bearer(booted.admin),
        )
        assert gone.status_code == 404, gone.text

    @pytest.mark.parametrize(
        "slug,data",
        [
            ("customer", {"customer_name": "X", "nickname": "x"}),
            ("customer", {"customer_name": "X", "owner": "someone@example.com"}),
            ("customer", {"customer_name": "X", "credit_limit": "lots"}),
            (
                "sales_invoice",
                {"customer": "X", "items": [{"item_code": "A", "parent": "B"}]},
            ),
        ],
    )
    def test_a_write_holds_only_the_doctypes_writable_fields(self, booted, slug, data):
        refused = booted.server.post(
            booted.typed(slug, "create"),
            json={"data": data},
            headers=bearer(booted.admin),
        )
        assert refused.status_code == 422, refused.text

    def test_the_sites_own_validation_still_decides(self, booted):
        refused = booted.server.post(
            booted.typed("sales_invoice", "create"),
            json={"data": {"customer": "Nobody"}},
            headers=bearer(booted.admin),
        )
        assert refused.status_code == 422, refused.text
        assert "items" in refused.text.lower() or "mandatory" in refused.text.lower()


class TestTypedAccess:
    def test_every_typed_route_needs_a_signed_in_user(self, booted):
        for operation in ("list", "get", "create"):
            response = booted.server.post(booted.typed("customer", operation), json={})
            assert response.status_code == 401, (operation, response.text)

    def test_who_may_use_the_instance_is_decided_live(self, booted):
        name = create(booted, "customer", {"customer_name": "Live"}).json()["name"]
        get = booted.typed("customer", "get")
        assert (
            booted.server.post(
                get, json={"name": name}, headers=bearer(booted.other)
            ).status_code
            == 200
        )
        manager = ProviderInstanceManager(
            model_registry=booted.server.app.state.model_registry,
            requester_id=env("ROOT_ID"),
        )
        manager.update(booted.operator_id, enabled=False)
        try:
            refused = booted.server.post(
                get, json={"name": name}, headers=bearer(booted.other)
            )
            assert refused.status_code == 404, refused.text
        finally:
            manager.update(booted.operator_id, enabled=True)

    def test_the_instances_credentials_are_never_answered(self, booted):
        made = create(booted, "customer", {"customer_name": "Secretive"})
        stale = booted.server.post(
            booted.typed("customer", "update"),
            json={
                "name": made.json()["name"],
                "data": {"credit_limit": 1},
                "if_match": "old",
            },
            headers=bearer(booted.admin),
        )
        listed = booted.server.post(
            booted.typed("customer", "list"), json={}, headers=bearer(booted.admin)
        )
        for answer in (made, stale, listed):
            for secret in (OPERATOR_SECRET, OPERATOR_KEY, WEBHOOK_SECRET, CLERK_SECRET):
                assert secret not in answer.text


class TestTypedGraphQL:
    def test_a_doctype_is_a_graphql_type(self, booted):
        name = create(
            booted, "customer", {"customer_name": "Graphed", "credit_limit": 5}
        ).json()["name"]
        field = graphql_field(booted.operator, "customer", "get")
        answer = booted.server.post(
            "/graphql",
            json={
                "query": f"query($n: String) {{ {field}(input: {{name: $n}}) "
                "{ name customerName creditLimit modified updatedAt docstatus } }",
                "variables": {"n": name},
            },
            headers=bearer(booted.admin),
        )
        assert answer.status_code == 200, answer.text
        assert "errors" not in answer.json(), answer.text
        got = answer.json()["data"][field]
        assert got["customerName"] == "Graphed" and got["creditLimit"] == 5
        stored = booted.frappe.site.stored("Customer", name)["modified"]
        assert got["modified"] == got["updatedAt"] == stored and got["docstatus"] == 0

    def test_a_document_and_its_child_rows_are_written_typed(self, booted):
        customer = create(booted, "customer", {"customer_name": "Nested"}).json()[
            "name"
        ]
        field = graphql_field(booted.operator, "sales_invoice", "create")
        mutation = (
            f"mutation {{ {field}(input: {{data: {{"
            f" customer: {json.dumps(customer)},"
            ' items: [{itemCode: "BOLT", qty: 3, rate: 1.25}]'
            " }}) { name customer items { itemCode qty parent } } }"
        )
        answer = booted.server.post(
            "/graphql", json={"query": mutation}, headers=bearer(booted.admin)
        )
        assert "errors" not in answer.json(), answer.text
        made = answer.json()["data"][field]
        assert made["customer"] == customer
        assert made["items"] == [
            {"itemCode": "BOLT", "qty": 3.0, "parent": made["name"]}
        ]

        type_name = f"{model_stem(booted.operator, 'sales_invoice')}Type"
        typed = booted.server.post(
            "/graphql",
            json={
                "query": f'{{ __type(name: "{type_name}") {{ fields {{ name }} }} }}'
            },
            headers=bearer(booted.admin),
        )
        fields = {f["name"] for f in typed.json()["data"]["__type"]["fields"]}
        assert {
            "name",
            "customer",
            "postingDate",
            "items",
            "updatedAt",
            "docstatus",
        } <= fields

    def test_graphql_names_its_version_in_the_input(self, booted):
        made = create(
            booted, "customer", {"customer_name": f"G-{uuid.uuid4().hex[:6]}"}
        ).json()
        field = graphql_field(booted.operator, "customer", "update")

        def update(version: str) -> Any:
            mutation = (
                f"mutation {{ {field}(input: {{name: {json.dumps(made['name'])},"
                f" data: {{creditLimit: 9}}, ifMatch: {json.dumps(version)}}})"
                " { creditLimit updatedAt } }"
            )
            return booted.server.post(
                "/graphql", json={"query": mutation}, headers=bearer(booted.admin)
            ).json()

        saved = update(made["updated_at"])
        assert "errors" not in saved, saved
        assert saved["data"][field]["creditLimit"] == 9
        stale = update(made["updated_at"])
        assert stale.get("errors") and not (stale.get("data") or {}).get(field)


class TestDiscovery:
    def test_the_catalogue_names_every_typed_model_the_requester_may_use(self, booted):
        listed = booted.server.get(
            "/v1/federated/catalogue", headers=bearer(booted.other)
        )
        assert listed.status_code == 200, listed.text
        ours = {
            row["name"]: row
            for row in listed.json()["types"]
            if row["source_reference"] == booted.operator_id
        }
        assert set(ours) == {"Customer", "Sales Invoice", "Delivery Route", ODD_DOCTYPE}
        invoice = ours["Sales Invoice"]
        stem = model_stem(booted.operator, "sales_invoice")
        assert (
            invoice["namespace"] == booted.operator
            and invoice["slug"] == "sales_invoice"
        )
        assert (
            invoice["key_field"] == "name" and invoice["version_field"] == "updated_at"
        )
        assert invoice["rest"]["get"] == booted.typed("sales_invoice", "get")
        assert invoice["graphql_type"] == f"{stem}Type"
        assert invoice["graphql_write_input"] == f"{stem}WriteInput"
        assert invoice["graphql_fields"]["create"] == graphql_field(
            booted.operator, "sales_invoice", "create"
        )
        assert invoice["graphql_inputs"]["update"] == f"{stem}UpdateArgsInputInput"
        assert "items" in invoice["record_schema"]["properties"]
        assert "owner" not in invoice["write_schema"]["properties"]
        # No other instance's types are listed to anyone.
        namespaces = {row["namespace"] for row in listed.json()["types"]}
        for role in ("user", "disabled", "unreachable", "clerk"):
            assert booted.namespaces[role] not in namespaces

    def test_a_doctype_named_outside_identifiers_has_a_predictable_name(self, booted):
        slug = "route_plan_eu_2026"
        stem = model_stem(booted.operator, slug)
        assert (
            stem
            == f"Erpnext{booted.operator.split('_')[1].capitalize()}RoutePlanEu2026"
        )
        made = create(booted, slug, {"plan_code": "R-1"})
        assert made.json()["plan_code"] == "R-1"
        field = graphql_field(booted.operator, slug, "get")
        assert field.endswith("RoutePlanEu2026Get")
        answer = booted.server.post(
            "/graphql",
            json={
                "query": f"{{ {field}(input: {{name: {json.dumps(made.json()['name'])}}}) "
                "{ planCode updatedAt } }"
            },
            headers=bearer(booted.admin),
        )
        assert "errors" not in answer.json(), answer.text
        assert answer.json()["data"][field]["planCode"] == "R-1"
