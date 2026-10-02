# SPDX-License-Identifier: AGPL-3.0-or-later
"""E-commerce: checks on amounts, limits and changes; the local mirror
(upserted by store and platform id, owned like the store, read-only over
the API); the sales report; the sync and report routes, which a user
reaches only for stores they can see; and Shopify's answers (as its API
documents them) read into the shared shapes."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Dict

import pytest

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.ecommerce.BLL_ECommerce import (
    StoreOrderManager,
    mirror,
    sales_report,
)
from zephyrex.extensions.ecommerce.EXT_ECommerce import (
    EXT_ECommerce,
    checked_changes,
    checked_money,
    item,
    money,
    order,
)
from zephyrex.extensions.ecommerce.PRV_Shopify import (
    PRV_Shopify_ECommerce,
    gid,
    shopify_order,
    shopify_status,
)
from zephyrex.extensions.ExternalErrors import InvalidInputExternalError

PLACED = datetime.now(UTC) - timedelta(days=2)


def auth(user) -> Dict[str, str]:
    return {"Authorization": f"Bearer {user.jwt}"}


class TestChecks:
    @pytest.mark.parametrize(
        "value, text",
        [("19.90", "19.9"), (12, "12"), ("0.10", "0.1"), (None, None), ("x", None)],
    )
    def test_money(self, value, text):
        assert money(value) == text

    def test_checked_money_and_changes(self):
        assert checked_money("5.50", "price") == "5.5"
        with pytest.raises(InvalidInputExternalError):
            checked_money("-1", "price")
        assert checked_changes({"price": "10.00"}) == {"price": "10"}
        for bad in ({}, {"stock": 3}, {"price": "abc"}):
            with pytest.raises(InvalidInputExternalError):
                checked_changes(bad)

    def test_an_unknown_status_is_other(self):
        assert order("1", status="teleported")["status"] == "other"


class TestShopify:
    NODE = {
        "id": "gid://shopify/Order/5551234",
        "name": "#1001",
        "createdAt": "2026-10-01T09:30:00Z",
        "cancelledAt": None,
        "email": "ada@example.com",
        "displayFinancialStatus": "PAID",
        "displayFulfillmentStatus": "UNFULFILLED",
        "customer": {"displayName": "Ada Lovelace"},
        "totalPriceSet": {"shopMoney": {"amount": "42.50", "currencyCode": "CAD"}},
        "shippingAddress": {"city": "Toronto", "countryCodeV2": "CA"},
        "lineItems": {
            "nodes": [
                {
                    "sku": "MUG-1",
                    "title": "Mug",
                    "quantity": 2,
                    "originalUnitPriceSet": {"shopMoney": {"amount": "21.25"}},
                }
            ]
        },
    }

    def test_an_order(self):
        assert shopify_order(self.NODE) == {
            "platform_order_id": "5551234",
            "order_number": "#1001",
            "status": "processing",
            "customer_name": "Ada Lovelace",
            "customer_email": "ada@example.com",
            "total": "42.5",
            "currency": "CAD",
            "order_date": "2026-10-01T09:30:00Z",
            "shipping_address": {"city": "Toronto", "countryCodeV2": "CA"},
            "line_items": [item("MUG-1", "Mug", 2, "21.25")],
        }

    @pytest.mark.parametrize(
        "changes, status",
        [
            ({"cancelledAt": "2026-10-02T00:00:00Z"}, "cancelled"),
            ({"displayFinancialStatus": "REFUNDED"}, "refunded"),
            ({"displayFulfillmentStatus": "FULFILLED"}, "shipped"),
            ({"displayFinancialStatus": "PENDING"}, "pending"),
        ],
    )
    def test_statuses(self, changes, status):
        assert shopify_status({**self.NODE, **changes}) == status

    def test_ids(self):
        assert gid("Order", "123") == "gid://shopify/Order/123"
        assert gid("Order", "gid://shopify/Order/9") == "gid://shopify/Order/9"
        with pytest.raises(InvalidInputExternalError):
            gid("Order", "../admin")

    @pytest.mark.parametrize(
        "domain",
        ["evil.example.com", "shop.myshopify.com.evil.io", "a/b.myshopify.com"],
    )
    def test_only_a_myshopify_domain_is_called(self, provider_instance, domain):
        instance = provider_instance(
            PRV_Shopify_ECommerce, api_key="token", settings={"shop_domain": domain}
        )
        with pytest.raises(InvalidInputExternalError):
            PRV_Shopify_ECommerce._endpoint(instance)


class TestMirror(ExtensionServerMixin):
    extension_class = EXT_ECommerce

    @pytest.fixture
    def store(self, server, admin_a) -> Dict[str, Any]:
        providers = server.get("/v1/provider", headers=auth(admin_a)).json()[
            "providers"
        ]
        shopify = next(p for p in providers if p["name"] == "shopify")
        response = server.post(
            "/v1/provider/instance",
            json={
                "provider_instance": {
                    "name": f"shop-{uuid.uuid4().hex}",
                    "provider_id": shopify["id"],
                }
            },
            headers=auth(admin_a),
        )
        assert response.status_code == 201, response.text
        created: Dict[str, Any] = response.json()["provider_instance"]
        return created

    def _orders(self, total: str = "40", status: str = "processing"):
        return [
            order(
                "1001",
                status=status,
                total=total,
                currency="CAD",
                order_date=PLACED.isoformat(),
                line_items=[item("MUG-1", "Mug", 2, "20")],
            ),
            order(
                "1002",
                status="cancelled",
                total="99",
                currency="CAD",
                order_date=PLACED.isoformat(),
                line_items=[item("HAT-1", "Hat", 1, "99")],
            ),
        ]

    def test_records_are_upserted_and_owned_like_the_store(
        self, server, admin_a, admin_b, store
    ):
        registry = server.app.state.model_registry
        mirror(registry, store["id"], "order", self._orders())
        mirror(
            registry, store["id"], "order", self._orders(total="45", status="shipped")
        )

        mine = server.get("/v1/store_order", headers=auth(admin_a)).json()[
            "store_orders"
        ]
        ours = [row for row in mine if row["provider_instance_id"] == store["id"]]
        assert sorted(row["platform_order_id"] for row in ours) == ["1001", "1002"]
        first = next(row for row in ours if row["platform_order_id"] == "1001")
        assert first["total"] == "45" and first["status"] == "shipped"
        assert first["provider"] == "shopify"

        theirs = server.get("/v1/store_order", headers=auth(admin_b)).json()
        assert not [
            row
            for row in theirs["store_orders"]
            if row["provider_instance_id"] == store["id"]
        ]

    def test_the_report(self, server, admin_a, store):
        registry = server.app.state.model_registry
        mirror(registry, store["id"], "order", self._orders())
        response = server.get(
            "/v1/store_order/report",
            params={"days": 7, "provider_instance_id": store["id"]},
            headers=auth(admin_a),
        )
        assert response.status_code == 200, response.text
        report = response.json()
        assert report["revenue"] == {"CAD": "40"}
        assert report["by_status"]["cancelled"] == 1
        assert report["top_skus"][0] == {"sku": "MUG-1", "quantity": 2}

    def test_another_user_cannot_sync_the_store(self, server, admin_b, store):
        response = server.post(
            "/v1/store_order/sync",
            json={"provider_instance_id": store["id"], "days": 7},
            headers=auth(admin_b),
        )
        assert response.status_code == 404, response.text

    def test_the_mirror_is_read_only_over_the_api(self, server, admin_a):
        paths = server.app.openapi()["paths"]
        writes = {
            (method, path)
            for path, operations in paths.items()
            if path.startswith("/v1/store_")
            for method in operations
            if method != "get"
        }
        assert writes == {
            ("post", "/v1/store_order/search"),
            ("post", "/v1/store_order/sync"),
            ("post", "/v1/store_product/search"),
            ("post", "/v1/store_return/search"),
        }

    def test_sales_report_counts_only_what_was_sold(self, server, admin_a, store):
        registry = server.app.state.model_registry
        mirror(registry, store["id"], "order", self._orders())
        manager = StoreOrderManager(model_registry=registry, requester_id=admin_a.id)
        report = sales_report(manager, PLACED - timedelta(days=1), store["id"])
        assert report["orders"] == 2 and report["revenue"] == {"CAD": "40"}
