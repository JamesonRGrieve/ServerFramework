"""
Payment extension for AGInfrastructure.
Implements the Provider Rotation System for payment processing.
"""

from abc import abstractmethod
from decimal import Decimal
from typing import Any, ClassVar, Dict, List, Optional

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractProviderInstance,
    AbstractStaticExtension,
    AbstractStaticProvider,
    ExtensionType,
    ability,
)
from zephyrex.logic.BLL_Providers import ProviderInstanceModel


class AbstractPaymentProvider(AbstractStaticProvider):
    """
    Abstract base class for payment service providers.
    Defines the common interface for all payment providers with static functionality.
    All payment providers should be static/abstract classes with no instantiation required.
    Integrates with the Provider Rotation System for failover and load balancing.
    """

    extension_type: ClassVar[str] = "payment"

    @classmethod
    @abstractmethod
    def services(cls) -> List[str]:
        """Return a list of services provided by this provider."""
        pass

    @classmethod
    @abstractmethod
    def get_platform_name(cls) -> str:
        """Get the name of the payment platform this provider interacts with."""
        pass

    @classmethod
    def get_extension_info(cls) -> Dict[str, Any]:
        """Get information about the payment extension."""
        return {
            "name": "Payment",
            "description": f"Payment extension for {cls.get_platform_name()}",
        }

    @classmethod
    @abstractmethod
    def bond_instance(cls, instance: ProviderInstanceModel) -> AbstractProviderInstance:
        """Bond a provider instance for API operations."""
        pass

    # Abstract abilities - must be implemented by providers
    @classmethod
    @abstractmethod
    @ability(name="payment_create")
    async def create_payment(
        cls,
        bonded_instance: AbstractProviderInstance,
        amount: Decimal,
        currency: str,
        customer_id: Optional[str] = None,
        description: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create a payment/charge."""
        pass

    @classmethod
    @abstractmethod
    @ability(name="payment_capture")
    async def capture_payment(
        cls,
        bonded_instance: AbstractProviderInstance,
        payment_id: str,
        amount: Optional[Decimal] = None,
    ) -> Dict[str, Any]:
        """Capture a previously authorized payment."""
        pass

    @classmethod
    @abstractmethod
    @ability(name="payment_refund")
    async def refund_payment(
        cls,
        bonded_instance: AbstractProviderInstance,
        payment_id: str,
        amount: Optional[Decimal] = None,
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Refund a payment."""
        pass

    @classmethod
    @abstractmethod
    @ability(name="customer_create")
    async def create_customer(
        cls,
        bonded_instance: AbstractProviderInstance,
        email: str,
        name: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create a customer."""
        pass

    @classmethod
    @abstractmethod
    @ability(name="subscription_create")
    async def create_subscription(
        cls,
        bonded_instance: AbstractProviderInstance,
        customer_id: str,
        plan_id: str,
        trial_days: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create a subscription."""
        pass

    @classmethod
    @abstractmethod
    @ability(name="subscription_cancel")
    async def cancel_subscription(
        cls,
        bonded_instance: AbstractProviderInstance,
        subscription_id: str,
        at_period_end: bool = True,
    ) -> Dict[str, Any]:
        """Cancel a subscription."""
        pass

    @classmethod
    @abstractmethod
    @ability(name="webhook_process")
    async def process_webhook(
        cls,
        bonded_instance: AbstractProviderInstance,
        payload: bytes,
        signature: str,
    ) -> Dict[str, Any]:
        """Process a webhook from the payment provider."""
        pass


class EXT_Payment(AbstractStaticExtension):
    """
    Payment extension for AGInfrastructure.

    Provides payment abilities including Stripe, PayPal, Square, and other payment
    providers. This extension uses the Provider Rotation System for failover and
    load balancing across multiple payment providers.

    The extension focuses on:
    - Payment processing and transaction management through provider rotation
    - Customer and subscription lifecycle management
    - Webhook processing for payment events
    - Integration with multiple payment providers via rotation system
    - Database schema extensions for payment data

    Usage:
        # Process payment using rotation system
        result = EXT_Payment.root.rotate(
            EXT_Payment.create_payment,
            amount=Decimal("50.00"),
            currency="USD",
            customer_id="cus_123"
        )
    """

    name: str = "payment"
    friendly_name: str = "Payment Processing"
    version: str = "1.0.0"
    description: str = (
        "Payment extension providing comprehensive payment processing abilities via Provider Rotation System"
    )
    types = {ExtensionType.DATABASE, ExtensionType.EXTERNAL}
    AbstractProvider = AbstractPaymentProvider

    # Item 16 — federation matrix descriptors. The framework's programmatic
    # test generator picks these up at collection time and emits one
    # ``Test_Federation_EXT_Payment_<Type>_Matrix`` class per declared
    # external type. The schema snapshot is loaded lazily so a missing
    # snapshot doesn't break extension import.
    @classmethod
    def federation_matrix_fixtures(cls):
        """Return :class:`FederationFixture` instances for matrix coverage.

        Each fixture exercises a single Stripe upstream type through the
        4-quadrant matrix. By default we use the bundled snapshot under
        ``contracts/stripe.openapi.json`` and a deterministic in-process
        upstream so the matrix runs in CI without credentials. Real-upstream
        runs are gated on ``STRIPE_API_KEY`` per ``EXT.Test.External.md``.
        """

        from zephyrex.extensions.AbstractFederationMatrixTest import (
            FederationFixture,
        )
        from zephyrex.lib.Environment import env

        # Minimal in-process spec covering Stripe's most-used resource.
        # Real coverage attaches when the extension's snapshot is committed.
        spec = {
            "components": {
                "schemas": {
                    "Customer": {
                        "type": "object",
                        "required": ["id"],
                        "properties": {
                            "id": {"type": "string"},
                            "email": {"type": "string"},
                            "name": {"type": "string"},
                        },
                    }
                }
            },
            "paths": {
                "/v1/customers/{id}": {
                    "get": {
                        "operationId": "get_customer",
                        "parameters": [{"name": "id", "in": "path"}],
                    }
                },
                "/v1/customers": {"get": {"operationId": "list_customer"}},
            },
        }

        return [
            FederationFixture(
                name="EXT_Payment.Customer",
                upstream_kind="rest",
                transport=cls._build_in_process_transport(spec),
                sample_id="cus_test",
                type_name="Customer",
                sdl_or_spec=spec,
                operations_supported=["get", "list"],
                crud_map={"get": "get_customer", "list": "list_customer"},
                requires_credentials=False,  # in-process upstream
                credentials_present=lambda: bool(env("STRIPE_API_KEY")),
            )
        ]

    @staticmethod
    def _build_in_process_transport(spec):
        """Build a deterministic in-process REST upstream for matrix tests."""

        import httpx
        from fastapi import FastAPI

        from zephyrex.extensions.federation.BLL_Federation_REST import (
            RESTUpstreamTransport,
            openapi_to_pydantic_models,
        )

        SEED = {
            "cus_test": {"id": "cus_test", "email": "test@x.com", "name": "Test"},
        }
        app = FastAPI()

        @app.get("/v1/customers/{cid}")
        async def _get(cid: str):
            return SEED.get(cid)

        @app.get("/v1/customers")
        async def _list():
            return list(SEED.values())

        sync_client = httpx.Client(
            transport=httpx.ASGITransport(app=app), base_url="http://stripe-test"
        )

        class _SyncHTTP:
            def get(self, url, **kw):
                return sync_client.get(url, params=kw.get("params") or None).json()

            def post(self, url, **kw):
                return sync_client.post(url, json=kw.get("json")).json()

            def put(self, url, **kw):
                return sync_client.put(url, json=kw.get("json")).json()

            def patch(self, url, **kw):
                return sync_client.patch(url, json=kw.get("json")).json()

            def delete(self, url, **kw):
                return sync_client.delete(url).json()

        operations = openapi_to_pydantic_models(spec).operations
        return RESTUpstreamTransport(
            _SyncHTTP(), base_url="http://stripe-test", operations=operations
        )
