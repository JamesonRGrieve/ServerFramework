# SPDX-License-Identifier: AGPL-3.0-or-later
"""WooCommerce, through its REST API v3 at the store's ``site_url``. The
instance's API key is a REST consumer key and ``consumer_secret`` its
secret (WooCommerce → Settings → Advanced → REST API), sent as HTTP Basic
over HTTPS.

WooCommerce has refunds rather than returns: a "return" here is a refund
on an order (its id ``<order id>:<refund id>``), and approving one is
refunding an order (``return_id`` the order's id) through its payment
gateway. Shipping has no tracking field in WooCommerce core; the
tracking is added to the order as a customer note.
"""

from datetime import datetime
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Tuple

import httpx

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.ecommerce.EXT_ECommerce import (
    AbstractEcommerceProvider,
    a_return,
    item,
    order,
    product,
)
from zephyrex.extensions.ExternalErrors import InvalidInputExternalError
from zephyrex.lib.ProviderHTTPClient import path_segment
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

API_PATH = "/wp-json/wc/v3"
REFUNDED_ORDERS_SCANNED = 50
_STATUSES = {
    "pending": "pending",
    "on-hold": "pending",
    "processing": "processing",
    "completed": "shipped",
    "cancelled": "cancelled",
    "refunded": "refunded",
    "failed": "cancelled",
}


def woo_order(found: Mapping[str, Any]) -> Dict[str, Any]:
    billing = found.get("billing") or {}
    name = " ".join(
        part for part in (billing.get("first_name"), billing.get("last_name")) if part
    )
    return order(
        found["id"],
        status=_STATUSES.get(str(found.get("status")), "other"),
        order_number=found.get("number"),
        customer_name=name or None,
        customer_email=billing.get("email"),
        total=found.get("total"),
        currency=found.get("currency"),
        # WooCommerce writes its GMT times without an offset.
        order_date=(
            f"{found['date_created_gmt']}+00:00"
            if found.get("date_created_gmt")
            else None
        ),
        shipping_address=found.get("shipping"),
        line_items=[
            item(
                line.get("sku"),
                line.get("name"),
                line.get("quantity"),
                line.get("price"),
            )
            for line in found.get("line_items", [])
        ],
    )


class PRV_WooCommerce_ECommerce(AbstractEcommerceProvider):
    name: ClassVar[str] = "woocommerce"
    friendly_name: ClassVar[str] = "WooCommerce"
    description: ClassVar[str] = "A WooCommerce store"
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting("api_key", "REST consumer key", secret=True, field="api_key"),
        InstanceSetting("consumer_secret", "REST consumer secret", secret=True),
        InstanceSetting("site_url", "The store's address (https://shop.example.com)"),
    )

    @classmethod
    async def _call(
        cls, instance: ProviderInstanceModel, method: str, path: str, **kwargs: Any
    ) -> Any:
        site = cls.required(instance, "site_url").rstrip("/")
        if not site.startswith("https://"):
            raise InvalidInputExternalError(
                "WooCommerce keys travel only over https", provider=cls.name
            )
        return await cls.http().request(
            method,
            f"{site}{API_PATH}{path}",
            auth=httpx.BasicAuth(
                cls.required(instance, "api_key"),
                cls.required(instance, "consumer_secret"),
            ),
            **kwargs,
        )

    @classmethod
    async def orders(
        cls, instance: ProviderInstanceModel, since: datetime, limit: int
    ) -> List[Dict[str, Any]]:
        found = await cls._call(
            instance,
            "GET",
            "/orders",
            params={
                "after": since.isoformat(),
                "per_page": min(limit, 100),
                "orderby": "date",
                "order": "desc",
            },
        )
        return [woo_order(entry) for entry in found][:limit]

    @classmethod
    async def order(
        cls, instance: ProviderInstanceModel, order_id: str
    ) -> Dict[str, Any]:
        found = await cls._call(
            instance, "GET", f"/orders/{path_segment(order_id, 'order id')}"
        )
        return woo_order(found)

    @classmethod
    async def _note(
        cls, instance: ProviderInstanceModel, order_id: str, note: str
    ) -> None:
        await cls._call(
            instance,
            "POST",
            f"/orders/{path_segment(order_id, 'order id')}/notes",
            json={"note": note, "customer_note": True},
        )

    @classmethod
    async def cancel_order(
        cls, instance: ProviderInstanceModel, order_id: str, reason: str
    ) -> Dict[str, Any]:
        found = await cls._call(
            instance,
            "PUT",
            f"/orders/{path_segment(order_id, 'order id')}",
            json={"status": "cancelled"},
        )
        if reason:
            await cls._note(instance, order_id, f"Cancelled: {reason}")
        return woo_order(found)

    @classmethod
    async def fulfill_order(
        cls,
        instance: ProviderInstanceModel,
        order_id: str,
        tracking_number: Optional[str],
        carrier: Optional[str],
    ) -> Dict[str, Any]:
        await cls._call(
            instance,
            "PUT",
            f"/orders/{path_segment(order_id, 'order id')}",
            json={"status": "completed"},
        )
        if tracking_number:
            await cls._note(
                instance,
                order_id,
                f"Shipped{f' with {carrier}' if carrier else ''}: tracking {tracking_number}",
            )
        return {"order_id": order_id, "status": "shipped", "provider": cls.name}

    @classmethod
    async def products(
        cls, instance: ProviderInstanceModel, limit: int
    ) -> List[Dict[str, Any]]:
        currency = await cls._call(instance, "GET", "/data/currencies/current")
        found = await cls._call(
            instance,
            "GET",
            "/products",
            params={
                "per_page": min(limit, 100),
                "orderby": "modified",
                "order": "desc",
            },
        )
        return [
            product(
                entry["id"],
                sku=entry.get("sku"),
                title=entry.get("name"),
                description=entry.get("description"),
                price=entry.get("price"),
                currency=currency.get("code"),
                quantity=entry.get("stock_quantity"),
                images=[
                    image["src"]
                    for image in entry.get("images", [])
                    if image.get("src")
                ],
                is_active=entry.get("status") == "publish",
                url=entry.get("permalink"),
            )
            for entry in found
        ][:limit]

    @classmethod
    async def _product_id(cls, instance: ProviderInstanceModel, sku: str) -> int:
        found = await cls._call(instance, "GET", "/products", params={"sku": sku})
        if len(found) != 1:
            raise InvalidInputExternalError(
                f"{len(found)} products have SKU {sku!r}", provider=cls.name
            )
        return int(found[0]["id"])

    @classmethod
    async def set_inventory(
        cls, instance: ProviderInstanceModel, sku: str, quantity: int
    ) -> Dict[str, Any]:
        product_id = await cls._product_id(instance, sku)
        await cls._call(
            instance,
            "PUT",
            f"/products/{product_id}",
            json={"manage_stock": True, "stock_quantity": quantity},
        )
        return {"sku": sku, "quantity": quantity, "provider": cls.name}

    @classmethod
    async def update_product(
        cls, instance: ProviderInstanceModel, sku: str, changes: Dict[str, Any]
    ) -> Dict[str, Any]:
        product_id = await cls._product_id(instance, sku)
        body = {
            woo: changes[ours]
            for ours, woo in (
                ("title", "name"),
                ("description", "description"),
                ("price", "regular_price"),
            )
            if ours in changes
        }
        await cls._call(instance, "PUT", f"/products/{product_id}", json=body)
        return {"sku": sku, "changed": sorted(changes), "provider": cls.name}

    @classmethod
    async def returns(
        cls, instance: ProviderInstanceModel, since: datetime, limit: int
    ) -> List[Dict[str, Any]]:
        refunded = await cls._call(
            instance,
            "GET",
            "/orders",
            params={
                "status": "refunded,completed,processing",
                "modified_after": since.isoformat(),
                "per_page": REFUNDED_ORDERS_SCANNED,
            },
        )
        found: List[Dict[str, Any]] = []
        for entry in refunded:
            if not entry.get("refunds"):
                continue
            for refund in await cls._call(
                instance, "GET", f"/orders/{int(entry['id'])}/refunds"
            ):
                found.append(
                    a_return(
                        f"{entry['id']}:{refund['id']}",
                        status="refunded",
                        platform_order_id=entry["id"],
                        reason=refund.get("reason"),
                        refund=str(refund.get("amount", "")).lstrip("-"),
                        currency=entry.get("currency"),
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
        if action == "reject":
            raise cls.cannot("reject a return", "WooCommerce has refunds, not returns")
        if refund is None:
            raise InvalidInputExternalError(
                "approving a WooCommerce return refunds an order: give the refund",
                provider=cls.name,
            )
        created = await cls._call(
            instance,
            "POST",
            f"/orders/{path_segment(return_id, 'order id')}/refunds",
            json={"amount": refund, "api_refund": True},
        )
        return {
            "return_id": f"{return_id}:{created['id']}",
            "status": "refunded",
            "refund": refund,
            "provider": cls.name,
        }
