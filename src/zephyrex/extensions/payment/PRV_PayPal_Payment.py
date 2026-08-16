# SPDX-License-Identifier: AGPL-3.0-or-later
"""PayPal payment provider for zephyrex.

Provides payment processing, customer management, and webhook handling
through PayPal's REST API. Fully static implementation compatible with
the Provider Rotation System.
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict, List, Optional

try:
    import paypalrestsdk
except ImportError:
    paypalrestsdk = None  # type: ignore[assignment]
    import warnings

    warnings.warn(
        "PayPal package currently missing, but in PIP_Dependencies, will likely install on run",
        ImportWarning,
    )

from pydantic import BaseModel, Field

from zephyrex.extensions.AbstractExtensionProvider import AbstractProviderInstance_SDK
from zephyrex.extensions.AbstractExternalModel import (
    AbstractExternalManager,
    AbstractExternalModel,
)
from zephyrex.extensions.payment.EXT_Payment import AbstractPaymentProvider
from zephyrex.lib.Dependencies import Dependencies, PIP_Dependency
from zephyrex.lib.Environment import env
from zephyrex.lib.Logging import logger
from zephyrex.lib.Pydantic import BaseModel  # type: ignore[no-redef]
from zephyrex.logic.AbstractLogicManager import ModelMeta
from zephyrex.logic.BLL_Providers import ProviderInstanceModel


# ============================================================================
# PayPal Customer External Model
# ============================================================================


class PayPal_CustomerModel(AbstractExternalModel, metaclass=ModelMeta):
    """External model for PayPal Customer (payer) resource.

    PayPal doesn't have a first-class customer object in the same way
    Stripe/Square do. This model maps to the payer concept used across
    PayPal orders and billing agreements.
    """

    class Reference:
        pass

    external_resource: ClassVar[str] = "customers"
    _is_extension_model: ClassVar[bool] = True
    _extension_target: ClassVar[str] = "payment"

    id: str = Field(..., description="PayPal payer/customer ID")
    email: Optional[str] = Field(None, description="Payer email")
    given_name: Optional[str] = Field(None, description="Payer first name")
    surname: Optional[str] = Field(None, description="Payer last name")
    phone: Optional[str] = Field(None, description="Payer phone")
    payer_id: Optional[str] = Field(None, description="PayPal payer ID")

    class Create(BaseModel):
        """Create model for PayPal Customer."""

        email: str = Field(..., description="Customer email")
        given_name: Optional[str] = Field(None, description="First name")
        surname: Optional[str] = Field(None, description="Last name")
        phone: Optional[str] = Field(None, description="Phone number")

    class Update(BaseModel):
        """Update model for PayPal Customer."""

        email: Optional[str] = Field(None, description="Customer email")
        given_name: Optional[str] = Field(None, description="First name")
        surname: Optional[str] = Field(None, description="Last name")

    class Search(BaseModel):
        """Search model for PayPal Customer."""

        email: Optional[str] = Field(None, description="Search by email")

    @classmethod
    def to_external_format(cls, internal_data: Dict[str, Any]) -> Dict[str, Any]:
        return {k: v for k, v in internal_data.items() if v is not None}

    @classmethod
    def from_external_format(cls, external_data: Dict[str, Any]) -> Dict[str, Any]:
        return {k: v for k, v in external_data.items() if v is not None}

    @classmethod
    def to_external_query_format(
        cls,
        query_params: Dict[str, Any],
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        order_by: Optional[List] = None,
    ) -> Dict[str, Any]:
        external_params = dict(query_params)
        if limit:
            external_params["page_size"] = min(limit, 20)
        if offset:
            external_params["page"] = (offset // (limit or 20)) + 1
        return external_params


# ============================================================================
# PayPal Order External Model
# ============================================================================


class PayPal_OrderModel(AbstractExternalModel, metaclass=ModelMeta):
    """External model for PayPal Order resource."""

    external_resource: ClassVar[str] = "orders"
    _is_extension_model: ClassVar[bool] = True

    id: str = Field(...)
    status: str = Field(...)
    intent: Optional[str] = Field(None)
    purchase_units: Optional[List[Dict[str, Any]]] = Field(None)


class PayPal_SubscriptionModel(AbstractExternalModel, metaclass=ModelMeta):
    """External model for PayPal Billing Subscription resource."""

    external_resource: ClassVar[str] = "subscriptions"
    _is_extension_model: ClassVar[bool] = True

    id: str = Field(...)
    plan_id: str = Field(...)
    status: str = Field(...)
    subscriber: Optional[Dict[str, Any]] = Field(None)


# ============================================================================
# PayPal Provider
# ============================================================================


class PaymentExtensionPayPalProvider(AbstractPaymentProvider):
    """PayPal payment provider for zephyrex.

    Supports orders (payments), billing subscriptions, and webhook
    processing. Fully compatible with the Provider Rotation System.
    """

    name = "paypal"
    version = "1.0.0"
    description = "PayPal payment provider"

    _paypal_configured = False

    dependencies = Dependencies(
        [
            PIP_Dependency(
                name="paypalrestsdk",
                friendly_name="PayPal REST SDK",
                semver=">=1.13.0",
                reason="PayPal payment provider support",
            ),
        ]
    )

    _env = {
        "PAYPAL_CLIENT_ID": "",
        "PAYPAL_SECRET": "",
        "PAYPAL_WEBHOOK_ID": "",
        "PAYPAL_MODE": "sandbox",
        "PAYPAL_CURRENCY": "USD",
    }

    @classmethod
    def bond_instance(
        cls, instance: ProviderInstanceModel
    ) -> Optional[AbstractProviderInstance_SDK]:
        if paypalrestsdk is None:
            logger.warning("PayPal library not available for bonding")
            return None
        try:
            client_id = cls.get_client_id()
            client_secret = (
                instance.api_key
                if hasattr(instance, "api_key") and instance.api_key
                else cls.get_client_secret()
            )
            if not client_id or not client_secret:
                logger.error("No credentials available for PayPal provider instance")
                return None
            mode = env("PAYPAL_MODE") or "sandbox"
            api = paypalrestsdk.Api(
                {
                    "mode": mode,
                    "client_id": client_id,
                    "client_secret": client_secret,
                }
            )
            return AbstractProviderInstance_SDK(api)
        except Exception as e:
            logger.error("Failed to bond PayPal provider instance: %s", e)
            return None

    @classmethod
    def _configure_paypal(cls) -> None:
        if paypalrestsdk is None:
            cls._paypal_configured = False
            return
        try:
            client_id = cls.get_client_id()
            client_secret = cls.get_client_secret()
            if client_id and client_secret:
                mode = env("PAYPAL_MODE") or "sandbox"
                paypalrestsdk.configure(
                    {
                        "mode": mode,
                        "client_id": client_id,
                        "client_secret": client_secret,
                    }
                )
                cls._paypal_configured = True
            else:
                cls._paypal_configured = False
        except Exception as e:
            cls._paypal_configured = False
            logger.error("Failed to configure PayPal: %s", e)

    @classmethod
    def get_client_id(cls) -> Optional[str]:
        return env("PAYPAL_CLIENT_ID")  # type: ignore[no-any-return]

    @classmethod
    def get_client_secret(cls) -> Optional[str]:
        return env("PAYPAL_SECRET")  # type: ignore[no-any-return]

    @classmethod
    def get_webhook_id(cls) -> Optional[str]:
        return env("PAYPAL_WEBHOOK_ID")  # type: ignore[no-any-return]

    @classmethod
    def get_default_currency(cls) -> str:
        return env("PAYPAL_CURRENCY") or "USD"

    @classmethod
    def get_env_value(cls, key: str, default: Any = None) -> Any:
        return env(key, default)

    @classmethod
    def validate_config(cls) -> bool:
        if not cls._paypal_configured:
            cls._configure_paypal()
        return cls._paypal_configured

    @classmethod
    def get_platform_name(cls) -> str:
        return "PayPal"

    @classmethod
    def services(cls) -> List[str]:
        return ["payment", "subscription", "commerce"]

    @classmethod
    def get_extension_info(cls) -> Dict[str, Any]:
        return {
            "name": "Payment",
            "description": "Payment extension providing payment processing via PayPal",
            "platform": cls.get_platform_name(),
            "currency": cls.get_env_value("PAYPAL_CURRENCY", "USD"),
        }

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
        if not cls._paypal_configured:
            cls._configure_paypal()
        if not cls._paypal_configured:
            raise Exception("PayPal client not configured")
        try:
            payment = paypalrestsdk.Payment(
                {
                    "intent": "sale",
                    "payer": {"payment_method": "paypal"},
                    "transactions": [
                        {
                            "amount": {
                                "total": f"{amount:.2f}",
                                "currency": currency.upper(),
                            },
                            "description": description or "",
                        }
                    ],
                    "redirect_urls": {
                        "return_url": "https://example.com/return",
                        "cancel_url": "https://example.com/cancel",
                    },
                }
            )
            if payment.create():
                return {
                    "success": True,
                    "payment_id": payment.id,
                    "amount": amount,
                    "currency": currency,
                    "status": payment.state,
                }
            return {"success": False, "error": payment.error}
        except Exception as e:
            logger.error("Error creating PayPal payment: %s", e)
            return {"success": False, "error": str(e)}

    @classmethod
    def get_payment(
        cls, provider_instance: ProviderInstanceModel, payment_id: str
    ) -> Dict:
        if not cls._paypal_configured:
            cls._configure_paypal()
        if not cls._paypal_configured:
            raise Exception("PayPal client not configured")
        try:
            payment = paypalrestsdk.Payment.find(payment_id)
            transactions = payment.transactions or []
            amount_data = transactions[0].amount if transactions else {}
            return {
                "success": True,
                "payment_id": payment.id,
                "amount": float(getattr(amount_data, "total", 0)),
                "currency": getattr(amount_data, "currency", "USD"),
                "status": payment.state,
            }
        except Exception as e:
            logger.error("Error getting PayPal payment %s: %s", payment_id, e)
            return {"success": False, "error": str(e)}

    @classmethod
    def refund_payment(
        cls,
        provider_instance: ProviderInstanceModel,
        payment_id: str,
        amount: Optional[float] = None,
        reason: Optional[str] = None,
    ) -> Dict:
        if not cls._paypal_configured:
            cls._configure_paypal()
        if not cls._paypal_configured:
            raise Exception("PayPal client not configured")
        try:
            payment = paypalrestsdk.Payment.find(payment_id)
            sale_id = None
            for txn in payment.transactions or []:
                for resource in getattr(txn, "related_resources", []):
                    if hasattr(resource, "sale"):
                        sale_id = resource.sale.id
                        break
                if sale_id:
                    break
            if not sale_id:
                return {"success": False, "error": "No sale found for refund"}
            sale = paypalrestsdk.Sale.find(sale_id)
            refund_data: Dict[str, Any] = {}
            if amount is not None:
                refund_data["amount"] = {
                    "total": f"{amount:.2f}",
                    "currency": cls.get_default_currency(),
                }
            refund = sale.refund(refund_data)
            if refund.success():
                return {
                    "success": True,
                    "refund_id": refund.id,
                    "payment_id": payment_id,
                    "amount": amount,
                    "reason": reason,
                    "status": refund.state,
                }
            return {"success": False, "error": str(refund.error)}
        except Exception as e:
            logger.error("Error refunding PayPal payment %s: %s", payment_id, e)
            return {"success": False, "error": str(e)}

    @classmethod
    def create_customer(
        cls,
        provider_instance: ProviderInstanceModel,
        email: str,
        name: Optional[str] = None,
        phone: Optional[str] = None,
        metadata: Optional[Dict] = None,
    ) -> Dict:
        """Create a customer record.

        PayPal doesn't have a standalone customer-create API in the REST
        SDK v1. We synthesize a local customer reference from the provided
        data; the real PayPal payer_id is captured when the customer
        completes their first payment approval.
        """
        import uuid

        customer_id = f"PAYPAL-{uuid.uuid4().hex[:12].upper()}"
        parts = (name or "").split(" ", 1)
        return {
            "success": True,
            "customer_id": customer_id,
            "email": email,
            "name": name,
            "phone": phone,
            "given_name": parts[0] if parts else None,
            "surname": parts[1] if len(parts) > 1 else None,
        }

    @classmethod
    def get_customer(
        cls, provider_instance: ProviderInstanceModel, customer_id: str
    ) -> Dict:
        return {
            "success": True,
            "customer_id": customer_id,
            "email": None,
            "name": None,
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
        if not cls._paypal_configured:
            cls._configure_paypal()
        if not cls._paypal_configured:
            raise Exception("PayPal client not configured")
        try:
            billing_agreement = paypalrestsdk.BillingAgreement(
                {
                    "name": f"Subscription for {customer_id}",
                    "description": "Billing subscription",
                    "start_date": "2025-01-01T00:00:00Z",
                    "plan": {"id": price_id},
                    "payer": {"payment_method": "paypal"},
                }
            )
            if billing_agreement.create():
                return {
                    "success": True,
                    "subscription_id": billing_agreement.id,
                    "customer_id": customer_id,
                    "status": billing_agreement.state,
                }
            return {"success": False, "error": billing_agreement.error}
        except Exception as e:
            logger.error("Error creating PayPal subscription: %s", e)
            return {"success": False, "error": str(e)}

    @classmethod
    def cancel_subscription(
        cls,
        provider_instance: ProviderInstanceModel,
        subscription_id: str,
        immediately: bool = False,
    ) -> Dict:
        if not cls._paypal_configured:
            cls._configure_paypal()
        if not cls._paypal_configured:
            raise Exception("PayPal client not configured")
        try:
            agreement = paypalrestsdk.BillingAgreement.find(subscription_id)
            note = {"note": "Cancelling subscription"}
            if agreement.cancel(note):
                return {
                    "success": True,
                    "subscription_id": subscription_id,
                    "status": "cancelled",
                    "cancelled_immediately": immediately,
                }
            return {"success": False, "error": "Failed to cancel subscription"}
        except Exception as e:
            logger.error(
                "Error cancelling PayPal subscription %s: %s", subscription_id, e
            )
            return {"success": False, "error": str(e)}

    @classmethod
    async def process_webhook(
        cls, provider_instance: ProviderInstanceModel, payload: str, signature: str
    ) -> Dict:
        """Process a webhook from PayPal."""
        if not signature:
            raise Exception("Webhook signature missing — cannot verify authenticity")
        webhook_id = cls.get_webhook_id()
        if not webhook_id:
            return {"success": False, "error": "Webhook ID not configured"}
        try:
            import json

            event = json.loads(
                payload if isinstance(payload, str) else payload.decode()
            )
            event_type = event.get("event_type", "unknown")
            return {
                "success": True,
                "event_type": event_type,
                "event_id": event.get("id"),
                "processed": True,
            }
        except Exception as e:
            logger.error("Error processing PayPal webhook: %s", e)
            return {"success": False, "error": str(e)}


# ============================================================================
# PayPal Customer Manager
# ============================================================================


class PayPal_CustomerManager(AbstractExternalManager):
    """Manager for PayPal Customer external API."""

    Model = PayPal_CustomerModel
    ReferenceModel = PayPal_CustomerModel.Reference
    provider_class = PaymentExtensionPayPalProvider

    def create_validation(self, entity):
        if not getattr(entity, "email", None):
            raise ValueError("Email is required for PayPal customer creation")
        return True

    @classmethod
    def sync_contact(cls, *args, **kwargs):
        try:
            if hasattr(cls.provider_class, "get_customer"):
                return cls.provider_class.get_customer(*args, **kwargs)
        except Exception:
            pass
        return None

    @classmethod
    def create_customer(cls, provider_instance, **kwargs):
        try:
            if hasattr(cls.provider_class, "create_customer"):
                return cls.provider_class.create_customer(provider_instance, **kwargs)
        except Exception:
            pass
        return {"success": False, "error": "No customer creation path available"}
