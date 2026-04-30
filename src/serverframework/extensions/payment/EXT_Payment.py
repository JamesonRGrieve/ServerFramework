"""
Payment extension for AGInfrastructure.
Implements the Provider Rotation System for payment processing.
"""

from abc import abstractmethod
from decimal import Decimal
from typing import Any, ClassVar, Dict, List, Optional

from serverframework.extensions.AbstractExtensionProvider import (
    AbstractProviderInstance,
    AbstractStaticExtension,
    AbstractStaticProvider,
    ExtensionType,
    ability,
)
from serverframework.logic.BLL_Providers import ProviderInstanceModel


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
