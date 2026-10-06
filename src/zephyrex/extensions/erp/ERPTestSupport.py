# SPDX-License-Identifier: AGPL-3.0-or-later
"""What tests against ERPNext share: a Frappe site with standard,
submittable and custom DocTypes and two accounts, served by
:class:`FrappeServer`, and provider instances of every scope pointing at it.

It imports from an install alone (no pytest, no repository conftest), so a
consumer can stand up a site for its own tests; the framework's own server
mixin is in ERPServer_test."""

import copy
import uuid
from typing import Any, Dict, Optional

from zephyrex.extensions.erp.FrappeTestServer import (
    Account,
    FrappeServer,
    FrappeSite,
    doctype_of,
    field_of,
)
from zephyrex.extensions.erp.PRV_ERPNext import PRV_ERPNext
from zephyrex.lib.Environment import env
from zephyrex.logic.BLL_Providers import (
    ProviderInstanceManager,
    ProviderInstanceModel,
    ProviderInstanceSettingManager,
    ProviderManager,
)

OPERATOR_KEY, OPERATOR_SECRET = "operator-key", "operator-secret-1f3a9c"
CLERK_KEY, CLERK_SECRET = "clerk-key", "clerk-secret-77b2e0"
WEBHOOK_SECRET = "frappe-webhook-secret-0123456789"
ALL_RIGHTS = {"read", "write", "create", "delete", "submit", "cancel"}

CUSTOMER = doctype_of(
    "Customer",
    [
        field_of("customer_name", reqd=1),
        field_of("customer_group", "Link", options="Customer Group"),
        field_of("details", "Section Break"),
        field_of("credit_limit", "Currency"),
        field_of("disabled", "Check"),
    ],
    module="Selling",
)
SALES_INVOICE_ITEM = doctype_of(
    "Sales Invoice Item",
    [
        field_of("item_code", reqd=1),
        field_of("qty", "Float", reqd=1),
        field_of("rate", "Currency"),
    ],
    module="Accounts",
    istable=1,
)
SALES_INVOICE = doctype_of(
    "Sales Invoice",
    [
        field_of("customer", "Link", options="Customer", reqd=1),
        field_of("posting_date", "Date"),
        field_of("remarks", "Small Text", allow_on_submit=1),
        field_of("items", "Table", options="Sales Invoice Item", reqd=1),
    ],
    module="Accounts",
    is_submittable=1,
    naming_prefix="ACC-SINV",
)
# A DocType the site's users made, with a field named as a BaseModel
# attribute is.
DELIVERY_ROUTE = doctype_of(
    "Delivery Route",
    [
        field_of("route_code", reqd=1),
        field_of("custom_stops", "Int"),
        field_of("json", "Data"),
    ],
    module="Custom",
    custom=1,
)


def standard_site() -> FrappeSite:
    """A site where the operator's account may do anything and the clerk's
    may only read customers."""
    site = FrappeSite()
    for meta in (CUSTOMER, SALES_INVOICE_ITEM, SALES_INVOICE, DELIVERY_ROUTE):
        site.add_doctype(copy.deepcopy(meta))
    site.add_account(
        OPERATOR_KEY,
        Account(
            user="operator@example.com",
            api_secret=OPERATOR_SECRET,
            rights={name: set(ALL_RIGHTS) for name in [*site.doctypes, "DocType"]},
        ),
    )
    site.add_account(
        CLERK_KEY,
        Account(
            user="clerk@example.com",
            api_secret=CLERK_SECRET,
            rights={"Customer": {"read"}},
        ),
    )
    return site


def erp_instance(
    model_registry: Any,
    base_url: str,
    *,
    api_key: str = OPERATOR_KEY,
    api_secret: str = OPERATOR_SECRET,
    webhook_secret: Optional[str] = WEBHOOK_SECRET,
    owner_id: Optional[str] = None,
    scope: str = "root",
    team_id: Optional[str] = None,
    extra_settings: Optional[Dict[str, str]] = None,
) -> ProviderInstanceModel:
    """A new ERPNext instance: the operator's (made by ROOT) unless
    ``owner_id`` names the user making it, for ``scope``; ``extra_settings``
    are further settings rows (``typed_doctypes``)."""
    maker = owner_id or env("ROOT_ID")
    provider = ProviderManager(
        model_registry=model_registry, requester_id=env("ROOT_ID")
    ).get(name=PRV_ERPNext.name)
    fields: Dict[str, Any] = {
        "name": f"erpnext_{uuid.uuid4().hex}",
        "provider_id": provider.id,
        "api_key": api_key,
        "scope": scope,
    }
    if owner_id is not None and scope == "user":
        fields["user_id"] = owner_id
    if team_id is not None:
        fields["team_id"] = team_id
    instance = ProviderInstanceModel.model_validate(
        ProviderInstanceManager(
            model_registry=model_registry, requester_id=maker
        ).create(**fields),
        from_attributes=True,
    )
    settings = {"base_url": base_url, "api_secret": api_secret}
    if webhook_secret is not None:
        settings["webhook_secret"] = webhook_secret
    settings.update(extra_settings or {})
    rows = ProviderInstanceSettingManager(
        model_registry=model_registry, requester_id=maker
    )
    for key, value in settings.items():
        rows.create(provider_instance_id=instance.id, key=key, value=value)
    return instance
