# SPDX-License-Identifier: AGPL-3.0-or-later
"""ERPNext on the wire: what is sent to a Frappe site and how its answers
and refusals are read, its webhook signatures, and the real ERPNext sandbox
(auto-xfailed without ERPNEXT_URL, ERPNEXT_API_KEY and ERPNEXT_API_SECRET)."""

import base64
import hashlib
import hmac
import json
import uuid
from typing import Any, Dict
from urllib.parse import urlsplit

import pytest
from fastapi import HTTPException

from zephyrex.extensions.erp.BLL_ERP import ERPDocuments, ListQuery
from zephyrex.extensions.erp.ERPTestSupport import (
    OPERATOR_KEY,
    OPERATOR_SECRET,
    WEBHOOK_SECRET,
    ERPServerMixin,
    erp_instance,
)
from zephyrex.extensions.erp.PRV_ERPNext import (
    MAX_REFUSAL_CHARACTERS,
    PRV_ERPNext,
    frappe_refusal,
    webhook_signature,
)
from zephyrex.lib.Preconditions import PreconditionFailed, PreconditionRequired


def frappe_error(*messages: str, exc_type: str = "ValidationError") -> str:
    """An error body as ``frappe.utils.response.report_error`` sends it."""
    return json.dumps(
        {
            "exception": f"frappe.exceptions.{exc_type}: {messages[0] if messages else ''}",
            "exc_type": exc_type,
            "_server_messages": json.dumps(
                [json.dumps({"message": m, "indicator": "red"}) for m in messages]
            ),
        }
    )


class TestRefusals:
    def test_the_sites_messages_are_its_reason_without_markup(self):
        body = frappe_error("Row #1: <b>Qty</b> is mandatory", "Rate is negative")
        assert frappe_refusal(body) == "Row #1: Qty is mandatory; Rate is negative"

    def test_without_messages_the_exception_type_is_the_reason(self):
        assert frappe_refusal(json.dumps({"exc_type": "LinkValidationError"})) == (
            "LinkValidationError"
        )

    def test_a_body_cut_short_still_names_its_exception(self):
        """An error body reaches the provider cut to 512 bytes, so it may no
        longer be JSON."""
        assert frappe_refusal('{"exc_type": "TimestampMismatchError", "exc": "[') == (
            "TimestampMismatchError"
        )

    def test_a_reason_is_bounded_and_garbage_gives_none(self):
        assert len(frappe_refusal(frappe_error("y" * 5000))) == MAX_REFUSAL_CHARACTERS
        assert frappe_refusal("<html>Bad Gateway</html>") == ""
        assert frappe_refusal(None) == ""


class TestWebhookSignature:
    def test_it_is_frappes_base64_hmac_sha256_of_the_body(self):
        """``get_webhook_headers``: ``base64.b64encode(hmac.new(secret,
        frappe.as_json(data), sha256).digest())``."""
        body = b'{\n "doctype": "ToDo"\n}'
        expected = base64.b64encode(
            hmac.new(b"s3cret-0123456789", body, hashlib.sha256).digest()
        ).decode()
        assert webhook_signature("s3cret-0123456789", body) == expected


class TestWire(ERPServerMixin):
    async def test_requests_carry_the_accounts_token_and_ask_for_json(
        self, model_registry, frappe, operator, admin_a
    ):
        documents = ERPDocuments.for_requester(
            model_registry, admin_a.id, str(operator.id)
        )
        await documents.list(
            ListQuery.checked(
                "Sales Invoice",
                ["customer"],
                filters={"customer": ["like", "%Ac%"]},
                order_by="modified desc",
                start=5,
                page_length=7,
            )
        )
        [sent] = frappe.requests
        assert sent["headers"]["authorization"] == (
            f"token {OPERATOR_KEY}:{OPERATOR_SECRET}"
        )
        assert sent["headers"]["accept"] == "application/json"
        assert sent["path"] == "/api/resource/Sales Invoice"
        assert json.loads(sent["query"]["fields"]) == [
            "customer",
            "name",
            "modified",
            "docstatus",
        ]
        assert json.loads(sent["query"]["filters"]) == {"customer": ["like", "%Ac%"]}
        assert sent["query"]["order_by"] == "modified desc"
        assert (sent["query"]["limit_start"], sent["query"]["limit_page_length"]) == (
            "5",
            "7",
        )

    async def test_the_doctype_catalogue_is_paged(
        self, model_registry, frappe, operator, admin_a, monkeypatch
    ):
        from zephyrex.extensions.erp import PRV_ERPNext as module
        from zephyrex.extensions.erp.FrappeTestServer import doctype_of

        for n in range(4):
            frappe.site.add_doctype(doctype_of(f"Extra {n}", []))
        monkeypatch.setattr(module, "DOCTYPE_PAGE_LENGTH", 3)
        found = await PRV_ERPNext.doctypes(operator)
        assert len(found) == len(frappe.site.doctypes)
        assert [r["query"]["limit_start"] for r in frappe.requests] == ["0", "3", "6"]

    async def test_a_refusal_echoing_the_credential_does_not_pass_it_on(
        self, model_registry, local_http_server, admin_a
    ):
        echoed = frappe_error(f"bad token {OPERATOR_SECRET}")
        site = local_http_server(
            {
                "/api/resource/Customer/x": (
                    417,
                    {"Content-Type": "application/json"},
                    echoed.encode(),
                )
            }
        )
        instance = erp_instance(model_registry, site.base_url)
        documents = ERPDocuments.for_requester(
            model_registry, admin_a.id, str(instance.id)
        )
        with pytest.raises(HTTPException) as refused:
            await documents.get("Customer", "x")
        assert refused.value.status_code == 422
        assert "bad token ***" in str(refused.value.detail)
        assert OPERATOR_SECRET not in str(refused.value.detail)
        assert OPERATOR_SECRET not in repr(refused.value.__cause__)

    def test_a_webhook_is_verified_against_the_instances_secret(
        self, model_registry, frappe
    ):
        instance = erp_instance(model_registry, frappe.base_url)
        body = b'{"event": "on_update"}'
        good = {"x-frappe-webhook-signature": webhook_signature(WEBHOOK_SECRET, body)}
        assert PRV_ERPNext.verified_webhook(instance, body, good) == (
            good["x-frappe-webhook-signature"]
        )
        assert PRV_ERPNext.verified_webhook(instance, body + b" ", good) is None
        assert PRV_ERPNext.verified_webhook(instance, body, {}) is None


def live_instance_for(model_registry: Any, credentials: Dict[str, str]) -> Any:
    return erp_instance(
        model_registry,
        credentials["ERPNEXT_URL"],
        api_key=credentials["ERPNEXT_API_KEY"],
        api_secret=credentials["ERPNEXT_API_SECRET"],
        webhook_secret=None,
    )


@pytest.fixture
def sandbox(sandbox_credentials_for, monkeypatch) -> Dict[str, str]:
    """The sandbox's credentials, its host let through the SSRF guard (a
    sandbox is often on a private network)."""
    credentials: Dict[str, str] = sandbox_credentials_for("erpnext")
    host = urlsplit(credentials.get("ERPNEXT_URL") or "").netloc
    if host:
        monkeypatch.setenv("EGRESS_ALLOWED_HOSTS", host)
    return credentials


@pytest.mark.external_api(provider="erpnext")
class TestLiveERPNext(ERPServerMixin):
    """A real ERPNext site. The tests make, change and remove their own ToDo
    documents and read DocTypes every Frappe site has."""

    async def test_the_catalogue_and_a_doctype_with_a_child_table(
        self, model_registry, sandbox, admin_a
    ):
        instance = live_instance_for(model_registry, sandbox)
        documents = ERPDocuments.for_requester(
            model_registry, admin_a.id, str(instance.id)
        )
        names = {d.name for d in await documents.doctypes()}
        assert {"ToDo", "User", "Has Role"} <= names
        user = await documents.schema("User")
        assert user.tables["roles"].doctype == "Has Role"
        assert user.tables["roles"].istable

    async def test_a_todo_round_trip_held_to_its_version(
        self, model_registry, sandbox, admin_a
    ):
        instance = live_instance_for(model_registry, sandbox)
        documents = ERPDocuments.for_requester(
            model_registry, admin_a.id, str(instance.id)
        )
        marker = f"zephyrex erp live test {uuid.uuid4().hex}"
        made = await documents.create("ToDo", {"description": marker})
        try:
            assert made.updated_at
            saved = await documents.update(
                "ToDo", made.name, {"description": marker + " (1)"}, made.updated_at
            )
            with pytest.raises(PreconditionFailed):
                await documents.update(
                    "ToDo", made.name, {"description": "stale"}, made.updated_at
                )
            with pytest.raises(PreconditionRequired):
                await documents.update("ToDo", made.name, {"description": "x"}, None)
            listed = await documents.list(
                ListQuery.checked("ToDo", ["description"], filters={"name": made.name})
            )
            assert [d.data["description"] for d in listed] == [marker + " (1)"]
            with pytest.raises(PreconditionFailed):
                await documents.delete("ToDo", made.name, made.updated_at)
        finally:
            current = await documents.get("ToDo", made.name)
            await documents.delete("ToDo", made.name, current.updated_at)
        assert saved.data["description"] == marker + " (1)"
