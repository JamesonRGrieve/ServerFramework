# SPDX-License-Identifier: AGPL-3.0-or-later
"""Stripe on the wire (a local server answering as Stripe does), its
signed notifications, and the real Stripe test mode."""

import hashlib
import hmac
import json
import time
from decimal import Decimal
from typing import Any, Dict
from urllib.parse import parse_qs

import httpx
import pytest

from zephyrex.extensions.ExternalErrors import (
    AuthExternalError,
    InvalidInputExternalError,
    PermanentExternalError,
    TransientExternalError,
)
from zephyrex.extensions.payment.EXT_Payment import (
    CustomerRequest,
    MoneyAction,
    PaymentRequest,
    SubscriptionRequest,
)
from zephyrex.extensions.payment.PRV_Stripe_Payment import (
    API_VERSION,
    PRV_Stripe_Payment,
    form,
    signature_parts,
)

SECRET = "whsec_test_secret"
INTENT = {
    "id": "pi_1",
    "object": "payment_intent",
    "amount": 1250,
    "currency": "usd",
    "status": "succeeded",
    "customer": "cus_1",
    "client_secret": "pi_1_secret_x",
    "latest_charge": {"id": "ch_1", "amount_refunded": 0, "refunded": False},
}
SUBSCRIPTION = {
    "id": "sub_1",
    "object": "subscription",
    "status": "active",
    "customer": "cus_1",
    "current_period_end": 1_900_000_000,
    "cancel_at_period_end": False,
    "items": {"data": [{"price": {"id": "price_1"}}]},
}


def answer(body: Any, status: int = 200):
    return (status, {"Content-Type": "application/json"}, json.dumps(body).encode())


def sent(request) -> Dict[str, Any]:
    return {k: v[0] for k, v in parse_qs(request.body.decode()).items()}


def signed(payload: bytes, secret: str = SECRET, at: int = 0) -> Dict[str, str]:
    timestamp = str(at or int(time.time()))
    digest = hmac.new(
        secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256
    ).hexdigest()
    return {"stripe-signature": f"t={timestamp},v1={digest}"}


def request(**fields: Any) -> PaymentRequest:
    base: Dict[str, Any] = dict(
        amount=Decimal("12.50"), currency="USD", user_id="u1", idempotency_key="k1"
    )
    return PaymentRequest(**{**base, **fields})


@pytest.fixture
def stripe(local_http_server, provider_instance):
    def _start(routes, **settings):
        server = local_http_server(routes)
        instance = provider_instance(
            PRV_Stripe_Payment,
            api_key="sk_test_local",
            settings={
                "api_base": server.base_url,
                "webhook_secret": SECRET,
                **settings,
            },
        )
        return server, instance

    return _start


class TestEncoding:
    def test_nested_form(self):
        assert form(
            {
                "amount": 5,
                "confirm": True,
                "customer": None,
                "metadata": {"user_id": "u1"},
                "items": [{"price": "price_1"}],
            }
        ) == {
            "amount": "5",
            "confirm": "true",
            "metadata[user_id]": "u1",
            "items[0][price]": "price_1",
        }

    def test_signature_header(self):
        assert signature_parts("t=12,v1=aa,v0=bb,v1=cc") == ("12", ["aa", "cc"])
        assert signature_parts("garbage") == (None, [])


class TestWire:
    async def test_a_payment_with_a_card_is_confirmed_once(self, stripe):
        server, instance = stripe({"/v1/payment_intents": answer(INTENT)})
        made = await PRV_Stripe_Payment.create_payment(
            instance,
            request(
                payment_method_id="pm_card", customer_id="cus_1", metadata={"o": "7"}
            ),
        )
        (call,) = server.requests
        body = sent(call)
        assert body["amount"] == "1250" and body["currency"] == "usd"
        assert body["payment_method"] == "pm_card" and body["confirm"] == "true"
        assert body["metadata[user_id]"] == "u1" and body["metadata[o]"] == "7"
        assert body["capture_method"] == "automatic"
        assert call.headers["idempotency-key"] == "k1"
        assert call.headers["stripe-version"] == API_VERSION
        assert call.headers["authorization"] == "Bearer sk_test_local"
        assert made["status"] == "succeeded" and made["amount"] == "12.50"
        assert made["client_secret"] == "pi_1_secret_x"
        assert made["provider_instance_id"] == str(instance.id)

    async def test_an_authorization_only_is_manual_capture(self, stripe):
        server, instance = stripe(
            {"/v1/payment_intents": answer({**INTENT, "status": "requires_capture"})}
        )
        made = await PRV_Stripe_Payment.create_payment(instance, request(capture=False))
        assert sent(server.requests[0])["capture_method"] == "manual"
        assert made["status"] == "authorized"

    async def test_a_zero_decimal_currency(self, stripe):
        server, instance = stripe(
            {
                "/v1/payment_intents": answer(
                    {**INTENT, "amount": 500, "currency": "jpy"}
                )
            }
        )
        made = await PRV_Stripe_Payment.create_payment(
            instance, request(amount=Decimal("500"), currency="JPY")
        )
        assert sent(server.requests[0])["amount"] == "500"
        assert made["amount"] == "500" and made["currency"] == "JPY"

    async def test_a_refunded_payment_reads_as_refunded(self, stripe):
        refunded = {
            **INTENT,
            "latest_charge": {"amount_refunded": 1250, "refunded": True},
        }
        _, instance = stripe(
            {"/v1/payment_intents/pi_1?expand%5B%5D=latest_charge": answer(refunded)}
        )
        found = await PRV_Stripe_Payment.get_payment(instance, "pi_1")
        assert found["status"] == "refunded" and found["amount_refunded"] == "12.50"

    async def test_a_refund_keeps_an_unknown_reason_in_metadata(self, stripe):
        server, instance = stripe(
            {
                "/v1/refunds": answer(
                    {
                        "id": "re_1",
                        "status": "succeeded",
                        "amount": 500,
                        "currency": "usd",
                    }
                )
            }
        )
        refund = await PRV_Stripe_Payment.refund_payment(
            instance,
            "pi_1",
            MoneyAction(
                currency="USD", idempotency_key="k2", amount=Decimal("5"), reason="late"
            ),
        )
        body = sent(server.requests[0])
        assert body == {
            "payment_intent": "pi_1",
            "amount": "500",
            "metadata[reason]": "late",
        }
        assert refund["amount"] == "5.00" and refund["status"] == "succeeded"

    async def test_capture_of_part(self, stripe):
        server, instance = stripe({"/v1/payment_intents/pi_1/capture": answer(INTENT)})
        await PRV_Stripe_Payment.capture_payment(
            instance,
            "pi_1",
            MoneyAction(currency="USD", idempotency_key="k3", amount=Decimal("10")),
        )
        assert sent(server.requests[0])["amount_to_capture"] == "1000"

    async def test_customer_and_subscription(self, stripe):
        server, instance = stripe(
            {
                "/v1/customers": answer(
                    {"id": "cus_1", "email": "a@b.c", "name": "A B"}
                ),
                "/v1/subscriptions": answer(SUBSCRIPTION),
                "/v1/subscriptions/sub_1": answer(
                    {**SUBSCRIPTION, "cancel_at_period_end": True}
                ),
            }
        )
        customer = await PRV_Stripe_Payment.create_customer(
            instance, CustomerRequest(email="a@b.c", user_id="u1", idempotency_key="k")
        )
        assert customer["customer_id"] == "cus_1"
        subscription = await PRV_Stripe_Payment.create_subscription(
            instance,
            SubscriptionRequest(
                plan_id="price_1",
                user_id="u1",
                idempotency_key="k",
                customer_id="cus_1",
            ),
        )
        assert sent(server.requests[1])["items[0][price]"] == "price_1"
        assert sent(server.requests[1])["payment_behavior"] == "default_incomplete"
        assert subscription["active"] and subscription["plan_id"] == "price_1"
        assert subscription["current_period_end"].year == 2030
        ending = await PRV_Stripe_Payment.cancel_subscription(instance, "sub_1", True)
        assert sent(server.requests[2]) == {"cancel_at_period_end": "true"}
        assert ending["cancel_at_period_end"]

    async def test_a_subscription_needs_a_customer(self, stripe):
        _, instance = stripe({})
        with pytest.raises(InvalidInputExternalError):
            await PRV_Stripe_Payment.create_subscription(
                instance,
                SubscriptionRequest(plan_id="p", user_id="u", idempotency_key="k"),
            )

    async def test_an_id_is_one_path_segment(self, stripe):
        server, instance = stripe({})
        with pytest.raises(InvalidInputExternalError):
            await PRV_Stripe_Payment.get_payment(instance, "../v1/customers")
        assert server.requests == []


class TestFailures:
    async def test_a_declined_card_carries_stripes_reason(self, stripe):
        declined = {
            "error": {"type": "card_error", "message": "Your card was declined."}
        }
        _, instance = stripe({"/v1/payment_intents": answer(declined, 402)})
        with pytest.raises(InvalidInputExternalError, match="card was declined"):
            await PRV_Stripe_Payment.create_payment(instance, request())

    async def test_a_refused_key(self, stripe):
        _, instance = stripe(
            {
                "/v1/customers/cus_1": answer(
                    {"error": {"message": "Invalid API Key"}}, 401
                )
            }
        )
        with pytest.raises(AuthExternalError):
            await PRV_Stripe_Payment.get_customer(instance, "cus_1")

    async def test_an_unclear_answer_to_a_payment_is_not_retried_elsewhere(
        self, stripe
    ):
        """A 5xx after a charge was sent may hide a taken payment: it is
        permanent (the rotation stops), where a read stays transient."""
        server, instance = stripe(
            {
                "/v1/payment_intents": answer({}, 503),
                "/v1/customers/cus_1": answer({}, 503),
            }
        )
        with pytest.raises(PermanentExternalError, match="unknown"):
            await PRV_Stripe_Payment.create_payment(instance, request())
        with pytest.raises(TransientExternalError):
            await PRV_Stripe_Payment.get_customer(instance, "cus_1")

    async def test_an_account_without_a_key_calls_nothing(
        self, local_http_server, provider_instance, set_env
    ):
        set_env("STRIPE_API_KEY", "")
        server = local_http_server({})
        keyless = provider_instance(
            PRV_Stripe_Payment, settings={"api_base": server.base_url}
        )
        with pytest.raises(TransientExternalError, match="not configured"):
            await PRV_Stripe_Payment.create_payment(keyless, request())
        assert server.requests == []


class TestNotifications:
    PAYLOAD = json.dumps(
        {
            "id": "evt_1",
            "type": "payment_intent.succeeded",
            "data": {"object": {"object": "payment_intent", "id": "pi_1"}},
        }
    ).encode()

    async def test_a_signed_notification_names_its_payment(self, stripe):
        _, instance = stripe({})
        event = await PRV_Stripe_Payment.verify_webhook(
            instance, self.PAYLOAD, signed(self.PAYLOAD)
        )
        assert (event["event_id"], event["kind"], event["object_id"]) == (
            "evt_1",
            "payment",
            "pi_1",
        )

    @pytest.mark.parametrize(
        "headers",
        [
            {},
            {"stripe-signature": "t=1,v1="},
            signed(PAYLOAD, secret="whsec_other"),
            signed(PAYLOAD + b" "),
            signed(PAYLOAD, at=int(time.time()) - 3600),
        ],
        ids=["unsigned", "empty", "other-secret", "tampered", "stale"],
    )
    async def test_refused(self, stripe, headers):
        _, instance = stripe({})
        with pytest.raises(InvalidInputExternalError):
            await PRV_Stripe_Payment.verify_webhook(instance, self.PAYLOAD, headers)

    async def test_a_charge_names_its_payment_intent(self, stripe):
        _, instance = stripe({})
        payload = json.dumps(
            {
                "id": "evt_2",
                "type": "charge.refunded",
                "data": {"object": {"object": "charge", "payment_intent": "pi_9"}},
            }
        ).encode()
        event = await PRV_Stripe_Payment.verify_webhook(
            instance, payload, signed(payload)
        )
        assert event["object_id"] == "pi_9"


def _online() -> bool:
    try:
        httpx.head("https://api.stripe.com", timeout=5)
        return True
    except httpx.HTTPError:
        return False


class TestRealStripe:
    async def test_a_bogus_key_is_refused(self, provider_instance):
        if not _online():
            pytest.xfail("https://api.stripe.com is unreachable")
        bogus = provider_instance(PRV_Stripe_Payment, api_key="sk_test_bogus")
        with pytest.raises(AuthExternalError):
            await PRV_Stripe_Payment.get_customer(bogus, "cus_none")

    @pytest.mark.external_api(provider="stripe")
    async def test_test_mode_customer_payment_and_refund(
        self, provider_instance, sandbox_credentials_for
    ):
        """Test mode: a customer, a payment with Stripe's test card, a full
        refund."""
        creds = sandbox_credentials_for("stripe")
        instance = provider_instance(
            PRV_Stripe_Payment, api_key=creds["STRIPE_API_KEY"]
        )
        customer = await PRV_Stripe_Payment.create_customer(
            instance,
            CustomerRequest(
                email="zephyrex-test@example.com",
                user_id="u",
                idempotency_key=str(time.time()),
            ),
        )
        paid = await PRV_Stripe_Payment.create_payment(
            instance,
            request(
                payment_method_id="pm_card_visa",
                customer_id=customer["customer_id"],
                idempotency_key=f"pay-{time.time()}",
            ),
        )
        assert paid["status"] == "succeeded"
        await PRV_Stripe_Payment.refund_payment(
            instance,
            paid["external_id"],
            MoneyAction(currency="USD", idempotency_key=f"refund-{time.time()}"),
        )
        assert (await PRV_Stripe_Payment.get_payment(instance, paid["external_id"]))[
            "status"
        ] == "refunded"
