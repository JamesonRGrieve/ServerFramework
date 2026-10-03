# SPDX-License-Identifier: AGPL-3.0-or-later
"""Square on the wire (a local server answering as Square does), its
signed notifications, and the real Square sandbox."""

import base64
import hashlib
import hmac
import json
import uuid
from decimal import Decimal
from typing import Any, Dict

import pytest

from zephyrex.extensions.ExternalErrors import (
    InvalidInputExternalError,
    PermanentExternalError,
)
from zephyrex.extensions.payment.EXT_Payment import (
    CustomerRequest,
    MoneyAction,
    PaymentRequest,
    SubscriptionRequest,
)
from zephyrex.extensions.payment.PRV_Square_Payment import (
    API_VERSION,
    PRV_Square_Payment,
    split_name,
)

KEY = "square-signature-key"
URL = "https://example.test/hooks/square"
PAYMENT = {
    "id": "sq_1",
    "status": "COMPLETED",
    "amount_money": {"amount": 1000, "currency": "CAD"},
    "customer_id": "C1",
}


def answer(body: Any, status: int = 200):
    return (status, {"Content-Type": "application/json"}, json.dumps(body).encode())


def body(request) -> Dict[str, Any]:
    parsed: Dict[str, Any] = json.loads(request.body)
    return parsed


def signature(payload: bytes, key: str = KEY, url: str = URL) -> Dict[str, str]:
    digest = hmac.new(key.encode(), url.encode() + payload, hashlib.sha256).digest()
    return {"x-square-hmacsha256-signature": base64.b64encode(digest).decode()}


def payment_request(**fields: Any) -> PaymentRequest:
    base: Dict[str, Any] = dict(
        amount=Decimal("10"),
        currency="CAD",
        user_id="u1",
        idempotency_key="k1",
        payment_method_id="cnon:card-nonce-ok",
    )
    return PaymentRequest(**{**base, **fields})


@pytest.fixture
def square(local_http_server, provider_instance):
    def _start(routes):
        server = local_http_server(routes)
        instance = provider_instance(
            PRV_Square_Payment,
            api_key="EAAA-local",
            settings={
                "api_base": server.base_url,
                "location_id": "L1",
                "webhook_signature_key": KEY,
                "webhook_notification_url": URL,
            },
        )
        return server, instance

    return _start


class TestWire:
    def test_names(self):
        assert split_name("Ada King Lovelace") == ("Ada", "King Lovelace")
        assert split_name(None) == (None, None)

    async def test_a_payment(self, square):
        server, instance = square({"/v2/payments": answer({"payment": PAYMENT})})
        made = await PRV_Square_Payment.create_payment(
            instance, payment_request(description="order 7", capture=False)
        )
        sent = body(server.requests[0])
        assert sent["amount_money"] == {"amount": 1000, "currency": "CAD"}
        assert (
            sent["source_id"] == "cnon:card-nonce-ok" and sent["autocomplete"] is False
        )
        assert sent["idempotency_key"] == "k1" and sent["note"] == "order 7"
        assert server.requests[0].headers["square-version"] == API_VERSION
        assert made["status"] == "succeeded" and made["amount"] == "10.00"

    async def test_a_payment_needs_a_source(self, square):
        server, instance = square({})
        with pytest.raises(InvalidInputExternalError, match="payment_method_id"):
            await PRV_Square_Payment.create_payment(
                instance, payment_request(payment_method_id=None)
            )
        assert server.requests == []

    async def test_a_full_refund_takes_whats_left(self, square):
        partly = {**PAYMENT, "refunded_money": {"amount": 300, "currency": "CAD"}}
        server, instance = square(
            {
                "/v2/payments/sq_1": answer({"payment": partly}),
                "/v2/refunds": answer(
                    {
                        "refund": {
                            "id": "r1",
                            "status": "PENDING",
                            "amount_money": {"amount": 700, "currency": "CAD"},
                        }
                    }
                ),
            }
        )
        refund = await PRV_Square_Payment.refund_payment(
            instance, "sq_1", MoneyAction(currency="CAD", idempotency_key="k2")
        )
        assert body(server.requests[1])["amount_money"] == {
            "amount": 700,
            "currency": "CAD",
        }
        assert refund["amount"] == "7.00" and refund["status"] == "pending"

    async def test_customers_and_subscriptions(self, square):
        server, instance = square(
            {
                "/v2/customers": answer(
                    {
                        "customer": {
                            "id": "C1",
                            "email_address": "a@b.c",
                            "given_name": "Ada",
                        }
                    }
                ),
                "/v2/subscriptions": answer(
                    {
                        "subscription": {
                            "id": "S1",
                            "status": "ACTIVE",
                            "plan_variation_id": "PV",
                            "customer_id": "C1",
                            "charged_through_date": "2030-01-31",
                        }
                    }
                ),
            }
        )
        customer = await PRV_Square_Payment.create_customer(
            instance,
            CustomerRequest(
                email="a@b.c", user_id="u1", idempotency_key="k", name="Ada"
            ),
        )
        assert customer["customer_id"] == "C1" and customer["name"] == "Ada"
        made = await PRV_Square_Payment.create_subscription(
            instance,
            SubscriptionRequest(
                plan_id="PV", user_id="u1", idempotency_key="k", customer_id="C1"
            ),
        )
        assert body(server.requests[1])["location_id"] == "L1"
        assert made["active"] and made["current_period_end"].day == 31

    async def test_what_square_cannot_do(self, square):
        _, instance = square({})
        with pytest.raises(PermanentExternalError):
            await PRV_Square_Payment.cancel_subscription(instance, "S1", False)


class TestNotifications:
    PAYLOAD = json.dumps(
        {
            "event_id": "e1",
            "type": "payment.updated",
            "data": {"type": "payment", "id": "sq_1", "object": {"payment": PAYMENT}},
        }
    ).encode()

    async def test_signed_over_the_url_and_body(self, square):
        _, instance = square({})
        event = await PRV_Square_Payment.verify_webhook(
            instance, self.PAYLOAD, signature(self.PAYLOAD)
        )
        assert (event["kind"], event["object_id"]) == ("payment", "sq_1")

    @pytest.mark.parametrize(
        "headers",
        [
            {},
            signature(PAYLOAD, url="https://elsewhere.test/"),
            signature(PAYLOAD, key="x"),
        ],
        ids=["unsigned", "other-url", "other-key"],
    )
    async def test_refused(self, square, headers):
        _, instance = square({})
        with pytest.raises(InvalidInputExternalError):
            await PRV_Square_Payment.verify_webhook(instance, self.PAYLOAD, headers)


@pytest.mark.external_api(provider="square")
class TestSandbox:
    """Square's sandbox: a customer, and a payment with its test nonce
    refunded in full."""

    @pytest.fixture
    def instance(self, provider_instance, sandbox_credentials_for):
        creds = sandbox_credentials_for("square")
        return provider_instance(
            PRV_Square_Payment,
            api_key=creds["SQUARE_ACCESS_TOKEN"],
            settings={"api_base": "https://connect.squareupsandbox.com"},
        )

    async def test_customer_round_trip(self, instance):
        made = await PRV_Square_Payment.create_customer(
            instance,
            CustomerRequest(
                email="zephyrex-test@example.com",
                user_id="u",
                idempotency_key=str(uuid.uuid4()),
                name="Zephyrex Test",
            ),
        )
        found = await PRV_Square_Payment.get_customer(instance, made["customer_id"])
        assert found["email"] == "zephyrex-test@example.com"

    async def test_payment_then_full_refund(self, instance):
        """In the main location's currency: Square refuses any other."""
        locations = await PRV_Square_Payment.square(instance, "GET", "/v2/locations")
        main = next(
            (
                location
                for location in locations.get("locations") or []
                if location.get("status") == "ACTIVE"
            ),
            None,
        )
        assert main is not None, "the sandbox account has no active location"
        paid = await PRV_Square_Payment.create_payment(
            instance,
            payment_request(
                currency=main["currency"], idempotency_key=str(uuid.uuid4())
            ),
        )
        await PRV_Square_Payment.refund_payment(
            instance,
            paid["external_id"],
            MoneyAction(currency=paid["currency"], idempotency_key=str(uuid.uuid4())),
        )
        assert paid["status"] == "succeeded"
