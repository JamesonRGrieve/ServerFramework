# SPDX-License-Identifier: AGPL-3.0-or-later
"""Square payment provider for zephyrex.

Provides payment processing, customer management, and webhook handling
through Square's API. Fully static implementation compatible with the
Provider Rotation System.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar, Dict, List, Optional

try:
    from square.client import Client as SquareClient
except ImportError:
    SquareClient = None  # type: ignore[assignment, misc]
    import warnings

    warnings.warn(
        "Square package currently missing, but in PIP_Dependencies, will likely install on run",
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
# Square Customer External Model
# ============================================================================


class Square_CustomerModel(AbstractExternalModel, metaclass=ModelMeta):
    """External model for Square Customer API resource."""

    class Reference:
        pass

    external_resource: ClassVar[str] = "customers"
    _is_extension_model: ClassVar[bool] = True
    _extension_target: ClassVar[str] = "payment"

    id: str = Field(..., description="Square customer ID")
    given_name: Optional[str] = Field(None, description="Customer first name")
    family_name: Optional[str] = Field(None, description="Customer last name")
    email_address: Optional[str] = Field(None, description="Customer email")
    phone_number: Optional[str] = Field(None, description="Customer phone")
    company_name: Optional[str] = Field(None, description="Company name")
    note: Optional[str] = Field(None, description="Customer note")
    reference_id: Optional[str] = Field(None, description="External reference ID")
    created_at: Optional[str] = Field(None, description="Creation timestamp")
    updated_at: Optional[str] = Field(None, description="Last update timestamp")

    class Create(BaseModel):
        """Create model for Square Customer."""

        email_address: Optional[str] = Field(None, description="Customer email")
        given_name: Optional[str] = Field(None, description="Customer first name")
        family_name: Optional[str] = Field(None, description="Customer last name")
        phone_number: Optional[str] = Field(None, description="Customer phone")
        company_name: Optional[str] = Field(None, description="Company name")
        note: Optional[str] = Field(None, description="Customer note")
        reference_id: Optional[str] = Field(None, description="External reference ID")

    class Update(BaseModel):
        """Update model for Square Customer."""

        email_address: Optional[str] = Field(None, description="Customer email")
        given_name: Optional[str] = Field(None, description="Customer first name")
        family_name: Optional[str] = Field(None, description="Customer last name")
        phone_number: Optional[str] = Field(None, description="Customer phone")
        company_name: Optional[str] = Field(None, description="Company name")
        note: Optional[str] = Field(None, description="Customer note")

    class Search(BaseModel):
        """Search model for Square Customer."""

        email_address: Optional[str] = Field(None, description="Search by email")
        phone_number: Optional[str] = Field(None, description="Search by phone")

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
        external_params = {}
        for field, value in query_params.items():
            external_params[field] = value
        if limit:
            external_params["limit"] = min(limit, 100)
        return external_params

    @staticmethod
    def create_via_provider(provider_instance, **kwargs) -> Dict[str, Any]:
        try:
            bonded = PaymentExtensionSquareProvider.bond_instance(provider_instance)
            if not bonded or not bonded.sdk:
                return {"success": False, "error": "Failed to bond provider instance"}
            result = bonded.sdk.customers.create_customer(body=kwargs)
            if result.is_success():
                customer = result.body.get("customer", {})
                return {"success": True, "data": customer}
            return {"success": False, "error": str(result.errors)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    def get_via_provider(provider_instance, external_id: str) -> Dict[str, Any]:
        try:
            bonded = PaymentExtensionSquareProvider.bond_instance(provider_instance)
            if not bonded or not bonded.sdk:
                return {"success": False, "error": "Failed to bond provider instance"}
            result = bonded.sdk.customers.retrieve_customer(customer_id=external_id)
            if result.is_success():
                return {"success": True, "data": result.body.get("customer", {})}
            return {"success": False, "error": str(result.errors)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    def list_via_provider(provider_instance, **kwargs) -> Dict[str, Any]:
        try:
            bonded = PaymentExtensionSquareProvider.bond_instance(provider_instance)
            if not bonded or not bonded.sdk:
                return {"success": False, "error": "Failed to bond provider instance"}
            result = bonded.sdk.customers.list_customers(**kwargs)
            if result.is_success():
                return {"success": True, "data": result.body.get("customers", [])}
            return {"success": False, "error": str(result.errors)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    def update_via_provider(
        provider_instance, external_id: str, **kwargs
    ) -> Dict[str, Any]:
        try:
            bonded = PaymentExtensionSquareProvider.bond_instance(provider_instance)
            if not bonded or not bonded.sdk:
                return {"success": False, "error": "Failed to bond provider instance"}
            result = bonded.sdk.customers.update_customer(
                customer_id=external_id, body=kwargs
            )
            if result.is_success():
                return {"success": True, "data": result.body.get("customer", {})}
            return {"success": False, "error": str(result.errors)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    def delete_via_provider(provider_instance, external_id: str) -> Dict[str, Any]:
        try:
            bonded = PaymentExtensionSquareProvider.bond_instance(provider_instance)
            if not bonded or not bonded.sdk:
                return {"success": False, "error": "Failed to bond provider instance"}
            result = bonded.sdk.customers.delete_customer(customer_id=external_id)
            if result.is_success():
                return {"success": True}
            return {"success": False, "error": str(result.errors)}
        except Exception as e:
            return {"success": False, "error": str(e)}


# ============================================================================
# Square Payment External Model
# ============================================================================


class Square_PaymentModel(AbstractExternalModel, metaclass=ModelMeta):
    """External model for Square Payment API resource."""

    external_resource: ClassVar[str] = "payments"
    _is_extension_model: ClassVar[bool] = True

    id: str = Field(...)
    amount_money: Optional[Dict[str, Any]] = Field(None)
    status: str = Field(...)
    source_type: Optional[str] = Field(None)
    customer_id: Optional[str] = Field(None)


class Square_SubscriptionModel(AbstractExternalModel, metaclass=ModelMeta):
    """External model for Square Subscription API resource."""

    external_resource: ClassVar[str] = "subscriptions"
    _is_extension_model: ClassVar[bool] = True

    id: str = Field(...)
    customer_id: str = Field(...)
    plan_variation_id: str = Field(...)
    status: str = Field(...)


# ============================================================================
# Square Provider
# ============================================================================


class PaymentExtensionSquareProvider(AbstractPaymentProvider):
    """Square payment provider for zephyrex.

    Supports payments, customers, subscriptions, and webhook processing.
    Fully compatible with the Provider Rotation System.
    """

    name = "square"
    version = "1.0.0"
    description = "Square payment provider"

    _square_client = None
    _square_available = False

    dependencies = Dependencies(
        [
            PIP_Dependency(
                name="squareup",
                friendly_name="Square Python SDK",
                semver=">=37.0.0",
                reason="Square payment provider support",
            ),
        ]
    )

    _env = {
        "SQUARE_ACCESS_TOKEN": "",
        "SQUARE_APP_ID": "",
        "SQUARE_WEBHOOK_SIGNATURE_KEY": "",
        "SQUARE_ENVIRONMENT": "sandbox",
        "SQUARE_CURRENCY": "USD",
    }

    @classmethod
    def bond_instance(
        cls, instance: ProviderInstanceModel
    ) -> Optional[AbstractProviderInstance_SDK]:
        if SquareClient is None:
            logger.warning("Square library not available for bonding")
            return None
        try:
            access_token = (
                instance.api_key
                if hasattr(instance, "api_key") and instance.api_key
                else cls.get_access_token()
            )
            if not access_token:
                logger.error("No access token available for Square provider instance")
                return None
            square_env = env("SQUARE_ENVIRONMENT") or "sandbox"
            client = SquareClient(access_token=access_token, environment=square_env)
            return AbstractProviderInstance_SDK(client)
        except Exception as e:
            logger.error("Failed to bond Square provider instance: %s", e)
            return None

    @classmethod
    def _configure_square(cls) -> None:
        if SquareClient is None:
            cls._square_available = False
            cls._square_client = None
            return
        try:
            access_token = cls.get_access_token()
            if access_token:
                square_env = env("SQUARE_ENVIRONMENT") or "sandbox"
                cls._square_client = SquareClient(
                    access_token=access_token, environment=square_env
                )
                cls._square_available = True
            else:
                cls._square_available = False
        except Exception as e:
            cls._square_available = False
            logger.error("Failed to configure Square: %s", e)

    @classmethod
    def _get_square_client(cls):
        if not cls._square_available:
            cls._configure_square()
        return cls._square_client if cls._square_available else None

    @classmethod
    def get_access_token(cls) -> Optional[str]:
        return env("SQUARE_ACCESS_TOKEN")  # type: ignore[no-any-return]

    @classmethod
    def get_app_id(cls) -> Optional[str]:
        return env("SQUARE_APP_ID")  # type: ignore[no-any-return]

    @classmethod
    def get_webhook_signature_key(cls) -> Optional[str]:
        return env("SQUARE_WEBHOOK_SIGNATURE_KEY")  # type: ignore[no-any-return]

    @classmethod
    def get_default_currency(cls) -> str:
        return env("SQUARE_CURRENCY") or "USD"

    @classmethod
    def get_env_value(cls, key: str, default: Any = None) -> Any:
        return env(key, default)

    @classmethod
    def validate_config(cls) -> bool:
        if not cls._square_available:
            cls._configure_square()
        return cls._square_available and bool(cls.get_access_token())

    @classmethod
    def get_platform_name(cls) -> str:
        return "Square"

    @classmethod
    def services(cls) -> List[str]:
        return ["payment", "subscription", "commerce"]

    @classmethod
    def get_extension_info(cls) -> Dict[str, Any]:
        return {
            "name": "Payment",
            "description": "Payment extension providing payment processing via Square",
            "platform": cls.get_platform_name(),
            "currency": cls.get_env_value("SQUARE_CURRENCY", "USD"),
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
        client = cls._get_square_client()
        if not client:
            raise Exception("Square client not configured")
        try:
            import uuid

            body: Dict[str, Any] = {
                "idempotency_key": str(uuid.uuid4()),
                "amount_money": {
                    "amount": int(amount * 100),
                    "currency": currency.upper(),
                },
                "source_id": payment_method_id or "EXTERNAL",
            }
            if customer_id:
                body["customer_id"] = customer_id
            if description:
                body["note"] = description
            result = client.payments.create_payment(body=body)
            if result.is_success():
                payment = result.body.get("payment", {})
                return {
                    "success": True,
                    "payment_id": payment.get("id"),
                    "amount": amount,
                    "currency": currency,
                    "status": payment.get("status"),
                }
            return {"success": False, "error": str(result.errors)}
        except Exception as e:
            logger.error("Error creating Square payment: %s", e)
            return {"success": False, "error": str(e)}

    @classmethod
    def get_payment(
        cls, provider_instance: ProviderInstanceModel, payment_id: str
    ) -> Dict:
        client = cls._get_square_client()
        if not client:
            raise Exception("Square client not configured")
        try:
            result = client.payments.get_payment(payment_id=payment_id)
            if result.is_success():
                payment = result.body.get("payment", {})
                amount_money = payment.get("amount_money", {})
                return {
                    "success": True,
                    "payment_id": payment.get("id"),
                    "amount": amount_money.get("amount", 0) / 100.0,
                    "currency": amount_money.get("currency", "USD"),
                    "status": payment.get("status"),
                }
            return {"success": False, "error": str(result.errors)}
        except Exception as e:
            logger.error("Error getting Square payment %s: %s", payment_id, e)
            return {"success": False, "error": str(e)}

    @classmethod
    def refund_payment(
        cls,
        provider_instance: ProviderInstanceModel,
        payment_id: str,
        amount: Optional[float] = None,
        reason: Optional[str] = None,
    ) -> Dict:
        client = cls._get_square_client()
        if not client:
            raise Exception("Square client not configured")
        try:
            import uuid

            body: Dict[str, Any] = {
                "idempotency_key": str(uuid.uuid4()),
                "payment_id": payment_id,
            }
            if amount is not None:
                body["amount_money"] = {
                    "amount": int(amount * 100),
                    "currency": cls.get_default_currency(),
                }
            if reason:
                body["reason"] = reason
            result = client.refunds.refund_payment(body=body)
            if result.is_success():
                refund = result.body.get("refund", {})
                refund_amount = refund.get("amount_money", {})
                return {
                    "success": True,
                    "refund_id": refund.get("id"),
                    "payment_id": payment_id,
                    "amount": refund_amount.get("amount", 0) / 100.0,
                    "reason": reason,
                    "status": refund.get("status"),
                }
            return {"success": False, "error": str(result.errors)}
        except Exception as e:
            logger.error("Error refunding Square payment %s: %s", payment_id, e)
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
        client = cls._get_square_client()
        if not client:
            raise Exception("Square client not configured")
        try:
            body: Dict[str, Any] = {"email_address": email}
            if name:
                parts = name.split(" ", 1)
                body["given_name"] = parts[0]
                if len(parts) > 1:
                    body["family_name"] = parts[1]
            if phone:
                body["phone_number"] = phone
            if metadata:
                body["reference_id"] = metadata.get("user_id", "")
            result = client.customers.create_customer(body=body)
            if result.is_success():
                customer = result.body.get("customer", {})
                return {
                    "success": True,
                    "customer_id": customer.get("id"),
                    "email": customer.get("email_address"),
                    "name": f"{customer.get('given_name', '')} {customer.get('family_name', '')}".strip(),
                    "phone": customer.get("phone_number"),
                }
            return {"success": False, "error": str(result.errors)}
        except Exception as e:
            logger.error("Error creating Square customer: %s", e)
            return {"success": False, "error": str(e)}

    @classmethod
    def get_customer(
        cls, provider_instance: ProviderInstanceModel, customer_id: str
    ) -> Dict:
        client = cls._get_square_client()
        if not client:
            raise Exception("Square client not configured")
        try:
            result = client.customers.retrieve_customer(customer_id=customer_id)
            if result.is_success():
                customer = result.body.get("customer", {})
                return {
                    "success": True,
                    "customer_id": customer.get("id"),
                    "email": customer.get("email_address"),
                    "name": f"{customer.get('given_name', '')} {customer.get('family_name', '')}".strip(),
                    "phone": customer.get("phone_number"),
                }
            return {"success": False, "error": str(result.errors)}
        except Exception as e:
            logger.error("Error getting Square customer %s: %s", customer_id, e)
            return {"success": False, "error": str(e)}

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
        client = cls._get_square_client()
        if not client:
            raise Exception("Square client not configured")
        try:
            import uuid

            body: Dict[str, Any] = {
                "idempotency_key": str(uuid.uuid4()),
                "location_id": env("SQUARE_LOCATION_ID") or "",
                "customer_id": customer_id,
                "plan_variation_id": price_id,
            }
            result = client.subscriptions.create_subscription(body=body)
            if result.is_success():
                sub = result.body.get("subscription", {})
                return {
                    "success": True,
                    "subscription_id": sub.get("id"),
                    "customer_id": sub.get("customer_id"),
                    "status": sub.get("status"),
                }
            return {"success": False, "error": str(result.errors)}
        except Exception as e:
            logger.error("Error creating Square subscription: %s", e)
            return {"success": False, "error": str(e)}

    @classmethod
    def cancel_subscription(
        cls,
        provider_instance: ProviderInstanceModel,
        subscription_id: str,
        immediately: bool = False,
    ) -> Dict:
        client = cls._get_square_client()
        if not client:
            raise Exception("Square client not configured")
        try:
            result = client.subscriptions.cancel_subscription(
                subscription_id=subscription_id
            )
            if result.is_success():
                sub = result.body.get("subscription", {})
                return {
                    "success": True,
                    "subscription_id": sub.get("id"),
                    "status": sub.get("status"),
                    "cancelled_immediately": immediately,
                }
            return {"success": False, "error": str(result.errors)}
        except Exception as e:
            logger.error(
                "Error cancelling Square subscription %s: %s", subscription_id, e
            )
            return {"success": False, "error": str(e)}

    @classmethod
    async def process_webhook(
        cls, provider_instance: ProviderInstanceModel, payload: str, signature: str
    ) -> Dict:
        """Process a webhook from Square. Async to match the abstract interface."""
        sig_key = cls.get_webhook_signature_key()
        if not sig_key:
            return {"success": False, "error": "Webhook signature key not configured"}
        try:
            import hashlib
            import hmac

            payload_bytes = payload if isinstance(payload, bytes) else payload.encode()
            expected = hmac.new(
                sig_key.encode(), payload_bytes, hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(expected, signature):
                return {"success": False, "error": "Invalid signature"}
            import json

            event = json.loads(payload_bytes)
            event_type = event.get("type", "unknown")
            return {
                "success": True,
                "event_type": event_type,
                "event_id": event.get("event_id"),
                "processed": True,
            }
        except Exception as e:
            logger.error("Error processing Square webhook: %s", e)
            return {"success": False, "error": str(e)}


# ============================================================================
# Square Customer Manager
# ============================================================================


class Square_CustomerManager(AbstractExternalManager):
    """Manager for Square Customer external API."""

    Model = Square_CustomerModel
    ReferenceModel = Square_CustomerModel.Reference
    provider_class = PaymentExtensionSquareProvider

    def create_validation(self, entity):
        if not getattr(entity, "email_address", None):
            raise ValueError("Email is required for Square customer creation")
        return True

    @classmethod
    def sync_contact(cls, *args, **kwargs):
        try:
            if hasattr(cls.Model, "get_via_provider"):
                return cls.Model.get_via_provider(*args, **kwargs)
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
        if hasattr(cls.Model, "create_via_provider"):
            return cls.Model.create_via_provider(provider_instance, **kwargs)
        return {"success": False, "error": "No customer creation path available"}
