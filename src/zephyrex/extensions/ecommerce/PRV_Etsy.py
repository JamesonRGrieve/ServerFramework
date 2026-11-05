# SPDX-License-Identifier: AGPL-3.0-or-later
"""Etsy, through the Open API v3. The instance's API key is the app's
keystring, with its ``shared_secret``, an OAuth ``refresh_token`` (valid
90 days; access tokens are traded for it hourly) and the ``shop_id``.

Etsy's API has no order cancellation and no returns (both are handled on
Etsy); those are refused. Marking an order shipped takes its tracking.
"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Tuple

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

API_URL = "https://openapi.etsy.com/v3/application"
TOKEN_URL = "https://api.etsy.com/v3/public/oauth/token"
MAX_PAGE = 100
_STATUSES = {
    "paid": "processing",
    "completed": "shipped",
    "open": "pending",
    "payment processing": "pending",
    "canceled": "cancelled",
    "fully refunded": "refunded",
    "partially refunded": "processing",
}


def etsy_money(money: Optional[Mapping[str, Any]]) -> Optional[str]:
    """Etsy's ``{amount, divisor}`` as a decimal string."""
    if not money or not money.get("divisor"):
        return None
    return format(
        (Decimal(money["amount"]) / Decimal(money["divisor"])).normalize(), "f"
    )


def etsy_order(receipt: Mapping[str, Any]) -> Dict[str, Any]:
    status = _STATUSES.get(str(receipt.get("status", "")).lower(), "other")
    if status == "processing" and receipt.get("is_shipped"):
        status = "shipped"
    created = receipt.get("create_timestamp")
    total = receipt.get("grandtotal") or {}
    return order(
        receipt["receipt_id"],
        status=status,
        order_number=receipt.get("receipt_id"),
        customer_name=receipt.get("name"),
        customer_email=receipt.get("buyer_email"),
        total=etsy_money(total),
        currency=total.get("currency_code"),
        order_date=(
            datetime.fromtimestamp(created, UTC).isoformat() if created else None
        ),
        shipping_address=(
            {"formatted": receipt["formatted_address"]}
            if receipt.get("formatted_address")
            else None
        ),
        line_items=[
            item(
                line.get("sku"),
                line.get("title"),
                line.get("quantity"),
                etsy_money(line.get("price")),
            )
            for line in receipt.get("transactions", [])
        ],
    )


class PRV_Etsy_ECommerce(AbstractEcommerceProvider):
    name: ClassVar[str] = "etsy"
    friendly_name: ClassVar[str] = "Etsy"
    description: ClassVar[str] = "An Etsy shop"
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting("api_key", "App keystring", field="api_key"),
        InstanceSetting("shared_secret", "App shared secret", secret=True),
        InstanceSetting("refresh_token", "OAuth refresh token", secret=True),
        InstanceSetting("shop_id", "Shop id"),
    )

    @classmethod
    def _shop(cls, instance: ProviderInstanceModel) -> str:
        return path_segment(cls.required(instance, "shop_id"), "Etsy shop_id")

    @classmethod
    def _api_key(cls, instance: ProviderInstanceModel) -> str:
        return f"{cls.required(instance, 'api_key')}:{cls.required(instance, 'shared_secret')}"

    @classmethod
    async def _token(cls, instance: ProviderInstanceModel) -> str:
        keystring = cls.required(instance, "api_key")
        refresh = cls.required(instance, "refresh_token")

        async def fetch() -> Tuple[str, float]:
            answer = await cls.http().post(
                TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "client_id": keystring,
                    "refresh_token": refresh,
                },
                headers={"x-api-key": cls._api_key(instance)},
            )
            return str(answer["access_token"]), float(answer.get("expires_in", 3600))

        return await cls.access_token(instance, fetch, cls._api_key(instance), refresh)

    @classmethod
    async def _call(
        cls, instance: ProviderInstanceModel, method: str, path: str, **kwargs: Any
    ) -> Any:
        headers = {
            "x-api-key": cls._api_key(instance),
            "Authorization": f"Bearer {await cls._token(instance)}",
        }
        try:
            return await cls.http().request(
                method, f"{API_URL}{path}", headers=headers, **kwargs
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
            f"/shops/{cls._shop(instance)}/receipts",
            params={
                "min_created": int(since.timestamp()),
                "limit": min(limit, MAX_PAGE),
                "sort_on": "created",
                "sort_order": "desc",
            },
        )
        return [etsy_order(receipt) for receipt in found.get("results", [])][:limit]

    @classmethod
    async def order(
        cls, instance: ProviderInstanceModel, order_id: str
    ) -> Dict[str, Any]:
        found = await cls._call(
            instance,
            "GET",
            f"/shops/{cls._shop(instance)}/receipts/{path_segment(order_id, 'receipt id')}",
        )
        return etsy_order(found)

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
                "Etsy marks an order shipped with its tracking number and carrier",
                provider=cls.name,
            )
        await cls._call(
            instance,
            "POST",
            f"/shops/{cls._shop(instance)}/receipts/"
            f"{path_segment(order_id, 'receipt id')}/tracking",
            json={"tracking_code": tracking_number, "carrier_name": carrier},
        )
        return {"order_id": order_id, "status": "shipped", "provider": cls.name}

    @classmethod
    async def _listings(
        cls, instance: ProviderInstanceModel, limit: int
    ) -> List[Dict[str, Any]]:
        found = await cls._call(
            instance,
            "GET",
            f"/shops/{cls._shop(instance)}/listings",
            params={
                "state": "active",
                "limit": min(limit, MAX_PAGE),
                "sort_on": "updated",
                "includes": "Images",
            },
        )
        listings: List[Dict[str, Any]] = found.get("results", [])
        return listings

    @classmethod
    async def products(
        cls, instance: ProviderInstanceModel, limit: int
    ) -> List[Dict[str, Any]]:
        return [
            product(
                listing["listing_id"],
                sku=(listing.get("skus") or [None])[0],
                title=listing.get("title"),
                description=listing.get("description"),
                price=etsy_money(listing.get("price")),
                currency=(listing.get("price") or {}).get("currency_code"),
                quantity=listing.get("quantity"),
                images=[
                    i["url_570xN"]
                    for i in listing.get("images", [])
                    if i.get("url_570xN")
                ],
                is_active=listing.get("state") == "active",
                url=listing.get("url"),
            )
            for listing in await cls._listings(instance, MAX_PAGE)
        ][:limit]

    @classmethod
    async def _listing_with(cls, instance: ProviderInstanceModel, sku: str) -> int:
        matches = [
            listing["listing_id"]
            for listing in await cls._listings(instance, MAX_PAGE)
            if sku in (listing.get("skus") or [])
        ]
        if len(matches) != 1:
            raise InvalidInputExternalError(
                f"{len(matches)} active listings have SKU {sku!r}", provider=cls.name
            )
        return int(matches[0])

    @classmethod
    async def _rewrite_inventory(
        cls,
        instance: ProviderInstanceModel,
        sku: str,
        quantity: Optional[int],
        price: Optional[str],
    ) -> None:
        """Etsy replaces a listing's whole inventory: read it, change the
        SKU's offerings, write it back."""
        listing = await cls._listing_with(instance, sku)
        current = await cls._call(instance, "GET", f"/listings/{listing}/inventory")
        products = []
        for entry in current.get("products", []):
            offerings = []
            for offering in entry.get("offerings", []):
                offerings.append(
                    {
                        "price": float(
                            price
                            if price is not None and entry.get("sku") == sku
                            else etsy_money(offering.get("price")) or 0
                        ),
                        "quantity": (
                            quantity
                            if quantity is not None and entry.get("sku") == sku
                            else offering.get("quantity")
                        ),
                        "is_enabled": offering.get("is_enabled", True),
                    }
                )
            products.append(
                {
                    "sku": entry.get("sku"),
                    "property_values": entry.get("property_values", []),
                    "offerings": offerings,
                }
            )
        await cls._call(
            instance,
            "PUT",
            f"/listings/{listing}/inventory",
            json={
                "products": products,
                "price_on_property": current.get("price_on_property", []),
                "quantity_on_property": current.get("quantity_on_property", []),
                "sku_on_property": current.get("sku_on_property", []),
            },
        )

    @classmethod
    async def set_inventory(
        cls, instance: ProviderInstanceModel, sku: str, quantity: int
    ) -> Dict[str, Any]:
        await cls._rewrite_inventory(instance, sku, quantity, None)
        return {"sku": sku, "quantity": quantity, "provider": cls.name}

    @classmethod
    async def update_product(
        cls, instance: ProviderInstanceModel, sku: str, changes: Dict[str, Any]
    ) -> Dict[str, Any]:
        details = {
            key: changes[key] for key in ("title", "description") if key in changes
        }
        if details:
            listing = await cls._listing_with(instance, sku)
            await cls._call(
                instance,
                "PATCH",
                f"/shops/{cls._shop(instance)}/listings/{listing}",
                data=details,
            )
        if "price" in changes:
            await cls._rewrite_inventory(instance, sku, None, changes["price"])
        return {"sku": sku, "changed": sorted(changes), "provider": cls.name}
