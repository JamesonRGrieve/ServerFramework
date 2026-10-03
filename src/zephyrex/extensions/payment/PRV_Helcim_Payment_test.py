# SPDX-License-Identifier: AGPL-3.0-or-later
"""Helcim on the wire (a local server answering as Helcim does), its
Standard Webhooks signatures, and the real Helcim API refusing a bogus
token."""

import base64
import hashlib
import hmac
import json
import time
from decimal import Decimal
from typing import Any, Dict

import httpx
import pytest

from zephyrex.extensions.ExternalErrors import (
    AuthExternalError,
    InvalidInputExternalError,
    PermanentExternalError,
)
from zephyrex.extensions.payment.EXT_Payment import (
    CustomerRequest,
    MoneyAction,
    PaymentRequest,
)
from zephyrex.extensions.payment.PRV_Helcim_Payment import PRV_Helcim_Payment

VERIFIER = base64.b64encode(b"helcim-verifier-key").decode()
PURCHASE = {
    "transactionId": 101,
    "status": "APPROVED",
    "type": "purchase",
    "amount": 15.5,
    "currency": "CAD",
    "customerCode": "CST1",
}


def answer(body: Any, status: int = 200):
    return (status, {"Content-Type": "application/json"}, json.dumps(body).encode())


def body(request) -> Dict[str, Any]:
    parsed: Dict[str, Any] = json.loads(request.body)
    return parsed


def signed(
    payload: bytes, key: bytes = b"helcim-verifier-key", at: int = 0
) -> Dict[str, str]:
    timestamp = str(at or int(time.time()))
    digest = hmac.new(key, f"msg_1.{timestamp}.".encode() + payload, hashlib.sha256)
    return {
        "webhook-id": "msg_1",
        "webhook-timestamp": timestamp,
        "webhook-signature": "v1,bogus v1,"
        + base64.b64encode(digest.digest()).decode(),
    }


@pytest.fixture
def helcim(local_http_server, provider_instance):
    def _start(routes):
        server = local_http_server(routes)
        instance = provider_instance(
            PRV_Helcim_Payment,
            api_key="helcim-token",
            settings={"api_base": server.base_url, "verifier_token": VERIFIER},
        )
        return server, instance

    return _start


def purchase(**fields: Any) -> PaymentRequest:
    base: Dict[str, Any] = dict(
        amount=Decimal("15.50"),
        currency="CAD",
        user_id="u1",
        idempotency_key="k1",
        payment_method_id="card-token",
        customer_ip="203.0.113.7",
    )
    return PaymentRequest(**{**base, **fields})


class TestWire:
    async def test_a_purchase(self, helcim):
        server, instance = helcim({"/v2/payment/purchase": answer(PURCHASE)})
        made = await PRV_Helcim_Payment.create_payment(instance, purchase())
        call = server.requests[0]
        assert body(call) == {
            "amount": 15.5,
            "currency": "CAD",
            "ipAddress": "203.0.113.7",
            "ecommerce": True,
            "cardData": {"cardToken": "card-token"},
        }
        assert call.headers["api-token"] == "helcim-token"
        assert call.headers["idempotency-key"] == "k1"
        assert made["external_id"] == "101" and made["status"] == "succeeded"

    async def test_a_preauthorization_then_capture(self, helcim):
        server, instance = helcim(
            {
                "/v2/payment/preauth": answer({**PURCHASE, "type": "preauth"}),
                "/v2/payment/capture": answer(
                    {**PURCHASE, "transactionId": 102, "type": "capture"}
                ),
            }
        )
        held = await PRV_Helcim_Payment.create_payment(
            instance, purchase(capture=False)
        )
        assert held["status"] == "authorized"
        captured = await PRV_Helcim_Payment.capture_payment(
            instance,
            "101",
            MoneyAction(
                currency="CAD",
                idempotency_key="k2",
                amount=Decimal("15.5"),
                customer_ip="203.0.113.7",
            ),
        )
        assert body(server.requests[1])["preAuthTransactionId"] == 101
        assert captured["external_id"] == "102" and captured["status"] == "succeeded"

    async def test_a_declined_purchase_is_failed(self, helcim):
        _, instance = helcim(
            {"/v2/payment/purchase": answer({**PURCHASE, "status": "DECLINED"})}
        )
        assert (await PRV_Helcim_Payment.create_payment(instance, purchase()))[
            "status"
        ] == "failed"

    async def test_the_payers_ip_is_required(self, helcim):
        server, instance = helcim({})
        with pytest.raises(InvalidInputExternalError, match="customer_ip"):
            await PRV_Helcim_Payment.create_payment(
                instance, purchase(customer_ip=None)
            )
        assert server.requests == []

    async def test_a_full_refund_reads_the_amount(self, helcim):
        server, instance = helcim(
            {
                "/v2/card-transactions/101": answer(PURCHASE),
                "/v2/payment/refund": answer(
                    {**PURCHASE, "transactionId": 103, "type": "refund"}
                ),
            }
        )
        refund = await PRV_Helcim_Payment.refund_payment(
            instance,
            "101",
            MoneyAction(
                currency="CAD", idempotency_key="k3", customer_ip="203.0.113.7"
            ),
        )
        assert body(server.requests[1]) == {
            "originalTransactionId": 101,
            "amount": 15.5,
            "ipAddress": "203.0.113.7",
        }
        assert refund["refund_id"] == "103"

    async def test_helcims_refusal_reason(self, helcim):
        _, instance = helcim(
            {"/v2/payment/purchase": answer({"errors": {"cardToken": "invalid"}}, 400)}
        )
        with pytest.raises(InvalidInputExternalError, match="cardToken: invalid"):
            await PRV_Helcim_Payment.create_payment(instance, purchase())

    async def test_no_customer_records(self, helcim):
        _, instance = helcim({})
        with pytest.raises(PermanentExternalError):
            await PRV_Helcim_Payment.create_customer(
                instance,
                CustomerRequest(email="a@b.c", user_id="u", idempotency_key="k"),
            )


class TestNotifications:
    PAYLOAD = json.dumps({"id": "101", "type": "cardTransaction"}).encode()

    async def test_a_signed_notification(self, helcim):
        _, instance = helcim({})
        event = await PRV_Helcim_Payment.verify_webhook(
            instance, self.PAYLOAD, signed(self.PAYLOAD)
        )
        assert (event["kind"], event["object_id"]) == ("payment", "101")

    @pytest.mark.parametrize(
        "headers",
        [
            {},
            signed(PAYLOAD, key=b"other"),
            signed(PAYLOAD + b"x"),
            signed(PAYLOAD, at=int(time.time()) - 3600),
        ],
        ids=["unsigned", "other-key", "tampered", "stale"],
    )
    async def test_refused(self, helcim, headers):
        _, instance = helcim({})
        with pytest.raises(InvalidInputExternalError):
            await PRV_Helcim_Payment.verify_webhook(instance, self.PAYLOAD, headers)

    async def test_the_api_token_does_not_sign(self, helcim):
        """The old check keyed a hex HMAC of the body with the API token, a
        scheme Helcim never uses: every real notification failed, while a
        holder of the token could pass one."""
        _, instance = helcim({})
        old = hmac.new(b"helcim-token", self.PAYLOAD, hashlib.sha256).hexdigest()
        with pytest.raises(InvalidInputExternalError):
            await PRV_Helcim_Payment.verify_webhook(
                instance, self.PAYLOAD, {"webhook-signature": old}
            )


async def test_helcim_refuses_a_bogus_token(provider_instance):
    try:
        httpx.head("https://api.helcim.com", timeout=5)
    except httpx.HTTPError:
        pytest.xfail("https://api.helcim.com is unreachable")
    bogus = provider_instance(PRV_Helcim_Payment, api_key="bogus-token")
    with pytest.raises((AuthExternalError, InvalidInputExternalError)):
        await PRV_Helcim_Payment.get_payment(bogus, "1")


@pytest.mark.external_api(provider="helcim")
async def test_live_transaction_read(provider_instance, sandbox_credentials_for):
    """Read-only: a transaction id that does not exist is refused as such."""
    creds = sandbox_credentials_for("helcim")
    instance = provider_instance(PRV_Helcim_Payment, api_key=creds["HELCIM_API_TOKEN"])
    with pytest.raises(InvalidInputExternalError):
        await PRV_Helcim_Payment.get_payment(instance, "1")
