# SPDX-License-Identifier: AGPL-3.0-or-later
"""Moneris, through the Moneris API (``api.moneris.io``; the sandbox is
``api.sb.moneris.io``): an API key (``X-Api-Key``), the merchant's
thirteen-character id (``X-Merchant-Id``) and the pinned ``Api-Version``
on every request, an ``idempotencyKey`` in every body that moves money.

A payment charges a Moneris token (temporary from Moneris Checkout, or
permanent); amounts are in minor units. Moneris documents no field that
holds a payment for later capture, so only immediate payments are taken
here; an authorized payment (made elsewhere) can still be completed.
Customer records, subscriptions and notifications are not offered:
Moneris documents no signed notifications, and the earlier verification
here (an HMAC keyed with the store id, which is not a secret) let anyone
forge one.
"""

import uuid
from typing import Any, ClassVar, Dict, Mapping, Optional, Set, Tuple

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.ExternalErrors import (
    InvalidInputExternalError,
    TransientExternalError,
)
from zephyrex.extensions.payment.EXT_Payment import (
    AbstractPaymentProvider,
    MoneyAction,
    PaymentRequest,
)
from zephyrex.lib.ProviderHTTPClient import path_segment
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

API_VERSION = "2026-08-14"
_STATUSES = {
    "SUCCEEDED": "succeeded",
    "AUTHORIZED": "authorized",
    "PROCESSING": "pending",
    "DECLINED": "failed",
    "CANCELED": "canceled",
}
ORDER_ID_CHARACTERS = 50
TEMPORARY_TOKEN_PREFIX = "ot-"


def _minor(money: Any) -> Tuple[int, str]:
    money = money if isinstance(money, Mapping) else {}
    return int(money.get("amount") or 0), str(money.get("currency") or "CAD")


class PRV_Moneris_Payment(AbstractPaymentProvider):
    name: ClassVar[str] = "moneris"
    friendly_name: ClassVar[str] = "Moneris"
    description: ClassVar[str] = "A Moneris merchant account"
    _abilities: ClassVar[Set[str]] = {
        "payment_create",
        "payment_get",
        "payment_capture",
        "payment_refund",
    }
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting(
            "api_key", "API key", env="MONERIS_API_KEY", secret=True, field="api_key"
        ),
        InstanceSetting(
            "merchant_id",
            "The thirteen-character merchant id",
            env="MONERIS_MERCHANT_ID",
        ),
        InstanceSetting(
            "api_base",
            "API address (https://api.sb.moneris.io for the sandbox)",
            default="https://api.moneris.io",
        ),
    )

    refusal_fields: ClassVar[Tuple[str, ...]] = ("detail", "errorMessage")

    @classmethod
    async def moneris(
        cls,
        instance: ProviderInstanceModel,
        method: str,
        path: str,
        body: Optional[Dict[str, Any]] = None,
        *,
        moves_money: bool = False,
    ) -> Dict[str, Any]:
        headers = {
            "X-Api-Key": cls.required(instance, "api_key"),
            "X-Merchant-Id": cls.required(instance, "merchant_id"),
            "Api-Version": API_VERSION,
            "X-Correlation-Id": str(uuid.uuid4()),
        }
        answer = await cls.call(
            method,
            cls.endpoint(instance, path),
            headers=headers,
            json=body,
            moves_money=moves_money,
        )
        if not isinstance(answer, dict):
            raise TransientExternalError("Moneris answered without a JSON object")
        return answer

    @classmethod
    def payment(
        cls, instance: ProviderInstanceModel, payment: Mapping[str, Any]
    ) -> Dict[str, Any]:
        amount, currency = _minor(payment.get("amount"))
        return cls.payment_answer(
            instance,
            payment.get("paymentId"),
            status=_STATUSES.get(str(payment.get("paymentStatus")), "pending"),
            provider_status=payment.get("paymentStatus"),
            amount=cls.from_minor_units(amount, currency),
            currency=currency,
            customer_id=payment.get("customerId"),
        )

    @classmethod
    async def create_payment(
        cls, instance: ProviderInstanceModel, request: PaymentRequest
    ) -> Dict[str, Any]:
        if not request.capture:
            raise cls.cannot("hold a payment for later capture")
        token = request.payment_method_id
        if not token:
            raise InvalidInputExternalError(
                "A Moneris payment needs payment_method_id: a Moneris token",
                provider=cls.name,
            )
        if token.startswith(TEMPORARY_TOKEN_PREFIX):
            method = {"paymentMethodSource": "TEMPORARY_TOKEN", "temporaryToken": token}
        else:
            method = {"paymentMethodSource": "PERMANENT_TOKEN", "permanentToken": token}
        body: Dict[str, Any] = {
            "idempotencyKey": request.idempotency_key,
            "orderId": request.idempotency_key[:ORDER_ID_CHARACTERS],
            "amount": {
                "amount": cls.to_minor_units(request.amount, request.currency),
                "currency": request.currency,
            },
            "paymentMethod": method,
        }
        if request.customer_id:
            body["customerId"] = request.customer_id
        payment = await cls.moneris(
            instance, "POST", "/payments", body, moves_money=True
        )
        return cls.payment(instance, payment)

    @classmethod
    async def get_payment(
        cls, instance: ProviderInstanceModel, payment_id: str
    ) -> Dict[str, Any]:
        payment = await cls.moneris(
            instance, "GET", f"/payments/{path_segment(payment_id, 'payment id')}"
        )
        return cls.payment(instance, payment)

    @classmethod
    async def capture_payment(
        cls, instance: ProviderInstanceModel, payment_id: str, action: MoneyAction
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {"idempotencyKey": action.idempotency_key}
        if action.amount is not None:
            body["amount"] = {
                "amount": cls.to_minor_units(action.amount, action.currency),
                "currency": action.currency,
            }
        answer = await cls.moneris(
            instance,
            "POST",
            f"/payments/{path_segment(payment_id, 'payment id')}/complete",
            body,
            moves_money=True,
        )
        payment = answer.get("payment")
        return cls.payment(instance, payment if isinstance(payment, dict) else answer)

    @classmethod
    async def refund_payment(
        cls, instance: ProviderInstanceModel, payment_id: str, action: MoneyAction
    ) -> Dict[str, Any]:
        """Moneris takes the amount on every refund: with none, all of it."""
        if action.amount is None:
            current = await cls.get_payment(instance, payment_id)
            minor = cls.to_minor_units(current["amount"], action.currency)
        else:
            minor = cls.to_minor_units(action.amount, action.currency)
        body: Dict[str, Any] = {
            "paymentId": payment_id,
            "idempotencyKey": action.idempotency_key,
            "refundAmount": {"amount": minor, "currency": action.currency},
        }
        if action.reason:
            body["reason"] = action.reason
        refund = await cls.moneris(instance, "POST", "/refunds", body, moves_money=True)
        amount, currency = _minor(refund.get("refundAmount"))
        return cls.refund_answer(
            instance,
            refund.get("refundId"),
            payment_id=payment_id,
            status=refund.get("refundStatus"),
            amount=cls.from_minor_units(amount, currency),
            currency=currency,
        )
