from datetime import datetime
from enum import Enum
from typing import ClassVar, Dict, List, Optional

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from fastapi import HTTPException

from zephyrex.pydantic2.sqlalchemy import DatabaseMixin
from zephyrex.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    DateSearchModel,
    ModelMeta,
    StringSearchModel,
    UpdateMixinModel,
)
from zephyrex.logic.BLL_Auth import UserModel


# E-commerce provider type constants
class EcommerceProviderType:
    WALMART = "walmart"
    AMAZON = "amazon"
    SHOPIFY = "shopify"
    EBAY = "ebay"
    ETSY = "etsy"

    @classmethod
    def values(cls):
        return [
            cls.WALMART,
            cls.AMAZON,
            cls.SHOPIFY,
            cls.EBAY,
            cls.ETSY,
        ]


class EcommerceProviderModel(
    ApplicationModel,
    UpdateMixinModel,
    UserModel.Reference.ID,
    DatabaseMixin,
    metaclass=ModelMeta,
):
    name: str = Field(..., description="Name of the e-commerce provider")
    provider_type: str = Field(..., description="Type of e-commerce provider")
    api_key: Optional[str] = Field(None, description="API key for the provider")
    access_token: Optional[str] = Field(None, description="OAuth access token")
    refresh_token: Optional[str] = Field(None, description="OAuth refresh token")
    token_expires_at: Optional[datetime] = Field(
        None, description="Token expiration time"
    )
    settings: Optional[Dict] = Field(None, description="Provider-specific settings")
    is_active: bool = Field(True, description="Whether the provider is active")

    # Database metadata
    table_comment: ClassVar[str] = (
        "E-commerce provider configurations for users including API credentials and settings"
    )

    class ReferenceID:
        ecommerceprovider_id: str = Field(
            ..., description="The ID of the e-commerce provider"
        )

        class Optional:
            ecommerceprovider_id: Optional[str] = None

        class Search:
            ecommerceprovider_id: Optional[StringSearchModel] = None

    class Create(BaseModel, UserModel.Reference.ID):
        name: str = Field(..., description="Name of the e-commerce provider")
        provider_type: str = Field(..., description="Type of e-commerce provider")
        api_key: Optional[str] = Field(None, description="API key for the provider")
        access_token: Optional[str] = Field(None, description="OAuth access token")
        refresh_token: Optional[str] = Field(None, description="OAuth refresh token")
        token_expires_at: Optional[datetime] = Field(
            None, description="Token expiration time"
        )
        settings: Optional[Dict] = Field(None, description="Provider-specific settings")
        is_active: bool = Field(True, description="Whether the provider is active")

    class Update(BaseModel):
        name: Optional[str] = Field(None, description="Name of the e-commerce provider")
        api_key: Optional[str] = Field(None, description="API key for the provider")
        access_token: Optional[str] = Field(None, description="OAuth access token")
        refresh_token: Optional[str] = Field(None, description="OAuth refresh token")
        token_expires_at: Optional[datetime] = Field(
            None, description="Token expiration time"
        )
        settings: Optional[Dict] = Field(None, description="Provider-specific settings")
        is_active: Optional[bool] = Field(
            None, description="Whether the provider is active"
        )

    class Search(
        ApplicationModel.Search, UpdateMixinModel.Search, UserModel.Reference.ID.Search
    ):
        name: Optional[StringSearchModel] = None
        provider_type: Optional[str] = None
        is_active: Optional[bool] = None


class EcommerceProviderReferenceModel(EcommerceProviderModel.Reference.ID):
    ecommerce_provider: Optional[EcommerceProviderModel] = None

    class Optional(EcommerceProviderModel.Reference.ID.Optional):
        ecommerce_provider: Optional[EcommerceProviderModel] = None


class EcommerceProviderNetworkModel:
    class POST(BaseModel):
        ecommerce_provider: EcommerceProviderModel.Create

    class PUT(BaseModel):
        ecommerce_provider: EcommerceProviderModel.Update

    class SEARCH(BaseModel):
        ecommerce_provider: EcommerceProviderModel.Search

    class ResponseSingle(BaseModel):
        ecommerce_provider: EcommerceProviderModel

    class ResponsePlural(BaseModel):
        ecommerce_providers: List[EcommerceProviderModel]


# E-commerce order status constants
class EcommerceOrderStatus:
    PENDING = "pending"
    PROCESSING = "processing"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    RETURNED = "returned"


class EcommerceOrderModel(
    ApplicationModel,
    UpdateMixinModel,
    EcommerceProviderModel.Reference.ID,
    DatabaseMixin,
    metaclass=ModelMeta,
):
    platform_order_id: str = Field(
        ..., description="Order ID from the e-commerce platform"
    )
    order_number: Optional[str] = Field(None, description="Human-readable order number")
    status: str = Field(..., description="Current order status")
    customer_name: Optional[str] = Field(None, description="Customer name")
    customer_email: Optional[str] = Field(None, description="Customer email")
    total_amount: Optional[float] = Field(None, description="Total order amount")
    currency: Optional[str] = Field(None, description="Currency code")
    order_date: Optional[datetime] = Field(
        None, description="Date when order was placed"
    )
    shipping_address: Optional[Dict] = Field(None, description="Shipping address")
    billing_address: Optional[Dict] = Field(None, description="Billing address")
    items: Optional[List[Dict]] = Field(None, description="Order items")
    platform_data: Optional[Dict] = Field(None, description="Raw platform data")

    # Database metadata
    table_comment: ClassVar[str] = (
        "E-commerce orders from various platforms with customer and item details"
    )

    class ReferenceID:
        ecommerceorder_id: str = Field(
            ..., description="The ID of the e-commerce order"
        )

        class Optional:
            ecommerceorder_id: Optional[str] = None

        class Search:
            ecommerceorder_id: Optional[StringSearchModel] = None

    class Create(BaseModel, EcommerceProviderModel.Reference.ID):
        platform_order_id: str = Field(
            ..., description="Order ID from the e-commerce platform"
        )
        order_number: Optional[str] = Field(
            None, description="Human-readable order number"
        )
        status: str = Field(..., description="Current order status")
        customer_name: Optional[str] = Field(None, description="Customer name")
        customer_email: Optional[str] = Field(None, description="Customer email")
        total_amount: Optional[float] = Field(None, description="Total order amount")
        currency: Optional[str] = Field(None, description="Currency code")
        order_date: Optional[datetime] = Field(
            None, description="Date when order was placed"
        )
        shipping_address: Optional[Dict] = Field(None, description="Shipping address")
        billing_address: Optional[Dict] = Field(None, description="Billing address")
        items: Optional[List[Dict]] = Field(None, description="Order items")
        platform_data: Optional[Dict] = Field(None, description="Raw platform data")

    class Update(BaseModel):
        order_number: Optional[str] = Field(
            None, description="Human-readable order number"
        )
        status: Optional[str] = Field(None, description="Current order status")
        customer_name: Optional[str] = Field(None, description="Customer name")
        customer_email: Optional[str] = Field(None, description="Customer email")
        total_amount: Optional[float] = Field(None, description="Total order amount")
        currency: Optional[str] = Field(None, description="Currency code")
        order_date: Optional[datetime] = Field(
            None, description="Date when order was placed"
        )
        shipping_address: Optional[Dict] = Field(None, description="Shipping address")
        billing_address: Optional[Dict] = Field(None, description="Billing address")
        items: Optional[List[Dict]] = Field(None, description="Order items")
        platform_data: Optional[Dict] = Field(None, description="Raw platform data")

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
        EcommerceProviderModel.Reference.ID.Search,
    ):
        platform_order_id: Optional[StringSearchModel] = None
        order_number: Optional[StringSearchModel] = None
        status: Optional[str] = None
        customer_name: Optional[StringSearchModel] = None
        customer_email: Optional[StringSearchModel] = None
        order_date: Optional[DateSearchModel] = None


class EcommerceOrderReferenceModel(EcommerceOrderModel.Reference.ID):
    ecommerce_order: Optional[EcommerceOrderModel] = None

    class Optional(EcommerceOrderModel.Reference.ID.Optional):
        ecommerce_order: Optional[EcommerceOrderModel] = None


class EcommerceOrderNetworkModel:
    class POST(BaseModel):
        ecommerce_order: EcommerceOrderModel.Create

    class PUT(BaseModel):
        ecommerce_order: EcommerceOrderModel.Update

    class SEARCH(BaseModel):
        ecommerce_order: EcommerceOrderModel.Search

    class ResponseSingle(BaseModel):
        ecommerce_order: EcommerceOrderModel

    class ResponsePlural(BaseModel):
        ecommerce_orders: List[EcommerceOrderModel]


class EcommerceProductModel(
    ApplicationModel,
    UpdateMixinModel,
    EcommerceProviderModel.Reference.ID,
    DatabaseMixin,
    metaclass=ModelMeta,
):
    sku: str = Field(..., description="Stock Keeping Unit")
    platform_product_id: Optional[str] = Field(
        None, description="Product ID from platform"
    )
    title: Optional[str] = Field(None, description="Product title")
    description: Optional[str] = Field(None, description="Product description")
    price: Optional[float] = Field(None, description="Product price")
    currency: Optional[str] = Field(None, description="Currency code")
    quantity: Optional[int] = Field(None, description="Available quantity")
    category: Optional[str] = Field(None, description="Product category")
    images: Optional[List[str]] = Field(None, description="Product image URLs")
    attributes: Optional[Dict] = Field(None, description="Product attributes")
    platform_data: Optional[Dict] = Field(None, description="Raw platform data")
    is_active: bool = Field(True, description="Whether the product is active")

    # Database metadata
    table_comment: ClassVar[str] = (
        "E-commerce products with pricing, inventory, and platform-specific data"
    )

    class ReferenceID:
        ecommerceproduct_id: str = Field(
            ..., description="The ID of the e-commerce product"
        )

        class Optional:
            ecommerceproduct_id: Optional[str] = None

        class Search:
            ecommerceproduct_id: Optional[StringSearchModel] = None

    class Create(BaseModel, EcommerceProviderModel.Reference.ID):
        sku: str = Field(..., description="Stock Keeping Unit")
        platform_product_id: Optional[str] = Field(
            None, description="Product ID from platform"
        )
        title: Optional[str] = Field(None, description="Product title")
        description: Optional[str] = Field(None, description="Product description")
        price: Optional[float] = Field(None, description="Product price")
        currency: Optional[str] = Field(None, description="Currency code")
        quantity: Optional[int] = Field(None, description="Available quantity")
        category: Optional[str] = Field(None, description="Product category")
        images: Optional[List[str]] = Field(None, description="Product image URLs")
        attributes: Optional[Dict] = Field(None, description="Product attributes")
        platform_data: Optional[Dict] = Field(None, description="Raw platform data")
        is_active: bool = Field(True, description="Whether the product is active")

    class Update(BaseModel):
        platform_product_id: Optional[str] = Field(
            None, description="Product ID from platform"
        )
        title: Optional[str] = Field(None, description="Product title")
        description: Optional[str] = Field(None, description="Product description")
        price: Optional[float] = Field(None, description="Product price")
        currency: Optional[str] = Field(None, description="Currency code")
        quantity: Optional[int] = Field(None, description="Available quantity")
        category: Optional[str] = Field(None, description="Product category")
        images: Optional[List[str]] = Field(None, description="Product image URLs")
        attributes: Optional[Dict] = Field(None, description="Product attributes")
        platform_data: Optional[Dict] = Field(None, description="Raw platform data")
        is_active: Optional[bool] = Field(
            None, description="Whether the product is active"
        )

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
        EcommerceProviderModel.Reference.ID.Search,
    ):
        sku: Optional[StringSearchModel] = None
        platform_product_id: Optional[StringSearchModel] = None
        title: Optional[StringSearchModel] = None
        category: Optional[StringSearchModel] = None
        is_active: Optional[bool] = None


class EcommerceProductReferenceModel(EcommerceProductModel.Reference.ID):
    ecommerce_product: Optional[EcommerceProductModel] = None

    class Optional(EcommerceProductModel.Reference.ID.Optional):
        ecommerce_product: Optional[EcommerceProductModel] = None


class EcommerceProductNetworkModel:
    class POST(BaseModel):
        ecommerce_product: EcommerceProductModel.Create

    class PUT(BaseModel):
        ecommerce_product: EcommerceProductModel.Update

    class SEARCH(BaseModel):
        ecommerce_product: EcommerceProductModel.Search

    class ResponseSingle(BaseModel):
        ecommerce_product: EcommerceProductModel

    class ResponsePlural(BaseModel):
        ecommerce_products: List[EcommerceProductModel]


# E-commerce return status constants
class EcommerceReturnStatus:
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    PROCESSING = "processing"
    COMPLETED = "completed"


class EcommerceReturnModel(
    ApplicationModel,
    UpdateMixinModel,
    EcommerceProviderModel.Reference.ID,
    EcommerceOrderModel.Reference.ID.Optional,
    DatabaseMixin,
    metaclass=ModelMeta,
):
    platform_return_id: str = Field(..., description="Return ID from the platform")
    status: str = Field(..., description="Current return status")
    reason: Optional[str] = Field(None, description="Reason for return")
    refund_amount: Optional[float] = Field(None, description="Refund amount")
    currency: Optional[str] = Field(None, description="Currency code")
    items: Optional[List[Dict]] = Field(None, description="Returned items")
    platform_data: Optional[Dict] = Field(None, description="Raw platform data")

    # Database metadata
    table_comment: ClassVar[str] = (
        "E-commerce returns and refunds with status tracking and item details"
    )

    class ReferenceID:
        ecommercereturn_id: str = Field(
            ..., description="The ID of the e-commerce return"
        )

        class Optional:
            ecommercereturn_id: Optional[str] = None

        class Search:
            ecommercereturn_id: Optional[StringSearchModel] = None

    class Create(
        BaseModel,
        EcommerceProviderModel.Reference.ID,
        EcommerceOrderModel.Reference.ID.Optional,
    ):
        platform_return_id: str = Field(..., description="Return ID from the platform")
        status: str = Field(..., description="Current return status")
        reason: Optional[str] = Field(None, description="Reason for return")
        refund_amount: Optional[float] = Field(None, description="Refund amount")
        currency: Optional[str] = Field(None, description="Currency code")
        items: Optional[List[Dict]] = Field(None, description="Returned items")
        platform_data: Optional[Dict] = Field(None, description="Raw platform data")

    class Update(BaseModel):
        status: Optional[str] = Field(None, description="Current return status")
        reason: Optional[str] = Field(None, description="Reason for return")
        refund_amount: Optional[float] = Field(None, description="Refund amount")
        currency: Optional[str] = Field(None, description="Currency code")
        items: Optional[List[Dict]] = Field(None, description="Returned items")
        platform_data: Optional[Dict] = Field(None, description="Raw platform data")

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
        EcommerceProviderModel.Reference.ID.Search,
        EcommerceOrderModel.Reference.ID.Search,
    ):
        platform_return_id: Optional[StringSearchModel] = None
        status: Optional[str] = None
        reason: Optional[StringSearchModel] = None


class EcommerceReturnReferenceModel(EcommerceReturnModel.Reference.ID):
    ecommerce_return: Optional[EcommerceReturnModel] = None

    class Optional(EcommerceReturnModel.Reference.ID.Optional):
        ecommerce_return: Optional[EcommerceReturnModel] = None


class EcommerceReturnNetworkModel:
    class POST(BaseModel):
        ecommerce_return: EcommerceReturnModel.Create

    class PUT(BaseModel):
        ecommerce_return: EcommerceReturnModel.Update

    class SEARCH(BaseModel):
        ecommerce_return: EcommerceReturnModel.Search

    class ResponseSingle(BaseModel):
        ecommerce_return: EcommerceReturnModel

    class ResponsePlural(BaseModel):
        ecommerce_returns: List[EcommerceReturnModel]


class EcommerceProviderManager(AbstractBLLManager):
    Model = EcommerceProviderModel
    ReferenceModel = EcommerceProviderReferenceModel
    NetworkModel = EcommerceProviderNetworkModel

    def __init__(
        self,
        requester_id: str,
        target_user_id: Optional[str] = None,
        target_team_id: Optional[str] = None,
        db: Optional[Session] = None,
    ):
        super().__init__(
            requester_id=requester_id,
            target_id=target_user_id,
            target_team_id=target_team_id,
            db=db,
        )
        self._orders = None
        self._products = None
        self._returns = None

    def create(self, create_model: Model.Create) -> Model:
        # Validate provider type
        if create_model.provider_type not in EcommerceProviderType.values():
            raise HTTPException(
                status_code=400,
                detail=f"Invalid provider type: {create_model.provider_type}. Must be one of: {', '.join(EcommerceProviderType.values())}",
            )
        return super().create(create_model)

    def update(self, update_model: Model.Update, id: str) -> Model:
        # Validate provider type if provided
        if (
            hasattr(update_model, "provider_type")
            and update_model.provider_type is not None
        ):
            if update_model.provider_type not in EcommerceProviderType.values():
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid provider type: {update_model.provider_type}. Must be one of: {', '.join(EcommerceProviderType.values())}",
                )
        return super().update(update_model, id)

    @property
    def orders(self):
        if self._orders is None:
            self._orders = EcommerceOrderManager(
                requester_id=self.requester.id,
                target_user_id=self.target_user_id,
                target_team_id=self.target_team_id,
                db=self.db,
            )
        return self._orders

    @property
    def products(self):
        if self._products is None:
            self._products = EcommerceProductManager(
                requester_id=self.requester.id,
                target_user_id=self.target_user_id,
                target_team_id=self.target_team_id,
                db=self.db,
            )
        return self._products

    @property
    def returns(self):
        if self._returns is None:
            self._returns = EcommerceReturnManager(
                requester_id=self.requester.id,
                target_user_id=self.target_user_id,
                target_team_id=self.target_team_id,
                db=self.db,
            )
        return self._returns


class EcommerceOrderManager(AbstractBLLManager):
    Model = EcommerceOrderModel
    ReferenceModel = EcommerceOrderReferenceModel
    NetworkModel = EcommerceOrderNetworkModel

    def __init__(
        self,
        requester_id: str,
        target_user_id: Optional[str] = None,
        target_team_id: Optional[str] = None,
        db: Optional[Session] = None,
    ):
        super().__init__(
            requester_id=requester_id,
            target_id=target_user_id,
            target_team_id=target_team_id,
            db=db,
        )
        self._providers = None
        self._returns = None

    @property
    def providers(self):
        if self._providers is None:
            self._providers = EcommerceProviderManager(
                requester_id=self.requester.id,
                target_user_id=self.target_user_id,
                target_team_id=self.target_team_id,
                db=self.db,
            )
        return self._providers

    @property
    def returns(self):
        if self._returns is None:
            self._returns = EcommerceReturnManager(
                requester_id=self.requester.id,
                target_user_id=self.target_user_id,
                target_team_id=self.target_team_id,
                db=self.db,
            )
        return self._returns


class EcommerceProductManager(AbstractBLLManager):
    Model = EcommerceProductModel
    ReferenceModel = EcommerceProductReferenceModel
    NetworkModel = EcommerceProductNetworkModel

    def __init__(
        self,
        requester_id: str,
        target_user_id: Optional[str] = None,
        target_team_id: Optional[str] = None,
        db: Optional[Session] = None,
    ):
        super().__init__(
            requester_id=requester_id,
            target_id=target_user_id,
            target_team_id=target_team_id,
            db=db,
        )
        self._providers = None

    @property
    def providers(self):
        if self._providers is None:
            self._providers = EcommerceProviderManager(
                requester_id=self.requester.id,
                target_user_id=self.target_user_id,
                target_team_id=self.target_team_id,
                db=self.db,
            )
        return self._providers


class EcommerceReturnManager(AbstractBLLManager):
    Model = EcommerceReturnModel
    ReferenceModel = EcommerceReturnReferenceModel
    NetworkModel = EcommerceReturnNetworkModel

    def __init__(
        self,
        requester_id: str,
        target_user_id: Optional[str] = None,
        target_team_id: Optional[str] = None,
        db: Optional[Session] = None,
    ):
        super().__init__(
            requester_id=requester_id,
            target_id=target_user_id,
            target_team_id=target_team_id,
            db=db,
        )
        self._providers = None
        self._orders = None

    @property
    def providers(self):
        if self._providers is None:
            self._providers = EcommerceProviderManager(
                requester_id=self.requester.id,
                target_user_id=self.target_user_id,
                target_team_id=self.target_team_id,
                db=self.db,
            )
        return self._providers

    @property
    def orders(self):
        if self._orders is None:
            self._orders = EcommerceOrderManager(
                requester_id=self.requester.id,
                target_user_id=self.target_user_id,
                target_team_id=self.target_team_id,
                db=self.db,
            )
        return self._orders
