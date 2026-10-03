# SPDX-License-Identifier: AGPL-3.0-or-later
"""Moneris on the wire (a local server answering as the Moneris API
does), and what it does not offer."""

import hashlib
import hmac
import json
import os
import uuid
from decimal import Decimal
from typing import Any, Dict

import pytest

from zephyrex.extensions.ExternalErrors import (
    InvalidInputExternalError,
    PermanentExternalError,
)
from zephyrex.extensions.payment.EXT_Payment import MoneyAction, PaymentRequest
from zephyrex.extensions.payment.PRV_Moneris_Payment import (
    API_VERSION,
    PRV_Moneris_Payment,
)

MERCHANT = "0123456789101"
PAYMENT = {
    "paymentId": "pay_1",
    "paymentStatus": "SUCCEEDED",
    "amount": {"amount": 2500, "currency": "CAD"},
}


def answer(body: Any, status: int = 200):
    return (status, {"Content-Type": "application/json"}, json.dumps(body).encode())


def body(request) -> Dict[str, Any]:
    parsed: Dict[str, Any] = json.loads(request.body)
    return parsed


@pytest.fixture
def moneris(local_http_server, provider_instance):
    def _start(routes):
        server = local_http_server(routes)
        instance = provider_instance(
            PRV_Moneris_Payment,
            api_key="moneris-key",
            settings={"api_base": server.base_url, "merchant_id": MERCHANT},
        )
        return server, instance

    return _start


def payment_request(**fields: Any) -> PaymentRequest:
    base: Dict[str, Any] = dict(
        amount=Decimal("25"),
        currency="CAD",
        user_id="u1",
        idempotency_key="8c5b1f2e-0000-4000-8000-000000000001",
        payment_method_id="ot-temporary",
    )
    return PaymentRequest(**{**base, **fields})


class TestWire:
    async def test_a_payment_with_a_temporary_token(self, moneris):
        server, instance = moneris({"/payments": answer(PAYMENT, 201)})
        made = await PRV_Moneris_Payment.create_payment(instance, payment_request())
        call = server.requests[0]
        sent = body(call)
        assert sent["amount"] == {"amount": 2500, "currency": "CAD"}
        assert sent["paymentMethod"] == {
            "paymentMethodSource": "TEMPORARY_TOKEN",
            "temporaryToken": "ot-temporary",
        }
        assert sent["idempotencyKey"] == payment_request().idempotency_key
        assert call.headers["x-api-key"] == "moneris-key"
        assert call.headers["x-merchant-id"] == MERCHANT
        assert call.headers["api-version"] == API_VERSION
        assert made["status"] == "succeeded" and made["amount"] == "25.00"

    async def test_a_permanent_token(self, moneris):
        server, instance = moneris({"/payments": answer(PAYMENT, 201)})
        await PRV_Moneris_Payment.create_payment(
            instance, payment_request(payment_method_id="perm-token")
        )
        assert body(server.requests[0])["paymentMethod"]["paymentMethodSource"] == (
            "PERMANENT_TOKEN"
        )

    async def test_a_full_refund_reads_the_amount(self, moneris):
        server, instance = moneris(
            {
                "/payments/pay_1": answer(PAYMENT),
                "/refunds": answer(
                    {
                        "refundId": "ref_1",
                        "refundStatus": "SUCCEEDED",
                        "refundAmount": {"amount": 2500, "currency": "CAD"},
                    },
                    201,
                ),
            }
        )
        refund = await PRV_Moneris_Payment.refund_payment(
            instance, "pay_1", MoneyAction(currency="CAD", idempotency_key="k2")
        )
        assert body(server.requests[1])["refundAmount"] == {
            "amount": 2500,
            "currency": "CAD",
        }
        assert refund["amount"] == "25.00" and refund["status"] == "succeeded"

    async def test_the_problem_detail_is_the_reason(self, moneris):
        _, instance = moneris(
            {
                "/payments": answer(
                    {"title": "INSUFFICIENT_FUNDS", "detail": "Not enough funds"}, 402
                )
            }
        )
        with pytest.raises(InvalidInputExternalError, match="Not enough funds"):
            await PRV_Moneris_Payment.create_payment(instance, payment_request())

    async def test_no_authorization_only(self, moneris):
        server, instance = moneris({})
        with pytest.raises(PermanentExternalError):
            await PRV_Moneris_Payment.create_payment(
                instance, payment_request(capture=False)
            )
        assert server.requests == []

    async def test_a_notification_keyed_with_the_store_id_is_refused(self, moneris):
        """The old check took an HMAC keyed with the store id (printed on
        receipts, no secret) as proof, so anyone could forge one. Moneris
        documents no signed notifications: none is accepted."""
        _, instance = moneris({})
        payload = json.dumps({"id": "e1", "type": "payment.succeeded"}).encode()
        forged = hmac.new(b"store5", payload, hashlib.sha256).hexdigest()
        with pytest.raises(PermanentExternalError):
            await PRV_Moneris_Payment.verify_webhook(
                instance, payload, {"x-signature": forged}
            )


@pytest.mark.external_api(provider="moneris")
async def test_sandbox_payment_read(provider_instance, sandbox_credentials_for):
    """Read-only on the Moneris sandbox: an unknown payment is refused."""
    creds = sandbox_credentials_for("moneris")
    api_key = os.environ.get("MONERIS_API_KEY")
    if not api_key:
        pytest.xfail("The Moneris API authenticates with MONERIS_API_KEY, unset")
    instance = provider_instance(
        PRV_Moneris_Payment,
        api_key=api_key,
        settings={
            "merchant_id": creds["MONERIS_MERCHANT_ID"],
            "api_base": "https://api.sb.moneris.io",
        },
    )
    with pytest.raises(InvalidInputExternalError):
        await PRV_Moneris_Payment.get_payment(instance, f"none{uuid.uuid4().hex}")
