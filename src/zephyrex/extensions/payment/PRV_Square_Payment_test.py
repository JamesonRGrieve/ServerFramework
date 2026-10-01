# SPDX-License-Identifier: AGPL-3.0-or-later
import base64
import hashlib
import hmac
import inspect
import uuid
from dataclasses import dataclass

import pytest
from square import Square

from zephyrex.extensions.payment.BLL_Payment import *  # noqa: F401,F403
from zephyrex.extensions.payment.PRV_Square_Payment import (
    PaymentExtensionSquareProvider,
    Square_CustomerManager,
    Square_CustomerModel,
    Square_PaymentModel,
    Square_SubscriptionModel,
)
from zephyrex.lib.Environment import env

# Square's sandbox test nonce for a card that is always approved.
SANDBOX_APPROVED_CARD = "cnon:card-nonce-ok"


@dataclass(frozen=True)
class _SquareWebhook:
    key: str
    url: str

    def sign(self, payload: bytes, url: str = "") -> str:
        """Square's signature, computed independently of the SDK: base64
        HMAC-SHA256 of the notification URL followed by the body."""
        message = (url or self.url).encode() + payload
        digest = hmac.new(self.key.encode(), message, hashlib.sha256).digest()
        return base64.b64encode(digest).decode()


@pytest.fixture
def square_webhook(monkeypatch) -> _SquareWebhook:
    webhook = _SquareWebhook(key="sq_sig_key_test", url="https://api.example.test/hook")
    monkeypatch.setattr(
        PaymentExtensionSquareProvider,
        "get_webhook_signature_key",
        classmethod(lambda cls: webhook.key),
    )
    monkeypatch.setattr(
        PaymentExtensionSquareProvider,
        "get_webhook_notification_url",
        classmethod(lambda cls: webhook.url),
    )
    return webhook


@pytest.mark.payment
@pytest.mark.square
class TestSquareProvider:
    """Test suite for Square payment provider.

    Tests provider static methods, payment processing, and Square API
    integration. Fully compatible with the Provider Rotation System.
    """

    provider_class = PaymentExtensionSquareProvider
    extension_id = "payment"

    @pytest.fixture
    def square_access_token(self):
        api_key = env("SQUARE_ACCESS_TOKEN")
        if not api_key:
            pytest.xfail("SQUARE_ACCESS_TOKEN environment variable not set")
        return api_key

    @pytest.fixture
    def provider_instance(self, square_access_token):
        class MockProviderInstance:
            def __init__(self, api_key):
                self.id = "test_square_instance_id"
                self.api_key = api_key
                self.provider_id = "square"
                self.name = "Test Square Instance"

        return MockProviderInstance(square_access_token)

    def test_provider_structure(self):
        assert hasattr(PaymentExtensionSquareProvider, "name")
        assert hasattr(PaymentExtensionSquareProvider, "version")
        assert hasattr(PaymentExtensionSquareProvider, "description")
        assert hasattr(PaymentExtensionSquareProvider, "dependencies")
        assert hasattr(PaymentExtensionSquareProvider, "_env")
        assert hasattr(PaymentExtensionSquareProvider, "bond_instance")
        assert hasattr(PaymentExtensionSquareProvider, "get_platform_name")

    def test_provider_metadata(self):
        assert PaymentExtensionSquareProvider.name == "square"
        assert isinstance(PaymentExtensionSquareProvider.version, str)
        assert isinstance(PaymentExtensionSquareProvider.description, str)
        assert PaymentExtensionSquareProvider.get_platform_name() == "Square"

    def test_provider_dependencies(self):
        deps = PaymentExtensionSquareProvider.dependencies
        assert deps is not None
        assert hasattr(deps, "pip")
        assert len(deps.pip) > 0
        squareup_dep = next((dep for dep in deps.pip if dep.name == "squareup"), None)
        assert squareup_dep is not None

    def test_provider_env_vars(self):
        env_vars = PaymentExtensionSquareProvider._env
        assert isinstance(env_vars, dict)
        assert "SQUARE_ACCESS_TOKEN" in env_vars
        assert "SQUARE_APP_ID" in env_vars
        assert "SQUARE_WEBHOOK_SIGNATURE_KEY" in env_vars
        assert "SQUARE_CURRENCY" in env_vars

    def test_bond_instance_without_api_key(self, monkeypatch):
        """No key on the instance and none configured: the provider falls back
        to SQUARE_ACCESS_TOKEN, so it is cleared where ``env()`` reads it."""
        from zephyrex.lib import Environment

        monkeypatch.setenv("SQUARE_ACCESS_TOKEN", "")
        if hasattr(Environment.settings, "SQUARE_ACCESS_TOKEN"):
            monkeypatch.setattr(Environment.settings, "SQUARE_ACCESS_TOKEN", "")

        class MockInstanceWithoutKey:
            id = "test_id"
            api_key = None

        instance = MockInstanceWithoutKey()
        bonded = PaymentExtensionSquareProvider.bond_instance(instance)
        assert bonded is None

    def test_bond_instance_with_api_key(self, provider_instance):
        """Bonds a v42+ client: ``square.client.Client`` no longer exists."""
        bonded = PaymentExtensionSquareProvider.bond_instance(provider_instance)
        assert bonded is not None
        assert isinstance(bonded.sdk, Square)

    def test_an_unknown_environment_does_not_bond(self, monkeypatch):
        monkeypatch.setenv("SQUARE_ENVIRONMENT", "staging")
        from zephyrex.lib import Environment

        if hasattr(Environment.settings, "SQUARE_ENVIRONMENT"):
            monkeypatch.setattr(Environment.settings, "SQUARE_ENVIRONMENT", "staging")

        class Instance:
            id = "test_id"
            api_key = "token"

        assert PaymentExtensionSquareProvider.bond_instance(Instance()) is None

    def test_provider_methods_are_plain_classmethods(self):
        """create_payment was declared ``@classmethod`` twice; Python 3.13
        no longer chains classmethods, so it could not be called there."""
        for name, member in vars(PaymentExtensionSquareProvider).items():
            if isinstance(member, classmethod):
                assert not isinstance(member.__func__, classmethod), name
        assert inspect.ismethod(PaymentExtensionSquareProvider.create_payment)

    def test_static_configuration_methods(self):
        access_token = PaymentExtensionSquareProvider.get_access_token()
        if env("SQUARE_ACCESS_TOKEN"):
            assert access_token == env("SQUARE_ACCESS_TOKEN")

        app_id = PaymentExtensionSquareProvider.get_app_id()
        if env("SQUARE_APP_ID"):
            assert app_id == env("SQUARE_APP_ID")

    def test_currency_and_environment_handling(self):
        default_currency = PaymentExtensionSquareProvider.get_default_currency()
        assert isinstance(default_currency, str)
        assert len(default_currency) == 3

    def test_external_models_exist(self):
        assert Square_CustomerModel is not None
        assert hasattr(Square_CustomerModel, "external_resource")
        assert Square_CustomerModel.external_resource == "customers"
        assert getattr(Square_CustomerModel, "_is_extension_model", False)

    def test_external_manager_exists(self):
        assert Square_CustomerManager is not None
        assert hasattr(Square_CustomerManager, "sync_contact")
        assert hasattr(Square_CustomerManager, "create_customer")
        assert callable(Square_CustomerManager.sync_contact)
        assert callable(Square_CustomerManager.create_customer)

    def test_square_models_structure(self):
        models = [
            (Square_CustomerModel, "customers"),
            (Square_PaymentModel, "payments"),
            (Square_SubscriptionModel, "subscriptions"),
        ]
        for model_class, expected_resource in models:
            assert hasattr(model_class, "external_resource")
            assert model_class.external_resource == expected_resource
            assert getattr(model_class, "_is_extension_model", False)

    def test_services_method(self):
        services = PaymentExtensionSquareProvider.services()
        assert isinstance(services, list)
        assert "payment" in services

    def test_extension_info(self):
        info = PaymentExtensionSquareProvider.get_extension_info()
        assert isinstance(info, dict)
        assert info["platform"] == "Square"

    @pytest.mark.asyncio
    async def test_process_webhook_invalid_signature(self, provider_instance):
        try:
            result = await PaymentExtensionSquareProvider.process_webhook(
                provider_instance, b'{"test": "data"}', "invalid_signature"
            )
            assert isinstance(result, dict)
            assert "error" in result or not result.get("success", True)
        except Exception as e:
            error_msg = str(e).lower()
            assert any(
                err in error_msg
                for err in ["signature", "webhook", "invalid", "verify", "configured"]
            )

    @pytest.mark.asyncio
    async def test_process_webhook_empty_signature_rejected(self):
        # #228: Square previously had NO empty-signature guard and would fall
        # through to an HMAC compare, returning {"success": False}. It must now
        # reject an empty/missing signature up front — uniform with PayPal /
        # Moneris / Helcim, which raise before any HMAC is computed. The guard
        # runs before provider_instance / the signing key are touched.
        with pytest.raises(Exception) as exc_info:
            await PaymentExtensionSquareProvider.process_webhook(
                None, b'{"type": "payment.updated"}', ""
            )
        assert "signature" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_process_webhook_valid_signature(self, square_webhook):
        """Square signs the notification URL followed by the body, base64
        HMAC-SHA256 (developer.squareup.com/docs/webhooks/step3validate)."""
        payload = b'{"type": "payment.updated", "event_id": "evt_sq_1"}'
        result = await PaymentExtensionSquareProvider.process_webhook(
            None, payload, square_webhook.sign(payload)
        )
        assert result["success"] is True
        assert result["event_type"] == "payment.updated"
        assert result["event_id"] == "evt_sq_1"

    @pytest.mark.asyncio
    async def test_process_webhook_body_only_digest_refused(self, square_webhook):
        """The hex digest of the body alone, which the provider used to
        expect, is not Square's signature: no genuine webhook carried it."""
        payload = b'{"type": "payment.updated"}'
        body_only = hmac.new(
            square_webhook.key.encode(), payload, hashlib.sha256
        ).hexdigest()
        result = await PaymentExtensionSquareProvider.process_webhook(
            None, payload, body_only
        )
        assert result == {"success": False, "error": "Invalid signature"}

    @pytest.mark.asyncio
    async def test_process_webhook_signed_for_another_url_refused(self, square_webhook):
        payload = b'{"type": "payment.updated"}'
        other = square_webhook.sign(payload, url="https://elsewhere.example/hook")
        result = await PaymentExtensionSquareProvider.process_webhook(
            None, payload, other
        )
        assert result == {"success": False, "error": "Invalid signature"}

    @pytest.mark.asyncio
    async def test_process_webhook_wrong_signature_returns_failure(
        self, square_webhook
    ):
        payload = b'{"type": "payment.updated"}'
        result = await PaymentExtensionSquareProvider.process_webhook(
            None, payload, "deadbeef_not_the_real_digest"
        )
        assert result == {"success": False, "error": "Invalid signature"}

    @pytest.mark.asyncio
    async def test_process_webhook_needs_the_notification_url(
        self, square_webhook, monkeypatch
    ):
        monkeypatch.setattr(
            PaymentExtensionSquareProvider,
            "get_webhook_notification_url",
            classmethod(lambda cls: ""),
        )
        payload = b'{"type": "payment.updated"}'
        result = await PaymentExtensionSquareProvider.process_webhook(
            None, payload, square_webhook.sign(payload)
        )
        assert result == {
            "success": False,
            "error": "Webhook notification URL not configured",
        }


@pytest.mark.payment
@pytest.mark.square
@pytest.mark.external_api(provider="square")
class TestSquareSandbox:
    """Real calls to the Square sandbox through the v42+ SDK; auto-xfailed
    without SQUARE_ACCESS_TOKEN. The environment is pinned to the sandbox,
    so a production token fails to authenticate instead of acting."""

    @pytest.fixture(autouse=True)
    def _sandbox(self, monkeypatch):
        from zephyrex.lib import Environment

        monkeypatch.setenv("SQUARE_ENVIRONMENT", "sandbox")
        if hasattr(Environment.settings, "SQUARE_ENVIRONMENT"):
            monkeypatch.setattr(Environment.settings, "SQUARE_ENVIRONMENT", "sandbox")
        PaymentExtensionSquareProvider._configure_square()
        yield
        monkeypatch.undo()
        PaymentExtensionSquareProvider._configure_square()

    @pytest.fixture
    def instance(self):
        class Instance:
            id = "square_sandbox"
            api_key = env("SQUARE_ACCESS_TOKEN")

        return Instance()

    def test_customer_round_trip(self, instance):
        email = f"zephyrex-test-{uuid.uuid4().hex[:12]}@example.com"
        created = PaymentExtensionSquareProvider.create_customer(
            instance, email=email, name="Ada Lovelace", metadata={"user_id": "u-1"}
        )
        assert created["success"] is True, created
        customer_id = created["customer_id"]
        try:
            fetched = PaymentExtensionSquareProvider.get_customer(instance, customer_id)
            assert fetched["success"] is True, fetched
            assert fetched["email"] == email
            assert fetched["name"] == "Ada Lovelace"

            updated = Square_CustomerModel.update_via_provider(
                instance, customer_id, note="updated by the zephyrex suite"
            )
            assert updated["success"] is True, updated
            assert updated["data"]["note"] == "updated by the zephyrex suite"
        finally:
            deleted = Square_CustomerModel.delete_via_provider(instance, customer_id)
        assert deleted == {"success": True}

    @pytest.fixture
    def merchant_currency(self, instance) -> str:
        """The sandbox merchant takes payments only in its own currency."""
        from square.environment import SquareEnvironment

        client = Square(token=instance.api_key, environment=SquareEnvironment.SANDBOX)
        currency = client.merchants.get(merchant_id="me").merchant.currency
        assert currency
        return str(currency)

    def test_payment_then_full_refund(self, instance, merchant_currency):
        """A refund with no amount refunds the whole payment: Square
        requires the amount on every refund, so it is read from the payment."""
        paid = PaymentExtensionSquareProvider.create_payment(
            instance,
            amount=1.25,
            currency=merchant_currency,
            payment_method_id=SANDBOX_APPROVED_CARD,
        )
        assert paid["success"] is True, paid
        assert paid["status"] == "COMPLETED"

        fetched = PaymentExtensionSquareProvider.get_payment(
            instance, paid["payment_id"]
        )
        assert fetched["success"] is True, fetched
        assert fetched["amount"] == 1.25
        assert fetched["currency"] == merchant_currency

        refunded = PaymentExtensionSquareProvider.refund_payment(
            instance, paid["payment_id"]
        )
        assert refunded["success"] is True, refunded
        assert refunded["amount"] == 1.25
        assert refunded["status"] in ("PENDING", "COMPLETED")

    def test_a_refused_request_is_an_error_result(self, instance):
        """Square's status and detail, never its response headers (which
        carry cookies) reach the caller."""
        result = PaymentExtensionSquareProvider.get_payment(instance, "no-such-payment")
        assert result["success"] is False
        assert result["error"].startswith("Square refused the request (404): ")
        assert "headers" not in result["error"]
        assert "cookie" not in result["error"].lower()
