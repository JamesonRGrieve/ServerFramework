# SPDX-License-Identifier: AGPL-3.0-or-later
"""Stripe, through its REST API (form-encoded requests, JSON answers) at
the pinned API version.

A payment is a PaymentIntent: with a payment method it is confirmed at
once, otherwise its ``client_secret`` lets the payer's browser confirm it
(Stripe.js). A subscription is made ``default_incomplete``, so a customer
with no saved card gets an incomplete subscription to pay rather than a
refusal. Notifications are verified as Stripe signs them: an HMAC-SHA256
over ``<timestamp>.<body>`` with the endpoint's signing secret, in the
``Stripe-Signature`` header, within five minutes.
"""

import json
import time
from datetime import UTC, datetime
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Set, Tuple

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.ExternalErrors import (
    InvalidInputExternalError,
    TransientExternalError,
)
from zephyrex.extensions.payment.EXT_Payment import (
    AbstractPaymentProvider,
    CustomerRequest,
    MoneyAction,
    PaymentRequest,
    SubscriptionRequest,
    fresh,
    hmac_sha256,
    one_matches,
)
from zephyrex.lib.ProviderHTTPClient import path_segment
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

API_VERSION = "2024-06-20"
_STATUSES = {
    "requires_payment_method": "pending",
    "requires_confirmation": "pending",
    "requires_action": "pending",
    "processing": "pending",
    "requires_capture": "authorized",
    "succeeded": "succeeded",
    "canceled": "canceled",
}
_ACTIVE = ("active", "trialing")
# The refund reasons Stripe takes; any other is kept in the metadata.
_REFUND_REASONS = ("duplicate", "fraudulent", "requested_by_customer")
_EXPAND = {"expand[0]": "latest_charge"}


def _scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def form(params: Mapping[str, Any], prefix: str = "") -> Dict[str, str]:
    """``params`` as Stripe's form encoding: ``metadata[key]``,
    ``items[0][price]``; None values are left out."""
    encoded: Dict[str, str] = {}
    for key, value in params.items():
        name = f"{prefix}[{key}]" if prefix else str(key)
        if value is None:
            continue
        if isinstance(value, Mapping):
            encoded.update(form(value, name))
        elif isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                if isinstance(item, Mapping):
                    encoded.update(form(item, f"{name}[{index}]"))
                else:
                    encoded[f"{name}[{index}]"] = _scalar(item)
        else:
            encoded[name] = _scalar(value)
    return encoded


def signature_parts(header: str) -> Tuple[Optional[str], List[str]]:
    """The timestamp and the v1 signatures of a ``Stripe-Signature``."""
    timestamp: Optional[str] = None
    signatures: List[str] = []
    for part in header.split(","):
        key, _, value = part.strip().partition("=")
        if key == "t":
            timestamp = value
        elif key == "v1" and value:
            signatures.append(value)
    return timestamp, signatures


def _moment(seconds: Any) -> Optional[datetime]:
    if isinstance(seconds, bool) or not isinstance(seconds, int):
        return None
    return datetime.fromtimestamp(seconds, UTC)


class PRV_Stripe_Payment(AbstractPaymentProvider):
    name: ClassVar[str] = "stripe"
    friendly_name: ClassVar[str] = "Stripe"
    description: ClassVar[str] = "A Stripe account"
    external_api_version: ClassVar[Optional[str]] = API_VERSION
    external_api_version_header: ClassVar[Optional[str]] = "Stripe-Version"
    _abilities: ClassVar[Set[str]] = {
        "payment_create",
        "payment_get",
        "payment_capture",
        "payment_refund",
        "customer_create",
        "customer_get",
        "subscription_create",
        "subscription_get",
        "subscription_cancel",
        "webhook_process",
    }
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting(
            "api_key",
            "Secret API key (sk_live_… or sk_test_…)",
            env="STRIPE_API_KEY",
            secret=True,
            field="api_key",
        ),
        InstanceSetting(
            "webhook_secret",
            "The webhook endpoint's signing secret (whsec_…)",
            env="STRIPE_WEBHOOK_SECRET",
            secret=True,
        ),
        InstanceSetting("api_base", "API address", default="https://api.stripe.com"),
    )

    refusal_fields: ClassVar[Tuple[str, ...]] = ("message",)

    @classmethod
    async def stripe(
        cls,
        instance: ProviderInstanceModel,
        method: str,
        path: str,
        params: Optional[Mapping[str, Any]] = None,
        *,
        idempotency_key: Optional[str] = None,
        moves_money: bool = False,
    ) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {cls.required(instance, 'api_key')}"}
        url = cls.endpoint(instance, path)
        options: Dict[str, Any] = {"headers": headers}
        if method == "GET":
            options["params"] = dict(params or {})
        elif params:
            options["data"] = form(params)
        answer = await cls.call(
            method,
            url,
            idempotency_key=idempotency_key,
            moves_money=moves_money,
            **options,
        )
        if not isinstance(answer, dict):
            raise TransientExternalError("Stripe answered without a JSON object")
        return answer

    @classmethod
    def payment(
        cls, instance: ProviderInstanceModel, intent: Mapping[str, Any]
    ) -> Dict[str, Any]:
        currency = str(intent.get("currency") or "usd")
        charge = intent.get("latest_charge")
        charge = charge if isinstance(charge, Mapping) else {}
        status = _STATUSES.get(str(intent.get("status")), "pending")
        if status == "succeeded" and charge.get("refunded"):
            status = "refunded"
        refunded = charge.get("amount_refunded")
        return cls.payment_answer(
            instance,
            intent.get("id"),
            status=status,
            provider_status=intent.get("status"),
            amount=cls.from_minor_units(int(intent.get("amount") or 0), currency),
            currency=currency,
            customer_id=(
                intent.get("customer")
                if isinstance(intent.get("customer"), str)
                else None
            ),
            amount_refunded=(
                None
                if not isinstance(refunded, int)
                else cls.from_minor_units(refunded, currency)
            ),
            client_secret=intent.get("client_secret"),
        )

    @classmethod
    async def create_payment(
        cls, instance: ProviderInstanceModel, request: PaymentRequest
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "amount": cls.to_minor_units(request.amount, request.currency),
            "currency": request.currency.lower(),
            "customer": request.customer_id,
            "description": request.description,
            "capture_method": "automatic" if request.capture else "manual",
            "metadata": {**request.metadata, "user_id": request.user_id},
            **_EXPAND,
        }
        if request.payment_method_id:
            params["payment_method"] = request.payment_method_id
            params["confirm"] = True
            params["automatic_payment_methods"] = {
                "enabled": True,
                "allow_redirects": "never",
            }
        else:
            params["automatic_payment_methods"] = {"enabled": True}
        intent = await cls.stripe(
            instance,
            "POST",
            "/v1/payment_intents",
            params,
            idempotency_key=request.idempotency_key,
            moves_money=True,
        )
        return cls.payment(instance, intent)

    @classmethod
    async def get_payment(
        cls, instance: ProviderInstanceModel, payment_id: str
    ) -> Dict[str, Any]:
        intent = await cls.stripe(
            instance,
            "GET",
            f"/v1/payment_intents/{path_segment(payment_id, 'payment id')}",
            {"expand[]": "latest_charge"},
        )
        return cls.payment(instance, intent)

    @classmethod
    async def capture_payment(
        cls, instance: ProviderInstanceModel, payment_id: str, action: MoneyAction
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = dict(_EXPAND)
        if action.amount is not None:
            params["amount_to_capture"] = cls.to_minor_units(
                action.amount, action.currency
            )
        intent = await cls.stripe(
            instance,
            "POST",
            f"/v1/payment_intents/{path_segment(payment_id, 'payment id')}/capture",
            params,
            idempotency_key=action.idempotency_key,
            moves_money=True,
        )
        return cls.payment(instance, intent)

    @classmethod
    async def refund_payment(
        cls, instance: ProviderInstanceModel, payment_id: str, action: MoneyAction
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {"payment_intent": payment_id}
        if action.amount is not None:
            params["amount"] = cls.to_minor_units(action.amount, action.currency)
        if action.reason in _REFUND_REASONS:
            params["reason"] = action.reason
        elif action.reason:
            params["metadata"] = {"reason": action.reason}
        refund = await cls.stripe(
            instance,
            "POST",
            "/v1/refunds",
            params,
            idempotency_key=action.idempotency_key,
            moves_money=True,
        )
        currency = str(refund.get("currency") or action.currency)
        return cls.refund_answer(
            instance,
            refund.get("id"),
            payment_id=payment_id,
            status=refund.get("status"),
            amount=cls.from_minor_units(int(refund.get("amount") or 0), currency),
            currency=currency,
        )

    @classmethod
    def customer(
        cls, instance: ProviderInstanceModel, customer: Mapping[str, Any]
    ) -> Dict[str, Any]:
        if customer.get("deleted"):
            raise InvalidInputExternalError(
                "Stripe has deleted this customer", provider=cls.name
            )
        return cls.customer_answer(
            instance,
            customer.get("id"),
            email=customer.get("email"),
            name=customer.get("name"),
        )

    @classmethod
    async def create_customer(
        cls, instance: ProviderInstanceModel, request: CustomerRequest
    ) -> Dict[str, Any]:
        customer = await cls.stripe(
            instance,
            "POST",
            "/v1/customers",
            {
                "email": request.email,
                "name": request.name,
                "metadata": {"user_id": request.user_id},
            },
            idempotency_key=request.idempotency_key,
        )
        return cls.customer(instance, customer)

    @classmethod
    async def get_customer(
        cls, instance: ProviderInstanceModel, customer_id: str
    ) -> Dict[str, Any]:
        customer = await cls.stripe(
            instance, "GET", f"/v1/customers/{path_segment(customer_id, 'customer id')}"
        )
        return cls.customer(instance, customer)

    @classmethod
    def subscription(
        cls, instance: ProviderInstanceModel, subscription: Mapping[str, Any]
    ) -> Dict[str, Any]:
        items = (subscription.get("items") or {}).get("data") or [{}]
        price = items[0].get("price") or {}
        status = str(subscription.get("status") or "")
        customer = subscription.get("customer")
        return cls.subscription_answer(
            instance,
            subscription.get("id"),
            status=status,
            active=status in _ACTIVE,
            plan_id=price.get("id") if isinstance(price, Mapping) else None,
            customer_id=customer if isinstance(customer, str) else None,
            current_period_end=_moment(subscription.get("current_period_end")),
            cancel_at_period_end=bool(subscription.get("cancel_at_period_end")),
        )

    @classmethod
    async def create_subscription(
        cls, instance: ProviderInstanceModel, request: SubscriptionRequest
    ) -> Dict[str, Any]:
        if not request.customer_id:
            raise InvalidInputExternalError(
                "A Stripe subscription is a customer's: make one first",
                provider=cls.name,
            )
        subscription = await cls.stripe(
            instance,
            "POST",
            "/v1/subscriptions",
            {
                "customer": request.customer_id,
                "items": [{"price": request.plan_id}],
                "trial_period_days": request.trial_days,
                "payment_behavior": "default_incomplete",
                "metadata": {**request.metadata, "user_id": request.user_id},
            },
            idempotency_key=request.idempotency_key,
        )
        return cls.subscription(instance, subscription)

    @classmethod
    async def get_subscription(
        cls, instance: ProviderInstanceModel, subscription_id: str
    ) -> Dict[str, Any]:
        subscription = await cls.stripe(
            instance,
            "GET",
            f"/v1/subscriptions/{path_segment(subscription_id, 'subscription id')}",
        )
        return cls.subscription(instance, subscription)

    @classmethod
    async def cancel_subscription(
        cls, instance: ProviderInstanceModel, subscription_id: str, at_period_end: bool
    ) -> Dict[str, Any]:
        path = f"/v1/subscriptions/{path_segment(subscription_id, 'subscription id')}"
        if at_period_end:
            subscription = await cls.stripe(
                instance, "POST", path, {"cancel_at_period_end": True}
            )
        else:
            subscription = await cls.stripe(instance, "DELETE", path)
        return cls.subscription(instance, subscription)

    @classmethod
    async def verify_webhook(
        cls, instance: ProviderInstanceModel, payload: bytes, headers: Dict[str, str]
    ) -> Dict[str, Any]:
        secret = cls.required(instance, "webhook_secret")
        timestamp, signatures = signature_parts(headers.get("stripe-signature", ""))
        if timestamp is None or not signatures:
            raise InvalidInputExternalError("The notification is not signed")
        expected = hmac_sha256(
            secret.encode(), timestamp.encode() + b"." + payload
        ).hex()
        if not one_matches(expected, signatures):
            raise InvalidInputExternalError("The notification's signature is wrong")
        if not fresh(timestamp, time.time()):
            raise InvalidInputExternalError("The notification is too old")
        try:
            event = json.loads(payload)
        except ValueError:
            raise InvalidInputExternalError("The notification is not JSON") from None
        named = ((event.get("data") or {}).get("object")) or {}
        kind, object_id = None, None
        if named.get("object") == "payment_intent":
            kind, object_id = "payment", named.get("id")
        elif named.get("object") in ("charge", "refund") and named.get(
            "payment_intent"
        ):
            kind, object_id = "payment", named.get("payment_intent")
        elif named.get("object") == "subscription":
            kind, object_id = "subscription", named.get("id")
        return cls.event_answer(
            instance,
            event.get("id"),
            event_type=event.get("type"),
            kind=kind,
            object_id=object_id,
        )
