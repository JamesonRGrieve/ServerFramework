# SPDX-License-Identifier: AGPL-3.0-or-later
"""Square payment provider for zephyrex.

Provides payment processing, customer management, and webhook handling
through Square's API (the ``squareup`` SDK, v42 and later). Fully static
implementation compatible with the Provider Rotation System.

The SDK raises ``square.core.api_error.ApiError`` for a refused request and
returns typed responses; each call here reports either as the provider's
``{"success": ...}`` dict.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Callable, ClassVar, Dict, List, Optional

from pydantic import Field

from zephyrex.extensions.AbstractExtensionProvider import AbstractProviderInstance_SDK
from zephyrex.extensions.AbstractExternalModel import (
    AbstractExternalManager,
    AbstractExternalModel,
)
from zephyrex.extensions.payment.EXT_Payment import (
    AbstractPaymentProvider,
    PassthroughExternalModel,
)
from zephyrex.lib.Dependencies import Dependencies, PIP_Dependency, importable
from zephyrex.lib.Environment import env
from zephyrex.lib.Logging import logger
from zephyrex.logic.AbstractLogicManager import ModelMeta
from zephyrex.logic.BLL_Providers import ProviderInstanceModel
from zephyrex.pydantic2.registry import BaseModel

SQUARE_ENVIRONMENTS = ("sandbox", "production")


def _square_dict(model: Any) -> Dict[str, Any]:
    """A Square SDK model as the JSON object Square's API documents."""
    if model is None:
        return {}
    dumped: Dict[str, Any] = model.model_dump(mode="json", exclude_none=True)
    return dumped


def _error_message(error: Exception) -> str:
    """What a caller is told of a failed Square call: for a refusal, its
    status and Square's error details, never the response headers."""
    from square.core.api_error import ApiError

    if not isinstance(error, ApiError):
        return str(error)
    body = error.body if isinstance(error.body, dict) else {}
    details = [
        e.get("detail") or e.get("code", "")
        for e in body.get("errors", [])
        if isinstance(e, dict)
    ]
    return f"Square refused the request ({error.status_code}): " + (
        "; ".join(d for d in details if d) or "no detail"
    )


def _with_bonded_client(
    provider_instance: Any, call: Callable[[Any], Dict[str, Any]]
) -> Dict[str, Any]:
    """``call`` with the instance's bonded Square client, as a result."""
    try:
        bonded = PaymentExtensionSquareProvider.bond_instance(provider_instance)
        if not bonded or not bonded.sdk:
            return {"success": False, "error": "Failed to bond provider instance"}
        return {"success": True, **call(bonded.sdk)}
    except Exception as e:
        message = _error_message(e)
        logger.error("Square call failed: %s", message)
        return {"success": False, "error": message}


def _customer_summary(customer: Dict[str, Any]) -> Dict[str, Any]:
    name = f"{customer.get('given_name', '')} {customer.get('family_name', '')}"
    return {
        "success": True,
        "customer_id": customer.get("id"),
        "email": customer.get("email_address"),
        "name": name.strip(),
        "phone": customer.get("phone_number"),
    }


# ============================================================================
# Square Customer External Model
# ============================================================================


class Square_CustomerModel(PassthroughExternalModel, metaclass=ModelMeta):
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
    def to_external_query_format(
        cls,
        query_params: Dict[str, Any],
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        order_by: Optional[List] = None,
    ) -> Dict[str, Any]:
        external_params = dict(query_params)
        if limit:
            external_params["limit"] = min(limit, 100)
        return external_params

    @staticmethod
    def create_via_provider(provider_instance, **kwargs) -> Dict[str, Any]:
        return _with_bonded_client(
            provider_instance,
            lambda sdk: {"data": _square_dict(sdk.customers.create(**kwargs).customer)},
        )

    @staticmethod
    def get_via_provider(provider_instance, external_id: str) -> Dict[str, Any]:
        return _with_bonded_client(
            provider_instance,
            lambda sdk: {
                "data": _square_dict(
                    sdk.customers.get(customer_id=external_id).customer
                )
            },
        )

    @staticmethod
    def list_via_provider(provider_instance, **kwargs) -> Dict[str, Any]:
        return _with_bonded_client(
            provider_instance,
            lambda sdk: {
                "data": [
                    _square_dict(customer)
                    for customer in sdk.customers.list(**kwargs).items or []
                ]
            },
        )

    @staticmethod
    def update_via_provider(
        provider_instance, external_id: str, **kwargs
    ) -> Dict[str, Any]:
        return _with_bonded_client(
            provider_instance,
            lambda sdk: {
                "data": _square_dict(
                    sdk.customers.update(customer_id=external_id, **kwargs).customer
                )
            },
        )

    @staticmethod
    def delete_via_provider(provider_instance, external_id: str) -> Dict[str, Any]:
        def delete(sdk: Any) -> Dict[str, Any]:
            sdk.customers.delete(customer_id=external_id)
            return {}

        return _with_bonded_client(provider_instance, delete)


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
    _currency_env_var: ClassVar[str] = "SQUARE_CURRENCY"
    _default_currency: ClassVar[str] = "USD"

    _square_client: ClassVar[Any] = None
    _square_available: ClassVar[bool] = False

    dependencies = Dependencies(
        [
            PIP_Dependency(
                name="squareup",
                friendly_name="Square Python SDK",
                # square.Square, its client since v42.
                semver=">=42.0.0",
                reason="Square payment provider support",
            ),
        ]
    )

    _env = {
        "SQUARE_ACCESS_TOKEN": "",
        "SQUARE_APP_ID": "",
        "SQUARE_LOCATION_ID": "",
        "SQUARE_WEBHOOK_SIGNATURE_KEY": "",
        "SQUARE_WEBHOOK_NOTIFICATION_URL": "",
        "SQUARE_ENVIRONMENT": "sandbox",
        "SQUARE_CURRENCY": "USD",
    }

    @classmethod
    def _new_client(cls, access_token: str) -> Any:
        """A Square client for ``access_token`` in SQUARE_ENVIRONMENT."""
        from square import Square
        from square.environment import SquareEnvironment

        square_env = (env("SQUARE_ENVIRONMENT") or "sandbox").strip().lower()
        if square_env not in SQUARE_ENVIRONMENTS:
            raise ValueError(
                f"SQUARE_ENVIRONMENT must be one of {', '.join(SQUARE_ENVIRONMENTS)}"
            )
        return Square(
            token=access_token, environment=SquareEnvironment[square_env.upper()]
        )

    @classmethod
    def bond_instance(
        cls, instance: ProviderInstanceModel
    ) -> Optional[AbstractProviderInstance_SDK]:
        if not importable("square"):
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
            return AbstractProviderInstance_SDK(cls._new_client(access_token))
        except Exception as e:
            logger.error("Failed to bond Square provider instance: %s", e)
            return None

    @classmethod
    def _configure_square(cls) -> None:
        cls._square_available = False
        cls._square_client = None
        access_token = cls.get_access_token()
        if not (importable("square") and access_token):
            return
        try:
            cls._square_client = cls._new_client(access_token)
            cls._square_available = True
        except Exception as e:
            logger.error("Failed to configure Square: %s", e)

    @classmethod
    def _get_square_client(cls) -> Any:
        if not cls._square_available:
            cls._configure_square()
        if not cls._square_available:
            raise Exception("Square client not configured")
        return cls._square_client

    @classmethod
    def get_access_token(cls) -> Optional[str]:
        return env("SQUARE_ACCESS_TOKEN")

    @classmethod
    def get_app_id(cls) -> Optional[str]:
        return env("SQUARE_APP_ID")

    @classmethod
    def get_webhook_signature_key(cls) -> Optional[str]:
        return env("SQUARE_WEBHOOK_SIGNATURE_KEY")

    @classmethod
    def get_webhook_notification_url(cls) -> Optional[str]:
        """The URL Square posts this subscription's webhooks to: Square signs
        it together with the body."""
        return env("SQUARE_WEBHOOK_NOTIFICATION_URL")

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
    def _call(
        cls, action: str, call: Callable[[Any], Dict[str, Any]]
    ) -> Dict[str, Any]:
        """``call`` with the configured client; a refusal or failure is the
        provider's error result."""
        client = cls._get_square_client()
        try:
            return {"success": True, **call(client)}
        except Exception as e:
            message = _error_message(e)
            logger.error("Square %s failed: %s", action, message)
            return {"success": False, "error": message}

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
        request: Dict[str, Any] = {
            "idempotency_key": str(uuid.uuid4()),
            "amount_money": {
                "amount": cls.to_minor_units(amount, currency),
                "currency": currency.upper(),
            },
            "source_id": payment_method_id or "EXTERNAL",
        }
        if customer_id:
            request["customer_id"] = customer_id
        if description:
            request["note"] = description

        def create(client: Any) -> Dict[str, Any]:
            payment = _square_dict(client.payments.create(**request).payment)
            return {
                "payment_id": payment.get("id"),
                "amount": amount,
                "currency": currency,
                "status": payment.get("status"),
            }

        return cls._call("payment creation", create)

    @classmethod
    def get_payment(
        cls, provider_instance: ProviderInstanceModel, payment_id: str
    ) -> Dict:
        def get(client: Any) -> Dict[str, Any]:
            payment = _square_dict(client.payments.get(payment_id=payment_id).payment)
            amount_money = payment.get("amount_money", {})
            return {
                "payment_id": payment.get("id"),
                "amount": float(
                    cls.from_minor_units(
                        amount_money.get("amount", 0), amount_money.get("currency")
                    )
                ),
                "currency": amount_money.get("currency", "USD"),
                "status": payment.get("status"),
            }

        return cls._call(f"payment {payment_id} retrieval", get)

    @classmethod
    def refund_payment(
        cls,
        provider_instance: ProviderInstanceModel,
        payment_id: str,
        amount: Optional[float] = None,
        reason: Optional[str] = None,
    ) -> Dict:
        """Refund ``amount``, or with none the payment's whole amount: Square
        requires the amount on every refund."""

        def refund(client: Any) -> Dict[str, Any]:
            if amount is None:
                payment = _square_dict(
                    client.payments.get(payment_id=payment_id).payment
                )
                amount_money = payment["amount_money"]
            else:
                amount_money = {
                    "amount": cls.to_minor_units(amount),
                    "currency": cls.get_default_currency(),
                }
            request: Dict[str, Any] = {
                "idempotency_key": str(uuid.uuid4()),
                "payment_id": payment_id,
                "amount_money": amount_money,
            }
            if reason:
                request["reason"] = reason
            refunded = _square_dict(client.refunds.refund_payment(**request).refund)
            refund_amount = refunded.get("amount_money", {})
            return {
                "refund_id": refunded.get("id"),
                "payment_id": payment_id,
                "amount": float(
                    cls.from_minor_units(
                        refund_amount.get("amount", 0), refund_amount.get("currency")
                    )
                ),
                "reason": reason,
                "status": refunded.get("status"),
            }

        return cls._call(f"refund of payment {payment_id}", refund)

    @classmethod
    def create_customer(
        cls,
        provider_instance: ProviderInstanceModel,
        email: str,
        name: Optional[str] = None,
        phone: Optional[str] = None,
        metadata: Optional[Dict] = None,
    ) -> Dict:
        request: Dict[str, Any] = {"email_address": email}
        if name:
            parts = name.split(" ", 1)
            request["given_name"] = parts[0]
            if len(parts) > 1:
                request["family_name"] = parts[1]
        if phone:
            request["phone_number"] = phone
        if metadata:
            request["reference_id"] = metadata.get("user_id", "")
        return cls._call(
            "customer creation",
            lambda client: _customer_summary(
                _square_dict(client.customers.create(**request).customer)
            ),
        )

    @classmethod
    def get_customer(
        cls, provider_instance: ProviderInstanceModel, customer_id: str
    ) -> Dict:
        return cls._call(
            f"customer {customer_id} retrieval",
            lambda client: _customer_summary(
                _square_dict(client.customers.get(customer_id=customer_id).customer)
            ),
        )

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
        request: Dict[str, Any] = {
            "idempotency_key": str(uuid.uuid4()),
            "location_id": env("SQUARE_LOCATION_ID") or "",
            "customer_id": customer_id,
            "plan_variation_id": price_id,
        }

        def create(client: Any) -> Dict[str, Any]:
            subscription = _square_dict(
                client.subscriptions.create(**request).subscription
            )
            return {
                "subscription_id": subscription.get("id"),
                "customer_id": subscription.get("customer_id"),
                "status": subscription.get("status"),
            }

        return cls._call("subscription creation", create)

    @classmethod
    def cancel_subscription(
        cls,
        provider_instance: ProviderInstanceModel,
        subscription_id: str,
        immediately: bool = False,
    ) -> Dict:
        def cancel(client: Any) -> Dict[str, Any]:
            subscription = _square_dict(
                client.subscriptions.cancel(
                    subscription_id=subscription_id
                ).subscription
            )
            return {
                "subscription_id": subscription.get("id"),
                "status": subscription.get("status"),
                "cancelled_immediately": immediately,
            }

        return cls._call(f"cancellation of subscription {subscription_id}", cancel)

    @classmethod
    async def process_webhook(
        cls, provider_instance: ProviderInstanceModel, payload: str, signature: str
    ) -> Dict:
        """Process a webhook from Square. Async to match the abstract interface.

        Square signs the subscription's notification URL followed by the body
        (base64 HMAC-SHA256, ``x-square-hmacsha256-signature``); the SDK's
        ``verify_signature`` checks it in constant time."""
        # Empty-signature guard, uniform with the other providers (#228): an
        # empty or missing signature can never be verified.
        if not signature:
            raise Exception("Webhook signature missing — cannot verify authenticity")
        sig_key = cls.get_webhook_signature_key()
        if not sig_key:
            return {"success": False, "error": "Webhook signature key not configured"}
        notification_url = cls.get_webhook_notification_url()
        if not notification_url:
            return {
                "success": False,
                "error": "Webhook notification URL not configured",
            }
        try:
            from square.utils.webhooks_helper import verify_signature

            body = payload.decode() if isinstance(payload, bytes) else payload
            if not verify_signature(
                request_body=body,
                signature_header=signature,
                signature_key=sig_key,
                notification_url=notification_url,
            ):
                return {"success": False, "error": "Invalid signature"}
            event = json.loads(body)
            return {
                "success": True,
                "event_type": event.get("type", "unknown"),
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
