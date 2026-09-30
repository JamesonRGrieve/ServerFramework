# SPDX-License-Identifier: AGPL-3.0-or-later
import hashlib
import hmac
import importlib
import importlib.util
import json
import time
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Dict

import pytest

from zephyrex.extensions.payment.PRV_Stripe_Payment import (
    PaymentExtensionStripeProvider,
    Stripe_CustomerManager,
    Stripe_CustomerModel,
)
from zephyrex.lib.Environment import env

# BLL_Payment's @extension_model decorators apply on import.
importlib.import_module("zephyrex.extensions.payment.BLL_Payment")


@pytest.mark.payment
@pytest.mark.stripe
class TestStripeProvider:
    """
    Test suite for Stripe payment provider.
    Tests provider static methods, payment processing, Stripe API integration,
    and GraphQL functionality for external Stripe models.
    Fully compatible with the Provider Rotation System.
    """

    # Configure the test class
    provider_class = PaymentExtensionStripeProvider
    extension_id = "payment"

    @pytest.fixture
    def stripe_api_key(self):
        """Get Stripe API key from environment or skip test."""
        api_key = env("STRIPE_SECRET_KEY")
        if not api_key:
            pytest.xfail("STRIPE_SECRET_KEY environment variable not set")
        return api_key

    @pytest.fixture
    def stripe_publishable_key(self):
        """Get Stripe publishable key from environment or skip test."""
        pub_key = env("STRIPE_PUBLISHABLE_KEY")
        if not pub_key:
            pytest.xfail("STRIPE_PUBLISHABLE_KEY environment variable not set")
        return pub_key

    @pytest.fixture
    def provider_instance(self, stripe_api_key):
        """Create a real provider instance for testing."""

        class MockProviderInstance:
            def __init__(self, api_key):
                self.id = "test_stripe_instance_id"
                self.api_key = api_key
                self.provider_id = "stripe"
                self.name = "Test Stripe Instance"

        return MockProviderInstance(stripe_api_key)

    def test_provider_structure(self):
        """Test that provider has correct structure."""
        assert hasattr(PaymentExtensionStripeProvider, "name")
        assert hasattr(PaymentExtensionStripeProvider, "version")
        assert hasattr(PaymentExtensionStripeProvider, "description")
        assert hasattr(PaymentExtensionStripeProvider, "dependencies")
        assert hasattr(PaymentExtensionStripeProvider, "_env")
        assert hasattr(PaymentExtensionStripeProvider, "bond_instance")
        assert hasattr(PaymentExtensionStripeProvider, "get_platform_name")

    def test_provider_metadata(self):
        """Test provider metadata."""
        assert PaymentExtensionStripeProvider.name == "stripe"
        assert isinstance(PaymentExtensionStripeProvider.version, str)
        assert isinstance(PaymentExtensionStripeProvider.description, str)
        assert PaymentExtensionStripeProvider.get_platform_name() == "Stripe"

    def test_provider_dependencies(self):
        """Test provider dependencies."""
        deps = PaymentExtensionStripeProvider.dependencies
        assert deps is not None
        assert hasattr(deps, "pip")
        assert len(deps.pip) > 0

        # Should have stripe dependency
        stripe_dep = next((dep for dep in deps.pip if dep.name == "stripe"), None)
        assert stripe_dep is not None

    def test_provider_env_vars(self):
        """Test provider environment variables."""
        env_vars = PaymentExtensionStripeProvider._env
        assert isinstance(env_vars, dict)
        assert "STRIPE_API_KEY" in env_vars
        assert "STRIPE_SECRET_KEY" in env_vars
        assert "STRIPE_PUBLISHABLE_KEY" in env_vars
        assert "STRIPE_WEBHOOK_SECRET" in env_vars
        assert "STRIPE_CURRENCY" in env_vars

    def test_bond_instance_without_api_key(self):
        """Test bonding instance without API key."""

        class MockInstanceWithoutKey:
            id = "test_id"
            api_key = None

        instance = MockInstanceWithoutKey()
        bonded = PaymentExtensionStripeProvider.bond_instance(instance)
        assert bonded is None

    def test_bond_instance_with_api_key(self, provider_instance):
        """Test bonding instance with API key."""
        bonded = PaymentExtensionStripeProvider.bond_instance(provider_instance)

        # Bonding succeeds exactly when the Stripe SDK is installed.
        if importlib.util.find_spec("stripe") is not None:
            assert bonded is not None
            assert hasattr(bonded, "sdk")
        else:
            assert bonded is None

    def test_static_configuration_methods(self):
        """Test static configuration methods."""
        # Test secret key retrieval
        secret_key = PaymentExtensionStripeProvider.get_secret_key()
        if env("STRIPE_SECRET_KEY"):
            assert secret_key == env("STRIPE_SECRET_KEY")
        else:
            assert secret_key == ""

        # Test publishable key retrieval
        pub_key = PaymentExtensionStripeProvider.get_publishable_key()
        if env("STRIPE_PUBLISHABLE_KEY"):
            assert pub_key == env("STRIPE_PUBLISHABLE_KEY")
        else:
            assert pub_key == ""

        # Test webhook secret retrieval
        webhook_secret = PaymentExtensionStripeProvider.get_webhook_secret()
        # This might be empty, which is fine
        assert isinstance(webhook_secret, str)

    def test_stripe_configuration(self):
        """Test Stripe configuration without real API calls."""
        # Test configuration method exists
        assert hasattr(PaymentExtensionStripeProvider, "_configure_stripe")

        # Test that configuration can be called
        PaymentExtensionStripeProvider._configure_stripe()

        # Check availability flag
        assert hasattr(PaymentExtensionStripeProvider, "_stripe_available")
        assert isinstance(PaymentExtensionStripeProvider._stripe_available, bool)

    @pytest.mark.asyncio
    async def test_payment_abilities_exist(self):
        """Test that payment ability methods exist."""
        # Check that the provider has the required payment abilities
        payment_methods = [
            "create_payment",
            "capture_payment",
            "refund_payment",
            "create_customer",
            "create_subscription",
            "cancel_subscription",
            "process_webhook",
        ]

        for method_name in payment_methods:
            assert hasattr(PaymentExtensionStripeProvider, method_name)
            method = getattr(PaymentExtensionStripeProvider, method_name)
            assert callable(method)

    @pytest.mark.asyncio
    async def test_create_payment_without_api_key(self):
        """Test creating payment without any API key (instance or class)."""

        class MockInstanceWithoutKey:
            id = "test_id"
            api_key = None

        instance = MockInstanceWithoutKey()

        saved = PaymentExtensionStripeProvider._stripe_available
        saved_client = PaymentExtensionStripeProvider._stripe_client
        PaymentExtensionStripeProvider._stripe_available = False
        PaymentExtensionStripeProvider._stripe_client = None
        try:
            result = await PaymentExtensionStripeProvider.create_payment(
                instance,
                amount=Decimal("10.00"),
                currency="USD",
                description="Test payment",
            )
            assert isinstance(result, dict)
            assert "error" in result or "failed" in str(result).lower()
        except Exception as e:
            error_msg = str(e).lower()
            assert any(
                word in error_msg
                for word in ["api", "key", "stripe", "config", "not configured"]
            )
        finally:
            PaymentExtensionStripeProvider._stripe_available = saved
            PaymentExtensionStripeProvider._stripe_client = saved_client

    @pytest.mark.asyncio
    async def test_create_payment_with_real_api(self, provider_instance):
        """Test creating payment with real API."""
        if not env("STRIPE_SECRET_KEY"):
            pytest.xfail(
                "STRIPE_SECRET_KEY not set - cannot test real payment creation"
            )

        # Try to create a payment - this is a real API call
        try:
            result = await PaymentExtensionStripeProvider.create_payment(
                provider_instance,
                amount=Decimal("1.00"),  # Minimal amount for testing
                currency="USD",
                description="Test payment",
            )

            # Should either succeed or fail with recognizable error
            assert isinstance(result, dict)
            if "error" not in result:
                # If successful, should have payment ID
                assert "id" in result
                assert result["id"].startswith("pi_")  # Stripe payment intent ID
        except Exception as e:
            # If it fails, should be due to API issues, not code structure
            error_msg = str(e).lower()
            expected_errors = ["unauthorized", "invalid", "api", "stripe", "test"]
            assert any(
                err in error_msg for err in expected_errors
            ), f"Unexpected error: {e}"

    @pytest.mark.asyncio
    async def test_create_customer_with_real_api(self, provider_instance):
        """Test creating customer with real API."""
        if not env("STRIPE_SECRET_KEY"):
            pytest.xfail(
                "STRIPE_SECRET_KEY not set - cannot test real customer creation"
            )

        try:
            result = await PaymentExtensionStripeProvider.create_customer(
                provider_instance, email="test@example.com", name="Test Customer"
            )

            # Should either succeed or fail with recognizable error
            assert isinstance(result, dict)
            if "error" not in result:
                # If successful, should have customer ID
                assert "id" in result
                assert result["id"].startswith("cus_")  # Stripe customer ID
        except Exception as e:
            # If it fails, should be due to API issues
            error_msg = str(e).lower()
            expected_errors = ["unauthorized", "invalid", "api", "stripe"]
            assert any(
                err in error_msg for err in expected_errors
            ), f"Unexpected error: {e}"

    def test_external_models_exist(self):
        """Test that external models are defined."""
        # Check that external models exist
        assert Stripe_CustomerModel is not None
        assert hasattr(Stripe_CustomerModel, "external_resource")
        assert Stripe_CustomerModel.external_resource == "customers"

        # Check that model has _is_extension_model attribute
        assert getattr(Stripe_CustomerModel, "_is_extension_model", False)

        # Check that model has _extension_target
        assert hasattr(Stripe_CustomerModel, "_extension_target")

    def test_external_manager_exists(self):
        """Test that external manager is defined."""
        assert Stripe_CustomerManager is not None
        assert hasattr(Stripe_CustomerManager, "sync_contact")
        assert hasattr(Stripe_CustomerManager, "create_customer")

        # Manager should be callable
        assert callable(Stripe_CustomerManager.sync_contact)
        assert callable(Stripe_CustomerManager.create_customer)

    def test_stripe_models_structure(self):
        """Test Stripe model structure."""
        from zephyrex.extensions.payment.PRV_Stripe_Payment import (
            Stripe_PaymentIntentModel,
            Stripe_SubscriptionModel,
            Stripe_ProductModel,
        )

        # All models should have external_resource
        models = [
            (Stripe_CustomerModel, "customers"),
            (Stripe_PaymentIntentModel, "payment_intents"),
            (Stripe_SubscriptionModel, "subscriptions"),
            (Stripe_ProductModel, "products"),
        ]

        for model_class, expected_resource in models:
            assert hasattr(model_class, "external_resource")
            assert model_class.external_resource == expected_resource
            assert getattr(model_class, "_is_extension_model", False)

    def test_webhook_processing_method(self):
        """Test webhook processing method exists."""
        assert hasattr(PaymentExtensionStripeProvider, "process_webhook")

        # Should be async
        import inspect

        assert inspect.iscoroutinefunction(
            PaymentExtensionStripeProvider.process_webhook
        )

    @pytest.mark.asyncio
    async def test_process_webhook_invalid_signature(self, provider_instance):
        """Test webhook processing with invalid signature."""
        try:
            result = await PaymentExtensionStripeProvider.process_webhook(
                provider_instance, b'{"test": "data"}', "invalid_signature"
            )
            assert isinstance(result, dict)
            assert "error" in result or not result.get("success", True)
        except Exception as e:
            error_msg = str(e).lower()
            if "not configured" in error_msg or "not available" in error_msg:
                pytest.skip("Stripe SDK not installed")
            expected_errors = ["signature", "webhook", "invalid", "verify"]
            assert any(
                err in error_msg for err in expected_errors
            ), f"Unexpected error: {e}"

    def test_currency_and_environment_handling(self):
        """Test currency and environment configuration."""
        # Test default currency
        default_currency = PaymentExtensionStripeProvider.get_default_currency()
        assert isinstance(default_currency, str)
        assert len(default_currency) == 3  # Should be ISO currency code

        # Test currency from environment
        if env("STRIPE_CURRENCY"):
            assert default_currency == env("STRIPE_CURRENCY")
        else:
            assert default_currency == "USD"  # Default fallback

    # ------------------------------------------------------------------
    # Security: webhook explicit-deny tests.
    # ------------------------------------------------------------------

    @pytest.mark.security
    @pytest.mark.asyncio
    async def test_process_webhook_rejects_tampered_body(self, provider_instance):
        """A body altered after signing must be rejected."""
        # The body as altered after signing (it was payment_intent.succeeded).
        tampered = b'{"id": "evt_1", "type": "payment_intent.refunded"}'
        # There is no valid signature for it, so use a syntactically
        # plausible-but-wrong sig to confirm the verify path engages.
        bogus_sig = "t=1700000000,v1=" + ("00" * 32)
        try:
            result = await PaymentExtensionStripeProvider.process_webhook(
                provider_instance, tampered, bogus_sig
            )
            assert isinstance(result, dict)
            assert "error" in result or not result.get(
                "success", True
            ), "Tampered body must surface as error"
        except Exception as e:
            err = str(e).lower()
            assert any(
                t in err
                for t in (
                    "signature",
                    "webhook",
                    "invalid",
                    "verify",
                    "not configured",
                    "not available",
                )
            ), f"Tampered body raised unexpected error: {e}"
            if "not configured" in err or "not available" in err:
                pytest.skip("Stripe SDK not installed")

    @pytest.mark.security
    @pytest.mark.asyncio
    async def test_process_webhook_rejects_old_timestamp(self, provider_instance):
        """A signature whose `t=` is far in the past must be rejected (replay).

        EXPECTED FAIL today if the provider doesn't enforce a max
        timestamp tolerance.
        """
        body = b'{"id": "evt_old", "type": "payment_intent.succeeded"}'
        old_ts = "1000000000"  # 2001 — definitely older than any tolerance
        old_sig = f"t={old_ts},v1=" + ("00" * 32)
        try:
            result = await PaymentExtensionStripeProvider.process_webhook(
                provider_instance, body, old_sig
            )
            assert isinstance(result, dict)
            assert "error" in result or not result.get(
                "success", True
            ), "Old-timestamp webhook must be rejected"
        except Exception as e:
            err = str(e).lower()
            assert any(
                t in err
                for t in (
                    "timestamp",
                    "tolerance",
                    "stale",
                    "signature",
                    "not configured",
                    "not available",
                )
            ), f"Old timestamp raised unexpected error: {e}"
            if "not configured" in err or "not available" in err:
                pytest.skip("Stripe SDK not installed")


_WEBHOOK_SECRET = "whsec_zx_probe_secret"


def _stripe_signature(payload: str, secret: str, timestamp: int) -> str:
    """The Stripe-Signature header Stripe sends for ``payload``."""
    signed = f"{timestamp}.{payload}".encode()
    digest = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


@pytest.mark.payment
@pytest.mark.stripe
class TestStripeWebhookVerification:
    """Signature checking is local HMAC work, so it runs without a live key.

    The provider used to detect a bad signature through ``stripe.error``,
    which the Stripe SDK no longer has: the branch never ran, and every
    rejection returned the SDK's exception text to the caller.
    """

    @pytest.fixture(autouse=True)
    def configured(self, monkeypatch):
        monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_zx_probe")
        monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", _WEBHOOK_SECRET)
        monkeypatch.setattr(PaymentExtensionStripeProvider, "_stripe_available", False)
        monkeypatch.setattr(PaymentExtensionStripeProvider, "_stripe_client", None)

    async def _process(self, payload: str, signature: str) -> dict:
        from datetime import datetime, timezone

        from zephyrex.logic.BLL_Providers import ProviderInstanceModel

        now = datetime.now(timezone.utc)
        instance = ProviderInstanceModel(
            id="stripe-instance",
            provider_id="stripe",
            name="stripe",
            created_at=now,
            created_by_user_id=env("ROOT_ID"),
            updated_at=now,
            updated_by_user_id=env("ROOT_ID"),
        )
        result: dict = await PaymentExtensionStripeProvider.process_webhook(
            instance, payload, signature
        )
        return result

    @pytest.mark.asyncio
    async def test_a_correctly_signed_event_is_processed(self):
        payload = json.dumps(
            {"id": "evt_1", "type": "payment_intent.succeeded", "data": {"object": {}}}
        )
        signature = _stripe_signature(payload, _WEBHOOK_SECRET, int(time.time()))
        assert await self._process(payload, signature) == {
            "success": True,
            "event_type": "payment_intent.succeeded",
            "event_id": "evt_1",
            "processed": True,
        }

    @pytest.mark.security
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "secret, age",
        [("whsec_someone_else", 0), (_WEBHOOK_SECRET, 3600)],
        ids=["wrong-secret", "replayed-stale"],
    )
    async def test_a_bad_signature_is_rejected_without_detail(self, secret, age):
        payload = json.dumps({"id": "evt_2", "type": "payment_intent.succeeded"})
        signature = _stripe_signature(payload, secret, int(time.time()) - age)
        assert await self._process(payload, signature) == {
            "success": False,
            "error": "Invalid signature",
        }

    @pytest.mark.asyncio
    async def test_a_signed_body_that_is_not_an_event_is_rejected(self):
        payload = "not json at all"
        signature = _stripe_signature(payload, _WEBHOOK_SECRET, int(time.time()))
        assert await self._process(payload, signature) == {
            "success": False,
            "error": "Invalid payload",
        }


class _RecordingResource:
    """Stands in for one ``StripeClient.v1.<resource>`` service."""

    def __init__(self, calls: list, resource: str, record: dict) -> None:
        self._calls, self._resource, self._record = calls, resource, record

    def create(self, **params):
        self._calls.append((self._resource, "create", params))
        return SimpleNamespace(**self._record)

    def list(self, **params):
        self._calls.append((self._resource, "list", params))
        return SimpleNamespace(data=[SimpleNamespace(**self._record)])


_PRODUCT: Dict[str, Any] = dict(
    id="prod_1",
    name="Plan",
    description=None,
    active=True,
    images=[],
    metadata={},
    created=1,
    updated=1,
    livemode=False,
)
_CUSTOMER: Dict[str, Any] = dict(
    id="cus_1",
    email="a@example.com",
    name="A",
    phone=None,
    metadata={},
    created=1,
    balance=0,
    delinquent=False,
    tax_exempt="none",
    livemode=False,
)


@pytest.mark.payment
@pytest.mark.stripe
class TestStripeExternalResources:
    """Each external model calls its own Stripe resource.

    The product CRUD methods sat inside Stripe_SubscriptionModel, so product
    CRUD reached only the abstract stubs (nothing happened) while
    subscription CRUD would have created and deleted products. Calls also
    go through the SDK's current ``v1`` namespace.
    """

    @pytest.fixture
    def calls(self, monkeypatch) -> list:
        from zephyrex.extensions.AbstractExtensionProvider import (
            AbstractProviderInstance_SDK,
        )

        calls: list = []
        v1 = SimpleNamespace(
            products=_RecordingResource(calls, "products", _PRODUCT),
            customers=_RecordingResource(calls, "customers", _CUSTOMER),
        )
        client = SimpleNamespace(v1=v1)
        monkeypatch.setattr(
            PaymentExtensionStripeProvider,
            "bond_instance",
            classmethod(lambda cls, instance: AbstractProviderInstance_SDK(client)),
        )
        return calls

    def test_product_crud_reaches_products(self, calls):
        from zephyrex.extensions.payment.PRV_Stripe_Payment import (
            Stripe_ProductModel,
        )

        created = Stripe_ProductModel.create_via_provider(None, name="Plan")
        listed = Stripe_ProductModel.list_via_provider(None, limit=5)

        assert created["success"] is True, created
        assert created["data"]["id"] == "prod_1"
        assert listed["success"] is True, listed
        assert [call[:2] for call in calls] == [
            ("products", "create"),
            ("products", "list"),
        ]

    def test_customer_crud_reaches_customers(self, calls):
        created = Stripe_CustomerModel.create_via_provider(None, email="a@example.com")
        assert created["success"] is True, created
        assert calls == [("customers", "create", {"email": "a@example.com"})]

    def test_subscriptions_carry_no_product_crud(self):
        from zephyrex.extensions.payment.PRV_Stripe_Payment import (
            Stripe_SubscriptionModel,
        )

        own = {
            name
            for name in vars(Stripe_SubscriptionModel)
            if name.endswith("_via_provider")
        }
        assert own == {"get_subscription_status_via_provider"}
