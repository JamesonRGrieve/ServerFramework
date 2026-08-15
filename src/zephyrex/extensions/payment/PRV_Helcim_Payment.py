# SPDX-License-Identifier: AGPL-3.0-or-later
"""Helcim payment provider for zephyrex.

Integrates with the Helcim REST API for payment processing, customer
management, and webhook handling. Uses ``httpx`` against the REST
surface directly (no third-party SDK). Fully static implementation
compatible with the Provider Rotation System.

Credentials (HELCIM_API_TOKEN) must be set in the environment before
the provider becomes active.
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

_BASE_URL = "https://api.helcim.com/v2"


def _default_headers() -> Dict[str, str]:
    headers: Dict[str, str] = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    api_token = env("HELCIM_API_TOKEN")
    if api_token:
        headers["api-token"] = api_token
    return headers


# ============================================================================
# Helcim Customer External Model
# ============================================================================


class Helcim_CustomerModel(AbstractExternalModel, metaclass=ModelMeta):
    """External model for Helcim Customer API resource."""

    class Reference:
        pass

    external_resource: ClassVar[str] = "customers"
    _is_extension_model: ClassVar[bool] = True
    _extension_target: ClassVar[str] = "payment"

    id: str = Field(..., description="Helcim customer ID")
    email: Optional[str] = Field(None, description="Customer email")
    first_name: Optional[str] = Field(None, description="First name")
    last_name: Optional[str] = Field(None, description="Last name")
    phone: Optional[str] = Field(None, description="Phone")
    company_name: Optional[str] = Field(None, description="Company name")

    class Create(BaseModel):
        email: Optional[str] = Field(None)
        first_name: Optional[str] = Field(None)
        last_name: Optional[str] = Field(None)
        phone: Optional[str] = Field(None)
        company_name: Optional[str] = Field(None)

    class Update(BaseModel):
        email: Optional[str] = Field(None)
        first_name: Optional[str] = Field(None)
        last_name: Optional[str] = Field(None)
        phone: Optional[str] = Field(None)

    class Search(BaseModel):
        email: Optional[str] = Field(None)

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
            params["limit"] = limit
        return params

    @staticmethod
    def create_via_provider(provider_instance, **kwargs) -> Dict[str, Any]:
        try:
            bonded = PaymentExtensionHelcimProvider.bond_instance(provider_instance)
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
            bonded = PaymentExtensionHelcimProvider.bond_instance(provider_instance)
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
            bonded = PaymentExtensionHelcimProvider.bond_instance(provider_instance)
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
            bonded = PaymentExtensionHelcimProvider.bond_instance(provider_instance)
            if not bonded or not bonded.sdk:
                return {"success": False, "error": "Failed to bond provider instance"}
            client: httpx.Client = bonded.sdk
            resp = client.put(f"/customers/{external_id}", json=kwargs)
            if resp.status_code == 200:
                return {"success": True, "data": resp.json()}
            return {"success": False, "error": resp.text}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    def delete_via_provider(provider_instance, external_id: str) -> Dict[str, Any]:
        try:
            bonded = PaymentExtensionHelcimProvider.bond_instance(provider_instance)
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
# Helcim Payment / Subscription External Models
# ============================================================================


class Helcim_PaymentModel(AbstractExternalModel, metaclass=ModelMeta):
    """External model for Helcim Payment (transaction) resource."""

    external_resource: ClassVar[str] = "payment"
    _is_extension_model: ClassVar[bool] = True

    id: str = Field(..., description="Helcim transaction ID")
    amount: Optional[float] = Field(None)
    currency: Optional[str] = Field(None)
    status: Optional[str] = Field(None)
    type: Optional[str] = Field(None, description="purchase, preauth, refund, etc.")


class Helcim_InvoiceModel(AbstractExternalModel, metaclass=ModelMeta):
    """External model for Helcim Invoice resource (recurring billing)."""

    external_resource: ClassVar[str] = "invoices"
    _is_extension_model: ClassVar[bool] = True

    id: str = Field(...)
    customer_id: Optional[str] = Field(None)
    status: Optional[str] = Field(None)
    amount: Optional[float] = Field(None)


# ============================================================================
# Helcim Provider
# ============================================================================


class PaymentExtensionHelcimProvider(AbstractPaymentProvider):
    """Helcim payment provider for zephyrex.

    Uses the Helcim Commerce API v2 directly via ``httpx``. Supports
    purchases, pre-auths, refunds, customers, and invoices. Fully
    compatible with the Provider Rotation System.
    """

    name = "helcim"
    version = "1.0.0"
    description = "Helcim payment provider"

    _client: ClassVar[Optional[Any]] = None

    dependencies = Dependencies(
        [
            PIP_Dependency(
                name="httpx",
                friendly_name="HTTPX HTTP Client",
                semver=">=0.24.0",
                reason="Helcim REST API HTTP client",
            ),
        ]
    )

    _env = {
        "HELCIM_API_TOKEN": "",
        "HELCIM_CURRENCY": "CAD",
    }

    @classmethod
    def bond_instance(
        cls, instance: ProviderInstanceModel
    ) -> Optional[AbstractProviderInstance_SDK]:
        if httpx is None:
            logger.warning("httpx not available for Helcim provider bonding")
            return None
        try:
            headers = _default_headers()
            api_token = (
                instance.api_key
                if hasattr(instance, "api_key") and instance.api_key
                else headers.get("api-token")
            )
            if not api_token:
                logger.error("No API token available for Helcim provider instance")
                return None
            headers["api-token"] = api_token
            client = httpx.Client(
                base_url=_BASE_URL, headers=headers, timeout=30.0
            )
            return AbstractProviderInstance_SDK(client)
        except Exception as e:
            logger.error("Failed to bond Helcim provider instance: %s", e)
            return None

    @classmethod
    def _get_client(cls) -> Optional[Any]:
        if cls._client is not None:
            return cls._client
        if httpx is None:
            return None
        headers = _default_headers()
        if not headers.get("api-token"):
            return None
        cls._client = httpx.Client(
            base_url=_BASE_URL, headers=headers, timeout=30.0
        )
        return cls._client

    @classmethod
    def get_api_token(cls) -> Optional[str]:
        return env("HELCIM_API_TOKEN")  # type: ignore[no-any-return]

    @classmethod
    def get_default_currency(cls) -> str:
        return env("HELCIM_CURRENCY") or "CAD"

    @classmethod
    def get_env_value(cls, key: str, default: Any = None) -> Any:
        return env(key, default)

    @classmethod
    def validate_config(cls) -> bool:
        return bool(cls.get_api_token())

    @classmethod
    def get_platform_name(cls) -> str:
        return "Helcim"

    @classmethod
    def services(cls) -> List[str]:
        return ["payment", "commerce"]

    @classmethod
    def get_extension_info(cls) -> Dict[str, Any]:
        return {
            "name": "Payment",
            "description": "Payment extension providing payment processing via Helcim",
            "platform": cls.get_platform_name(),
            "currency": cls.get_env_value("HELCIM_CURRENCY", "CAD"),
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
            raise Exception("Helcim client not configured")
        try:
            body: Dict[str, Any] = {
                "amount": amount,
                "currency": currency.upper(),
                "idempotencyKey": str(uuid.uuid4()),
            }
            if payment_method_id:
                body["cardToken"] = payment_method_id
            if customer_id:
                body["customerId"] = int(customer_id)
            resp = client.post("/payment/purchase", json=body)
            if resp.status_code in (200, 201):
                data = resp.json()
                return {
                    "success": True,
                    "payment_id": str(data.get("transactionId")),
                    "amount": amount,
                    "currency": currency,
                    "status": data.get("status", "APPROVED"),
                }
            return {"success": False, "error": resp.text}
        except Exception as e:
            logger.error("Error creating Helcim payment: %s", e)
            return {"success": False, "error": str(e)}

    @classmethod
    def get_payment(
        cls, provider_instance: ProviderInstanceModel, payment_id: str
    ) -> Dict:
        client = cls._get_client()
        if not client:
            raise Exception("Helcim client not configured")
        try:
            resp = client.get(f"/payment/transaction/{payment_id}")
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "success": True,
                    "payment_id": str(data.get("transactionId")),
                    "amount": data.get("amount", 0),
                    "currency": data.get("currency", "CAD"),
                    "status": data.get("status"),
                }
            return {"success": False, "error": resp.text}
        except Exception as e:
            logger.error("Error getting Helcim payment %s: %s", payment_id, e)
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
            raise Exception("Helcim client not configured")
        try:
            body: Dict[str, Any] = {
                "originalTransactionId": int(payment_id),
                "idempotencyKey": str(uuid.uuid4()),
            }
            if amount is not None:
                body["amount"] = amount
            resp = client.post("/payment/refund", json=body)
            if resp.status_code in (200, 201):
                data = resp.json()
                return {
                    "success": True,
                    "refund_id": str(data.get("transactionId")),
                    "payment_id": payment_id,
                    "amount": data.get("amount", amount),
                    "reason": reason,
                    "status": data.get("status", "APPROVED"),
                }
            return {"success": False, "error": resp.text}
        except Exception as e:
            logger.error("Error refunding Helcim payment %s: %s", payment_id, e)
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
            raise Exception("Helcim client not configured")
        try:
            body: Dict[str, Any] = {"contactEmail": email}
            if name:
                parts = name.split(" ", 1)
                body["contactName"] = parts[0]
                if len(parts) > 1:
                    body["contactName"] = name
            if phone:
                body["contactPhone"] = phone
            resp = client.post("/customers", json=body)
            if resp.status_code in (200, 201):
                data = resp.json()
                return {
                    "success": True,
                    "customer_id": str(data.get("customerId") or data.get("id")),
                    "email": data.get("contactEmail", email),
                    "name": name,
                    "phone": data.get("contactPhone"),
                }
            return {"success": False, "error": resp.text}
        except Exception as e:
            logger.error("Error creating Helcim customer: %s", e)
            return {"success": False, "error": str(e)}

    @classmethod
    def get_customer(
        cls, provider_instance: ProviderInstanceModel, customer_id: str
    ) -> Dict:
        client = cls._get_client()
        if not client:
            raise Exception("Helcim client not configured")
        try:
            resp = client.get(f"/customers/{customer_id}")
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "success": True,
                    "customer_id": str(data.get("customerId") or data.get("id")),
                    "email": data.get("contactEmail"),
                    "name": data.get("contactName"),
                    "phone": data.get("contactPhone"),
                }
            return {"success": False, "error": resp.text}
        except Exception as e:
            logger.error("Error getting Helcim customer %s: %s", customer_id, e)
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
        """Helcim uses invoices for recurring billing, not subscriptions."""
        return {
            "success": False,
            "error": "Helcim does not support subscriptions directly; use invoices for recurring billing",
        }

    @classmethod
    def cancel_subscription(
        cls,
        provider_instance: ProviderInstanceModel,
        subscription_id: str,
        immediately: bool = False,
    ) -> Dict:
        return {
            "success": False,
            "error": "Helcim does not support subscriptions directly",
        }

    @classmethod
    async def process_webhook(
        cls, provider_instance: ProviderInstanceModel, payload: str, signature: str
    ) -> Dict:
        """Process a webhook from Helcim. Async to match the abstract interface."""
        try:
            payload_str = payload if isinstance(payload, str) else payload.decode()
            event = json.loads(payload_str)
            event_type = event.get("eventName") or event.get("type", "unknown")
            return {
                "success": True,
                "event_type": event_type,
                "event_id": event.get("id") or event.get("eventId"),
                "processed": True,
            }
        except Exception as e:
            logger.error("Error processing Helcim webhook: %s", e)
            return {"success": False, "error": str(e)}


# ============================================================================
# Helcim Customer Manager
# ============================================================================


class Helcim_CustomerManager(AbstractExternalManager):
    """Manager for Helcim Customer external API."""

    Model = Helcim_CustomerModel
    ReferenceModel = Helcim_CustomerModel.Reference
    provider_class = PaymentExtensionHelcimProvider

    def create_validation(self, entity):
        if not getattr(entity, "email", None):
            raise ValueError("Email is required for Helcim customer creation")
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
