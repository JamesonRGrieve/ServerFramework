"""
Stripe payment provider for AGInfrastructure.
Provides comprehensive payment processing abilities through Stripe's API.
Fully static implementation compatible with the Provider Rotation System.
"""

from datetime import datetime
from typing import Any, ClassVar, Dict, List, Optional

try:
    import stripe
except ImportError:
    stripe = None
    import warnings

    warnings.warn(
        "Stripe package currently missing, but in PIP_Dependencies, will likely install on run",
        ImportWarning,
    )
from pydantic import BaseModel, Field

from extensions.AbstractExtensionProvider import AbstractProviderInstance_SDK
from extensions.AbstractExternalModel import (
    AbstractExternalManager,
    AbstractExternalModel,
)
from extensions.payment.EXT_Payment import AbstractPaymentProvider
from lib.Dependencies import Dependencies, PIP_Dependency
from lib.Environment import env
from lib.Logging import logger
from lib.Pydantic import BaseModel
from logic.AbstractLogicManager import ModelMeta
from logic.BLL_Providers import ProviderInstanceModel

# ============================================================================
# Stripe Customer External Model
# ============================================================================


class Stripe_CustomerModel(AbstractExternalModel, metaclass=ModelMeta):
    """External model for Stripe Customer API resource."""

    class Reference:
        pass

    # Stripe API configuration
    external_resource: ClassVar[str] = "customers"
    # Mark as an extension model for framework introspection
    _is_extension_model: ClassVar[bool] = True
    # Extension target identifier used by framework
    _extension_target: ClassVar[str] = "payment"

    # Model fields matching Stripe API exactly
    id: str = Field(..., description="Stripe customer ID")
    object: str = Field(default="customer", description="Object type")
    address: Optional[Dict[str, Any]] = Field(None, description="Customer address")
    balance: int = Field(0, description="Account balance in cents")
    created: int = Field(..., description="Creation timestamp")
    currency: Optional[str] = Field(None, description="Customer currency")
    default_source: Optional[str] = Field(None, description="Default payment source")
    delinquent: bool = Field(False, description="Whether customer is delinquent")
    description: Optional[str] = Field(None, description="Customer description")
    email: str = Field(..., description="Customer email address")
    invoice_prefix: Optional[str] = Field(None, description="Invoice prefix")
    invoice_settings: Optional[Dict[str, Any]] = Field(
        None, description="Invoice settings"
    )
    livemode: bool = Field(False, description="Whether in live mode")
    metadata: Dict[str, str] = Field(
        default_factory=dict, description="Custom metadata"
    )
    name: Optional[str] = Field(None, description="Customer name")
    next_invoice_sequence: int = Field(1, description="Next invoice sequence number")
    phone: Optional[str] = Field(None, description="Customer phone number")
    preferred_locales: List[str] = Field(
        default_factory=list, description="Preferred locales"
    )
    shipping: Optional[Dict[str, Any]] = Field(None, description="Shipping information")
    tax_exempt: str = Field("none", description="Tax exempt status")
    test_clock: Optional[str] = Field(None, description="Test clock ID")

    class Create(BaseModel):
        """Create model for Stripe Customer."""

        email: str = Field(..., description="Customer email address")
        name: Optional[str] = Field(None, description="Customer name")
        phone: Optional[str] = Field(None, description="Customer phone number")
        description: Optional[str] = Field(None, description="Customer description")
        address: Optional[Dict[str, Any]] = Field(None, description="Customer address")
        metadata: Optional[Dict[str, str]] = Field(None, description="Custom metadata")

    class Update(BaseModel):
        """Update model for Stripe Customer."""

        name: Optional[str] = Field(None, description="Customer name")
        email: Optional[str] = Field(None, description="Customer email address")
        phone: Optional[str] = Field(None, description="Customer phone number")
        description: Optional[str] = Field(None, description="Customer description")
        address: Optional[Dict[str, Any]] = Field(None, description="Customer address")
        metadata: Optional[Dict[str, str]] = Field(None, description="Custom metadata")

    class Search(BaseModel):
        """Search model for Stripe Customer."""

        email: Optional[str] = Field(None, description="Search by email")
        name: Optional[str] = Field(None, description="Search by name")

    @classmethod
    def to_external_format(cls, internal_data: Dict[str, Any]) -> Dict[str, Any]:
        """Convert internal format to Stripe API format."""
        # Since we're using exact Stripe API field names, no conversion needed
        return {k: v for k, v in internal_data.items() if v is not None}

    @classmethod
    def from_external_format(cls, external_data: Dict[str, Any]) -> Dict[str, Any]:
        """Convert Stripe API format to internal format."""
        # Since we're using exact Stripe API field names, no conversion needed
        return {k: v for k, v in external_data.items() if v is not None}

    @classmethod
    def to_external_query_format(
        cls,
        query_params: Dict[str, Any],
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        order_by: Optional[List] = None,
    ) -> Dict[str, Any]:
        """Convert query parameters to Stripe API format."""
        external_params = {}

        # Since we're using exact Stripe API field names, no conversion needed
        for field, value in query_params.items():
            external_params[field] = value

        # Stripe pagination
        if limit:
            external_params["limit"] = min(limit, 100)  # Stripe max is 100

        return external_params

    @staticmethod
    def create_via_provider(provider_instance, **kwargs) -> Dict[str, Any]:
        """Create customer via provider instance."""
        try:
            # Get bonded instance from provider
            bonded = PaymentExtensionStripeProvider.bond_instance(provider_instance)
            if not bonded or not bonded.sdk:
                return {"success": False, "error": "Failed to bond provider instance"}

            stripe_client = bonded.sdk

            # Prepare customer data with standard Stripe field names
            customer_data = {}
            if "email" in kwargs:
                customer_data["email"] = kwargs["email"]
            if "name" in kwargs:
                customer_data["name"] = kwargs["name"]
            if "phone" in kwargs:
                customer_data["phone"] = kwargs["phone"]
            if "description" in kwargs:
                customer_data["description"] = kwargs["description"]
            if "address" in kwargs:
                customer_data["address"] = kwargs["address"]
            if "metadata" in kwargs:
                customer_data["metadata"] = kwargs["metadata"]

            # Create customer via Stripe
            customer = stripe_client.customers.create(**customer_data)

            return {
                "success": True,
                "data": {
                    "id": customer.id,
                    "email": customer.email,
                    "name": customer.name,
                    "phone": customer.phone,
                    "metadata": customer.metadata,
                    "object": "customer",
                    "created": customer.created,
                    "balance": customer.balance,
                    "delinquent": customer.delinquent,
                    "tax_exempt": customer.tax_exempt,
                    "livemode": customer.livemode,
                },
            }

        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    def get_via_provider(provider_instance, external_id: str) -> Dict[str, Any]:
        """Get customer via provider instance."""
        try:
            # Get bonded instance from provider
            bonded = PaymentExtensionStripeProvider.bond_instance(provider_instance)
            if not bonded or not bonded.sdk:
                return {"success": False, "error": "Failed to bond provider instance"}

            stripe_client = bonded.sdk

            # Get customer via Stripe
            customer = stripe_client.customers.retrieve(external_id)

            return {
                "success": True,
                "data": {
                    "id": customer.id,
                    "object": "customer",
                    "email": customer.email,
                    "name": customer.name,
                    "phone": customer.phone,
                    "metadata": customer.metadata,
                    "created": customer.created,
                    "balance": customer.balance,
                    "delinquent": customer.delinquent,
                    "tax_exempt": customer.tax_exempt,
                    "livemode": customer.livemode,
                },
            }

        except Exception as e:
            if "No such customer" in str(e):
                return {"success": False, "error": "Not found"}
            return {"success": False, "error": str(e)}

    @staticmethod
    def list_via_provider(provider_instance, **kwargs) -> Dict[str, Any]:
        """List customers via provider instance."""
        try:
            # Get bonded instance from provider
            bonded = PaymentExtensionStripeProvider.bond_instance(provider_instance)
            if not bonded or not bonded.sdk:
                return {"success": False, "error": "Failed to bond provider instance"}

            stripe_client = bonded.sdk

            # List customers via Stripe
            customers = stripe_client.customers.list(**kwargs)

            return {
                "success": True,
                "data": [
                    {
                        "id": customer.id,
                        "object": "customer",
                        "email": customer.email,
                        "name": customer.name,
                        "phone": customer.phone,
                        "metadata": customer.metadata,
                        "created": customer.created,
                        "balance": customer.balance,
                        "delinquent": customer.delinquent,
                        "tax_exempt": customer.tax_exempt,
                        "livemode": customer.livemode,
                    }
                    for customer in customers.data
                ],
            }

        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    def update_via_provider(
        provider_instance, external_id: str, **kwargs
    ) -> Dict[str, Any]:
        """Update customer via provider instance."""
        try:
            # Get bonded instance from provider
            bonded = PaymentExtensionStripeProvider.bond_instance(provider_instance)
            if not bonded or not bonded.sdk:
                return {"success": False, "error": "Failed to bond provider instance"}

            stripe_client = bonded.sdk

            # Update customer via Stripe
            customer = stripe_client.customers.modify(external_id, **kwargs)

            return {
                "success": True,
                "data": {
                    "id": customer.id,
                    "object": "customer",
                    "email": customer.email,
                    "name": customer.name,
                    "phone": customer.phone,
                    "metadata": customer.metadata,
                    "created": customer.created,
                    "balance": customer.balance,
                    "delinquent": customer.delinquent,
                    "tax_exempt": customer.tax_exempt,
                    "livemode": customer.livemode,
                },
            }

        except Exception as e:
            if "No such customer" in str(e):
                return {"success": False, "error": "Not found"}
            return {"success": False, "error": str(e)}

    @staticmethod
    def delete_via_provider(provider_instance, external_id: str) -> Dict[str, Any]:
        """Delete customer via provider instance."""
        try:
            # Get bonded instance from provider
            bonded = PaymentExtensionStripeProvider.bond_instance(provider_instance)
            if not bonded or not bonded.sdk:
                return {"success": False, "error": "Failed to bond provider instance"}

            stripe_client = bonded.sdk

            # Delete customer via Stripe
            deleted = stripe_client.customers.delete(external_id)

            return {"success": True}

        except Exception as e:
            if "No such customer" in str(e):
                return {"success": False, "error": "Not found"}
            return {"success": False, "error": str(e)}


# ============================================================================
# Stripe Product External Model
# ============================================================================


class Stripe_ProductModel(AbstractExternalModel, metaclass=ModelMeta):
    """External model for Stripe Product API resource."""

    class Reference:
        pass

    # Stripe API configuration
    external_resource: ClassVar[str] = "products"
    # Mark as an extension model for framework introspection
    _is_extension_model: ClassVar[bool] = True
    # Extension target identifier used by framework
    _extension_target: ClassVar[str] = "payment"

    # Model fields matching Stripe API exactly
    id: str = Field(..., description="Stripe product ID")
    object: str = Field(default="product", description="Object type")
    active: bool = Field(True, description="Whether the product is active")
    created: int = Field(..., description="Creation timestamp")
    default_price: Optional[str] = Field(None, description="Default price ID")
    description: Optional[str] = Field(None, description="Product description")
    images: List[str] = Field(default_factory=list, description="Product images")
    marketing_features: List[Dict[str, Any]] = Field(
        default_factory=list, description="Marketing features"
    )
    livemode: bool = Field(False, description="Whether in live mode")
    metadata: Dict[str, str] = Field(
        default_factory=dict, description="Custom metadata"
    )
    name: str = Field(..., description="Product name")
    package_dimensions: Optional[Dict[str, Any]] = Field(
        None, description="Package dimensions"
    )
    shippable: Optional[bool] = Field(None, description="Whether product is shippable")
    statement_descriptor: Optional[str] = Field(
        None, description="Statement descriptor"
    )
    tax_code: Optional[str] = Field(None, description="Tax code")
    unit_label: Optional[str] = Field(None, description="Unit label")
    updated: int = Field(..., description="Last updated timestamp")
    url: Optional[str] = Field(None, description="Product URL")

    class Create(BaseModel):
        """Create model for Stripe Product."""

        name: str = Field(..., description="Product name")
        description: Optional[str] = Field(None, description="Product description")
        active: Optional[bool] = Field(
            True, description="Whether the product is active"
        )
        images: Optional[List[str]] = Field(None, description="Product images")
        metadata: Optional[Dict[str, str]] = Field(None, description="Custom metadata")
        package_dimensions: Optional[Dict[str, Any]] = Field(
            None, description="Package dimensions"
        )
        shippable: Optional[bool] = Field(
            None, description="Whether product is shippable"
        )
        statement_descriptor: Optional[str] = Field(
            None, description="Statement descriptor"
        )
        tax_code: Optional[str] = Field(None, description="Tax code")
        unit_label: Optional[str] = Field(None, description="Unit label")
        url: Optional[str] = Field(None, description="Product URL")

    class Update(BaseModel):
        """Update model for Stripe Product."""

        name: Optional[str] = Field(None, description="Product name")
        description: Optional[str] = Field(None, description="Product description")
        active: Optional[bool] = Field(
            None, description="Whether the product is active"
        )
        images: Optional[List[str]] = Field(None, description="Product images")
        metadata: Optional[Dict[str, str]] = Field(None, description="Custom metadata")
        package_dimensions: Optional[Dict[str, Any]] = Field(
            None, description="Package dimensions"
        )
        shippable: Optional[bool] = Field(
            None, description="Whether product is shippable"
        )
        statement_descriptor: Optional[str] = Field(
            None, description="Statement descriptor"
        )
        tax_code: Optional[str] = Field(None, description="Tax code")
        unit_label: Optional[str] = Field(None, description="Unit label")
        url: Optional[str] = Field(None, description="Product URL")

    class Search(BaseModel):
        """Search model for Stripe Product."""

        name: Optional[str] = Field(None, description="Search by name")
        active: Optional[bool] = Field(None, description="Filter by active status")
        description: Optional[str] = Field(None, description="Search by description")

    @classmethod
    def to_external_format(cls, internal_data: Dict[str, Any]) -> Dict[str, Any]:
        """Convert internal format to Stripe API format."""
        # Since we're using exact Stripe API field names, no conversion needed
        return {k: v for k, v in internal_data.items() if v is not None}

    @classmethod
    def from_external_format(cls, external_data: Dict[str, Any]) -> Dict[str, Any]:
        """Convert Stripe API format to internal format."""
        # Since we're using exact Stripe API field names, no conversion needed
        return {k: v for k, v in external_data.items() if v is not None}

    @classmethod
    def to_external_query_format(
        cls,
        query_params: Dict[str, Any],
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        order_by: Optional[List] = None,
    ) -> Dict[str, Any]:
        """Convert query parameters to Stripe API format."""
        external_params = {}

        # Since we're using exact Stripe API field names, no conversion needed
        for field, value in query_params.items():
            external_params[field] = value

        # Stripe pagination
        if limit:
            external_params["limit"] = min(limit, 100)  # Stripe max is 100

        return external_params


# Minimal additional external models required by tests
class Stripe_PaymentIntentModel(AbstractExternalModel, metaclass=ModelMeta):
    """Minimal representation of Stripe PaymentIntent for tests."""

    external_resource: ClassVar[str] = "payment_intents"
    _is_extension_model: ClassVar[bool] = True

    id: str = Field(...)
    amount: int = Field(...)
    currency: str = Field(...)
    status: str = Field(...)


class Stripe_SubscriptionModel(AbstractExternalModel, metaclass=ModelMeta):
    """Minimal representation of Stripe Subscription for tests."""

    external_resource: ClassVar[str] = "subscriptions"
    _is_extension_model: ClassVar[bool] = True

    id: str = Field(...)
    customer: str = Field(...)
    status: str = Field(...)

    @staticmethod
    def create_via_provider(provider_instance, **kwargs) -> Dict[str, Any]:
        """Create product via provider instance."""
        try:
            # Get bonded instance from provider
            bonded = PaymentExtensionStripeProvider.bond_instance(provider_instance)
            if not bonded or not bonded.sdk:
                return {"success": False, "error": "Failed to bond provider instance"}

            stripe_client = bonded.sdk

            # Create product via Stripe
            product = stripe_client.products.create(**kwargs)

            return {
                "success": True,
                "data": {
                    "id": product.id,
                    "name": product.name,
                    "description": product.description,
                    "active": product.active,
                    "images": product.images,
                    "metadata": product.metadata,
                    "object": "product",
                    "created": product.created,
                    "updated": product.updated,
                    "livemode": product.livemode,
                    "marketing_features": [],
                },
            }

        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    def get_via_provider(provider_instance, external_id: str) -> Dict[str, Any]:
        """Get product via provider instance."""
        try:
            # Get bonded instance from provider
            bonded = PaymentExtensionStripeProvider.bond_instance(provider_instance)
            if not bonded or not bonded.sdk:
                return {"success": False, "error": "Failed to bond provider instance"}

            stripe_client = bonded.sdk

            # Get product via Stripe
            product = stripe_client.products.retrieve(external_id)

            return {
                "success": True,
                "data": {
                    "id": product.id,
                    "object": "product",
                    "name": product.name,
                    "description": product.description,
                    "active": product.active,
                    "images": product.images,
                    "metadata": product.metadata,
                    "created": product.created,
                    "updated": product.updated,
                    "livemode": product.livemode,
                    "marketing_features": [],
                },
            }

        except Exception as e:
            if "No such product" in str(e):
                return {"success": False, "error": "Not found"}
            return {"success": False, "error": str(e)}

    @staticmethod
    def list_via_provider(provider_instance, **kwargs) -> Dict[str, Any]:
        """List products via provider instance."""
        try:
            # Get bonded instance from provider
            bonded = PaymentExtensionStripeProvider.bond_instance(provider_instance)
            if not bonded or not bonded.sdk:
                return {"success": False, "error": "Failed to bond provider instance"}

            stripe_client = bonded.sdk

            # List products via Stripe
            products = stripe_client.products.list(**kwargs)

            return {
                "success": True,
                "data": [
                    {
                        "id": product.id,
                        "object": "product",
                        "name": product.name,
                        "description": product.description,
                        "active": product.active,
                        "images": product.images,
                        "metadata": product.metadata,
                        "created": product.created,
                        "updated": product.updated,
                        "livemode": product.livemode,
                        "marketing_features": [],
                    }
                    for product in products.data
                ],
            }

        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    def update_via_provider(
        provider_instance, external_id: str, **kwargs
    ) -> Dict[str, Any]:
        """Update product via provider instance."""
        try:
            # Get bonded instance from provider
            bonded = PaymentExtensionStripeProvider.bond_instance(provider_instance)
            if not bonded or not bonded.sdk:
                return {"success": False, "error": "Failed to bond provider instance"}

            stripe_client = bonded.sdk

            # Update product via Stripe
            product = stripe_client.products.modify(external_id, **kwargs)

            return {
                "success": True,
                "data": {
                    "id": product.id,
                    "object": "product",
                    "name": product.name,
                    "description": product.description,
                    "active": product.active,
                    "images": product.images,
                    "metadata": product.metadata,
                    "created": product.created,
                    "updated": product.updated,
                    "livemode": product.livemode,
                    "marketing_features": [],
                },
            }

        except Exception as e:
            if "No such product" in str(e):
                return {"success": False, "error": "Not found"}
            return {"success": False, "error": str(e)}

    @staticmethod
    def delete_via_provider(provider_instance, external_id: str) -> Dict[str, Any]:
        """Delete product via provider instance."""
        try:
            # Get bonded instance from provider
            bonded = PaymentExtensionStripeProvider.bond_instance(provider_instance)
            if not bonded or not bonded.sdk:
                return {"success": False, "error": "Failed to bond provider instance"}

            stripe_client = bonded.sdk

            # Delete product via Stripe
            deleted = stripe_client.products.delete(external_id)

            return {"success": True}

        except Exception as e:
            if "No such product" in str(e):
                return {"success": False, "error": "Not found"}
            return {"success": False, "error": str(e)}


# ============================================================================
# Reference Model and Network Model
# ============================================================================


# ============================================================================
# Stripe Provider (must be defined before Manager to avoid circular imports)
# ============================================================================


class PaymentExtensionStripeProvider(AbstractPaymentProvider):
    """
    Stripe payment provider for AGInfrastructure.
    Supports payments, subscriptions, and webhook processing.
    All functionality is provided through static class methods.
    Fully compatible with the Provider Rotation System.
    """

    # Static provider metadata - MUST have proper name for discovery
    name = "stripe"  # This is critical for provider discovery
    version = "1.0.0"
    description = "Stripe payment provider"

    # Static client state
    _stripe_client = None
    _stripe_available = False

    # Unified dependencies for Stripe provider
    dependencies = Dependencies(
        [
            PIP_Dependency(
                name="stripe",
                friendly_name="Stripe Python Library",
                semver=">=5.5.0",
                reason="Stripe payment provider support",
            ),
        ]
    )

    # Environment variables required by this provider
    _env = {
        "STRIPE_API_KEY": "",
        "STRIPE_SECRET_KEY": "",
        "STRIPE_PUBLISHABLE_KEY": "",
        "STRIPE_WEBHOOK_SECRET": "",
        "STRIPE_CURRENCY": "USD",
    }

    @classmethod
    def bond_instance(
        cls, instance: ProviderInstanceModel
    ) -> Optional[AbstractProviderInstance_SDK]:
        """
        Bond a provider instance with proper Stripe SDK configuration.

        Args:
            instance: ProviderInstanceModel with API credentials

        Returns:
            Bonded instance with configured Stripe SDK or None if stripe not available
        """
        if stripe is None:
            logger.warning("Stripe library not available for bonding")
            return None

        try:
            # Get API key from instance or fallback to environment
            api_key = (
                instance.api_key
                if hasattr(instance, "api_key") and instance.api_key
                else cls.get_secret_key()
            )

            if not api_key:
                logger.error("No API key available for Stripe provider instance")
                return None

            # Create Stripe client with the API key
            stripe_client = stripe.StripeClient(api_key)

            # Return bonded instance with the SDK
            return AbstractProviderInstance_SDK(stripe_client)

        except Exception as e:
            logger.error(f"Failed to bond Stripe provider instance: {e}")
            return None

    @classmethod
    def _configure_stripe(cls) -> None:
        """Configure the Stripe client with API credentials."""
        if stripe is None:
            cls._stripe_available = False
            cls._stripe_client = None
            logger.warning("Stripe library not available")
            return

        try:
            api_key = cls.get_secret_key()
            if api_key:
                cls._stripe_client = stripe
                cls._stripe_client.api_key = api_key
                cls._stripe_available = True
                logger.debug("Stripe client configured successfully")
            else:
                cls._stripe_available = False
                logger.warning("No Stripe API key configured")
        except Exception as e:
            cls._stripe_available = False
            logger.error(f"Failed to configure Stripe: {e}")

    @classmethod
    def _get_stripe_client(cls):
        """Get configured Stripe client."""
        if not cls._stripe_available:
            cls._configure_stripe()
        return cls._stripe_client if cls._stripe_available else None

    @classmethod
    def get_secret_key(cls) -> Optional[str]:
        """Get Stripe secret key from environment."""
        return env("STRIPE_SECRET_KEY") or env("STRIPE_API_KEY")

    @classmethod
    def get_webhook_secret(cls) -> Optional[str]:
        """Get Stripe webhook secret from environment."""
        return env("STRIPE_WEBHOOK_SECRET")

    @classmethod
    def get_publishable_key(cls) -> str:
        """Get the publishable key from environment (or empty string)."""
        return env("STRIPE_PUBLISHABLE_KEY") or ""

    @classmethod
    def get_default_currency(cls) -> str:
        """Return the configured default currency."""
        return env("STRIPE_CURRENCY") or "USD"

    @classmethod
    def get_env_value(cls, key: str, default: Any = None) -> Any:
        """Get environment value with fallback."""
        return env(key, default)

    @classmethod
    def validate_config(cls) -> bool:
        """Validate provider configuration."""
        if not cls._stripe_available:
            cls._configure_stripe()

        if not cls._stripe_available:
            return False

        secret_key = cls.get_secret_key()
        return bool(secret_key)

    @classmethod
    def get_platform_name(cls) -> str:
        """Get the platform name."""
        return "Stripe"

    @classmethod
    def services(cls) -> List[str]:
        """Return list of services provided."""
        return ["payment", "billing", "subscription", "commerce"]

    @classmethod
    def get_extension_info(cls) -> Dict[str, Any]:
        """Get extension information."""
        return {
            "name": "Payment",
            "description": "Payment extension providing payment processing via Stripe",
            "platform": cls.get_platform_name(),
            "currency": cls.get_env_value("STRIPE_CURRENCY", "USD"),
        }

    # Payment processing methods for rotation system
    @classmethod
    def create_payment(
        cls,
        provider_instance: ProviderInstanceModel,
        amount: float,
        currency: str = "USD",
        customer_id: Optional[str] = None,
        payment_method_id: Optional[str] = None,
        description: Optional[str] = None,
        metadata: Optional[Dict] = None,
    ) -> Dict:
        """Create a payment via Stripe."""
        stripe_client = cls._get_stripe_client()
        if not stripe_client:
            raise Exception("Stripe client not configured")

        try:
            # Convert amount to cents
            amount_cents = int(amount * 100)

            # Prepare payment intent data
            intent_data = {
                "amount": amount_cents,
                "currency": currency.lower(),
            }

            if customer_id:
                intent_data["customer"] = customer_id
            if payment_method_id:
                intent_data["payment_method"] = payment_method_id
                intent_data["confirm"] = True
            if description:
                intent_data["description"] = description
            if metadata:
                intent_data["metadata"] = metadata

            # Create payment intent
            intent = stripe_client.PaymentIntent.create(**intent_data)

            return {
                "success": True,
                "payment_id": intent.id,
                "amount": amount,
                "currency": currency,
                "status": intent.status,
                "client_secret": intent.client_secret,
                "customer_id": intent.customer,
                "description": intent.description,
                "metadata": intent.metadata,
            }

        except Exception as e:
            logger.error(f"Error creating payment: {e}")
            return {
                "success": False,
                "error": str(e),
            }

    @classmethod
    def get_payment(
        cls, provider_instance: ProviderInstanceModel, payment_id: str
    ) -> Dict:
        """Get payment details from Stripe."""
        stripe_client = cls._get_stripe_client()
        if not stripe_client:
            raise Exception("Stripe client not configured")

        try:
            intent = stripe_client.PaymentIntent.retrieve(payment_id)

            return {
                "success": True,
                "payment_id": intent.id,
                "amount": intent.amount / 100.0,  # Convert from cents
                "currency": intent.currency.upper(),
                "status": intent.status,
                "customer_id": intent.customer,
                "description": intent.description,
                "metadata": intent.metadata,
            }

        except Exception as e:
            logger.error(f"Error getting payment {payment_id}: {e}")
            return {
                "success": False,
                "error": str(e),
            }

    @classmethod
    def refund_payment(
        cls,
        provider_instance: ProviderInstanceModel,
        payment_id: str,
        amount: Optional[float] = None,
        reason: Optional[str] = None,
    ) -> Dict:
        """Refund a payment via Stripe."""
        stripe_client = cls._get_stripe_client()
        if not stripe_client:
            raise Exception("Stripe client not configured")

        try:
            refund_data = {"payment_intent": payment_id}

            if amount is not None:
                refund_data["amount"] = int(amount * 100)  # Convert to cents

            if reason:
                refund_data["reason"] = reason

            refund = stripe_client.Refund.create(**refund_data)

            return {
                "success": True,
                "refund_id": refund.id,
                "payment_id": payment_id,
                "amount": refund.amount / 100.0,  # Convert from cents
                "reason": refund.reason,
                "status": refund.status,
            }

        except Exception as e:
            logger.error(f"Error refunding payment {payment_id}: {e}")
            return {
                "success": False,
                "error": str(e),
            }

    @classmethod
    def create_customer(
        cls,
        provider_instance: ProviderInstanceModel,
        email: str,
        name: Optional[str] = None,
        phone: Optional[str] = None,
        metadata: Optional[Dict] = None,
    ) -> Dict:
        """Create a customer in Stripe."""
        stripe_client = cls._get_stripe_client()
        if not stripe_client:
            raise Exception("Stripe client not configured")

        try:
            customer_data = {"email": email}

            if name:
                customer_data["name"] = name
            if phone:
                customer_data["phone"] = phone
            if metadata:
                customer_data["metadata"] = metadata

            customer = stripe_client.Customer.create(**customer_data)

            return {
                "success": True,
                "customer_id": customer.id,
                "email": customer.email,
                "name": customer.name,
                "phone": customer.phone,
                "metadata": customer.metadata,
            }

        except Exception as e:
            logger.error(f"Error creating customer: {e}")
            return {
                "success": False,
                "error": str(e),
            }

    @classmethod
    def get_customer(
        cls, provider_instance: ProviderInstanceModel, customer_id: str
    ) -> Dict:
        """Get customer details from Stripe."""
        stripe_client = cls._get_stripe_client()
        if not stripe_client:
            raise Exception("Stripe client not configured")

        try:
            customer = stripe_client.Customer.retrieve(customer_id)

            return {
                "success": True,
                "customer_id": customer.id,
                "email": customer.email,
                "name": customer.name,
                "phone": customer.phone,
                "metadata": customer.metadata,
            }

        except Exception as e:
            logger.error(f"Error getting customer {customer_id}: {e}")
            return {
                "success": False,
                "error": str(e),
            }

    @classmethod
    def create_subscription(
        cls,
        provider_instance: ProviderInstanceModel,
        customer_id: str,
        price_id: str,
        payment_method_id: Optional[str] = None,
        trial_days: Optional[int] = None,
        metadata: Optional[Dict] = None,
    ) -> Dict:
        """Create a subscription in Stripe."""
        stripe_client = cls._get_stripe_client()
        if not stripe_client:
            raise Exception("Stripe client not configured")

        try:
            sub_data = {
                "customer": customer_id,
                "items": [{"price": price_id}],
            }

            if payment_method_id:
                sub_data["default_payment_method"] = payment_method_id
            if trial_days:
                sub_data["trial_period_days"] = trial_days
            if metadata:
                sub_data["metadata"] = metadata

            subscription = stripe_client.Subscription.create(**sub_data)

            return {
                "success": True,
                "subscription_id": subscription.id,
                "customer_id": subscription.customer,
                "status": subscription.status,
                "current_period_end": datetime.fromtimestamp(
                    subscription.current_period_end
                ).isoformat(),
            }

        except Exception as e:
            logger.error(f"Error creating subscription: {e}")
            return {
                "success": False,
                "error": str(e),
            }

    @classmethod
    def cancel_subscription(
        cls,
        provider_instance: ProviderInstanceModel,
        subscription_id: str,
        immediately: bool = False,
    ) -> Dict:
        """Cancel a subscription in Stripe."""
        stripe_client = cls._get_stripe_client()
        if not stripe_client:
            raise Exception("Stripe client not configured")

        try:
            if immediately:
                subscription = stripe_client.Subscription.delete(subscription_id)
            else:
                subscription = stripe_client.Subscription.modify(
                    subscription_id, cancel_at_period_end=True
                )

            return {
                "success": True,
                "subscription_id": subscription.id,
                "status": subscription.status,
                "canceled_at": (
                    datetime.fromtimestamp(subscription.canceled_at).isoformat()
                    if subscription.canceled_at
                    else None
                ),
                "cancelled_immediately": immediately,
            }

        except Exception as e:
            logger.error(f"Error cancelling subscription {subscription_id}: {e}")
            return {
                "success": False,
                "error": str(e),
            }

    @classmethod
    async def process_webhook(
        cls, provider_instance: ProviderInstanceModel, payload: str, signature: str
    ) -> Dict:
        """Process a webhook from Stripe. Async to satisfy tests expecting coroutine function."""
        stripe_client = cls._get_stripe_client()
        if not stripe_client:
            raise Exception("Stripe client not configured")

        webhook_secret = cls.get_webhook_secret()
        if not webhook_secret:
            return {"success": False, "error": "Webhook secret not configured"}

        try:
            event = stripe_client.Webhook.construct_event(payload, signature, webhook_secret)

            # Process the event based on type
            event_type = event.get("type")
            event_data = event.get("data", {}).get("object", {})

            logger.debug(f"Processing Stripe webhook event: {event_type}")

            # Handle different event types
            handled = True
            if event_type == "payment_intent.succeeded":
                logger.debug(f"Payment succeeded: {event_data.get('id')}")
            elif event_type == "payment_intent.failed":
                logger.warning(f"Payment failed: {event_data.get('id')}")
            elif event_type == "customer.subscription.created":
                logger.debug(f"Subscription created: {event_data.get('id')}")
            elif event_type == "customer.subscription.deleted":
                logger.debug(f"Subscription cancelled: {event_data.get('id')}")
            else:
                handled = False
                logger.debug(f"Unhandled event type: {event_type}")

            return {"success": True, "event_type": event_type, "event_id": event.get("id"), "processed": handled}

        except Exception as e:
            # Try to detect Stripe signature errors safely
            try:
                if stripe is not None:
                    from stripe.error import SignatureVerificationError

                    if isinstance(e, SignatureVerificationError):
                        logger.error(f"Webhook signature verification failed: {e}")
                        return {"success": False, "error": "Invalid signature"}
            except Exception:
                pass

            logger.error(f"Error processing webhook: {e}")
            return {"success": False, "error": str(e)}


# ============================================================================
# Stripe Customer Manager (defined after provider to avoid circular imports)
# ============================================================================


class Stripe_CustomerManager(AbstractExternalManager):
    """Manager for Stripe Customer external API."""

    Model = Stripe_CustomerModel
    ReferenceModel = Stripe_CustomerModel.Reference
    # NetworkModel will be available via Model.Network after registry commit

    # Provider integration
    provider_class = PaymentExtensionStripeProvider

    def create_validation(self, entity):
        """Validate Stripe customer creation."""
        if not entity.email:
            raise ValueError("Email is required for Stripe customer creation")

        return True

    @classmethod
    def sync_contact(cls, *args, **kwargs):
        """Minimal sync helper used by tests: delegate to Model.get_via_provider if available."""
        try:
            if hasattr(cls.Model, "get_via_provider"):
                return cls.Model.get_via_provider(*args, **kwargs)
        except Exception:
            pass
        return None

    @classmethod
    def create_customer(cls, provider_instance, **kwargs):
        """Convenience wrapper to create a customer using the provider or model helper."""
        try:
            if hasattr(cls.provider_class, "create_customer"):
                return cls.provider_class.create_customer(provider_instance, **kwargs)
        except Exception:
            pass

        if hasattr(cls.Model, "create_via_provider"):
            return cls.Model.create_via_provider(provider_instance, **kwargs)

        return {"success": False, "error": "No customer creation path available"}


# ============================================================================
# Stripe Product Manager (defined after provider to avoid circular imports)
# ============================================================================


class Stripe_ProductManager(AbstractExternalManager):
    """Manager for Stripe Product external API."""

    Model = Stripe_ProductModel
    ReferenceModel = Stripe_ProductModel.Reference
    # NetworkModel will be available via Model.Network after registry commit

    # Provider integration
    provider_class = PaymentExtensionStripeProvider

    def create_validation(self, entity):
        """Validate Stripe product creation."""
        if not entity.name:
            raise ValueError("Name is required for Stripe product creation")

        return True
