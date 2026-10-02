# SPDX-License-Identifier: AGPL-3.0-or-later
"""The local mirror of each store's orders, products and returns.

A store (a provider instance) writes its records here as they are read
from it: by the abilities, and by a sync (``POST
/v1/store_order/sync``). A record belongs to the store's owner, so
whoever can see the store sees its records; the routes read and search
only, since a store's own data is changed through the store.
``GET /v1/store_order/report`` sums sales across the stores the caller
can see.
"""

from collections import Counter, defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, ClassVar, Dict, List, Optional, Sequence, Type

from fastapi import HTTPException
from pydantic import BaseModel as PydanticModel
from pydantic import Field

from zephyrex.lib.CustomRoute import ExposeIn, custom_route
from zephyrex.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    DateSearchModel,
    ModelMeta,
    StringSearchModel,
    UpdateMixinModel,
)
from zephyrex.logic.BLL_Auth import TeamModel, UserModel
from zephyrex.logic.BLL_Providers import (
    ProviderInstanceManager,
    ProviderManager,
    instance_owner,
)
from zephyrex.lib.Environment import env
from zephyrex.pydantic2.fastapi import RouterMixin
from zephyrex.pydantic2.fastapi.types import RouteType
from zephyrex.pydantic2.registry import BaseModel

READ_ONLY = [RouteType.GET, RouteType.LIST, RouteType.SEARCH]
TOP_SKUS = 10
# Orders whose money did not stay with the store.
UNSOLD = {"cancelled", "refunded"}


class StoreFields(PydanticModel):
    """What every mirrored record carries: where it came from, and when."""

    provider: str = Field(..., description="The platform (shopify, etsy, …)")
    provider_instance_id: str = Field(..., description="The store it came from")
    synced_at: datetime = Field(..., description="When it was last read from the store")


class StoreOrderModel(
    ApplicationModel,
    UpdateMixinModel,
    UserModel.Reference.Optional,
    TeamModel.Reference.Optional,
    StoreFields,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["StoreOrderManager"]]
    platform_order_id: str = Field(..., description="The platform's order id")
    order_number: Optional[str] = Field(None, description="The number customers see")
    status: str = Field(..., description="pending, processing, shipped, delivered, …")
    customer_name: Optional[str] = Field(None, description="Customer")
    customer_email: Optional[str] = Field(None, description="Customer's email")
    total: Optional[str] = Field(None, description="Order total (decimal)")
    currency: Optional[str] = Field(None, description="ISO currency code")
    order_date: Optional[datetime] = Field(None, description="When it was placed")
    shipping_address: Optional[Dict[str, Any]] = Field(None, description="Ship to")
    line_items: Optional[List[Dict[str, Any]]] = Field(
        None, description="Lines ordered"
    )

    table_comment: ClassVar[str] = "Orders read from each store (a local mirror)"

    class Create(BaseModel):
        model_config = {"extra": "allow"}

    class Update(BaseModel):
        model_config = {"extra": "allow"}

    class Search(ApplicationModel.Search):
        provider: Optional[StringSearchModel] = None
        provider_instance_id: Optional[StringSearchModel] = None
        status: Optional[StringSearchModel] = None
        customer_email: Optional[StringSearchModel] = None
        order_date: Optional[DateSearchModel] = None


class StoreProductModel(
    ApplicationModel,
    UpdateMixinModel,
    UserModel.Reference.Optional,
    TeamModel.Reference.Optional,
    StoreFields,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["StoreProductManager"]]
    platform_product_id: str = Field(..., description="The platform's product id")
    sku: Optional[str] = Field(None, description="Stock keeping unit")
    title: Optional[str] = Field(None, description="Title")
    description: Optional[str] = Field(None, description="Description")
    price: Optional[str] = Field(None, description="Price (decimal)")
    currency: Optional[str] = Field(None, description="ISO currency code")
    quantity: Optional[int] = Field(None, description="Stock, where the store says")
    images: Optional[List[str]] = Field(None, description="Image addresses")
    is_active: bool = Field(True, description="Listed for sale")
    url: Optional[str] = Field(None, description="The product's page")

    table_comment: ClassVar[str] = "Products read from each store (a local mirror)"

    class Create(BaseModel):
        model_config = {"extra": "allow"}

    class Update(BaseModel):
        model_config = {"extra": "allow"}

    class Search(ApplicationModel.Search):
        provider_instance_id: Optional[StringSearchModel] = None
        sku: Optional[StringSearchModel] = None
        title: Optional[StringSearchModel] = None


class StoreReturnModel(
    ApplicationModel,
    UpdateMixinModel,
    UserModel.Reference.Optional,
    TeamModel.Reference.Optional,
    StoreFields,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["StoreReturnManager"]]
    platform_return_id: str = Field(..., description="The platform's return id")
    platform_order_id: Optional[str] = Field(None, description="The order returned")
    status: str = Field(..., description="requested, approved, rejected, refunded, …")
    reason: Optional[str] = Field(None, description="Why")
    refund: Optional[str] = Field(None, description="Refund (decimal)")
    currency: Optional[str] = Field(None, description="ISO currency code")
    line_items: Optional[List[Dict[str, Any]]] = Field(
        None, description="Lines returned"
    )

    table_comment: ClassVar[str] = "Returns read from each store (a local mirror)"

    class Create(BaseModel):
        model_config = {"extra": "allow"}

    class Update(BaseModel):
        model_config = {"extra": "allow"}

    class Search(ApplicationModel.Search):
        provider_instance_id: Optional[StringSearchModel] = None
        status: Optional[StringSearchModel] = None


class SyncRequest(BaseModel):
    provider_instance_id: str
    days: int = Field(30, ge=1, le=365)


class SyncResponse(BaseModel):
    store: str
    synced: Dict[str, Any]


class SalesReport(BaseModel):
    since: datetime
    orders: int
    by_status: Dict[str, int]
    revenue: Dict[str, str] = Field(..., description="Per currency, unsold excluded")
    top_skus: List[Dict[str, Any]]


class StoreProductManager(AbstractBLLManager, RouterMixin):
    _model = StoreProductModel
    routes_to_register: ClassVar[Optional[List[RouteType]]] = READ_ONLY


class StoreReturnManager(AbstractBLLManager, RouterMixin):
    _model = StoreReturnModel
    routes_to_register: ClassVar[Optional[List[RouteType]]] = READ_ONLY


class StoreOrderManager(AbstractBLLManager, RouterMixin):
    _model = StoreOrderModel
    routes_to_register: ClassVar[Optional[List[RouteType]]] = READ_ONLY

    @custom_route(
        method="POST",
        path="/sync",
        input_model=SyncRequest,
        output_model=SyncResponse,
        authentication_type="jwt",
        openapi_tags=("E-commerce",),
        summary="Refresh a store's orders, products and returns from the store",
        expose_in=(ExposeIn.REST,),
    )
    async def sync_route(self, body: SyncRequest) -> SyncResponse:
        from zephyrex.extensions.ecommerce.EXT_ECommerce import EXT_ECommerce

        # The caller must be able to see the store.
        ProviderInstanceManager(
            model_registry=self.model_registry, requester_id=self.requester.id
        ).get(id=body.provider_instance_id)
        synced = await EXT_ECommerce.sync(body.provider_instance_id, body.days)
        return SyncResponse(store=body.provider_instance_id, synced=synced["synced"])

    @custom_route(
        method="GET",
        path="/report",
        output_model=SalesReport,
        authentication_type="jwt",
        openapi_tags=("E-commerce",),
        summary="Sales across the stores you can see, from the local mirror",
        expose_in=(ExposeIn.REST,),
    )
    def report_route(
        self, days: int = 30, provider_instance_id: Optional[str] = None
    ) -> SalesReport:
        if not 1 <= days <= 365:
            raise HTTPException(status_code=400, detail="days must be 1-365")
        from datetime import timedelta

        since = datetime.now(UTC) - timedelta(days=days)
        return SalesReport(**sales_report(self, since, provider_instance_id))


_MANAGERS: Dict[str, Any] = {
    "order": (StoreOrderManager, "platform_order_id"),
    "product": (StoreProductManager, "platform_product_id"),
    "return": (StoreReturnManager, "platform_return_id"),
}


def _stored(kind: str, record: Dict[str, Any]) -> Dict[str, Any]:
    fields = dict(record)
    if kind == "order" and fields.get("order_date"):
        moment = datetime.fromisoformat(str(fields["order_date"]))
        fields["order_date"] = moment if moment.tzinfo else moment.replace(tzinfo=UTC)
    return fields


def mirror(
    model_registry: Any,
    provider_instance_id: str,
    kind: str,
    records: Sequence[Dict[str, Any]],
) -> int:
    """Upsert a store's records, by the store and the platform's id, as the
    store's owner; how many were written."""
    manager_class, key = _MANAGERS[kind]
    owner = instance_owner(model_registry, provider_instance_id)
    root = env("ROOT_ID")
    instance = ProviderInstanceManager(
        model_registry=model_registry, requester_id=root
    ).get(id=provider_instance_id)
    provider = ProviderManager(model_registry=model_registry, requester_id=root).get(
        id=instance.provider_id
    )
    manager = manager_class(
        model_registry=model_registry, requester_id=owner.requester_id
    )
    now = datetime.now(UTC)
    for record in records:
        fields = {**_stored(kind, record), "synced_at": now}
        existing = manager.list(
            provider_instance_id=provider_instance_id, **{key: record[key]}
        )
        if existing:
            manager.update(existing[0].id, **fields)
        else:
            manager.create(
                **fields,
                provider=provider.name,
                provider_instance_id=provider_instance_id,
                user_id=owner.user_id,
                team_id=owner.team_id,
            )
    return len(records)


def sales_report(
    orders: StoreOrderManager, since: datetime, provider_instance_id: Optional[str]
) -> Dict[str, Any]:
    """Sales since ``since`` from the orders ``orders``' requester can see."""
    search: Dict[str, Any] = {"order_date": {"after": since}}
    if provider_instance_id:
        search["provider_instance_id"] = {"eq": provider_instance_id}
    found = orders.search(**search) or []
    revenue: Dict[str, Decimal] = defaultdict(Decimal)
    quantities: Counter = Counter()
    for record in found:
        if record.status in UNSOLD:
            continue
        if record.total and record.currency:
            revenue[record.currency] += Decimal(record.total)
        for line in record.line_items or []:
            if line.get("sku"):
                quantities[line["sku"]] += int(line.get("quantity") or 0)
    return {
        "since": since,
        "orders": len(found),
        "by_status": dict(Counter(record.status for record in found)),
        "revenue": {
            currency: format(total, "f") for currency, total in revenue.items()
        },
        "top_skus": [
            {"sku": sku, "quantity": quantity}
            for sku, quantity in quantities.most_common(TOP_SKUS)
        ],
    }
