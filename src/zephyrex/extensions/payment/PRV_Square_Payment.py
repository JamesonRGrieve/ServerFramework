# SPDX-License-Identifier: AGPL-3.0-or-later
"""Square, through its v2 REST API (JSON, bearer access token, pinned
``Square-Version``).

A payment needs a source: a card nonce from Square's Web Payments SDK, or
a card on file. ``capture=False`` leaves it APPROVED for a later complete.
A subscription is a customer's, at the account's ``location_id``, to a
plan variation; Square ends one only at the end of the period paid for.
Notifications are verified as Square signs them: base64 HMAC-SHA256 over
the subscription's notification URL followed by the body, in
``x-square-hmacsha256-signature``.
"""

import base64
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, ClassVar, Dict, Mapping, Optional, Set, Tuple

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
    hmac_sha256,
    one_matches,
)
from zephyrex.lib.ProviderHTTPClient import path_segment
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

API_VERSION = "2024-10-17"
_STATUSES = {
    "APPROVED": "authorized",
    "PENDING": "pending",
    "COMPLETED": "succeeded",
    "CANCELED": "canceled",
    "FAILED": "failed",
}
# Square's limit on a payment's note and reference.
NOTE_CHARACTERS = 500
REFERENCE_CHARACTERS = 40


def _minor(money: Any) -> Tuple[int, str]:
    money = money if isinstance(money, Mapping) else {}
    return int(money.get("amount") or 0), str(money.get("currency") or "USD")


def _day_end(day: Any) -> Optional[datetime]:
    """Square's ``YYYY-MM-DD`` billing date as that day's start in UTC."""
    if not isinstance(day, str):
        return None
    try:
        return datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError:
        return None


def split_name(name: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    if not name:
        return None, None
    given, _, family = name.strip().partition(" ")
    return given or None, family.strip() or None


class PRV_Square_Payment(AbstractPaymentProvider):
    name: ClassVar[str] = "square"
    friendly_name: ClassVar[str] = "Square"
    description: ClassVar[str] = "A Square seller account"
    external_api_version: ClassVar[Optional[str]] = API_VERSION
    external_api_version_header: ClassVar[Optional[str]] = "Square-Version"
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
            "Access token",
            env="SQUARE_ACCESS_TOKEN",
            secret=True,
            field="api_key",
        ),
        InstanceSetting(
            "location_id",
            "The location subscriptions are billed at",
            env="SQUARE_LOCATION_ID",
        ),
        InstanceSetting(
            "webhook_signature_key",
            "The webhook subscription's signature key",
            env="SQUARE_WEBHOOK_SIGNATURE_KEY",
            secret=True,
        ),
        InstanceSetting(
            "webhook_notification_url",
            "The URL Square posts this account's notifications to",
            env="SQUARE_WEBHOOK_NOTIFICATION_URL",
        ),
        InstanceSetting(
            "api_base",
            "API address (https://connect.squareupsandbox.com for the sandbox)",
            default="https://connect.squareup.com",
        ),
    )

    refusal_fields: ClassVar[Tuple[str, ...]] = ("detail",)

    @classmethod
    async def square(
        cls,
        instance: ProviderInstanceModel,
        method: str,
        path: str,
        body: Optional[Dict[str, Any]] = None,
        *,
        moves_money: bool = False,
    ) -> Dict[str, Any]:
        answer = await cls.call(
            method,
            cls.endpoint(instance, path),
            headers={"Authorization": f"Bearer {cls.required(instance, 'api_key')}"},
            json=body,
            moves_money=moves_money,
        )
        if not isinstance(answer, dict):
            raise TransientExternalError("Square answered without a JSON object")
        return answer

    @classmethod
    def payment(
        cls, instance: ProviderInstanceModel, payment: Mapping[str, Any]
    ) -> Dict[str, Any]:
        amount, currency = _minor(payment.get("amount_money"))
        refunded, _ = _minor(payment.get("refunded_money"))
        status = _STATUSES.get(str(payment.get("status")), "pending")
        if status == "succeeded" and amount and refunded >= amount:
            status = "refunded"
        return cls.payment_answer(
            instance,
            payment.get("id"),
            status=status,
            provider_status=payment.get("status"),
            amount=cls.from_minor_units(amount, currency),
            currency=currency,
            customer_id=payment.get("customer_id"),
            amount_refunded=cls.from_minor_units(refunded, currency),
        )

    @classmethod
    async def create_payment(
        cls, instance: ProviderInstanceModel, request: PaymentRequest
    ) -> Dict[str, Any]:
        if not request.payment_method_id:
            raise InvalidInputExternalError(
                "A Square payment needs payment_method_id: a card nonce or a card on file",
                provider=cls.name,
            )
        body: Dict[str, Any] = {
            "idempotency_key": request.idempotency_key,
            "source_id": request.payment_method_id,
            "amount_money": {
                "amount": cls.to_minor_units(request.amount, request.currency),
                "currency": request.currency,
            },
            "autocomplete": request.capture,
            "reference_id": request.user_id[:REFERENCE_CHARACTERS],
        }
        if request.customer_id:
            body["customer_id"] = request.customer_id
        if request.description:
            body["note"] = request.description[:NOTE_CHARACTERS]
        answer = await cls.square(
            instance, "POST", "/v2/payments", body, moves_money=True
        )
        return cls.payment(instance, answer.get("payment") or {})

    @classmethod
    async def get_payment(
        cls, instance: ProviderInstanceModel, payment_id: str
    ) -> Dict[str, Any]:
        answer = await cls.square(
            instance, "GET", f"/v2/payments/{path_segment(payment_id, 'payment id')}"
        )
        return cls.payment(instance, answer.get("payment") or {})

    @classmethod
    async def capture_payment(
        cls, instance: ProviderInstanceModel, payment_id: str, action: MoneyAction
    ) -> Dict[str, Any]:
        if action.amount is not None:
            current = await cls.get_payment(instance, payment_id)
            if Decimal(current["amount"]) != action.amount:
                raise cls.cannot("capture part of a payment")
        answer = await cls.square(
            instance,
            "POST",
            f"/v2/payments/{path_segment(payment_id, 'payment id')}/complete",
            {},
            moves_money=True,
        )
        return cls.payment(instance, answer.get("payment") or {})

    @classmethod
    async def refund_payment(
        cls, instance: ProviderInstanceModel, payment_id: str, action: MoneyAction
    ) -> Dict[str, Any]:
        """Square takes the amount on every refund: with none, what is left."""
        if action.amount is None:
            current = await cls.get_payment(instance, payment_id)
            left = Decimal(current["amount"]) - Decimal(
                current["amount_refunded"] or "0"
            )
            minor = cls.to_minor_units(left, action.currency)
        else:
            minor = cls.to_minor_units(action.amount, action.currency)
        body: Dict[str, Any] = {
            "idempotency_key": action.idempotency_key,
            "payment_id": payment_id,
            "amount_money": {"amount": minor, "currency": action.currency},
        }
        if action.reason:
            body["reason"] = action.reason[:192]
        answer = await cls.square(
            instance, "POST", "/v2/refunds", body, moves_money=True
        )
        refund = answer.get("refund") or {}
        amount, currency = _minor(refund.get("amount_money"))
        return cls.refund_answer(
            instance,
            refund.get("id"),
            payment_id=payment_id,
            status=refund.get("status"),
            amount=cls.from_minor_units(amount, currency),
            currency=currency,
        )

    @classmethod
    def customer(
        cls, instance: ProviderInstanceModel, customer: Mapping[str, Any]
    ) -> Dict[str, Any]:
        name = " ".join(
            part
            for part in (customer.get("given_name"), customer.get("family_name"))
            if part
        )
        return cls.customer_answer(
            instance,
            customer.get("id"),
            email=customer.get("email_address"),
            name=name or None,
        )

    @classmethod
    async def create_customer(
        cls, instance: ProviderInstanceModel, request: CustomerRequest
    ) -> Dict[str, Any]:
        given, family = split_name(request.name)
        body: Dict[str, Any] = {
            "idempotency_key": request.idempotency_key,
            "email_address": request.email,
            "reference_id": request.user_id[:REFERENCE_CHARACTERS],
        }
        if given:
            body["given_name"] = given
        if family:
            body["family_name"] = family
        answer = await cls.square(instance, "POST", "/v2/customers", body)
        return cls.customer(instance, answer.get("customer") or {})

    @classmethod
    async def get_customer(
        cls, instance: ProviderInstanceModel, customer_id: str
    ) -> Dict[str, Any]:
        answer = await cls.square(
            instance,
            "GET",
            f"/v2/customers/{path_segment(customer_id, 'customer id')}",
        )
        return cls.customer(instance, answer.get("customer") or {})

    @classmethod
    def subscription(
        cls, instance: ProviderInstanceModel, subscription: Mapping[str, Any]
    ) -> Dict[str, Any]:
        status = str(subscription.get("status") or "")
        return cls.subscription_answer(
            instance,
            subscription.get("id"),
            status=status,
            active=status == "ACTIVE",
            plan_id=subscription.get("plan_variation_id"),
            customer_id=subscription.get("customer_id"),
            current_period_end=_day_end(subscription.get("charged_through_date")),
            cancel_at_period_end=status == "ACTIVE"
            and bool(subscription.get("canceled_date")),
        )

    @classmethod
    async def create_subscription(
        cls, instance: ProviderInstanceModel, request: SubscriptionRequest
    ) -> Dict[str, Any]:
        if not request.customer_id:
            raise InvalidInputExternalError(
                "A Square subscription is a customer's: make one first",
                provider=cls.name,
            )
        if request.trial_days is not None:
            raise cls.cannot("add a trial: a plan variation's phases set it")
        answer = await cls.square(
            instance,
            "POST",
            "/v2/subscriptions",
            {
                "idempotency_key": request.idempotency_key,
                "location_id": cls.required(instance, "location_id"),
                "plan_variation_id": request.plan_id,
                "customer_id": request.customer_id,
            },
        )
        return cls.subscription(instance, answer.get("subscription") or {})

    @classmethod
    async def get_subscription(
        cls, instance: ProviderInstanceModel, subscription_id: str
    ) -> Dict[str, Any]:
        answer = await cls.square(
            instance,
            "GET",
            f"/v2/subscriptions/{path_segment(subscription_id, 'subscription id')}",
        )
        return cls.subscription(instance, answer.get("subscription") or {})

    @classmethod
    async def cancel_subscription(
        cls, instance: ProviderInstanceModel, subscription_id: str, at_period_end: bool
    ) -> Dict[str, Any]:
        if not at_period_end:
            raise cls.cannot("end a subscription before the period paid for ends")
        answer = await cls.square(
            instance,
            "POST",
            f"/v2/subscriptions/{path_segment(subscription_id, 'subscription id')}"
            "/cancel",
            {},
        )
        return cls.subscription(instance, answer.get("subscription") or {})

    @classmethod
    async def verify_webhook(
        cls, instance: ProviderInstanceModel, payload: bytes, headers: Dict[str, str]
    ) -> Dict[str, Any]:
        key = cls.required(instance, "webhook_signature_key")
        url = cls.required(instance, "webhook_notification_url")
        signature = headers.get("x-square-hmacsha256-signature", "")
        if not signature:
            raise InvalidInputExternalError("The notification is not signed")
        expected = base64.b64encode(
            hmac_sha256(key.encode(), url.encode() + payload)
        ).decode()
        if not one_matches(expected, [signature]):
            raise InvalidInputExternalError("The notification's signature is wrong")
        try:
            event = json.loads(payload)
        except ValueError:
            raise InvalidInputExternalError("The notification is not JSON") from None
        data = event.get("data") or {}
        named = data.get("object") or {}
        kind, object_id = None, None
        if data.get("type") == "payment":
            kind, object_id = "payment", (named.get("payment") or {}).get("id")
        elif data.get("type") == "refund":
            kind, object_id = "payment", (named.get("refund") or {}).get("payment_id")
        elif data.get("type") == "subscription":
            kind, object_id = "subscription", data.get("id")
        return cls.event_answer(
            instance,
            event.get("event_id"),
            event_type=event.get("type"),
            kind=kind,
            object_id=object_id,
        )
