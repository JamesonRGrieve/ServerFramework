# SPDX-License-Identifier: AGPL-3.0-or-later
"""Moneris payment provider for zephyrex.

Integrates with the Moneris REST API (api.sb.moneris.io / api.moneris.io)
for payment processing, customer management, subscriptions, and webhook
handling. No third-party SDK — uses ``httpx`` against the REST surface
directly. Fully static implementation compatible with the Provider
Rotation System.
"""
from __future__ import annotations

import json
import uuid
from typing import Any, ClassVar, Dict, List, Optional

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore[assignment]

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

_SANDBOX_BASE = "https://api.sb.moneris.io"
_PRODUCTION_BASE = "https://api.moneris.io"
_API_VERSION = "2024-09-17"


def _base_url() -> str:
    mode = (env("MONERIS_ENVIRONMENT") or "testing").lower()
    if mode in ("production", "live"):
        return _PRODUCTION_BASE
    return _SANDBOX_BASE


def _default_headers() -> Dict[str, str]:
    headers: Dict[str, str] = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Api-Version": _API_VERSION,
    }
    merchant_id = env("MONERIS_MERCHANT_ID")
    if merchant_id:
        headers["X-Merchant-Id"] = merchant_id
    api_key = env("MONERIS_API_KEY") or env("MONERIS_STORE_ID")
    if api_key:
        headers["X-Api-Key"] = api_key
    return headers


# ============================================================================
# Moneris Customer External Model
# ============================================================================


class Moneris_CustomerModel(AbstractExternalModel, metaclass=ModelMeta):
    """External model for Moneris Customer API resource."""

    class Reference:
        pass

    external_resource: ClassVar[str] = "customers"
    _is_extension_model: ClassVar[bool] = True
    _extension_target: ClassVar[str] = "payment"

    id: str = Field(..., description="Moneris customer ID")
    email: Optional[str] = Field(None, description="Customer email")
    first_name: Optional[str] = Field(None, description="Customer first name")
    last_name: Optional[str] = Field(None, description="Customer last name")
    phone: Optional[str] = Field(None, description="Customer phone")
    created_at: Optional[str] = Field(None, description="Creation timestamp")

    class Create(BaseModel):
        email: Optional[str] = Field(None, description="Customer email")
        first_name: Optional[str] = Field(None, description="First name")
        last_name: Optional[str] = Field(None, description="Last name")
        phone: Optional[str] = Field(None, description="Phone number")

    class Update(BaseModel):
        email: Optional[str] = Field(None, description="Customer email")
        first_name: Optional[str] = Field(None, description="First name")
        last_name: Optional[str] = Field(None, description="Last name")
        phone: Optional[str] = Field(None, description="Phone number")

    class Search(BaseModel):
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
        params = dict(query_params)
        if limit:
            params["limit"] = min(limit, 20)
        return params

    @staticmethod
    def create_via_provider(provider_instance, **kwargs) -> Dict[str, Any]:
        try:
            bonded = PaymentExtensionMonerisProvider.bond_instance(provider_instance)
            if not bonded or not bonded.sdk:
                return {"success": False, "error": "Failed to bond provider instance"}
            client: httpx.Client = bonded.sdk
            resp = client.post("/customers", json=kwargs)
            if resp.status_code in (200, 201):
                return {"success": True, "data": resp.json()}
            return {"success": False, "error": resp.text}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    def get_via_provider(provider_instance, external_id: str) -> Dict[str, Any]:
        try:
            bonded = PaymentExtensionMonerisProvider.bond_instance(provider_instance)
            if not bonded or not bonded.sdk:
                return {"success": False, "error": "Failed to bond provider instance"}
            client: httpx.Client = bonded.sdk
            resp = client.get(f"/customers/{external_id}")
            if resp.status_code == 200:
                return {"success": True, "data": resp.json()}
            return {"success": False, "error": resp.text}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    def list_via_provider(provider_instance, **kwargs) -> Dict[str, Any]:
        try:
            bonded = PaymentExtensionMonerisProvider.bond_instance(provider_instance)
            if not bonded or not bonded.sdk:
                return {"success": False, "error": "Failed to bond provider instance"}
            client: httpx.Client = bonded.sdk
            resp = client.get("/customers", params=kwargs)
            if resp.status_code == 200:
                return {"success": True, "data": resp.json()}
            return {"success": False, "error": resp.text}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    def update_via_provider(
        provider_instance, external_id: str, **kwargs
    ) -> Dict[str, Any]:
        try:
            bonded = PaymentExtensionMonerisProvider.bond_instance(provider_instance)
            if not bonded or not bonded.sdk:
                return {"success": False, "error": "Failed to bond provider instance"}
            client: httpx.Client = bonded.sdk
            resp = client.patch(f"/customers/{external_id}", json=kwargs)
            if resp.status_code == 200:
                return {"success": True, "data": resp.json()}
            return {"success": False, "error": resp.text}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    def delete_via_provider(provider_instance, external_id: str) -> Dict[str, Any]:
        try:
            bonded = PaymentExtensionMonerisProvider.bond_instance(provider_instance)
            if not bonded or not bonded.sdk:
                return {"success": False, "error": "Failed to bond provider instance"}
            client: httpx.Client = bonded.sdk
            resp = client.delete(f"/customers/{external_id}")
            if resp.status_code in (200, 204):
                return {"success": True}
            return {"success": False, "error": resp.text}
        except Exception as e:
            return {"success": False, "error": str(e)}


# ============================================================================
# Moneris Payment / Subscription External Models
# ============================================================================


class Moneris_PaymentModel(AbstractExternalModel, metaclass=ModelMeta):
    """External model for Moneris Payment API resource."""

    external_resource: ClassVar[str] = "payments"
    _is_extension_model: ClassVar[bool] = True

    id: str = Field(..., description="Moneris payment ID")
    amount: Optional[int] = Field(None, description="Amount in cents")
    currency: Optional[str] = Field(None, description="ISO 4217 currency")
    payment_status: Optional[str] = Field(None, description="SUCCEEDED, DECLINED, etc.")


class Moneris_SubscriptionModel(AbstractExternalModel, metaclass=ModelMeta):
    """External model for Moneris Subscription API resource."""

    external_resource: ClassVar[str] = "subscriptions"
    _is_extension_model: ClassVar[bool] = True

    id: str = Field(...)
    customer_id: Optional[str] = Field(None)
    status: Optional[str] = Field(None)


# ============================================================================
# Moneris Provider
# ============================================================================


class PaymentExtensionMonerisProvider(AbstractPaymentProvider):
    """Moneris payment provider for zephyrex.

    Uses the Moneris REST API directly via ``httpx``. Supports payments,
    customers, subscriptions, refunds, and webhook processing. Fully
    compatible with the Provider Rotation System.
    """

    name = "moneris"
    version = "1.0.0"
    description = "Moneris payment provider"

    _client: ClassVar[Optional[Any]] = None

    dependencies = Dependencies(
        [
            PIP_Dependency(
                name="httpx",
                friendly_name="HTTPX HTTP Client",
                semver=">=0.24.0",
                reason="Moneris REST API HTTP client",
            ),
        ]
    )

    _env = {
        "MONERIS_STORE_ID": "",
        "MONERIS_MERCHANT_ID": "",
        "MONERIS_API_KEY": "",
        "MONERIS_ENVIRONMENT": "testing",
        "MONERIS_CURRENCY": "CAD",
    }

    @classmethod
    def bond_instance(
        cls, instance: ProviderInstanceModel
    ) -> Optional[AbstractProviderInstance_SDK]:
        if httpx is None:
            logger.warning("httpx not available for Moneris provider bonding")
            return None
        try:
            headers = _default_headers()
            api_key = (
                instance.api_key
                if hasattr(instance, "api_key") and instance.api_key
                else headers.get("X-Api-Key")
            )
            if not api_key:
                logger.error("No API key available for Moneris provider instance")
                return None
            headers["X-Api-Key"] = api_key
            client = httpx.Client(
                base_url=_base_url(), headers=headers, timeout=30.0
            )
            return AbstractProviderInstance_SDK(client)
        except Exception as e:
            logger.error("Failed to bond Moneris provider instance: %s", e)
            return None

    @classmethod
    def _get_client(cls) -> Optional[Any]:
        if cls._client is not None:
            return cls._client
        if httpx is None:
            return None
        headers = _default_headers()
        if not headers.get("X-Api-Key"):
            return None
        cls._client = httpx.Client(
            base_url=_base_url(), headers=headers, timeout=30.0
        )
        return cls._client

    @classmethod
    def get_store_id(cls) -> Optional[str]:
        return env("MONERIS_STORE_ID")  # type: ignore[no-any-return]

    @classmethod
    def get_merchant_id(cls) -> Optional[str]:
        return env("MONERIS_MERCHANT_ID")  # type: ignore[no-any-return]

    @classmethod
    def get_default_currency(cls) -> str:
        return env("MONERIS_CURRENCY") or "CAD"

    @classmethod
    def get_env_value(cls, key: str, default: Any = None) -> Any:
        return env(key, default)

    @classmethod
    def validate_config(cls) -> bool:
        return bool(cls.get_store_id() and cls.get_merchant_id())

    @classmethod
    def get_platform_name(cls) -> str:
        return "Moneris"

    @classmethod
    def services(cls) -> List[str]:
        return ["payment", "subscription", "commerce"]

    @classmethod
    def get_extension_info(cls) -> Dict[str, Any]:
        return {
            "name": "Payment",
            "description": "Payment extension providing payment processing via Moneris",
            "platform": cls.get_platform_name(),
            "currency": cls.get_env_value("MONERIS_CURRENCY", "CAD"),
        }

    # ----- Payment operations ------------------------------------------------

    @classmethod
    def create_payment(
        cls,
        provider_instance: ProviderInstanceModel,
        amount: float,
        currency: str = "CAD",
        customer_id: Optional[str] = None,
        payment_method_id: Optional[str] = None,
        description: Optional[str] = None,
        metadata: Optional[Dict] = None,
    ) -> Dict:
        client = cls._get_client()
        if not client:
            raise Exception("Moneris client not configured")
        try:
            body: Dict[str, Any] = {
                "idempotencyKey": str(uuid.uuid4()),
                "amount": {
                    "amount": int(amount * 100),
                    "currency": currency.upper(),
                },
                "automaticCapture": True,
            }
            if payment_method_id:
                body["paymentMethod"] = {
                    "paymentMethodSource": "TOKEN",
                    "token": {"tokenId": payment_method_id},
                }
            if customer_id:
                body["customerId"] = customer_id
            if description:
                body["orderId"] = description
            resp = client.post("/payments", json=body)
            if resp.status_code in (200, 201):
                data = resp.json()
                return {
                    "success": True,
                    "payment_id": data.get("paymentId"),
                    "amount": amount,
                    "currency": currency,
                    "status": data.get("paymentStatus"),
                }
            return {"success": False, "error": resp.text}
        except Exception as e:
            logger.error("Error creating Moneris payment: %s", e)
            return {"success": False, "error": str(e)}

    @classmethod
    def get_payment(
        cls, provider_instance: ProviderInstanceModel, payment_id: str
    ) -> Dict:
        client = cls._get_client()
        if not client:
            raise Exception("Moneris client not configured")
        try:
            resp = client.get(f"/payments/{payment_id}")
            if resp.status_code == 200:
                data = resp.json()
                amount_obj = data.get("amount", {})
                return {
                    "success": True,
                    "payment_id": data.get("paymentId"),
                    "amount": amount_obj.get("amount", 0) / 100.0,
                    "currency": amount_obj.get("currency", "CAD"),
                    "status": data.get("paymentStatus"),
                }
            return {"success": False, "error": resp.text}
        except Exception as e:
            logger.error("Error getting Moneris payment %s: %s", payment_id, e)
            return {"success": False, "error": str(e)}

    @classmethod
    def refund_payment(
        cls,
        provider_instance: ProviderInstanceModel,
        payment_id: str,
        amount: Optional[float] = None,
        reason: Optional[str] = None,
    ) -> Dict:
        client = cls._get_client()
        if not client:
            raise Exception("Moneris client not configured")
        try:
            body: Dict[str, Any] = {
                "paymentId": payment_id,
                "idempotencyKey": str(uuid.uuid4()),
            }
            if amount is not None:
                body["refundAmount"] = {
                    "amount": int(amount * 100),
                    "currency": cls.get_default_currency(),
                }
            if reason:
                body["reason"] = reason
            resp = client.post("/refunds", json=body)
            if resp.status_code in (200, 201):
                data = resp.json()
                refund_amount = data.get("refundAmount", {})
                return {
                    "success": True,
                    "refund_id": data.get("refundId"),
                    "payment_id": payment_id,
                    "amount": refund_amount.get("amount", 0) / 100.0,
                    "reason": reason,
                    "status": data.get("refundStatus"),
                }
            return {"success": False, "error": resp.text}
        except Exception as e:
            logger.error("Error refunding Moneris payment %s: %s", payment_id, e)
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
        client = cls._get_client()
        if not client:
            raise Exception("Moneris client not configured")
        try:
            body: Dict[str, Any] = {"email": email}
            if name:
                parts = name.split(" ", 1)
                body["firstName"] = parts[0]
                if len(parts) > 1:
                    body["lastName"] = parts[1]
            if phone:
                body["phone"] = phone
            resp = client.post("/customers", json=body)
            if resp.status_code in (200, 201):
                data = resp.json()
                return {
                    "success": True,
                    "customer_id": data.get("customerId") or data.get("id"),
                    "email": data.get("email"),
                    "name": name,
                    "phone": data.get("phone"),
                }
            return {"success": False, "error": resp.text}
        except Exception as e:
            logger.error("Error creating Moneris customer: %s", e)
            return {"success": False, "error": str(e)}

    @classmethod
    def get_customer(
        cls, provider_instance: ProviderInstanceModel, customer_id: str
    ) -> Dict:
        client = cls._get_client()
        if not client:
            raise Exception("Moneris client not configured")
        try:
            resp = client.get(f"/customers/{customer_id}")
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "success": True,
                    "customer_id": data.get("customerId") or data.get("id"),
                    "email": data.get("email"),
                    "name": f"{data.get('firstName', '')} {data.get('lastName', '')}".strip(),
                    "phone": data.get("phone"),
                }
            return {"success": False, "error": resp.text}
        except Exception as e:
            logger.error("Error getting Moneris customer %s: %s", customer_id, e)
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
        client = cls._get_client()
        if not client:
            raise Exception("Moneris client not configured")
        try:
            body: Dict[str, Any] = {
                "customerId": customer_id,
                "planId": price_id,
            }
            if payment_method_id:
                body["paymentMethodId"] = payment_method_id
            resp = client.post("/subscriptions", json=body)
            if resp.status_code in (200, 201):
                data = resp.json()
                return {
                    "success": True,
                    "subscription_id": data.get("subscriptionId") or data.get("id"),
                    "customer_id": customer_id,
                    "status": data.get("status"),
                }
            return {"success": False, "error": resp.text}
        except Exception as e:
            logger.error("Error creating Moneris subscription: %s", e)
            return {"success": False, "error": str(e)}

    @classmethod
    def cancel_subscription(
        cls,
        provider_instance: ProviderInstanceModel,
        subscription_id: str,
        immediately: bool = False,
    ) -> Dict:
        client = cls._get_client()
        if not client:
            raise Exception("Moneris client not configured")
        try:
            resp = client.post(f"/subscriptions/{subscription_id}/cancel")
            if resp.status_code in (200, 201):
                data = resp.json()
                return {
                    "success": True,
                    "subscription_id": subscription_id,
                    "status": data.get("status", "cancelled"),
                    "cancelled_immediately": immediately,
                }
            return {"success": False, "error": resp.text}
        except Exception as e:
            logger.error(
                "Error cancelling Moneris subscription %s: %s", subscription_id, e
            )
            return {"success": False, "error": str(e)}

    @classmethod
    async def process_webhook(
        cls, provider_instance: ProviderInstanceModel, payload: str, signature: str
    ) -> Dict:
        """Process a webhook from Moneris. Async to match the abstract interface."""
        try:
            payload_str = payload if isinstance(payload, str) else payload.decode()
            event = json.loads(payload_str)
            event_type = event.get("type") or event.get("event_type", "unknown")
            return {
                "success": True,
                "event_type": event_type,
                "event_id": event.get("id") or event.get("eventId"),
                "processed": True,
            }
        except Exception as e:
            logger.error("Error processing Moneris webhook: %s", e)
            return {"success": False, "error": str(e)}


# ============================================================================
# Moneris Customer Manager
# ============================================================================


class Moneris_CustomerManager(AbstractExternalManager):
    """Manager for Moneris Customer external API."""

    Model = Moneris_CustomerModel
    ReferenceModel = Moneris_CustomerModel.Reference
    provider_class = PaymentExtensionMonerisProvider

    def create_validation(self, entity):
        if not getattr(entity, "email", None):
            raise ValueError("Email is required for Moneris customer creation")
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
