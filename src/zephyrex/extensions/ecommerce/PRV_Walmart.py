# SPDX-License-Identifier: AGPL-3.0-or-later
"""Walmart Marketplace (US), through its seller APIs. The instance's API
key is the client id and ``client_secret`` its secret; access tokens are
traded for them every 15 minutes.

Walmart changes a listing's title or description only through item
feeds, which are refused here; prices and stock are set directly.
Rejecting a return is not in Walmart's API; approving one refunds every
returned line.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
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
from zephyrex.extensions.ExternalErrors import (
    AuthExternalError,
    InvalidInputExternalError,
)
from zephyrex.lib.ProviderHTTPClient import path_segment
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

API_URL = "https://marketplace.walmartapis.com"
SERVICE_NAME = "Walmart Marketplace"
MAX_ORDERS = 200
MAX_ITEMS = 50
_STATUSES = {
    "Created": "pending",
    "Acknowledged": "processing",
    "Shipped": "shipped",
    "Delivered": "delivered",
    "Cancelled": "cancelled",
}


def _lines(found: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    lines: List[Mapping[str, Any]] = (found.get("orderLines") or {}).get(
        "orderLine", []
    )
    return lines


def _line_status(line: Mapping[str, Any]) -> str:
    statuses = (line.get("orderLineStatuses") or {}).get("orderLineStatus", [{}])
    return str(statuses[-1].get("status", ""))


def walmart_order(found: Mapping[str, Any]) -> Dict[str, Any]:
    lines = _lines(found)
    statuses = {_STATUSES.get(_line_status(line), "other") for line in lines}
    status = statuses.pop() if len(statuses) == 1 else "processing"
    total, currency = Decimal(0), None
    items = []
    for line in lines:
        product_charge = None
        for charge in (line.get("charges") or {}).get("charge", []):
            amount = charge.get("chargeAmount") or {}
            total += Decimal(str(amount.get("amount", 0)))
            currency = amount.get("currency") or currency
            if charge.get("chargeType") == "PRODUCT":
                product_charge = amount.get("amount")
        line_item = line.get("item") or {}
        items.append(
            item(
                line_item.get("sku"),
                line_item.get("productName"),
                (line.get("orderLineQuantity") or {}).get("amount"),
                product_charge,
            )
        )
    placed = found.get("orderDate")
    address = (found.get("shippingInfo") or {}).get("postalAddress") or {}
    return order(
        found["purchaseOrderId"],
        status=status,
        order_number=found.get("customerOrderId"),
        customer_name=address.get("name"),
        customer_email=found.get("customerEmailId"),
        total=total,
        currency=currency,
        order_date=(
            datetime.fromtimestamp(int(placed) / 1000, UTC).isoformat()
            if placed
            else None
        ),
        shipping_address=address or None,
        line_items=items,
    )


class PRV_Walmart_ECommerce(AbstractEcommerceProvider):
    name: ClassVar[str] = "walmart"
    friendly_name: ClassVar[str] = "Walmart Marketplace"
    description: ClassVar[str] = "A Walmart Marketplace seller account"
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting("api_key", "Client id", field="api_key"),
        InstanceSetting("client_secret", "Client secret", secret=True),
    )

    @classmethod
    def _headers(cls) -> Dict[str, str]:
        return {
            "WM_SVC.NAME": SERVICE_NAME,
            "WM_QOS.CORRELATION_ID": str(uuid.uuid4()),
            "Accept": "application/json",
        }

    @classmethod
    async def _token(cls, instance: ProviderInstanceModel) -> str:
        client = cls.required(instance, "api_key")
        secret = cls.required(instance, "client_secret")

        async def fetch() -> Tuple[str, float]:
            answer = await cls.http().post(
                f"{API_URL}/v3/token",
                data={"grant_type": "client_credentials"},
                auth=httpx.BasicAuth(client, secret),
                headers=cls._headers(),
            )
            return str(answer["access_token"]), float(answer.get("expires_in", 900))

        return await cls.access_token(instance, fetch, client, secret)

    @classmethod
    async def _call(
        cls, instance: ProviderInstanceModel, method: str, path: str, **kwargs: Any
    ) -> Any:
        headers = {**cls._headers(), "WM_SEC.ACCESS_TOKEN": await cls._token(instance)}
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
            "/v3/orders",
            params={
                "createdStartDate": since.astimezone(UTC).isoformat(),
                "limit": min(limit, MAX_ORDERS),
            },
        )
        entries = ((found.get("list") or {}).get("elements") or {}).get("order", [])
        return [walmart_order(entry) for entry in entries][:limit]

    @classmethod
    async def _raw_order(
        cls, instance: ProviderInstanceModel, order_id: str
    ) -> Mapping[str, Any]:
        found = await cls._call(
            instance, "GET", f"/v3/orders/{path_segment(order_id, 'order id')}"
        )
        raw: Mapping[str, Any] = found.get("order", found)
        return raw

    @classmethod
    async def order(
        cls, instance: ProviderInstanceModel, order_id: str
    ) -> Dict[str, Any]:
        return walmart_order(await cls._raw_order(instance, order_id))

    @classmethod
    def _line_update(
        cls, line: Mapping[str, Any], status: Dict[str, Any]
    ) -> Dict[str, Any]:
        return {
            "lineNumber": line["lineNumber"],
            "orderLineStatuses": {
                "orderLineStatus": [
                    {
                        **status,
                        "statusQuantity": {
                            "unitOfMeasurement": "EACH",
                            "amount": str(
                                (line.get("orderLineQuantity") or {}).get("amount", "1")
                            ),
                        },
                    }
                ]
            },
        }

    @classmethod
    async def cancel_order(
        cls, instance: ProviderInstanceModel, order_id: str, reason: str
    ) -> Dict[str, Any]:
        found = await cls._raw_order(instance, order_id)
        await cls._call(
            instance,
            "POST",
            f"/v3/orders/{path_segment(order_id, 'order id')}/cancel",
            json={
                "orderCancellation": {
                    "orderLines": {
                        "orderLine": [
                            cls._line_update(
                                line,
                                {
                                    "status": "Cancelled",
                                    "cancellationReason": "CANCEL_BY_SELLER",
                                },
                            )
                            for line in _lines(found)
                        ]
                    }
                }
            },
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
                "Walmart records a shipment with its tracking number and carrier",
                provider=cls.name,
            )
        found = await cls._raw_order(instance, order_id)
        shipped = int(datetime.now(UTC).timestamp() * 1000)
        await cls._call(
            instance,
            "POST",
            f"/v3/orders/{path_segment(order_id, 'order id')}/shipping",
            json={
                "orderShipment": {
                    "orderLines": {
                        "orderLine": [
                            cls._line_update(
                                line,
                                {
                                    "status": "Shipped",
                                    "trackingInfo": {
                                        "shipDateTime": shipped,
                                        "carrierName": {"carrier": carrier},
                                        "methodCode": "Standard",
                                        "trackingNumber": tracking_number,
                                    },
                                },
                            )
                            for line in _lines(found)
                        ]
                    }
                }
            },
        )
        return {"order_id": order_id, "status": "shipped", "provider": cls.name}

    @classmethod
    async def products(
        cls, instance: ProviderInstanceModel, limit: int
    ) -> List[Dict[str, Any]]:
        found = await cls._call(
            instance, "GET", "/v3/items", params={"limit": min(limit, MAX_ITEMS)}
        )
        return [
            product(
                entry["sku"],
                sku=entry["sku"],
                title=entry.get("productName"),
                price=(entry.get("price") or {}).get("amount"),
                currency=(entry.get("price") or {}).get("currency"),
                is_active=entry.get("publishedStatus") == "PUBLISHED",
            )
            for entry in found.get("ItemResponse", [])
        ][:limit]

    @classmethod
    async def set_inventory(
        cls, instance: ProviderInstanceModel, sku: str, quantity: int
    ) -> Dict[str, Any]:
        await cls._call(
            instance,
            "PUT",
            "/v3/inventory",
            params={"sku": sku},
            json={"sku": sku, "quantity": {"unit": "EACH", "amount": quantity}},
        )
        return {"sku": sku, "quantity": quantity, "provider": cls.name}

    @classmethod
    async def update_product(
        cls, instance: ProviderInstanceModel, sku: str, changes: Dict[str, Any]
    ) -> Dict[str, Any]:
        if set(changes) - {"price"}:
            raise cls.cannot(
                "change a title or description directly",
                "Walmart takes those as item feeds",
            )
        await cls._call(
            instance,
            "PUT",
            "/v3/price",
            json={
                "sku": sku,
                "pricing": [
                    {
                        "currentPriceType": "BASE",
                        "currentPrice": {"currency": "USD", "amount": changes["price"]},
                    }
                ],
            },
        )
        return {"sku": sku, "changed": ["price"], "provider": cls.name}

    @classmethod
    def _return(cls, entry: Mapping[str, Any]) -> Dict[str, Any]:
        lines = entry.get("returnOrderLines", [])
        refunded = all(
            str(line.get("currentRefundStatus", ""))
            .upper()
            .startswith("REFUND_COMPLETE")
            for line in lines
        ) and bool(lines)
        amount = entry.get("totalRefundAmount") or {}
        return a_return(
            entry["returnOrderId"],
            status="refunded" if refunded else "requested",
            platform_order_id=entry.get("customerOrderId"),
            reason=(lines[0].get("returnReason") if lines else None),
            refund=amount.get("currencyAmount"),
            currency=amount.get("currencyUnit"),
            line_items=[
                item(
                    (line.get("item") or {}).get("sku"),
                    (line.get("item") or {}).get("productName"),
                    (line.get("quantity") or {}).get("measurementValue"),
                    None,
                )
                for line in lines
            ],
        )

    @classmethod
    async def returns(
        cls, instance: ProviderInstanceModel, since: datetime, limit: int
    ) -> List[Dict[str, Any]]:
        found = await cls._call(
            instance,
            "GET",
            "/v3/returns",
            params={
                "returnCreationStartDate": since.astimezone(UTC).isoformat(),
                "limit": min(limit, MAX_ORDERS),
            },
        )
        return [cls._return(entry) for entry in found.get("returnOrders", [])][:limit]

    @classmethod
    async def process_return(
        cls,
        instance: ProviderInstanceModel,
        return_id: str,
        action: str,
        refund: Optional[str],
    ) -> Dict[str, Any]:
        if action == "reject":
            raise cls.cannot("reject a return")
        if refund is not None:
            raise cls.cannot("refund a chosen amount", "Walmart refunds returned lines")
        found = await cls._call(
            instance, "GET", "/v3/returns", params={"returnOrderId": return_id}
        )
        entries = found.get("returnOrders", [])
        if not entries:
            raise InvalidInputExternalError(f"no return {return_id}", provider=cls.name)
        entry = entries[0]
        await cls._call(
            instance,
            "POST",
            f"/v3/returns/{path_segment(return_id, 'return id')}/refund",
            json={
                "customerOrderId": entry["customerOrderId"],
                "refundLines": [
                    {
                        "returnOrderLineNumber": line["returnOrderLineNumber"],
                        "quantity": line.get(
                            "quantity", {"unitOfMeasure": "EACH", "measurementValue": 1}
                        ),
                    }
                    for line in entry.get("returnOrderLines", [])
                ],
            },
        )
        return {"return_id": return_id, "status": "refunded", "provider": cls.name}
