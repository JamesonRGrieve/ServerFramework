# SPDX-License-Identifier: AGPL-3.0-or-later
"""PayPal on the wire (a local server answering as PayPal does), its
notifications verified by PayPal, and the real PayPal sandbox."""

import hashlib
import hmac
import json
import uuid
from decimal import Decimal
from typing import Any, Dict

import pytest

from zephyrex.extensions.ExternalErrors import (
    AuthExternalError,
    InvalidInputExternalError,
    PermanentExternalError,
)
from zephyrex.extensions.payment.EXT_Payment import (
    MoneyAction,
    PaymentRequest,
    SubscriptionRequest,
)
from zephyrex.extensions.payment.PRV_PayPal_Payment import PRV_PayPal_Payment

WEBHOOK_ID = "WH-1"
TOKEN = {"access_token": "A21-local", "expires_in": 3600}
ORDER = {
    "id": "O1",
    "intent": "CAPTURE",
    "status": "PAYER_ACTION_REQUIRED",
    "purchase_units": [{"amount": {"currency_code": "USD", "value": "20.00"}}],
    "links": [{"rel": "payer-action", "href": "https://paypal.test/approve/O1"}],
}
CAPTURED = {
    **ORDER,
    "status": "COMPLETED",
    "purchase_units": [
        {
            "amount": {"currency_code": "USD", "value": "20.00"},
            "payments": {
                "captures": [
                    {
                        "id": "CAP1",
                        "status": "COMPLETED",
                        "amount": {"currency_code": "USD", "value": "20.00"},
                    }
                ]
            },
        }
    ],
}
SIGNED_HEADERS = {
    "paypal-auth-algo": "SHA256withRSA",
    "paypal-cert-url": "https://api.paypal.com/v1/notifications/certs/CERT",
    "paypal-transmission-id": "T1",
    "paypal-transmission-sig": "c2ln",
    "paypal-transmission-time": "2026-10-03T00:00:00Z",
}


def answer(body: Any, status: int = 200):
    return (status, {"Content-Type": "application/json"}, json.dumps(body).encode())


def body(request) -> Dict[str, Any]:
    parsed: Dict[str, Any] = json.loads(request.body)
    return parsed


@pytest.fixture
def paypal(local_http_server, provider_instance):
    def _start(routes, **settings):
        server = local_http_server({"/v1/oauth2/token": answer(TOKEN), **routes})
        instance = provider_instance(
            PRV_PayPal_Payment,
            api_key="secret-local",
            settings={
                "api_base": server.base_url,
                "client_id": "client-local",
                "webhook_id": WEBHOOK_ID,
                **settings,
            },
        )
        return server, instance

    return _start


class TestWire:
    async def test_an_order_to_approve(self, paypal):
        server, instance = paypal(
            {"/v2/checkout/orders": answer(ORDER)}, return_url="https://shop.test/back"
        )
        made = await PRV_PayPal_Payment.create_payment(
            instance,
            PaymentRequest(
                amount=Decimal("20"), currency="USD", user_id="u1", idempotency_key="k1"
            ),
        )
        token_call, order_call = server.requests
        assert token_call.body == b"grant_type=client_credentials"
        assert token_call.headers["authorization"].startswith("Basic ")
        sent = body(order_call)
        assert sent["intent"] == "CAPTURE"
        assert sent["purchase_units"][0]["amount"] == {
            "currency_code": "USD",
            "value": "20.00",
        }
        assert sent["purchase_units"][0]["custom_id"] == "u1"
        assert sent["payment_source"]["paypal"]["experience_context"]["return_url"] == (
            "https://shop.test/back"
        )
        assert order_call.headers["paypal-request-id"] == "k1"
        assert made["status"] == "pending"
        assert made["approval_url"] == "https://paypal.test/approve/O1"

    async def test_the_token_is_reused(self, paypal):
        server, instance = paypal({"/v2/checkout/orders/O1": answer(ORDER)})
        await PRV_PayPal_Payment.get_payment(instance, "O1")
        await PRV_PayPal_Payment.get_payment(instance, "O1")
        paths = [request.path for request in server.requests]
        assert paths.count("/v1/oauth2/token") == 1

    async def test_refused_credentials(self, paypal):
        server, instance = paypal(
            {"/v1/oauth2/token": answer({"error": "invalid_client"}, 401)}
        )
        with pytest.raises(AuthExternalError):
            await PRV_PayPal_Payment.get_payment(instance, "O1")

    async def test_capture_then_refund(self, paypal):
        server, instance = paypal(
            {
                "/v2/checkout/orders/O1": lambda request: answer(
                    CAPTURED
                    if any(r.path.endswith("/capture") for r in server.requests)
                    else {**ORDER, "status": "APPROVED"}
                ),
                "/v2/checkout/orders/O1/capture": answer(CAPTURED),
                "/v2/payments/captures/CAP1/refund": answer(
                    {
                        "id": "R1",
                        "status": "COMPLETED",
                        "amount": {"currency_code": "USD", "value": "5.00"},
                    }
                ),
            }
        )
        captured = await PRV_PayPal_Payment.capture_payment(
            instance, "O1", MoneyAction(currency="USD", idempotency_key="k2")
        )
        assert captured["status"] == "succeeded"
        refund = await PRV_PayPal_Payment.refund_payment(
            instance,
            "O1",
            MoneyAction(
                currency="USD",
                idempotency_key="k3",
                amount=Decimal("5"),
                reason="sorry",
            ),
        )
        sent = body(server.requests[-1])
        assert sent == {
            "amount": {"currency_code": "USD", "value": "5.00"},
            "note_to_payer": "sorry",
        }
        assert refund["refund_id"] == "R1" and refund["amount"] == "5.00"

    async def test_a_subscription_needs_no_customer(self, paypal):
        server, instance = paypal(
            {
                "/v1/billing/subscriptions": answer(
                    {
                        "id": "I-1",
                        "status": "APPROVAL_PENDING",
                        "plan_id": "P-1",
                        "links": [{"rel": "approve", "href": "https://paypal.test/s"}],
                    }
                )
            }
        )
        made = await PRV_PayPal_Payment.create_subscription(
            instance,
            SubscriptionRequest(plan_id="P-1", user_id="u1", idempotency_key="k"),
        )
        assert body(server.requests[-1]) == {"plan_id": "P-1", "custom_id": "u1"}
        assert not made["active"] and made["approval_url"] == "https://paypal.test/s"

    async def test_what_paypal_cannot_do(self, paypal):
        _, instance = paypal({})
        with pytest.raises(PermanentExternalError):
            await PRV_PayPal_Payment.cancel_subscription(instance, "I-1", True)


class TestNotifications:
    EVENT = {
        "id": "WH-EVT-1",
        "event_type": "CHECKOUT.ORDER.APPROVED",
        "resource_type": "checkout-order",
        "resource": {"id": "O1"},
    }
    PAYLOAD = json.dumps(EVENT).encode()

    async def test_paypal_vouches_for_it(self, paypal):
        server, instance = paypal(
            {
                "/v1/notifications/verify-webhook-signature": answer(
                    {"verification_status": "SUCCESS"}
                )
            }
        )
        event = await PRV_PayPal_Payment.verify_webhook(
            instance, self.PAYLOAD, SIGNED_HEADERS
        )
        asked = body(server.requests[-1])
        assert asked["webhook_id"] == WEBHOOK_ID
        assert asked["transmission_sig"] == "c2ln"
        assert asked["webhook_event"] == self.EVENT
        assert (event["kind"], event["object_id"]) == ("payment", "O1")

    async def test_a_signature_keyed_with_the_webhook_id_is_refused(self, paypal):
        """The webhook id is no secret (PayPal lists it, it rides in every
        notification), yet the old verification took an HMAC keyed with it
        as proof: anyone could forge a payment notification. PayPal now
        judges the signature."""
        server, instance = paypal(
            {
                "/v1/notifications/verify-webhook-signature": answer(
                    {"verification_status": "FAILURE"}
                )
            }
        )
        forged = hmac.new(WEBHOOK_ID.encode(), self.PAYLOAD, hashlib.sha256).hexdigest()
        with pytest.raises(InvalidInputExternalError, match="signature is wrong"):
            await PRV_PayPal_Payment.verify_webhook(
                instance,
                self.PAYLOAD,
                {**SIGNED_HEADERS, "paypal-transmission-sig": forged},
            )

    async def test_unsigned_is_refused_without_asking(self, paypal):
        server, instance = paypal({})
        with pytest.raises(InvalidInputExternalError):
            await PRV_PayPal_Payment.verify_webhook(instance, self.PAYLOAD, {})
        assert server.requests == []


@pytest.mark.external_api(provider="paypal")
async def test_sandbox_order(provider_instance, sandbox_credentials_for):
    """PayPal's sandbox: an order, read back."""
    creds = sandbox_credentials_for("paypal")
    instance = provider_instance(
        PRV_PayPal_Payment,
        api_key=creds["PAYPAL_SECRET"],
        settings={
            "client_id": creds["PAYPAL_CLIENT_ID"],
            "api_base": "https://api-m.sandbox.paypal.com",
        },
    )
    made = await PRV_PayPal_Payment.create_payment(
        instance,
        PaymentRequest(
            amount=Decimal("1"),
            currency="USD",
            user_id="u",
            idempotency_key=str(uuid.uuid4()),
        ),
    )
    found = await PRV_PayPal_Payment.get_payment(instance, made["external_id"])
    assert found["status"] == "pending" and found["approval_url"]
