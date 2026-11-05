# SPDX-License-Identifier: AGPL-3.0-or-later
"""AliExpress, as a seller, through the Open Platform's Seller Solution
APIs (the ``/sync`` gateway, requests signed with HMAC-SHA256). The
instance's API key is the app key, with its ``app_secret`` and the
seller's ``access_token`` from authorizing the app (AliExpress rotates
refresh tokens, so renewal is re-authorizing the app).

A SKU here is ``<product id>:<sku code>``: AliExpress addresses stock and
prices by both. The order list's times are US Pacific, as the API takes
them. Cancelling orders and returns (disputes) are not in the seller
API; those are refused, as are title and description changes.
"""

import hashlib
import hmac
import json
import time
from datetime import datetime
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Tuple
from zoneinfo import ZoneInfo

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
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

GATEWAY = "https://api-sg.aliexpress.com/sync"
PACIFIC = ZoneInfo("America/Los_Angeles")
MAX_PAGE = 50
_STATUSES = {
    "PLACE_ORDER_SUCCESS": "pending",
    "RISK_CONTROL": "pending",
    "WAIT_SELLER_EXAMINE_MONEY": "pending",
    "WAIT_SELLER_SEND_GOODS": "processing",
    "SELLER_PART_SEND_GOODS": "processing",
    "WAIT_BUYER_ACCEPT_GOODS": "shipped",
    "FUND_PROCESSING": "delivered",
    "FINISH": "delivered",
    "IN_CANCEL": "cancelled",
}
# Error codes (lower-cased) that refuse the credentials, not the request:
# InvalidAppKey, IncompleteSignature (a wrong secret), IllegalAccessToken, …
_AUTH_CODES = ("appkey", "signature", "token", "session", "permission", "auth")


def sign(parameters: Mapping[str, str], secret: str, path: str = "") -> str:
    """The IOP signature: HMAC-SHA256 of the sorted key-value pairs (after
    the API path, for ``/rest`` calls), in upper-case hex."""
    text = path + "".join(f"{key}{parameters[key]}" for key in sorted(parameters))
    return hmac.new(secret.encode(), text.encode(), hashlib.sha256).hexdigest().upper()


def pacific(moment: datetime) -> str:
    return moment.astimezone(PACIFIC).strftime("%Y-%m-%d %H:%M:%S")


def sku_parts(sku: str) -> Tuple[int, str]:
    product_id, separator, sku_code = sku.partition(":")
    if not separator or not product_id.isdigit() or not sku_code:
        raise InvalidInputExternalError(
            "an AliExpress SKU is <product id>:<sku code>", provider="aliexpress"
        )
    return int(product_id), sku_code


def _amount(money: Optional[Mapping[str, Any]]) -> Tuple[Any, Any]:
    money = money or {}
    return money.get("amount"), money.get("currency_code")


def aliexpress_order(found: Mapping[str, Any]) -> Dict[str, Any]:
    """An order from the list (``order_dto``) or the detail (``data``)."""
    total, currency = _amount(found.get("pay_amount") or found.get("order_amount"))
    lines = (
        ((found.get("product_list") or {}).get("order_product_dto"))
        or ((found.get("child_order_list") or {}).get("global_aeop_tp_child_order_dto"))
        or []
    )
    return order(
        found.get("order_id") or found.get("id"),
        status=_STATUSES.get(str(found.get("order_status")), "other"),
        order_number=found.get("order_id") or found.get("id"),
        total=total,
        currency=currency,
        order_date=found.get("gmt_create"),
        line_items=[
            item(
                line.get("sku_code"),
                line.get("product_name"),
                line.get("product_count"),
                _amount(line.get("product_unit_price") or line.get("product_price"))[0],
            )
            for line in lines
        ],
    )


class PRV_Aliexpress_ECommerce(AbstractEcommerceProvider):
    name: ClassVar[str] = "aliexpress"
    friendly_name: ClassVar[str] = "AliExpress"
    description: ClassVar[str] = "An AliExpress seller account"
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting("api_key", "App key", field="api_key"),
        InstanceSetting("app_secret", "App secret", secret=True),
        InstanceSetting("access_token", "Seller access token", secret=True),
    )

    @classmethod
    async def call(
        cls, instance: ProviderInstanceModel, method: str, **parameters: Any
    ) -> Dict[str, Any]:
        """A Seller Solution API method's ``result``."""
        values = {
            **{key: str(value) for key, value in parameters.items()},
            "method": method,
            "app_key": cls.required(instance, "api_key"),
            "session": cls.required(instance, "access_token"),
            "timestamp": str(int(time.time() * 1000)),
            "sign_method": "sha256",
            "format": "json",
            "simplify": "false",
        }
        values["sign"] = sign(values, cls.required(instance, "app_secret"))
        answer = await cls.http().post(
            GATEWAY, data=values, headers={"Accept": "application/json"}
        )
        error = answer.get("error_response")
        if error:
            code = str(error.get("code", ""))
            detail = f"AliExpress: {code} {error.get('msg', '')}".strip()
            if any(word in code.lower() for word in _AUTH_CODES):
                raise AuthExternalError(detail, provider=cls.name)
            raise InvalidInputExternalError(detail, provider=cls.name)
        result: Dict[str, Any] = (
            answer.get(method.replace(".", "_") + "_response") or {}
        ).get("result") or {}
        return result

    @classmethod
    async def orders(
        cls, instance: ProviderInstanceModel, since: datetime, limit: int
    ) -> List[Dict[str, Any]]:
        result = await cls.call(
            instance,
            "aliexpress.solution.order.get",
            param0=json.dumps(
                {
                    "current_page": 1,
                    "page_size": min(limit, MAX_PAGE),
                    "create_date_start": pacific(since),
                }
            ),
        )
        entries = (result.get("target_list") or {}).get("order_dto") or []
        return [aliexpress_order(entry) for entry in entries][:limit]

    @classmethod
    async def order(
        cls, instance: ProviderInstanceModel, order_id: str
    ) -> Dict[str, Any]:
        if not order_id.isdigit():
            raise InvalidInputExternalError(
                f"{order_id!r} is not an AliExpress order id", provider=cls.name
            )
        result = await cls.call(
            instance,
            "aliexpress.solution.order.info.get",
            param1=json.dumps({"order_id": int(order_id), "ext_info_bit_flag": 0}),
        )
        if not result.get("data"):
            raise InvalidInputExternalError(
                f"no order {order_id}", provider=cls.name, upstream_status=404
            )
        return aliexpress_order(result["data"])

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
                "AliExpress ships an order with its tracking number and logistics "
                "service name",
                provider=cls.name,
            )
        await cls.call(
            instance,
            "aliexpress.solution.order.fulfill",
            out_ref=order_id,
            send_type="all",
            logistics_no=tracking_number,
            service_name=carrier,
        )
        return {"order_id": order_id, "status": "shipped", "provider": cls.name}

    @classmethod
    async def products(
        cls, instance: ProviderInstanceModel, limit: int
    ) -> List[Dict[str, Any]]:
        result = await cls.call(
            instance,
            "aliexpress.solution.product.list.get",
            aeop_a_e_product_list_query=json.dumps(
                {
                    "current_page": 1,
                    "page_size": min(limit, MAX_PAGE),
                    "product_status_type": "onSelling",
                }
            ),
        )
        entries = (result.get("aeop_a_e_product_display_d_t_o_list") or {}).get(
            "item_display_dto"
        ) or []
        return [
            product(
                entry["product_id"],
                sku=None,
                title=entry.get("subject"),
                price=entry.get("product_min_price"),
                currency=entry.get("currency_code"),
                images=[
                    url for url in str(entry.get("image_u_r_ls", "")).split(";") if url
                ],
            )
            for entry in entries
        ][:limit]

    @classmethod
    async def _update_skus(
        cls,
        instance: ProviderInstanceModel,
        method: str,
        sku: str,
        field: str,
        value: Any,
    ) -> None:
        product_id, sku_code = sku_parts(sku)
        result = await cls.call(
            instance,
            method,
            mutiple_product_update_list=json.dumps(
                [
                    {
                        "product_id": product_id,
                        "multiple_sku_update_list": [
                            {"sku_code": sku_code, field: value}
                        ],
                    }
                ]
            ),
        )
        if result.get("update_success") is not True:
            raise InvalidInputExternalError(
                f"AliExpress did not apply the update: "
                f"{result.get('update_error_message') or result.get('update_error_code')}",
                provider=cls.name,
            )

    @classmethod
    async def set_inventory(
        cls, instance: ProviderInstanceModel, sku: str, quantity: int
    ) -> Dict[str, Any]:
        await cls._update_skus(
            instance,
            "aliexpress.solution.batch.product.inventory.update",
            sku,
            "inventory",
            quantity,
        )
        return {"sku": sku, "quantity": quantity, "provider": cls.name}

    @classmethod
    async def update_product(
        cls, instance: ProviderInstanceModel, sku: str, changes: Dict[str, Any]
    ) -> Dict[str, Any]:
        if set(changes) - {"price"}:
            raise cls.cannot("change a title or description here")
        await cls._update_skus(
            instance,
            "aliexpress.solution.batch.product.price.update",
            sku,
            "price",
            changes["price"],
        )
        return {"sku": sku, "changed": ["price"], "provider": cls.name}
