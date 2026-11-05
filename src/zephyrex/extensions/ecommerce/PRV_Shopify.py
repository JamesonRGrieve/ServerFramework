# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shopify, through the GraphQL Admin API (Shopify's REST Admin API is
legacy for new apps). The instance's API key is an Admin API access token
(a custom app's, or an app's after OAuth) with the orders, products,
inventory, fulfillments and returns scopes; ``shop_domain`` is the
store's ``<name>.myshopify.com``.

A price change sets the SKU's variant price; a refund on an approved
return is issued through Shopify's refund flow, not here.
"""

import re
from datetime import datetime
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Tuple

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.ecommerce.EXT_ECommerce import (
    AbstractEcommerceProvider,
    a_return,
    item,
    order,
    product,
)
from zephyrex.extensions.ExternalErrors import InvalidInputExternalError
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

DEFAULT_API_VERSION = "2026-10"
LINE_ITEMS = 50
_SHOP_DOMAIN = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}\.myshopify\.com$")
_RETURN_STATUSES = {
    "REQUESTED": "requested",
    "OPEN": "approved",
    "CLOSED": "closed",
    "DECLINED": "rejected",
    "CANCELED": "closed",
}
ORDER_FIELDS = """
  id name createdAt cancelledAt email
  displayFinancialStatus displayFulfillmentStatus
  customer { displayName }
  totalPriceSet { shopMoney { amount currencyCode } }
  shippingAddress { name address1 address2 city province zip countryCodeV2 phone }
  lineItems(first: %d) {
    nodes { sku title quantity originalUnitPriceSet { shopMoney { amount } } }
  }
""" % (LINE_ITEMS)


def gid(kind: str, value: str) -> str:
    """A Shopify global id from a bare number or a gid."""
    value = value.strip()
    if value.startswith("gid://shopify/"):
        return value
    if not value.isdigit():
        raise InvalidInputExternalError(f"{value!r} is not a Shopify {kind} id")
    return f"gid://shopify/{kind}/{value}"


def short(global_id: str) -> str:
    return global_id.rsplit("/", 1)[-1]


def shopify_status(node: Mapping[str, Any]) -> str:
    if node.get("cancelledAt"):
        return "cancelled"
    financial = node.get("displayFinancialStatus")
    fulfillment = node.get("displayFulfillmentStatus")
    if financial in ("REFUNDED",):
        return "refunded"
    if fulfillment == "FULFILLED":
        return "shipped"
    if financial in ("PENDING", "AUTHORIZED", "EXPIRED"):
        return "pending"
    return "processing"


def shopify_order(node: Mapping[str, Any]) -> Dict[str, Any]:
    money = (node.get("totalPriceSet") or {}).get("shopMoney") or {}
    return order(
        short(node["id"]),
        status=shopify_status(node),
        order_number=node.get("name"),
        customer_name=(node.get("customer") or {}).get("displayName"),
        customer_email=node.get("email"),
        total=money.get("amount"),
        currency=money.get("currencyCode"),
        order_date=node.get("createdAt"),
        shipping_address=node.get("shippingAddress"),
        line_items=[
            item(
                line.get("sku"),
                line.get("title"),
                line.get("quantity"),
                ((line.get("originalUnitPriceSet") or {}).get("shopMoney") or {}).get(
                    "amount"
                ),
            )
            for line in (node.get("lineItems") or {}).get("nodes", [])
        ],
    )


class PRV_Shopify_ECommerce(AbstractEcommerceProvider):
    name: ClassVar[str] = "shopify"
    friendly_name: ClassVar[str] = "Shopify"
    description: ClassVar[str] = "A Shopify store"
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting(
            "api_key", "Admin API access token", secret=True, field="api_key"
        ),
        InstanceSetting("shop_domain", "The store's <name>.myshopify.com"),
        InstanceSetting(
            "api_version", "Admin API version", default=DEFAULT_API_VERSION
        ),
    )

    @classmethod
    def _endpoint(cls, instance: ProviderInstanceModel) -> str:
        domain = cls.required(instance, "shop_domain").strip().lower()
        if not _SHOP_DOMAIN.match(domain):
            raise InvalidInputExternalError(
                f"{domain!r} is not a <name>.myshopify.com domain", provider=cls.name
            )
        version = cls.setting(instance, "api_version") or DEFAULT_API_VERSION
        return f"https://{domain}/admin/api/{version}/graphql.json"

    @classmethod
    async def graphql(
        cls,
        instance: ProviderInstanceModel,
        query: str,
        variables: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """A query's ``data``; GraphQL errors and mutation userErrors are
        the caller's."""
        answer = await cls.http().post(
            cls._endpoint(instance),
            json={"query": query, "variables": variables or {}},
            headers={"X-Shopify-Access-Token": cls.required(instance, "api_key")},
        )
        if answer.get("errors"):
            raise InvalidInputExternalError(
                f"Shopify: {answer['errors'][0].get('message', answer['errors'])}",
                provider=cls.name,
            )
        data: Dict[str, Any] = answer.get("data") or {}
        for payload in data.values():
            errors = (
                (payload or {}).get("userErrors") if isinstance(payload, dict) else None
            )
            if errors:
                raise InvalidInputExternalError(
                    f"Shopify: {errors[0].get('message')}", provider=cls.name
                )
        return data

    @classmethod
    async def orders(
        cls, instance: ProviderInstanceModel, since: datetime, limit: int
    ) -> List[Dict[str, Any]]:
        data = await cls.graphql(
            instance,
            "query($first: Int!, $query: String!) { orders(first: $first, "
            "query: $query, sortKey: CREATED_AT, reverse: true) { nodes {"
            + ORDER_FIELDS
            + "} } }",
            {"first": limit, "query": f"created_at:>={since.date().isoformat()}"},
        )
        return [shopify_order(node) for node in data["orders"]["nodes"]]

    @classmethod
    async def order(
        cls, instance: ProviderInstanceModel, order_id: str
    ) -> Dict[str, Any]:
        data = await cls.graphql(
            instance,
            "query($id: ID!) { order(id: $id) {" + ORDER_FIELDS + "} }",
            {"id": gid("Order", order_id)},
        )
        if not data.get("order"):
            raise InvalidInputExternalError(
                f"no order {order_id}", provider=cls.name, upstream_status=404
            )
        return shopify_order(data["order"])

    @classmethod
    async def cancel_order(
        cls, instance: ProviderInstanceModel, order_id: str, reason: str
    ) -> Dict[str, Any]:
        await cls.graphql(
            instance,
            "mutation($id: ID!, $note: String) { orderCancel(orderId: $id, "
            "reason: OTHER, refund: false, restock: true, notifyCustomer: true, "
            "staffNote: $note) { job { id } userErrors: orderCancelUserErrors "
            "{ message } } }",
            {"id": gid("Order", order_id), "note": reason},
        )
        return await cls.order(instance, order_id)

    @classmethod
    async def fulfill_order(
        cls,
        instance: ProviderInstanceModel,
        order_id: str,
        tracking_number: Optional[str],
        carrier: Optional[str],
    ) -> Dict[str, Any]:
        data = await cls.graphql(
            instance,
            "query($id: ID!) { order(id: $id) { fulfillmentOrders(first: 10) "
            "{ nodes { id status } } } }",
            {"id": gid("Order", order_id)},
        )
        open_orders = [
            node["id"]
            for node in (data.get("order") or {})
            .get("fulfillmentOrders", {})
            .get("nodes", [])
            if node.get("status") in ("OPEN", "IN_PROGRESS")
        ]
        if not open_orders:
            raise InvalidInputExternalError(
                "the order has nothing left to fulfill", provider=cls.name
            )
        fulfillment: Dict[str, Any] = {
            "lineItemsByFulfillmentOrder": [
                {"fulfillmentOrderId": fulfillment_order}
                for fulfillment_order in open_orders
            ],
            "notifyCustomer": True,
        }
        if tracking_number:
            fulfillment["trackingInfo"] = {
                "number": tracking_number,
                **({"company": carrier} if carrier else {}),
            }
        created = await cls.graphql(
            instance,
            "mutation($fulfillment: FulfillmentInput!) { fulfillmentCreate("
            "fulfillment: $fulfillment) { fulfillment { id status } "
            "userErrors { message } } }",
            {"fulfillment": fulfillment},
        )
        shipped = created["fulfillmentCreate"]["fulfillment"]
        return {
            "order_id": order_id,
            "fulfillment_id": short(shipped["id"]),
            "provider": cls.name,
        }

    @classmethod
    async def products(
        cls, instance: ProviderInstanceModel, limit: int
    ) -> List[Dict[str, Any]]:
        data = await cls.graphql(
            instance,
            "query($first: Int!) { shop { currencyCode } products(first: $first, "
            "sortKey: UPDATED_AT, reverse: true) { nodes { id title description "
            "status onlineStoreUrl images(first: 5) { nodes { url } } "
            "variants(first: 1) { nodes { sku price inventoryQuantity } } } } }",
            {"first": limit},
        )
        currency = data["shop"]["currencyCode"]
        found = []
        for node in data["products"]["nodes"]:
            variant = ((node.get("variants") or {}).get("nodes") or [{}])[0]
            found.append(
                product(
                    short(node["id"]),
                    sku=variant.get("sku"),
                    title=node.get("title"),
                    description=node.get("description"),
                    price=variant.get("price"),
                    currency=currency,
                    quantity=variant.get("inventoryQuantity"),
                    images=[
                        i["url"] for i in (node.get("images") or {}).get("nodes", [])
                    ],
                    is_active=node.get("status") == "ACTIVE",
                    url=node.get("onlineStoreUrl"),
                )
            )
        return found

    @classmethod
    async def _variant(
        cls, instance: ProviderInstanceModel, sku: str
    ) -> Dict[str, Any]:
        data = await cls.graphql(
            instance,
            "query($query: String!) { productVariants(first: 2, query: $query) "
            "{ nodes { id sku product { id } inventoryItem { id } } } }",
            {"query": f'sku:"{sku}"'},
        )
        matches = [v for v in data["productVariants"]["nodes"] if v.get("sku") == sku]
        if len(matches) != 1:
            raise InvalidInputExternalError(
                f"{len(matches)} variants have SKU {sku!r}", provider=cls.name
            )
        found: Dict[str, Any] = matches[0]
        return found

    @classmethod
    async def set_inventory(
        cls, instance: ProviderInstanceModel, sku: str, quantity: int
    ) -> Dict[str, Any]:
        variant = await cls._variant(instance, sku)
        locations = await cls.graphql(
            instance, "{ locations(first: 1) { nodes { id } } }"
        )
        location = locations["locations"]["nodes"][0]["id"]
        await cls.graphql(
            instance,
            "mutation($input: InventorySetQuantitiesInput!) { "
            "inventorySetQuantities(input: $input) { inventoryAdjustmentGroup "
            "{ reason } userErrors { message } } }",
            {
                "input": {
                    "name": "available",
                    "reason": "correction",
                    "quantities": [
                        {
                            "inventoryItemId": variant["inventoryItem"]["id"],
                            "locationId": location,
                            "quantity": quantity,
                            "changeFromQuantity": None,
                        }
                    ],
                }
            },
        )
        return {"sku": sku, "quantity": quantity, "provider": cls.name}

    @classmethod
    async def update_product(
        cls, instance: ProviderInstanceModel, sku: str, changes: Dict[str, Any]
    ) -> Dict[str, Any]:
        variant = await cls._variant(instance, sku)
        product_changes = {
            key: value
            for key, value in (
                ("title", changes.get("title")),
                ("descriptionHtml", changes.get("description")),
            )
            if value is not None
        }
        if product_changes:
            await cls.graphql(
                instance,
                "mutation($product: ProductUpdateInput!) { productUpdate("
                "product: $product) { product { id } userErrors { message } } }",
                {"product": {"id": variant["product"]["id"], **product_changes}},
            )
        if "price" in changes:
            await cls.graphql(
                instance,
                "mutation($product: ID!, $variants: [ProductVariantsBulkInput!]!) { "
                "productVariantsBulkUpdate(productId: $product, variants: $variants) "
                "{ productVariants { id } userErrors { message } } }",
                {
                    "product": variant["product"]["id"],
                    "variants": [{"id": variant["id"], "price": changes["price"]}],
                },
            )
        return {"sku": sku, "changed": sorted(changes), "provider": cls.name}

    @classmethod
    async def returns(
        cls, instance: ProviderInstanceModel, since: datetime, limit: int
    ) -> List[Dict[str, Any]]:
        data = await cls.graphql(
            instance,
            "query($first: Int!, $query: String!) { orders(first: $first, "
            "query: $query, sortKey: UPDATED_AT, reverse: true) { nodes { id "
            "returns(first: 10) { nodes { id status } } } } }",
            {
                "first": limit,
                "query": f"updated_at:>={since.date().isoformat()} AND return_status:*",
            },
        )
        found = []
        for node in data["orders"]["nodes"]:
            for entry in (node.get("returns") or {}).get("nodes", []):
                found.append(
                    a_return(
                        short(entry["id"]),
                        status=_RETURN_STATUSES.get(str(entry.get("status")), "other"),
                        platform_order_id=short(node["id"]),
                    )
                )
        return found[:limit]

    @classmethod
    async def process_return(
        cls,
        instance: ProviderInstanceModel,
        return_id: str,
        action: str,
        refund: Optional[str],
    ) -> Dict[str, Any]:
        if refund is not None:
            raise cls.cannot("refund here", "issue it through Shopify's refund flow")
        mutation = (
            "mutation($input: ReturnApproveRequestInput!) { returnApproveRequest("
            "input: $input) { return { id status } userErrors { message } } }"
            if action == "approve"
            else "mutation($input: ReturnDeclineRequestInput!) { returnDeclineRequest("
            "input: $input) { return { id status } userErrors { message } } }"
        )
        payload: Dict[str, Any] = {"id": gid("Return", return_id)}
        if action == "reject":
            payload["declineReason"] = "OTHER"
        data = await cls.graphql(instance, mutation, {"input": payload})
        result = next(iter(data.values()))["return"]
        return {
            "return_id": return_id,
            "status": _RETURN_STATUSES.get(str(result.get("status")), "other"),
            "provider": cls.name,
        }
