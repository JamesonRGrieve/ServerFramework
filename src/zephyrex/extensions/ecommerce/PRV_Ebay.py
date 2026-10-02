# SPDX-License-Identifier: AGPL-3.0-or-later
"""eBay, through the Sell Fulfillment and Inventory APIs and the
Post-Order API (cancellations, returns). The instance's API key is the
app's client id, with its ``client_secret`` and a user ``refresh_token``
(valid 18 months; access tokens are traded for it every two hours);
``environment`` is ``production`` or ``sandbox``.

Products are inventory items, by SKU; a price lives on the SKU's offer
in ``marketplace_id``.
"""

from datetime import UTC, datetime
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Tuple
from urllib.parse import quote

import httpx

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.ecommerce.EXT_ECommerce import (
    AbstractEcommerceProvider,
    a_return,
    item,
    order,
    product,
)
from zephyrex.extensions.ExternalErrors import (
    AuthExternalError,
    InvalidInputExternalError,
)
from zephyrex.lib.ProviderHTTPClient import path_segment
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

HOSTS = {
    "production": "https://api.ebay.com",
    "sandbox": "https://api.sandbox.ebay.com",
}
DEFAULT_MARKETPLACE = "EBAY_US"
MAX_PAGE = 200
CANCEL_REASONS = (
    "BUYER_ASKED_CANCEL",
    "OUT_OF_STOCK_OR_CANNOT_FULFILL",
    "ADDRESS_ISSUES",
)
DEFAULT_CANCEL_REASON = "OUT_OF_STOCK_OR_CANNOT_FULFILL"
_RETURN_STATES = {
    "RETURN_REQUESTED": "requested",
    "RETURN_REQUESTED_TIMEOUT": "requested",
    "RETURN_ACCEPTED": "approved",
    "RETURN_REJECTED": "rejected",
    "REFUND_ISSUED": "refunded",
    "CLOSED": "closed",
}


def ebay_status(found: Mapping[str, Any]) -> str:
    if (found.get("cancelStatus") or {}).get("cancelState") == "CANCELED":
        return "cancelled"
    if found.get("orderPaymentStatus") == "FULLY_REFUNDED":
        return "refunded"
    fulfillment = found.get("orderFulfillmentStatus")
    if fulfillment == "FULFILLED":
        return "shipped"
    if found.get("orderPaymentStatus") in ("PENDING", "FAILED"):
        return "pending"
    return "processing"


def ebay_order(found: Mapping[str, Any]) -> Dict[str, Any]:
    total = (found.get("pricingSummary") or {}).get("total") or {}
    instructions = (found.get("fulfillmentStartInstructions") or [{}])[0]
    ship_to = (instructions.get("shippingStep") or {}).get("shipTo") or {}
    return order(
        found["orderId"],
        status=ebay_status(found),
        order_number=found.get("legacyOrderId") or found["orderId"],
        customer_name=ship_to.get("fullName")
        or (found.get("buyer") or {}).get("username"),
        customer_email=ship_to.get("email"),
        total=total.get("value"),
        currency=total.get("currency"),
        order_date=found.get("creationDate"),
        shipping_address=ship_to.get("contactAddress"),
        line_items=[
            item(
                line.get("sku"),
                line.get("title"),
                line.get("quantity"),
                (line.get("lineItemCost") or {}).get("value"),
            )
            for line in found.get("lineItems", [])
        ],
    )


class PRV_Ebay_ECommerce(AbstractEcommerceProvider):
    name: ClassVar[str] = "ebay"
    friendly_name: ClassVar[str] = "eBay"
    description: ClassVar[str] = "An eBay seller account"
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting("api_key", "App client id", field="api_key"),
        InstanceSetting("client_secret", "App client secret", secret=True),
        InstanceSetting("refresh_token", "User refresh token", secret=True),
        InstanceSetting("environment", "production or sandbox", default="production"),
        InstanceSetting(
            "marketplace_id",
            "Marketplace (EBAY_US, EBAY_GB, …)",
            default=DEFAULT_MARKETPLACE,
        ),
    )

    @classmethod
    def _host(cls, instance: ProviderInstanceModel) -> str:
        environment = str(cls.setting(instance, "environment") or "production")
        if environment not in HOSTS:
            raise InvalidInputExternalError(
                "eBay environment is production or sandbox", provider=cls.name
            )
        return HOSTS[environment]

    @classmethod
    def _marketplace(cls, instance: ProviderInstanceModel) -> str:
        return str(cls.setting(instance, "marketplace_id") or DEFAULT_MARKETPLACE)

    @classmethod
    async def _token(cls, instance: ProviderInstanceModel) -> str:
        client = cls.required(instance, "api_key")
        secret = cls.required(instance, "client_secret")
        refresh = cls.required(instance, "refresh_token")
        host = cls._host(instance)

        async def fetch() -> Tuple[str, float]:
            answer = await cls.http().post(
                f"{host}/identity/v1/oauth2/token",
                data={"grant_type": "refresh_token", "refresh_token": refresh},
                auth=httpx.BasicAuth(client, secret),
            )
            return str(answer["access_token"]), float(answer.get("expires_in", 7200))

        return await cls.access_token(instance, fetch, client, secret, refresh, host)

    @classmethod
    async def _call(
        cls,
        instance: ProviderInstanceModel,
        method: str,
        path: str,
        *,
        scheme: str = "Bearer",
        **kwargs: Any,
    ) -> Any:
        headers = {
            "Authorization": f"{scheme} {await cls._token(instance)}",
            "X-EBAY-C-MARKETPLACE-ID": cls._marketplace(instance),
            **kwargs.pop("headers", {}),
        }
        try:
            return await cls.http().request(
                method, f"{cls._host(instance)}{path}", headers=headers, **kwargs
            )
        except AuthExternalError:
            cls.drop_token(instance)
            raise

    @classmethod
    async def orders(
        cls, instance: ProviderInstanceModel, since: datetime, limit: int
    ) -> List[Dict[str, Any]]:
        stamp = since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        found = await cls._call(
            instance,
            "GET",
            "/sell/fulfillment/v1/order",
            params={
                "filter": f"creationdate:[{stamp}..]",
                "limit": min(limit, MAX_PAGE),
            },
        )
        return [ebay_order(entry) for entry in found.get("orders", [])][:limit]

    @classmethod
    async def order(
        cls, instance: ProviderInstanceModel, order_id: str
    ) -> Dict[str, Any]:
        found = await cls._call(
            instance,
            "GET",
            f"/sell/fulfillment/v1/order/{path_segment(order_id, 'order id')}",
        )
        return ebay_order(found)

    @classmethod
    async def cancel_order(
        cls, instance: ProviderInstanceModel, order_id: str, reason: str
    ) -> Dict[str, Any]:
        code = (
            reason.strip().upper()
            if reason.strip().upper() in CANCEL_REASONS
            else (DEFAULT_CANCEL_REASON)
        )
        await cls._call(
            instance,
            "POST",
            "/post-order/v2/cancellation",
            scheme="IAF",
            json={"legacyOrderId": order_id, "cancelReason": code},
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
        if not (tracking_number and carrier):
            raise InvalidInputExternalError(
                "eBay records a shipment with its tracking number and carrier code",
                provider=cls.name,
            )
        found = await cls._call(
            instance,
            "GET",
            f"/sell/fulfillment/v1/order/{path_segment(order_id, 'order id')}",
        )
        await cls._call(
            instance,
            "POST",
            f"/sell/fulfillment/v1/order/{path_segment(order_id, 'order id')}"
            "/shipping_fulfillment",
            json={
                "lineItems": [
                    {"lineItemId": line["lineItemId"], "quantity": line["quantity"]}
                    for line in found.get("lineItems", [])
                ],
                "shippedDate": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "shippingCarrierCode": carrier,
                "trackingNumber": tracking_number,
            },
        )
        return {"order_id": order_id, "status": "shipped", "provider": cls.name}

    @classmethod
    async def products(
        cls, instance: ProviderInstanceModel, limit: int
    ) -> List[Dict[str, Any]]:
        found = await cls._call(
            instance,
            "GET",
            "/sell/inventory/v1/inventory_item",
            params={"limit": min(limit, MAX_PAGE)},
        )
        return [
            product(
                entry["sku"],
                sku=entry["sku"],
                title=(entry.get("product") or {}).get("title"),
                description=(entry.get("product") or {}).get("description"),
                quantity=(
                    (entry.get("availability") or {}).get("shipToLocationAvailability")
                    or {}
                ).get("quantity"),
                images=(entry.get("product") or {}).get("imageUrls", []),
            )
            for entry in found.get("inventoryItems", [])
        ][:limit]

    @classmethod
    async def set_inventory(
        cls, instance: ProviderInstanceModel, sku: str, quantity: int
    ) -> Dict[str, Any]:
        await cls._call(
            instance,
            "POST",
            "/sell/inventory/v1/bulk_update_price_quantity",
            json={
                "requests": [
                    {"sku": sku, "shipToLocationAvailability": {"quantity": quantity}}
                ]
            },
        )
        return {"sku": sku, "quantity": quantity, "provider": cls.name}

    @classmethod
    async def update_product(
        cls, instance: ProviderInstanceModel, sku: str, changes: Dict[str, Any]
    ) -> Dict[str, Any]:
        path = f"/sell/inventory/v1/inventory_item/{quote(sku, safe='')}"
        if "title" in changes or "description" in changes:
            current = await cls._call(instance, "GET", path)
            current.setdefault("product", {})
            for key in ("title", "description"):
                if key in changes:
                    current["product"][key] = changes[key]
            current.pop("sku", None)
            await cls._call(
                instance,
                "PUT",
                path,
                json=current,
                headers={"Content-Language": "en-US"},
            )
        if "price" in changes:
            offers = await cls._call(
                instance,
                "GET",
                "/sell/inventory/v1/offer",
                params={"sku": sku, "marketplace_id": cls._marketplace(instance)},
            )
            found = offers.get("offers", [])
            if not found:
                raise InvalidInputExternalError(
                    f"SKU {sku!r} has no offer to price", provider=cls.name
                )
            currency = (
                (found[0].get("pricingSummary") or {}).get("price", {}).get("currency")
            )
            await cls._call(
                instance,
                "POST",
                "/sell/inventory/v1/bulk_update_price_quantity",
                json={
                    "requests": [
                        {
                            "sku": sku,
                            "offers": [
                                {
                                    "offerId": found[0]["offerId"],
                                    "price": {
                                        "value": changes["price"],
                                        "currency": currency,
                                    },
                                }
                            ],
                        }
                    ]
                },
            )
        return {"sku": sku, "changed": sorted(changes), "provider": cls.name}

    @classmethod
    async def returns(
        cls, instance: ProviderInstanceModel, since: datetime, limit: int
    ) -> List[Dict[str, Any]]:
        found = await cls._call(
            instance,
            "GET",
            "/post-order/v2/return/search",
            scheme="IAF",
            params={
                "creation_date_range_from": since.astimezone(UTC).strftime(
                    "%Y-%m-%dT%H:%M:%S.000Z"
                ),
                "limit": min(limit, MAX_PAGE),
            },
        )
        found_returns = []
        for entry in found.get("members", []):
            refund = (entry.get("sellerTotalRefund") or {}).get(
                "estimatedRefundAmount"
            ) or {}
            found_returns.append(
                a_return(
                    entry["returnId"],
                    status=_RETURN_STATES.get(str(entry.get("state")), "other"),
                    platform_order_id=entry.get("orderId"),
                    reason=(entry.get("creationInfo") or {}).get("reason"),
                    refund=refund.get("value"),
                    currency=refund.get("currency"),
                )
            )
        return found_returns[:limit]

    @classmethod
    async def process_return(
        cls,
        instance: ProviderInstanceModel,
        return_id: str,
        action: str,
        refund: Optional[str],
    ) -> Dict[str, Any]:
        decision: Dict[str, Any] = {"decision": "DECLINE"}
        if action == "approve":
            decision = (
                {
                    "decision": "OFFER_PARTIAL_REFUND",
                    "partialRefundAmount": {"value": refund},
                }
                if refund is not None
                else {"decision": "ACCEPT"}
            )
        await cls._call(
            instance,
            "POST",
            f"/post-order/v2/return/{path_segment(return_id, 'return id')}/decide",
            scheme="IAF",
            json=decision,
        )
        return {
            "return_id": return_id,
            "status": "approved" if action == "approve" else "rejected",
            "provider": cls.name,
        }
