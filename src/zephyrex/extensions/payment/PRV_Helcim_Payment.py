# SPDX-License-Identifier: AGPL-3.0-or-later
"""Helcim, through its Payment API v2 (``api-token`` header, an
``idempotency-key`` on every payment request).

A payment is a purchase (or, ``capture=False``, a pre-authorization) of a
card token from HelcimPay.js, and needs the payer's IP address, which
Helcim uses against fraud: so do a capture and a refund. A capture is a
transaction of its own; the payment's record then names it, since a
refund is of the capture. Helcim's customers need a billing address,
which this server does not hold, so customer records and subscriptions
are not offered. Notifications are verified as Helcim signs them
(Standard Webhooks): base64 HMAC-SHA256 over
``<webhook-id>.<webhook-timestamp>.<body>`` with the base64-decoded
verifier token, within five minutes.
"""

import base64
import binascii
import json
import time
from decimal import Decimal
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
    fresh,
    hmac_sha256,
    one_matches,
)
from zephyrex.lib.ProviderHTTPClient import path_segment
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

_KINDS = {"purchase": "succeeded", "capture": "succeeded", "preauth": "authorized"}


def _transaction_id(value: str) -> int:
    if not value.isdigit():
        raise InvalidInputExternalError(f"{value!r} is not a Helcim transaction id")
    return int(value)


class PRV_Helcim_Payment(AbstractPaymentProvider):
    name: ClassVar[str] = "helcim"
    friendly_name: ClassVar[str] = "Helcim"
    description: ClassVar[str] = "A Helcim merchant account"
    _abilities: ClassVar[Set[str]] = {
        "payment_create",
        "payment_get",
        "payment_capture",
        "payment_refund",
        "webhook_process",
    }
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting(
            "api_key", "API token", env="HELCIM_API_TOKEN", secret=True, field="api_key"
        ),
        InstanceSetting(
            "verifier_token",
            "The webhook verifier token",
            env="HELCIM_VERIFIER_TOKEN",
            secret=True,
        ),
        InstanceSetting("api_base", "API address", default="https://api.helcim.com"),
    )

    @classmethod
    def refusal_detail(cls, payload: Any) -> str:
        try:
            errors = json.loads(str(payload)).get("errors")
        except (ValueError, AttributeError):
            return ""
        if isinstance(errors, dict):
            return "; ".join(f"{key}: {value}" for key, value in errors.items())
        if isinstance(errors, list):
            return "; ".join(str(error) for error in errors)
        return str(errors or "")

    @classmethod
    async def helcim(
        cls,
        instance: ProviderInstanceModel,
        method: str,
        path: str,
        body: Optional[Dict[str, Any]] = None,
        *,
        idempotency_key: Optional[str] = None,
        moves_money: bool = False,
    ) -> Dict[str, Any]:
        headers = {"api-token": cls.required(instance, "api_key")}
        if idempotency_key:
            headers["idempotency-key"] = idempotency_key
        answer = await cls.call(
            method,
            cls.endpoint(instance, f"/v2{path}"),
            headers=headers,
            json=body,
            moves_money=moves_money,
        )
        if not isinstance(answer, dict):
            raise TransientExternalError("Helcim answered without a JSON object")
        return answer

    @classmethod
    def payment(
        cls, instance: ProviderInstanceModel, transaction: Mapping[str, Any]
    ) -> Dict[str, Any]:
        approved = str(transaction.get("status")).upper() == "APPROVED"
        kind = str(transaction.get("type") or "").lower()
        return cls.payment_answer(
            instance,
            transaction.get("transactionId"),
            status=_KINDS.get(kind, "succeeded") if approved else "failed",
            provider_status=f"{kind} {transaction.get('status')}".strip(),
            amount=str(transaction.get("amount") or "0"),
            currency=str(transaction.get("currency") or "CAD"),
            customer_id=transaction.get("customerCode") or None,
        )

    @classmethod
    def _ip(cls, customer_ip: Optional[str]) -> str:
        if not customer_ip:
            raise InvalidInputExternalError(
                "Helcim needs customer_ip, the payer's IP address", provider=cls.name
            )
        return customer_ip

    @classmethod
    async def create_payment(
        cls, instance: ProviderInstanceModel, request: PaymentRequest
    ) -> Dict[str, Any]:
        if not request.payment_method_id:
            raise InvalidInputExternalError(
                "A Helcim payment needs payment_method_id: a card token",
                provider=cls.name,
            )
        body: Dict[str, Any] = {
            "amount": float(cls.format_amount(request.amount, request.currency)),
            "currency": request.currency,
            "ipAddress": cls._ip(request.customer_ip),
            "ecommerce": True,
            "cardData": {"cardToken": request.payment_method_id},
        }
        if request.customer_id:
            body["customerCode"] = request.customer_id
        transaction = await cls.helcim(
            instance,
            "POST",
            "/payment/purchase" if request.capture else "/payment/preauth",
            body,
            idempotency_key=request.idempotency_key,
            moves_money=True,
        )
        return cls.payment(instance, transaction)

    @classmethod
    async def get_payment(
        cls, instance: ProviderInstanceModel, payment_id: str
    ) -> Dict[str, Any]:
        transaction = await cls.helcim(
            instance,
            "GET",
            f"/card-transactions/{path_segment(payment_id, 'transaction id')}",
        )
        return cls.payment(instance, transaction)

    @classmethod
    async def _amount(
        cls, instance: ProviderInstanceModel, payment_id: str, action: MoneyAction
    ) -> Decimal:
        if action.amount is not None:
            return action.amount
        return Decimal((await cls.get_payment(instance, payment_id))["amount"])

    @classmethod
    async def capture_payment(
        cls, instance: ProviderInstanceModel, payment_id: str, action: MoneyAction
    ) -> Dict[str, Any]:
        amount = await cls._amount(instance, payment_id, action)
        transaction = await cls.helcim(
            instance,
            "POST",
            "/payment/capture",
            {
                "preAuthTransactionId": _transaction_id(payment_id),
                "amount": float(cls.format_amount(amount, action.currency)),
                "ipAddress": cls._ip(action.customer_ip),
            },
            idempotency_key=action.idempotency_key,
            moves_money=True,
        )
        return cls.payment(instance, transaction)

    @classmethod
    async def refund_payment(
        cls, instance: ProviderInstanceModel, payment_id: str, action: MoneyAction
    ) -> Dict[str, Any]:
        amount = await cls._amount(instance, payment_id, action)
        transaction = await cls.helcim(
            instance,
            "POST",
            "/payment/refund",
            {
                "originalTransactionId": _transaction_id(payment_id),
                "amount": float(cls.format_amount(amount, action.currency)),
                "ipAddress": cls._ip(action.customer_ip),
            },
            idempotency_key=action.idempotency_key,
            moves_money=True,
        )
        return cls.refund_answer(
            instance,
            transaction.get("transactionId"),
            payment_id=payment_id,
            status=transaction.get("status"),
            amount=str(transaction.get("amount") or amount),
            currency=str(transaction.get("currency") or action.currency),
        )

    @classmethod
    async def verify_webhook(
        cls, instance: ProviderInstanceModel, payload: bytes, headers: Dict[str, str]
    ) -> Dict[str, Any]:
        try:
            key = base64.b64decode(
                cls.required(instance, "verifier_token"), validate=True
            )
        except binascii.Error:
            raise InvalidInputExternalError(
                "Helcim verifier_token is not base64", provider=cls.name
            ) from None
        webhook_id = headers.get("webhook-id", "")
        timestamp = headers.get("webhook-timestamp", "")
        signatures = [
            part.partition(",")[2]
            for part in headers.get("webhook-signature", "").split()
            if part.startswith("v1,")
        ]
        if not webhook_id or not timestamp or not signatures:
            raise InvalidInputExternalError("The notification is not signed")
        signed = f"{webhook_id}.{timestamp}.".encode() + payload
        expected = base64.b64encode(hmac_sha256(key, signed)).decode()
        if not one_matches(expected, signatures):
            raise InvalidInputExternalError("The notification's signature is wrong")
        if not fresh(timestamp, time.time()):
            raise InvalidInputExternalError("The notification is too old")
        try:
            event = json.loads(payload)
        except ValueError:
            raise InvalidInputExternalError("The notification is not JSON") from None
        is_transaction = event.get("type") == "cardTransaction"
        return cls.event_answer(
            instance,
            webhook_id,
            event_type=event.get("type"),
            kind="payment" if is_transaction else None,
            object_id=event.get("id") if is_transaction else None,
        )
