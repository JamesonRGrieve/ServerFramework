# SPDX-License-Identifier: AGPL-3.0-or-later
"""The payment extension's checks, money arithmetic and provider contract,
and its abilities end to end: real app, real database, providers answered
by local servers speaking Stripe's and PayPal's APIs."""

import hashlib
import hmac
import json
import time
import uuid
from decimal import Decimal
from typing import Any, Callable, Dict, Iterator, Mapping, Optional, Tuple
from urllib.parse import parse_qs

import pytest
from fastapi import HTTPException

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.ExternalErrors import (
    InvalidInputExternalError,
    PermanentExternalError,
)
from zephyrex.extensions.payment.EXT_Payment import (
    AbstractPaymentProvider,
    EXT_Payment,
    checked_amount,
    checked_currency,
    checked_ip,
    checked_metadata,
    checked_trial,
)
from zephyrex.extensions.payment.PRV_PayPal_Payment import PRV_PayPal_Payment
from zephyrex.extensions.payment.PRV_Stripe_Payment import PRV_Stripe_Payment
from zephyrex.lib.Environment import env
from zephyrex.logic.BLL_Auth import UserManager
from zephyrex.logic.BLL_Providers import (
    ProviderInstanceManager,
    ProviderInstanceModel,
    ProviderInstanceSettingManager,
    ProviderManager,
    RotationManager,
    RotationModel,
    RotationProviderInstanceManager,
    RotationProviderInstanceModel,
)

WEBHOOK_SECRET = "whsec_ext_test"
Answer = Tuple[int, Dict[str, str], bytes]


def json_answer(body: Any, status: int = 200) -> Answer:
    return (status, {"Content-Type": "application/json"}, json.dumps(body).encode())


def form_of(request: Any) -> Dict[str, str]:
    return {k: v[0] for k, v in parse_qs(request.body.decode()).items()}


class StripeAccount:
    """A local server keeping payment intents, customers and subscriptions
    as Stripe does, for what the abilities send it."""

    def __init__(self) -> None:
        self.intents: Dict[str, Dict[str, Any]] = {}
        self.subscriptions: Dict[str, Dict[str, Any]] = {}
        self.customers: Dict[str, Dict[str, Any]] = {}
        self.fail_payments = False

    def routes(self) -> "StripeRoutes":
        return StripeRoutes(self)

    def intent(self, intent_id: str) -> Dict[str, Any]:
        return self.intents[intent_id]


class StripeRoutes(Mapping[str, Callable[[Any], Answer]]):
    """A route for every path: the account answers it."""

    def __init__(self, account: StripeAccount) -> None:
        self.account = account

    def __getitem__(self, path: str) -> Callable[[Any], Answer]:
        return lambda request: self.answer(request, path.split("?")[0])

    def __iter__(self) -> Iterator[str]:
        return iter(())

    def __len__(self) -> int:
        return 0

    def answer(self, request: Any, path: str) -> Answer:
        account = self.account
        sent = form_of(request) if request.body else {}
        if path == "/v1/customers" and request.method == "POST":
            customer: Dict[str, Any] = {
                "id": f"cus_{len(account.customers) + 1}",
                "email": sent.get("email"),
                "name": sent.get("name"),
            }
            account.customers[customer["id"]] = customer
            return json_answer(customer)
        if path.startswith("/v1/customers/"):
            return json_answer(account.customers[path.rsplit("/", 1)[1]])
        if path == "/v1/payment_intents":
            if account.fail_payments:
                return json_answer({}, 503)
            intent: Dict[str, Any] = {
                "id": f"pi_{len(account.intents) + 1}",
                "amount": int(sent["amount"]),
                "currency": sent["currency"],
                "customer": sent.get("customer"),
                "status": (
                    "requires_capture"
                    if sent.get("capture_method") == "manual"
                    else "requires_payment_method"
                ),
                "client_secret": "secret",
                "latest_charge": {"amount_refunded": 0, "refunded": False},
            }
            account.intents[intent["id"]] = intent
            return json_answer(intent)
        if path.startswith("/v1/payment_intents/"):
            parts = path.split("/")
            intent = account.intents[parts[3]]
            if path.endswith("/capture"):
                intent["status"] = "succeeded"
            return json_answer(intent)
        if path == "/v1/refunds":
            intent = account.intents[sent["payment_intent"]]
            amount = int(sent.get("amount") or intent["amount"])
            charge = intent["latest_charge"]
            charge["amount_refunded"] += amount
            charge["refunded"] = charge["amount_refunded"] >= intent["amount"]
            return json_answer(
                {
                    "id": "re_1",
                    "status": "succeeded",
                    "amount": amount,
                    "currency": intent["currency"],
                }
            )
        if path == "/v1/subscriptions":
            subscription: Dict[str, Any] = {
                "id": f"sub_{len(account.subscriptions) + 1}",
                "status": "active",
                "customer": sent["customer"],
                "current_period_end": 1_900_000_000,
                "cancel_at_period_end": False,
                "items": {"data": [{"price": {"id": sent["items[0][price]"]}}]},
            }
            account.subscriptions[subscription["id"]] = subscription
            return json_answer(subscription)
        if path.startswith("/v1/subscriptions/"):
            subscription = account.subscriptions[path.rsplit("/", 1)[1]]
            if sent.get("cancel_at_period_end") == "true":
                subscription["cancel_at_period_end"] = True
            if request.method == "DELETE":
                subscription["status"] = "canceled"
            return json_answer(subscription)
        return json_answer({"error": {"message": f"no route {path}"}}, 404)


def add_instance(
    registry: Any,
    provider: str,
    api_key: Optional[str],
    settings: Dict[str, str],
) -> ProviderInstanceModel:
    """A root-scope account of the provider named ``provider``, with
    ``settings``."""
    root = env("ROOT_ID")
    record = ProviderManager(model_registry=registry, requester_id=root).get(
        name=provider
    )
    instance = ProviderInstanceModel.model_validate(
        ProviderInstanceManager(model_registry=registry, requester_id=root).create(
            name=f"{provider}_{uuid.uuid4().hex}",
            provider_id=record.id,
            api_key=api_key,
            scope="root",
        ),
        from_attributes=True,
    )
    settings_manager = ProviderInstanceSettingManager(
        model_registry=registry, requester_id=root
    )
    for key, value in settings.items():
        settings_manager.create(provider_instance_id=instance.id, key=key, value=value)
    return instance


def rotation(registry: Any, *instances: ProviderInstanceModel) -> RotationManager:
    """A rotation trying ``instances`` in order, as the extension's root."""
    root = env("ROOT_ID")
    manager = RotationManager(model_registry=registry, requester_id=root)
    made = RotationModel.model_validate(
        manager.create(name=f"payment_test_{uuid.uuid4().hex}"), from_attributes=True
    )
    links = RotationProviderInstanceManager(model_registry=registry, requester_id=root)
    parent = None
    for instance in instances:
        link = RotationProviderInstanceModel.model_validate(
            links.create(
                rotation_id=made.id, provider_instance_id=instance.id, parent_id=parent
            ),
            from_attributes=True,
        )
        parent = link.id
    manager.target_id = made.id
    return manager


def stripe_signed(payload: bytes) -> Dict[str, str]:
    timestamp = str(int(time.time()))
    digest = hmac.new(
        WEBHOOK_SECRET.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256
    ).hexdigest()
    return {"Stripe-Signature": f"t={timestamp},v1={digest}"}


class TestChecks:
    @pytest.mark.parametrize("value", ["12.50", 3, Decimal("0.01")])
    def test_amounts(self, value):
        assert checked_amount(value) == Decimal(str(value))

    @pytest.mark.parametrize("value", [0, "-1", "nan", "inf", "abc", 1.5, True, "1e9"])
    def test_refused_amounts(self, value):
        with pytest.raises(InvalidInputExternalError):
            checked_amount(value)

    def test_currency(self):
        assert checked_currency("cad") == "CAD"
        for bad in ("CA", "C4D", None, "DOLLARS"):
            with pytest.raises(InvalidInputExternalError):
                checked_currency(bad)

    def test_metadata_cannot_name_the_user(self):
        assert checked_metadata({"order": 7}) == {"order": "7"}
        with pytest.raises(InvalidInputExternalError):
            checked_metadata({"user_id": "someone-else"})
        with pytest.raises(InvalidInputExternalError):
            checked_metadata({str(n): n for n in range(21)})

    def test_trial_and_ip(self):
        assert checked_trial(14) == 14
        for bad in (0, 10_000, True):
            with pytest.raises(InvalidInputExternalError):
                checked_trial(bad)
        assert checked_ip("2001:db8::1") == "2001:db8::1"
        with pytest.raises(InvalidInputExternalError):
            checked_ip("10.0.0.1; rm")


class TestMoney:
    @pytest.mark.parametrize(
        "amount, currency, units",
        [
            ("1.15", "USD", 115),
            ("100", "JPY", 100),
            ("1.234", "BHD", 1234),
            ("0.005", "USD", 1),
        ],
    )
    def test_minor_units(self, amount, currency, units):
        assert AbstractPaymentProvider.to_minor_units(amount, currency) == units
        assert AbstractPaymentProvider.from_minor_units(
            units, currency
        ) == AbstractPaymentProvider.to_minor_units(amount, currency) / Decimal(
            10
        ) ** AbstractPaymentProvider._currency_exponent(
            currency
        )

    def test_format(self):
        assert AbstractPaymentProvider.format_amount("1.2345", "BHD") == "1.235"
        assert AbstractPaymentProvider.format_amount("100", "JPY") == "100"
        assert AbstractPaymentProvider.format_amount(Decimal("1"), "usd") == "1.00"


class TestContract:
    def test_the_providers(self):
        assert sorted(p.name for p in EXT_Payment.providers) == [
            "helcim",
            "moneris",
            "paypal",
            "square",
            "stripe",
        ]

    @pytest.mark.parametrize("provider", EXT_Payment.providers, ids=lambda p: p.name)
    def test_every_named_ability_is_implemented(self, provider):
        """A provider that names an ability (so a rotation picks it) has its
        own implementation, not the refusing default."""
        methods = {
            "payment_create": "create_payment",
            "payment_get": "get_payment",
            "payment_capture": "capture_payment",
            "payment_refund": "refund_payment",
            "customer_create": "create_customer",
            "customer_get": "get_customer",
            "subscription_create": "create_subscription",
            "subscription_get": "get_subscription",
            "subscription_cancel": "cancel_subscription",
            "webhook_process": "verify_webhook",
        }
        assert provider._abilities <= set(methods)
        for ability_name in provider._abilities:
            method = methods[ability_name]
            assert (
                getattr(provider, method).__func__
                is not getattr(AbstractPaymentProvider, method).__func__
            ), f"{provider.name} names {ability_name} without it"
        if "subscription_create" in provider._abilities:
            assert "subscription_get" in provider._abilities

    def test_the_abilities_are_declared(self):
        decorated = {
            getattr(EXT_Payment, name)._ability_info["name"]
            for name in dir(EXT_Payment)
            if hasattr(getattr(EXT_Payment, name), "_ability_info")
        }
        assert decorated == EXT_Payment._abilities

    async def test_a_refused_operation_names_the_provider(self, provider_instance):
        moneris = EXT_Payment.providers[
            [p.name for p in EXT_Payment.providers].index("moneris")
        ]
        instance = provider_instance(moneris)
        with pytest.raises(PermanentExternalError, match="Moneris's API cannot"):
            await moneris.get_subscription(instance, "s1")


class TestAbilities(ExtensionServerMixin):
    extension_class = EXT_Payment

    @pytest.fixture
    def registry(self, server) -> Any:
        return server.app.state.model_registry

    @pytest.fixture
    def stripe(self, server, registry, local_http_server, monkeypatch):
        """A Stripe account answered locally, alone in the root rotation."""
        account = StripeAccount()
        upstream = local_http_server(account.routes())
        instance = add_instance(
            registry,
            PRV_Stripe_Payment.name,
            "sk_test_local",
            {"api_base": upstream.base_url, "webhook_secret": WEBHOOK_SECRET},
        )
        monkeypatch.setattr(
            EXT_Payment, "_root_rotation_cache", rotation(registry, instance)
        )
        return account, upstream, instance

    @pytest.fixture
    def fresh_user(self, server):
        """A user with no customer record yet."""
        from conftest_factories import create_user

        return create_user(server, f"payer_{uuid.uuid4().hex[:8]}@example.com")

    async def test_abilities_need_a_requester(self, server):
        with pytest.raises(HTTPException) as raised:
            await EXT_Payment.payment_list("")
        assert raised.value.status_code == 400

    async def test_a_customer_is_made_under_the_requesters_own_email(
        self, stripe, fresh_user, registry
    ):
        account, upstream, instance = stripe
        made = await EXT_Payment.customer_create(fresh_user.id, name="Ada Lovelace")
        assert account.customers[made["customer_id"]]["email"] == fresh_user.email
        user = UserManager(model_registry=registry, requester_id=env("ROOT_ID")).get(
            id=fresh_user.id
        )
        assert (user.external_payment_id, user.payment_instance_id) == (
            made["customer_id"],
            str(instance.id),
        )
        again = await EXT_Payment.customer_create(fresh_user.id)
        assert again["customer_id"] == made["customer_id"]
        assert len(account.customers) == 1
        assert (await EXT_Payment.customer_get(fresh_user.id))["email"] == (
            fresh_user.email
        )

    async def test_a_payment_is_the_requesters_alone(self, stripe, fresh_user, admin_b):
        account, _, _ = stripe
        made = await EXT_Payment.payment_create(
            fresh_user.id, "12.50", "usd", description="a book"
        )
        assert made["user_id"] == fresh_user.id
        assert (made["amount"], made["currency"], made["status"]) == (
            "12.50",
            "USD",
            "pending",
        )
        assert made["client_secret"] == "secret"
        assert account.intents[made["external_id"]]["amount"] == 1250
        listed = await EXT_Payment.payment_list(fresh_user.id)
        assert made["id"] in [payment["id"] for payment in listed]
        with pytest.raises(HTTPException) as raised:
            await EXT_Payment.payment_get(admin_b.id, made["id"])
        assert raised.value.status_code == 404
        assert made["id"] not in [
            p["id"] for p in await EXT_Payment.payment_list(admin_b.id)
        ]

    async def test_a_linked_customer_pays_on_their_account(self, stripe, fresh_user):
        account, _, _ = stripe
        customer = await EXT_Payment.customer_create(fresh_user.id)
        made = await EXT_Payment.payment_create(fresh_user.id, 5, "USD")
        assert (
            account.intents[made["external_id"]]["customer"] == customer["customer_id"]
        )
        assert made["customer_id"] == customer["customer_id"]

    async def test_the_merchant_alone_captures_and_refunds(self, stripe, fresh_user):
        """A payer refunding (or capturing) their own payment would be the
        merchant's money: only ROOT or SYSTEM act for the merchant."""
        account, _, _ = stripe
        held = await EXT_Payment.payment_create(
            fresh_user.id, "20", "USD", capture=False
        )
        assert held["status"] == "authorized"
        for act in (EXT_Payment.payment_capture, EXT_Payment.payment_refund):
            with pytest.raises(HTTPException) as raised:
                await act(fresh_user.id, held["id"])
            assert raised.value.status_code == 403
        root = env("ROOT_ID")
        captured = await EXT_Payment.payment_capture(root, held["id"])
        assert (
            captured["status"] == "succeeded" and captured["user_id"] == fresh_user.id
        )
        part = await EXT_Payment.payment_refund(root, held["id"], amount="5")
        assert part["refund"]["amount"] == "5.00"
        assert part["payment"]["amount_refunded"] == "5.00"
        rest = await EXT_Payment.payment_refund(root, held["id"])
        assert rest["payment"]["status"] == "refunded"

    async def test_an_unclear_payment_is_not_taken_again_elsewhere(
        self, server, registry, local_http_server, monkeypatch, fresh_user
    ):
        """The first account answers 503 to a charge it may have taken: the
        call fails, and the second account is never asked to charge."""
        first, second = StripeAccount(), StripeAccount()
        first.fail_payments = True
        first_server = local_http_server(first.routes())
        second_server = local_http_server(second.routes())
        monkeypatch.setenv(
            "EGRESS_ALLOWED_HOSTS", f"{first_server.host},{second_server.host}"
        )
        instances = [
            add_instance(
                registry,
                PRV_Stripe_Payment.name,
                "sk_test",
                {"api_base": upstream.base_url},
            )
            for upstream in (first_server, second_server)
        ]
        monkeypatch.setattr(
            EXT_Payment, "_root_rotation_cache", rotation(registry, *instances)
        )
        with pytest.raises(PermanentExternalError):
            await EXT_Payment.payment_create(fresh_user.id, "9", "USD")
        assert first_server.requests and not second_server.requests
        assert await EXT_Payment.payment_list(fresh_user.id) == []

    async def test_a_signed_notification_refreshes_the_payment(
        self, stripe, fresh_user
    ):
        account, _, instance = stripe
        made = await EXT_Payment.payment_create(fresh_user.id, "3", "USD")
        account.intent(made["external_id"])["status"] = "succeeded"
        payload = json.dumps(
            {
                "id": "evt_1",
                "type": "payment_intent.succeeded",
                "data": {
                    "object": {"object": "payment_intent", "id": made["external_id"]}
                },
            }
        ).encode()
        event = await EXT_Payment.webhook_process(
            instance.name, payload, stripe_signed(payload)
        )
        assert event["refreshed"] == made["id"]
        found = await EXT_Payment.payment_list(fresh_user.id)
        assert [p["status"] for p in found if p["id"] == made["id"]] == ["succeeded"]
        with pytest.raises(InvalidInputExternalError):
            await EXT_Payment.webhook_process(
                instance.name, payload, {"Stripe-Signature": "t=1,v1=00"}
            )

    async def test_subscriptions(self, stripe, fresh_user):
        account, _, _ = stripe
        with pytest.raises(HTTPException) as raised:
            await EXT_Payment.subscription_create(fresh_user.id, "price_1")
        assert raised.value.status_code == 409
        await EXT_Payment.customer_create(fresh_user.id)
        made = await EXT_Payment.subscription_create(fresh_user.id, "price_1")
        assert made["active"] and made["plan_id"] == "price_1"
        status = await EXT_Payment.subscription_status(fresh_user.id)
        assert status["active"]
        ending = await EXT_Payment.subscription_cancel(fresh_user.id, made["id"])
        assert ending["cancel_at_period_end"] and ending["active"]
        account.subscriptions[made["external_id"]]["status"] = "canceled"
        assert not (await EXT_Payment.subscription_status(fresh_user.id))["active"]

    async def test_a_paypal_subscription_needs_no_customer(
        self, server, registry, local_http_server, monkeypatch, fresh_user
    ):
        upstream = local_http_server(
            {
                "/v1/oauth2/token": json_answer(
                    {"access_token": "t", "expires_in": 60}
                ),
                "/v1/billing/subscriptions": json_answer(
                    {
                        "id": "I-1",
                        "status": "APPROVAL_PENDING",
                        "plan_id": "P-1",
                        "links": [{"rel": "approve", "href": "https://paypal.test/a"}],
                    }
                ),
            }
        )
        instance = add_instance(
            registry,
            PRV_PayPal_Payment.name,
            "secret",
            {"api_base": upstream.base_url, "client_id": "client"},
        )
        monkeypatch.setattr(
            EXT_Payment, "_root_rotation_cache", rotation(registry, instance)
        )
        made = await EXT_Payment.subscription_create(fresh_user.id, "P-1")
        assert made["user_id"] == fresh_user.id and not made["active"]
        assert made["approval_url"] == "https://paypal.test/a"
