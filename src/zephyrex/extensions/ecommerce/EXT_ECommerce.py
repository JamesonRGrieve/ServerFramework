# SPDX-License-Identifier: AGPL-3.0-or-later
"""E-commerce: orders, products, inventory and returns across Shopify,
WooCommerce, Etsy, eBay, Amazon (SP-API), Walmart Marketplace and
AliExpress.

Each provider instance is one store; its credentials are write-only
settings. Every ability names its store and acts on it alone. What a
store returns is mirrored locally (``BLL_ECommerce``: orders, products,
returns, owned like the store), so reports and searches run across
stores without asking each one; ``sync_store`` (or the sync route)
refreshes a store's mirror.

Stores answer in shared shapes, money as decimal strings:

- order: ``platform_order_id, order_number, status, customer_name,
  customer_email, total, currency, order_date, shipping_address,
  line_items`` (``sku, title, quantity, price``)
- product: ``platform_product_id, sku, title, description, price,
  currency, quantity, images, is_active, url``
- return: ``platform_return_id, platform_order_id, status, reason,
  refund, currency, line_items``

An order's status is ``pending``, ``processing``, ``shipped``,
``delivered``, ``cancelled``, ``refunded`` or ``other``; a return's
``requested``, ``approved``, ``rejected``, ``refunded``, ``closed`` or
``other``. What a platform's API cannot do is refused with the reason.
"""

from abc import abstractmethod
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, ClassVar, Dict, List, Optional, Set, Tuple

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractProviderInstance,
    AbstractStaticExtension,
    AbstractStaticProvider,
    ability,
)
from zephyrex.extensions.ExternalErrors import (
    AuthExternalError,
    InvalidInputExternalError,
    PermanentExternalError,
    TransientExternalError,
)
from zephyrex.lib.Dependencies import Dependencies
from zephyrex.lib.TokenCache import Fetch, TokenCache, fingerprint
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

_TOKENS = TokenCache()

ECOMMERCE_REQUEST_TIMEOUT_SECONDS = 30.0
DEFAULT_DAYS = 30
MAX_DAYS = 365
DEFAULT_LIMIT = 50
MAX_LIMIT = 250
ORDER_STATUSES = (
    "pending",
    "processing",
    "shipped",
    "delivered",
    "cancelled",
    "refunded",
    "other",
)
RETURN_STATUSES = ("requested", "approved", "rejected", "refunded", "closed", "other")
RETURN_ACTIONS = ("approve", "reject")
PRODUCT_CHANGES = ("title", "description", "price")
MAX_QUANTITY = 1_000_000


def money(value: Any) -> Optional[str]:
    """A platform's amount (number or text) as a plain decimal string."""
    if value is None or value == "":
        return None
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return format(amount.normalize(), "f") if amount.is_finite() else None


def checked_money(value: str, what: str) -> str:
    amount = money(value)
    if amount is None or Decimal(amount) < 0:
        raise InvalidInputExternalError(f"{what} must be a non-negative amount")
    return amount


def window(days: int) -> datetime:
    if not 1 <= days <= MAX_DAYS:
        raise InvalidInputExternalError(f"days must be 1-{MAX_DAYS}")
    return datetime.now(UTC) - timedelta(days=days)


def checked_limit(limit: int) -> int:
    if not 1 <= limit <= MAX_LIMIT:
        raise InvalidInputExternalError(f"limit must be 1-{MAX_LIMIT}")
    return limit


def checked_quantity(quantity: int) -> int:
    if not 0 <= quantity <= MAX_QUANTITY:
        raise InvalidInputExternalError(f"quantity must be 0-{MAX_QUANTITY}")
    return quantity


def checked_changes(changes: Dict[str, Any]) -> Dict[str, Any]:
    unknown = set(changes) - set(PRODUCT_CHANGES)
    if unknown or not changes:
        raise InvalidInputExternalError(
            f"a product change sets any of {', '.join(PRODUCT_CHANGES)}"
        )
    if "price" in changes:
        changes = {**changes, "price": checked_money(str(changes["price"]), "price")}
    return changes


def order(
    platform_order_id: Any,
    *,
    status: str,
    order_number: Any = None,
    customer_name: Any = None,
    customer_email: Any = None,
    total: Any = None,
    currency: Any = None,
    order_date: Any = None,
    shipping_address: Optional[Dict[str, Any]] = None,
    line_items: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    return {
        "platform_order_id": str(platform_order_id),
        "order_number": str(order_number) if order_number is not None else None,
        "status": status if status in ORDER_STATUSES else "other",
        "customer_name": customer_name or None,
        "customer_email": customer_email or None,
        "total": money(total),
        "currency": currency or None,
        "order_date": str(order_date) if order_date else None,
        "shipping_address": shipping_address or None,
        "line_items": line_items or [],
    }


def item(sku: Any, title: Any, quantity: Any, price: Any) -> Dict[str, Any]:
    return {
        "sku": str(sku) if sku else None,
        "title": str(title) if title else None,
        "quantity": int(quantity or 0),
        "price": money(price),
    }


def product(
    platform_product_id: Any,
    *,
    sku: Any,
    title: Any,
    description: Any = None,
    price: Any = None,
    currency: Any = None,
    quantity: Any = None,
    images: Optional[List[str]] = None,
    is_active: bool = True,
    url: Any = None,
) -> Dict[str, Any]:
    return {
        "platform_product_id": str(platform_product_id),
        "sku": str(sku) if sku else None,
        "title": str(title) if title else None,
        "description": description or None,
        "price": money(price),
        "currency": currency or None,
        "quantity": int(quantity) if quantity is not None else None,
        "images": images or [],
        "is_active": bool(is_active),
        "url": url or None,
    }


def a_return(
    platform_return_id: Any,
    *,
    status: str,
    platform_order_id: Any = None,
    reason: Any = None,
    refund: Any = None,
    currency: Any = None,
    line_items: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    return {
        "platform_return_id": str(platform_return_id),
        "platform_order_id": str(platform_order_id) if platform_order_id else None,
        "status": status if status in RETURN_STATUSES else "other",
        "reason": reason or None,
        "refund": money(refund),
        "currency": currency or None,
        "line_items": line_items or [],
    }


class AbstractEcommerceProvider(AbstractStaticProvider):
    """A store platform; each instance is one store. Operations a
    platform's API lacks keep the refusing defaults below."""

    name: ClassVar[str] = ""
    friendly_name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    _abilities: ClassVar[Set[str]] = {
        "list_orders",
        "get_order",
        "cancel_order",
        "fulfill_order",
        "list_products",
        "set_inventory",
        "update_product",
        "list_returns",
        "process_return",
    }
    _env: ClassVar[Dict[str, Any]] = {}
    http_timeout_seconds: ClassVar[float] = ECOMMERCE_REQUEST_TIMEOUT_SECONDS

    @classmethod
    def bond_instance(cls, instance: ProviderInstanceModel) -> AbstractProviderInstance:
        return AbstractProviderInstance(instance)

    @classmethod
    def required(cls, instance: ProviderInstanceModel, key: str) -> str:
        value = cls.setting(instance, key)
        if not value:
            raise TransientExternalError(
                f"{cls.friendly_name} {key} not configured", provider=cls.name
            )
        return str(value)

    @classmethod
    async def access_token(
        cls, instance: ProviderInstanceModel, fetch: Fetch, *credentials: str
    ) -> str:
        """A short-lived token traded for ``credentials`` by ``fetch``,
        reused until shortly before it expires. A refused exchange is
        refused credentials, whatever status the platform answers with
        (Walmart: 400, Etsy: 403, eBay and Amazon: 401)."""

        async def exchange() -> Tuple[str, float]:
            try:
                return await fetch()
            except (InvalidInputExternalError, AuthExternalError) as exc:
                raise AuthExternalError(
                    f"{cls.friendly_name} refused the credentials",
                    provider=cls.name,
                    upstream_status=exc.upstream_status,
                ) from exc

        return await _TOKENS.obtain(
            f"{cls.name}:{instance.id}", fingerprint(*credentials), exchange
        )

    @classmethod
    def drop_token(cls, instance: ProviderInstanceModel) -> None:
        _TOKENS.drop(f"{cls.name}:{instance.id}")

    @classmethod
    def cannot(cls, what: str, why: str = "") -> PermanentExternalError:
        return PermanentExternalError(
            f"{cls.friendly_name}'s API cannot {what}" + (f": {why}" if why else ""),
            provider=cls.name,
        )

    @classmethod
    @abstractmethod
    async def orders(
        cls, instance: ProviderInstanceModel, since: datetime, limit: int
    ) -> List[Dict[str, Any]]:
        """Orders placed since ``since``, newest first."""

    @classmethod
    @abstractmethod
    async def order(
        cls, instance: ProviderInstanceModel, order_id: str
    ) -> Dict[str, Any]: ...

    @classmethod
    async def cancel_order(
        cls, instance: ProviderInstanceModel, order_id: str, reason: str
    ) -> Dict[str, Any]:
        raise cls.cannot("cancel an order")

    @classmethod
    async def fulfill_order(
        cls,
        instance: ProviderInstanceModel,
        order_id: str,
        tracking_number: Optional[str],
        carrier: Optional[str],
    ) -> Dict[str, Any]:
        raise cls.cannot("mark an order shipped")

    @classmethod
    @abstractmethod
    async def products(
        cls, instance: ProviderInstanceModel, limit: int
    ) -> List[Dict[str, Any]]: ...

    @classmethod
    async def set_inventory(
        cls, instance: ProviderInstanceModel, sku: str, quantity: int
    ) -> Dict[str, Any]:
        raise cls.cannot("set inventory")

    @classmethod
    async def update_product(
        cls, instance: ProviderInstanceModel, sku: str, changes: Dict[str, Any]
    ) -> Dict[str, Any]:
        raise cls.cannot("update a product")

    @classmethod
    async def returns(
        cls, instance: ProviderInstanceModel, since: datetime, limit: int
    ) -> List[Dict[str, Any]]:
        raise cls.cannot("list returns")

    @classmethod
    async def process_return(
        cls,
        instance: ProviderInstanceModel,
        return_id: str,
        action: str,
        refund: Optional[str],
    ) -> Dict[str, Any]:
        raise cls.cannot("act on a return")

    @classmethod
    def services(cls) -> List[str]:
        return ["ecommerce"]


class EXT_ECommerce(AbstractStaticExtension):
    name: ClassVar[str] = "ecommerce"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Orders, products, inventory and returns across Shopify, WooCommerce, "
        "Etsy, eBay, Amazon, Walmart and AliExpress"
    )

    _env: ClassVar[Dict[str, Any]] = {}
    dependencies: ClassVar[Dependencies] = Dependencies([])
    _abilities: ClassVar[Set[str]] = {
        "list_stores",
        "sync_store",
        "sales_report",
        *AbstractEcommerceProvider._abilities,
    }

    @classmethod
    async def _mirrored(cls, store: str, kind: str, method: str, *args: Any) -> Any:
        """A store's answer (one record or a list), mirrored locally."""
        from zephyrex.extensions.ecommerce.BLL_ECommerce import mirror

        found = await cls.rotate_on_instance(store, method, *args)
        records = found if isinstance(found, list) else [found]
        root = cls.root
        if root is not None:
            mirror(root.model_registry, cls.instance_id(store), kind, records)
        return found

    @classmethod
    def instance_id(cls, store: str) -> str:
        for instance in cls.instances_of():
            if store in (instance["id"], instance["name"]):
                return str(instance["id"])
        raise InvalidInputExternalError(f"no store {store!r}")

    @classmethod
    async def sync(cls, store: str, days: int) -> Dict[str, Any]:
        """Refresh a store's mirror: its recent orders and returns, and its
        products. A kind the platform cannot list is reported, not fatal."""
        since = window(days)
        counts: Dict[str, Any] = {}
        for kind, method, args in (
            ("order", "orders", (since, MAX_LIMIT)),
            ("product", "products", (MAX_LIMIT,)),
            ("return", "returns", (since, MAX_LIMIT)),
        ):
            try:
                counts[kind] = len(await cls._mirrored(store, kind, method, *args))
            except PermanentExternalError as exc:
                counts[kind] = f"unsupported: {exc}"
        return {"store": store, "synced": counts}

    @classmethod
    @ability("list_stores")
    async def list_stores(cls) -> List[Dict[str, Any]]:
        """The configured stores: id, name and platform."""
        return cls.instances_of()

    @classmethod
    @ability("sync_store")
    async def sync_store(cls, store: str, days: int = DEFAULT_DAYS) -> Dict[str, Any]:
        return await cls.sync(store, days)

    @classmethod
    @ability("list_orders")
    async def list_orders(
        cls, store: str, days: int = DEFAULT_DAYS, limit: int = DEFAULT_LIMIT
    ) -> List[Dict[str, Any]]:
        result: List[Dict[str, Any]] = await cls._mirrored(
            store, "order", "orders", window(days), checked_limit(limit)
        )
        return result

    @classmethod
    @ability("get_order")
    async def get_order(cls, store: str, order_id: str) -> Dict[str, Any]:
        result: Dict[str, Any] = await cls._mirrored(store, "order", "order", order_id)
        return result

    @classmethod
    @ability("cancel_order")
    async def cancel_order(
        cls, store: str, order_id: str, reason: str
    ) -> Dict[str, Any]:
        result: Dict[str, Any] = await cls._mirrored(
            store, "order", "cancel_order", order_id, reason
        )
        return result

    @classmethod
    @ability("fulfill_order")
    async def fulfill_order(
        cls,
        store: str,
        order_id: str,
        tracking_number: Optional[str] = None,
        carrier: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Mark an order shipped, with its tracking where given."""
        result: Dict[str, Any] = await cls.rotate_on_instance(
            store, "fulfill_order", order_id, tracking_number, carrier
        )
        return result

    @classmethod
    @ability("list_products")
    async def list_products(
        cls, store: str, limit: int = DEFAULT_LIMIT
    ) -> List[Dict[str, Any]]:
        result: List[Dict[str, Any]] = await cls._mirrored(
            store, "product", "products", checked_limit(limit)
        )
        return result

    @classmethod
    @ability("set_inventory")
    async def set_inventory(cls, store: str, sku: str, quantity: int) -> Dict[str, Any]:
        """Set the stock of a SKU."""
        result: Dict[str, Any] = await cls.rotate_on_instance(
            store, "set_inventory", sku, checked_quantity(quantity)
        )
        return result

    @classmethod
    @ability("update_product")
    async def update_product(
        cls, store: str, sku: str, changes: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Change a product's title, description or price."""
        result: Dict[str, Any] = await cls.rotate_on_instance(
            store, "update_product", sku, checked_changes(changes)
        )
        return result

    @classmethod
    @ability("list_returns")
    async def list_returns(
        cls, store: str, days: int = DEFAULT_DAYS, limit: int = DEFAULT_LIMIT
    ) -> List[Dict[str, Any]]:
        result: List[Dict[str, Any]] = await cls._mirrored(
            store, "return", "returns", window(days), checked_limit(limit)
        )
        return result

    @classmethod
    @ability("process_return")
    async def process_return(
        cls,
        store: str,
        return_id: str,
        action: str,
        refund: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Approve (refunding ``refund`` where given) or reject a return."""
        if action not in RETURN_ACTIONS:
            raise InvalidInputExternalError(
                f"action must be one of {', '.join(RETURN_ACTIONS)}"
            )
        result: Dict[str, Any] = await cls.rotate_on_instance(
            store,
            "process_return",
            return_id,
            action,
            checked_money(refund, "refund") if refund is not None else None,
        )
        return result

    @classmethod
    @ability("sales_report")
    async def sales_report(
        cls, requester_id: str, days: int = DEFAULT_DAYS, store: Optional[str] = None
    ) -> Dict[str, Any]:
        """Sales across the stores the user can see (or one), from the
        local mirror: revenue per currency, orders per status, top SKUs."""
        from zephyrex.extensions.ecommerce.BLL_ECommerce import (
            StoreOrderManager,
            sales_report,
        )

        orders = cls.as_requester(StoreOrderManager, requester_id)
        return sales_report(
            orders, window(days), cls.instance_id(store) if store else None
        )
