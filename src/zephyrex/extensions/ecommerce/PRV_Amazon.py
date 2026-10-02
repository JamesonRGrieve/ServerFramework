# SPDX-License-Identifier: AGPL-3.0-or-later
"""Amazon, through the Selling Partner API: Orders (v2026-01-01; v0 is
deprecated and kept only for shipment confirmation, which the new
version does not have) and Listings Items (2021-08-01) for products,
stock and prices.

The instance's API key is the app's LWA client id, with its
``client_secret`` and the seller's ``refresh_token`` (access tokens are
traded for it hourly), the ``seller_id``, the ``marketplace_id`` and the
SP-API ``region`` (na, eu or fe). Buyer details are restricted data and
are not requested. Amazon's API has no order cancellation and no returns
listing for sellers; those are refused.
"""

from datetime import UTC, datetime
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Tuple
from urllib.parse import quote

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.ecommerce.EXT_ECommerce import (
    AbstractEcommerceProvider,
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

TOKEN_URL = "https://api.amazon.com/auth/o2/token"
REGIONS = {
    "na": "https://sellingpartnerapi-na.amazon.com",
    "eu": "https://sellingpartnerapi-eu.amazon.com",
    "fe": "https://sellingpartnerapi-fe.amazon.com",
}
DEFAULT_MARKETPLACE = "ATVPDKIKX0DER"
ORDERS_PATH = "/orders/2026-01-01/orders"
LISTINGS_PATH = "/listings/2021-08-01/items"
MAX_ORDERS = 100
MAX_LISTINGS = 20
_STATUSES = {
    "PENDING_AVAILABILITY": "pending",
    "PENDING": "pending",
    "UNSHIPPED": "processing",
    "PARTIALLY_SHIPPED": "processing",
    "SHIPPED": "shipped",
    "CANCELLED": "cancelled",
}


def amazon_order(found: Mapping[str, Any]) -> Dict[str, Any]:
    total = (found.get("proceeds") or {}).get("grandTotal") or {}
    status = (found.get("fulfillment") or {}).get("fulfillmentStatus")
    return order(
        found["orderId"],
        status=_STATUSES.get(str(status), "other"),
        order_number=found["orderId"],
        total=total.get("amount"),
        currency=total.get("currencyCode"),
        order_date=found.get("createdTime"),
        line_items=[
            item(
                (line.get("product") or {}).get("sellerSku"),
                (line.get("product") or {}).get("title"),
                line.get("quantityOrdered"),
                (
                    ((line.get("product") or {}).get("price") or {}).get("unitPrice")
                    or {}
                ).get("amount"),
            )
            for line in found.get("orderItems", [])
        ],
    )


def amazon_product(listing: Mapping[str, Any]) -> Dict[str, Any]:
    summary = (listing.get("summaries") or [{}])[0]
    offer = (listing.get("offers") or [{}])[0]
    price = offer.get("price") or {}
    availability = (listing.get("fulfillmentAvailability") or [{}])[0]
    return product(
        listing["sku"],
        sku=listing["sku"],
        title=summary.get("itemName"),
        price=price.get("amount"),
        currency=price.get("currencyCode"),
        quantity=availability.get("quantity"),
        images=[summary["mainImage"]["link"]] if summary.get("mainImage") else [],
        is_active="BUYABLE" in (summary.get("status") or []),
    )


class PRV_Amazon_ECommerce(AbstractEcommerceProvider):
    name: ClassVar[str] = "amazon"
    friendly_name: ClassVar[str] = "Amazon"
    description: ClassVar[str] = "An Amazon seller account (SP-API)"
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting("api_key", "LWA client id", field="api_key"),
        InstanceSetting("client_secret", "LWA client secret", secret=True),
        InstanceSetting("refresh_token", "Seller refresh token", secret=True),
        InstanceSetting("seller_id", "Seller (merchant) id"),
        InstanceSetting(
            "marketplace_id",
            "Marketplace id (ATVPDKIKX0DER: US)",
            default=DEFAULT_MARKETPLACE,
        ),
        InstanceSetting("region", "SP-API region: na, eu or fe", default="na"),
    )

    @classmethod
    def _host(cls, instance: ProviderInstanceModel) -> str:
        region = str(cls.setting(instance, "region") or "na")
        if region not in REGIONS:
            raise InvalidInputExternalError(
                "Amazon region is na, eu or fe", provider=cls.name
            )
        return REGIONS[region]

    @classmethod
    def _marketplace(cls, instance: ProviderInstanceModel) -> str:
        return str(cls.setting(instance, "marketplace_id") or DEFAULT_MARKETPLACE)

    @classmethod
    async def _token(cls, instance: ProviderInstanceModel) -> str:
        client = cls.required(instance, "api_key")
        secret = cls.required(instance, "client_secret")
        refresh = cls.required(instance, "refresh_token")

        async def fetch() -> Tuple[str, float]:
            answer = await cls.http().post(
                TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh,
                    "client_id": client,
                    "client_secret": secret,
                },
            )
            return str(answer["access_token"]), float(answer.get("expires_in", 3600))

        return await cls.access_token(instance, fetch, client, secret, refresh)

    @classmethod
    async def _call(
        cls, instance: ProviderInstanceModel, method: str, path: str, **kwargs: Any
    ) -> Any:
        headers = {"x-amz-access-token": await cls._token(instance)}
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
        found = await cls._call(
            instance,
            "GET",
            ORDERS_PATH,
            params={
                "marketplaceIds": cls._marketplace(instance),
                "createdAfter": since.astimezone(UTC)
                .isoformat()
                .replace("+00:00", "Z"),
                "maxResultsPerPage": min(limit, MAX_ORDERS),
                "includedData": "FULFILLMENT,PROCEEDS",
            },
        )
        return [amazon_order(entry) for entry in found.get("orders", [])][:limit]

    @classmethod
    async def order(
        cls, instance: ProviderInstanceModel, order_id: str
    ) -> Dict[str, Any]:
        found = await cls._call(
            instance,
            "GET",
            f"{ORDERS_PATH}/{path_segment(order_id, 'order id')}",
            params={"includedData": "FULFILLMENT,PROCEEDS"},
        )
        return amazon_order(found.get("order", found))

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
                "Amazon confirms a shipment with its tracking number and carrier code",
                provider=cls.name,
            )
        found = await cls._call(
            instance, "GET", f"{ORDERS_PATH}/{path_segment(order_id, 'order id')}"
        )
        lines = found.get("order", found).get("orderItems", [])
        await cls._call(
            instance,
            "POST",
            f"/orders/v0/orders/{path_segment(order_id, 'order id')}/shipmentConfirmation",
            json={
                "marketplaceId": cls._marketplace(instance),
                "packageDetail": {
                    "packageReferenceId": "1",
                    "carrierCode": carrier,
                    "trackingNumber": tracking_number,
                    "shipDate": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    "orderItems": [
                        {
                            "orderItemId": line["orderItemId"],
                            "quantity": line["quantityOrdered"],
                        }
                        for line in lines
                    ],
                },
            },
        )
        return {"order_id": order_id, "status": "shipped", "provider": cls.name}

    @classmethod
    def _seller(cls, instance: ProviderInstanceModel) -> str:
        return path_segment(cls.required(instance, "seller_id"), "Amazon seller_id")

    @classmethod
    async def products(
        cls, instance: ProviderInstanceModel, limit: int
    ) -> List[Dict[str, Any]]:
        found = await cls._call(
            instance,
            "GET",
            f"{LISTINGS_PATH}/{cls._seller(instance)}",
            params={
                "marketplaceIds": cls._marketplace(instance),
                "includedData": "summaries,offers,fulfillmentAvailability",
                "pageSize": min(limit, MAX_LISTINGS),
            },
        )
        return [amazon_product(listing) for listing in found.get("items", [])][:limit]

    @classmethod
    async def _patch(
        cls, instance: ProviderInstanceModel, sku: str, patches: List[Dict[str, Any]]
    ) -> None:
        """Apply attribute patches to a listing (its product type read first,
        as the API requires it)."""
        path = f"{LISTINGS_PATH}/{cls._seller(instance)}/{quote(sku, safe='')}"
        params = {"marketplaceIds": cls._marketplace(instance)}
        current = await cls._call(
            instance, "GET", path, params={**params, "includedData": "summaries"}
        )
        product_type = (current.get("summaries") or [{}])[0].get("productType")
        if not product_type:
            raise InvalidInputExternalError(
                f"no listing with SKU {sku!r}", provider=cls.name
            )
        answer = await cls._call(
            instance,
            "PATCH",
            path,
            params=params,
            json={"productType": product_type, "patches": patches},
        )
        if answer.get("status") == "INVALID":
            issues = "; ".join(i.get("message", "") for i in answer.get("issues", []))
            raise InvalidInputExternalError(f"Amazon: {issues}", provider=cls.name)

    @classmethod
    async def set_inventory(
        cls, instance: ProviderInstanceModel, sku: str, quantity: int
    ) -> Dict[str, Any]:
        await cls._patch(
            instance,
            sku,
            [
                {
                    "op": "replace",
                    "path": "/attributes/fulfillment_availability",
                    "value": [
                        {"fulfillment_channel_code": "DEFAULT", "quantity": quantity}
                    ],
                }
            ],
        )
        return {"sku": sku, "quantity": quantity, "provider": cls.name}

    @classmethod
    async def update_product(
        cls, instance: ProviderInstanceModel, sku: str, changes: Dict[str, Any]
    ) -> Dict[str, Any]:
        marketplace = cls._marketplace(instance)
        patches: List[Dict[str, Any]] = []
        for ours, attribute in (
            ("title", "item_name"),
            ("description", "product_description"),
        ):
            if ours in changes:
                patches.append(
                    {
                        "op": "replace",
                        "path": f"/attributes/{attribute}",
                        "value": [
                            {
                                "value": changes[ours],
                                "language_tag": "en_US",
                                "marketplace_id": marketplace,
                            }
                        ],
                    }
                )
        if "price" in changes:
            patches.append(
                {
                    "op": "replace",
                    "path": "/attributes/purchasable_offer",
                    "value": [
                        {
                            "marketplace_id": marketplace,
                            "our_price": [
                                {"schedule": [{"value_with_tax": changes["price"]}]}
                            ],
                        }
                    ],
                }
            )
        await cls._patch(instance, sku, patches)
        return {"sku": sku, "changed": sorted(changes), "provider": cls.name}
