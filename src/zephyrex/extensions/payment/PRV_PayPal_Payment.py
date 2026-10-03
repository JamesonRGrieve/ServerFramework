# SPDX-License-Identifier: AGPL-3.0-or-later
"""PayPal, through its REST API: an OAuth 2.0 client-credentials token,
Orders v2 for payments and Billing Subscriptions v1.

A payment is an order the payer approves at its ``approval_url``; the
merchant then captures it (``capture=True`` asks for an order to capture,
``False`` one to authorize first). PayPal keeps no customer records, so a
subscription needs none: the payer approves it at its ``approval_url``.
PayPal ends a subscription at once, never at the period's end.
Notifications are verified by PayPal itself (``verify-webhook-signature``)
against the account's webhook id: their signature is over PayPal's
certificate, not a secret shared with this server.
"""

import base64
import json
from datetime import datetime
from decimal import Decimal
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Set, Tuple

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.ExternalErrors import (
    AuthExternalError,
    InvalidInputExternalError,
    TransientExternalError,
)
from zephyrex.extensions.payment.EXT_Payment import (
    AbstractPaymentProvider,
    MoneyAction,
    PaymentRequest,
    SubscriptionRequest,
)
from zephyrex.lib.ProviderHTTPClient import path_segment
from zephyrex.lib.TokenCache import TokenCache, fingerprint
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

_TOKENS = TokenCache()
_STATUSES = {
    "CREATED": "pending",
    "SAVED": "pending",
    "PAYER_ACTION_REQUIRED": "pending",
    "APPROVED": "authorized",
    "COMPLETED": "succeeded",
    "VOIDED": "canceled",
}
# The headers PayPal signs a notification with.
_SIGNED = {
    "auth_algo": "paypal-auth-algo",
    "cert_url": "paypal-cert-url",
    "transmission_id": "paypal-transmission-id",
    "transmission_sig": "paypal-transmission-sig",
    "transmission_time": "paypal-transmission-time",
}
CUSTOM_ID_CHARACTERS = 127
DESCRIPTION_CHARACTERS = 127


def _link(resource: Mapping[str, Any], *relations: str) -> Optional[str]:
    for link in resource.get("links") or []:
        if isinstance(link, Mapping) and link.get("rel") in relations:
            return str(link.get("href"))
    return None


def _total(entries: List[Any]) -> Decimal:
    return sum(
        (
            Decimal(str((entry.get("amount") or {}).get("value") or "0"))
            for entry in entries
            if isinstance(entry, Mapping)
            and entry.get("status") in ("COMPLETED", "PENDING")
        ),
        Decimal(0),
    )


def _moment(text: Any) -> Optional[datetime]:
    if not isinstance(text, str):
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


class PRV_PayPal_Payment(AbstractPaymentProvider):
    name: ClassVar[str] = "paypal"
    friendly_name: ClassVar[str] = "PayPal"
    description: ClassVar[str] = "A PayPal business account (a REST app)"
    subscriptions_need_customer: ClassVar[bool] = False
    _abilities: ClassVar[Set[str]] = {
        "payment_create",
        "payment_get",
        "payment_capture",
        "payment_refund",
        "subscription_create",
        "subscription_get",
        "subscription_cancel",
        "webhook_process",
    }
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting(
            "client_id", "The REST app's client id", env="PAYPAL_CLIENT_ID"
        ),
        InstanceSetting(
            "api_key",
            "The REST app's secret",
            env="PAYPAL_SECRET",
            secret=True,
            field="api_key",
        ),
        InstanceSetting(
            "webhook_id",
            "The webhook's id (as PayPal lists it)",
            env="PAYPAL_WEBHOOK_ID",
        ),
        InstanceSetting(
            "return_url",
            "Where the payer returns after approving",
            env="PAYPAL_RETURN_URL",
        ),
        InstanceSetting(
            "cancel_url",
            "Where the payer returns after declining",
            env="PAYPAL_CANCEL_URL",
        ),
        InstanceSetting(
            "api_base",
            "API address (https://api-m.sandbox.paypal.com for the sandbox)",
            default="https://api-m.paypal.com",
        ),
    )

    refusal_fields: ClassVar[Tuple[str, ...]] = (
        "message",
        "error_description",
        "description",
    )

    @classmethod
    async def token(cls, instance: ProviderInstanceModel) -> str:
        client_id = cls.required(instance, "client_id")
        secret = cls.required(instance, "api_key")

        async def exchange() -> Tuple[str, float]:
            basic = base64.b64encode(f"{client_id}:{secret}".encode()).decode()
            try:
                answer = await cls.call(
                    "POST",
                    cls.endpoint(instance, "/v1/oauth2/token"),
                    headers={"Authorization": f"Basic {basic}"},
                    data={"grant_type": "client_credentials"},
                )
            except InvalidInputExternalError as exc:
                raise AuthExternalError(
                    "PayPal refused the credentials",
                    provider=cls.name,
                    upstream_status=exc.upstream_status,
                ) from exc
            if not isinstance(answer, dict) or not answer.get("access_token"):
                raise TransientExternalError("PayPal answered without a token")
            return str(answer["access_token"]), float(answer.get("expires_in") or 0)

        return await _TOKENS.obtain(
            f"{cls.name}:{instance.id}", fingerprint(client_id, secret), exchange
        )

    @classmethod
    async def paypal(
        cls,
        instance: ProviderInstanceModel,
        method: str,
        path: str,
        body: Optional[Dict[str, Any]] = None,
        *,
        request_id: Optional[str] = None,
        moves_money: bool = False,
    ) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {await cls.token(instance)}"}
        if request_id:
            headers["PayPal-Request-Id"] = request_id
        if method != "GET":
            headers["Prefer"] = "return=representation"
        answer = await cls.call(
            method,
            cls.endpoint(instance, path),
            headers=headers,
            json=body,
            moves_money=moves_money,
        )
        if answer in (None, ""):
            return {}
        if not isinstance(answer, dict):
            raise TransientExternalError("PayPal answered without a JSON object")
        return answer

    @classmethod
    def payment(
        cls, instance: ProviderInstanceModel, order: Mapping[str, Any]
    ) -> Dict[str, Any]:
        unit = (order.get("purchase_units") or [{}])[0]
        money = unit.get("amount") or {}
        currency = str(money.get("currency_code") or "USD")
        payments = unit.get("payments") or {}
        captured = _total(payments.get("captures") or [])
        refunded = _total(payments.get("refunds") or [])
        status = _STATUSES.get(str(order.get("status")), "pending")
        if order.get("status") == "COMPLETED" and order.get("intent") == "AUTHORIZE":
            status = "succeeded" if captured else "authorized"
        if status == "succeeded" and captured and refunded >= captured:
            status = "refunded"
        return cls.payment_answer(
            instance,
            order.get("id"),
            status=status,
            provider_status=order.get("status"),
            amount=str(money.get("value") or "0"),
            currency=currency,
            amount_refunded=refunded,
            approval_url=_link(order, "approve", "payer-action"),
        )

    @classmethod
    async def create_payment(
        cls, instance: ProviderInstanceModel, request: PaymentRequest
    ) -> Dict[str, Any]:
        unit: Dict[str, Any] = {
            "amount": {
                "currency_code": request.currency,
                "value": cls.format_amount(request.amount, request.currency),
            },
            "custom_id": request.user_id[:CUSTOM_ID_CHARACTERS],
        }
        if request.description:
            unit["description"] = request.description[:DESCRIPTION_CHARACTERS]
        body: Dict[str, Any] = {
            "intent": "CAPTURE" if request.capture else "AUTHORIZE",
            "purchase_units": [unit],
        }
        return_url = cls.setting(instance, "return_url")
        if return_url:
            body["payment_source"] = {
                "paypal": {
                    "experience_context": {
                        "return_url": return_url,
                        "cancel_url": cls.setting(instance, "cancel_url") or return_url,
                    }
                }
            }
        order = await cls.paypal(
            instance,
            "POST",
            "/v2/checkout/orders",
            body,
            request_id=request.idempotency_key,
        )
        return cls.payment(instance, order)

    @classmethod
    def _order_path(cls, payment_id: str) -> str:
        return f"/v2/checkout/orders/{path_segment(payment_id, 'order id')}"

    @classmethod
    async def get_payment(
        cls, instance: ProviderInstanceModel, payment_id: str
    ) -> Dict[str, Any]:
        order = await cls.paypal(instance, "GET", cls._order_path(payment_id))
        return cls.payment(instance, order)

    @classmethod
    async def capture_payment(
        cls, instance: ProviderInstanceModel, payment_id: str, action: MoneyAction
    ) -> Dict[str, Any]:
        """An approved order's payment taken: captured at once (intent
        CAPTURE, all of it), or authorized and then captured (intent
        AUTHORIZE, all of it or ``amount``)."""
        path = cls._order_path(payment_id)
        order = await cls.paypal(instance, "GET", path)
        if order.get("intent") == "CAPTURE":
            if action.amount is not None:
                raise cls.cannot("capture part of an order made to capture")
            await cls.paypal(
                instance,
                "POST",
                f"{path}/capture",
                {},
                request_id=action.idempotency_key,
                moves_money=True,
            )
            return await cls.get_payment(instance, payment_id)
        unit = (order.get("purchase_units") or [{}])[0]
        authorizations = (unit.get("payments") or {}).get("authorizations") or []
        if not authorizations:
            order = await cls.paypal(
                instance,
                "POST",
                f"{path}/authorize",
                {},
                request_id=f"{action.idempotency_key}-authorize",
                moves_money=True,
            )
            unit = (order.get("purchase_units") or [{}])[0]
            authorizations = (unit.get("payments") or {}).get("authorizations") or []
        if not authorizations:
            raise InvalidInputExternalError("The payer has not approved this order")
        body: Dict[str, Any] = {"final_capture": True}
        if action.amount is not None:
            body["amount"] = {
                "currency_code": action.currency,
                "value": cls.format_amount(action.amount, action.currency),
            }
        authorization = path_segment(
            str(authorizations[0].get("id")), "authorization id"
        )
        await cls.paypal(
            instance,
            "POST",
            f"/v2/payments/authorizations/{authorization}/capture",
            body,
            request_id=action.idempotency_key,
            moves_money=True,
        )
        return await cls.get_payment(instance, payment_id)

    @classmethod
    async def refund_payment(
        cls, instance: ProviderInstanceModel, payment_id: str, action: MoneyAction
    ) -> Dict[str, Any]:
        order = await cls.paypal(instance, "GET", cls._order_path(payment_id))
        unit = (order.get("purchase_units") or [{}])[0]
        captures = [
            capture
            for capture in (unit.get("payments") or {}).get("captures") or []
            if capture.get("status") in ("COMPLETED", "PARTIALLY_REFUNDED")
        ]
        if not captures:
            raise InvalidInputExternalError("Nothing of this order was captured")
        body: Dict[str, Any] = {}
        if action.amount is not None:
            body["amount"] = {
                "currency_code": action.currency,
                "value": cls.format_amount(action.amount, action.currency),
            }
        if action.reason:
            body["note_to_payer"] = action.reason[:255]
        capture = path_segment(str(captures[0].get("id")), "capture id")
        refund = await cls.paypal(
            instance,
            "POST",
            f"/v2/payments/captures/{capture}/refund",
            body,
            request_id=action.idempotency_key,
            moves_money=True,
        )
        money = refund.get("amount") or {}
        return cls.refund_answer(
            instance,
            refund.get("id"),
            payment_id=payment_id,
            status=refund.get("status"),
            amount=str(money.get("value") or "0"),
            currency=str(money.get("currency_code") or action.currency),
        )

    @classmethod
    def subscription(
        cls, instance: ProviderInstanceModel, subscription: Mapping[str, Any]
    ) -> Dict[str, Any]:
        status = str(subscription.get("status") or "")
        billing = subscription.get("billing_info") or {}
        return cls.subscription_answer(
            instance,
            subscription.get("id"),
            status=status,
            active=status == "ACTIVE",
            plan_id=subscription.get("plan_id"),
            current_period_end=_moment(billing.get("next_billing_time")),
            approval_url=_link(subscription, "approve"),
        )

    @classmethod
    def _subscription_path(cls, subscription_id: str) -> str:
        return (
            "/v1/billing/subscriptions/"
            f"{path_segment(subscription_id, 'subscription id')}"
        )

    @classmethod
    async def create_subscription(
        cls, instance: ProviderInstanceModel, request: SubscriptionRequest
    ) -> Dict[str, Any]:
        if request.trial_days is not None:
            raise cls.cannot("add a trial: the plan's billing cycles set it")
        body: Dict[str, Any] = {
            "plan_id": request.plan_id,
            "custom_id": request.user_id[:CUSTOM_ID_CHARACTERS],
        }
        return_url = cls.setting(instance, "return_url")
        if return_url:
            body["application_context"] = {
                "return_url": return_url,
                "cancel_url": cls.setting(instance, "cancel_url") or return_url,
            }
        subscription = await cls.paypal(
            instance,
            "POST",
            "/v1/billing/subscriptions",
            body,
            request_id=request.idempotency_key,
        )
        return cls.subscription(instance, subscription)

    @classmethod
    async def get_subscription(
        cls, instance: ProviderInstanceModel, subscription_id: str
    ) -> Dict[str, Any]:
        subscription = await cls.paypal(
            instance, "GET", cls._subscription_path(subscription_id)
        )
        return cls.subscription(instance, subscription)

    @classmethod
    async def cancel_subscription(
        cls, instance: ProviderInstanceModel, subscription_id: str, at_period_end: bool
    ) -> Dict[str, Any]:
        if at_period_end:
            raise cls.cannot("end a subscription at the period's end, only now")
        path = cls._subscription_path(subscription_id)
        await cls.paypal(
            instance,
            "POST",
            f"{path}/cancel",
            {"reason": "Cancelled by the subscriber"},
        )
        return await cls.get_subscription(instance, subscription_id)

    @classmethod
    async def verify_webhook(
        cls, instance: ProviderInstanceModel, payload: bytes, headers: Dict[str, str]
    ) -> Dict[str, Any]:
        webhook_id = cls.required(instance, "webhook_id")
        signed = {field: headers.get(header) for field, header in _SIGNED.items()}
        if not all(signed.values()):
            raise InvalidInputExternalError("The notification is not signed")
        try:
            event = json.loads(payload)
        except ValueError:
            raise InvalidInputExternalError("The notification is not JSON") from None
        answer = await cls.paypal(
            instance,
            "POST",
            "/v1/notifications/verify-webhook-signature",
            {**signed, "webhook_id": webhook_id, "webhook_event": event},
        )
        if answer.get("verification_status") != "SUCCESS":
            raise InvalidInputExternalError("The notification's signature is wrong")
        resource = event.get("resource") or {}
        kind, object_id = None, None
        resource_type = event.get("resource_type")
        if resource_type == "checkout-order":
            kind, object_id = "payment", resource.get("id")
        elif resource_type in ("capture", "authorization", "refund"):
            related = (resource.get("supplementary_data") or {}).get(
                "related_ids"
            ) or {}
            kind, object_id = "payment", related.get("order_id")
        elif resource_type == "subscription":
            kind, object_id = "subscription", resource.get("id")
        return cls.event_answer(
            instance,
            event.get("id"),
            event_type=event.get("event_type"),
            kind=kind,
            object_id=object_id,
        )
