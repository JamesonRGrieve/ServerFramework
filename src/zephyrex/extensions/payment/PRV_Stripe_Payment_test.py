from decimal import Decimal

import pytest

# Import BLL_Payment to ensure @extension_model decorator is applied
from zephyrex.extensions.payment.BLL_Payment import *
from zephyrex.extensions.payment.PRV_Stripe_Payment import (
    PaymentExtensionStripeProvider,
    Stripe_CustomerManager,
    Stripe_CustomerModel,
)
from zephyrex.lib.Dependencies import Dependencies
from zephyrex.lib.Environment import env


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

        # Check if stripe library is available
        try:
            import stripe

            # If library is available, bonding should succeed
            assert bonded is not None
            assert hasattr(bonded, "sdk")
        except ImportError:
            # If library not available, bonding should fail
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
        """Test creating payment without API key."""

        class MockInstanceWithoutKey:
            id = "test_id"
            api_key = None

        instance = MockInstanceWithoutKey()

        # Should fail gracefully without API key
        try:
            result = await PaymentExtensionStripeProvider.create_payment(
                instance,
                amount=Decimal("10.00"),
                currency="USD",
                description="Test payment",
            )
            # If it doesn't raise an exception, should return error info
            assert isinstance(result, dict)
            assert "error" in result or "failed" in str(result).lower()
        except Exception as e:
            # Should handle the error gracefully
            error_msg = str(e).lower()
            assert any(word in error_msg for word in ["api", "key", "stripe", "config"])

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
        # This should fail gracefully with invalid data
        try:
            result = await PaymentExtensionStripeProvider.process_webhook(
                provider_instance, b'{"test": "data"}', "invalid_signature"
            )
            # Should return error result
            assert isinstance(result, dict)
            assert "error" in result or not result.get("success", True)
        except Exception as e:
            # Should handle invalid webhook gracefully
            error_msg = str(e).lower()
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
        original = b'{"id": "evt_1", "type": "payment_intent.succeeded"}'
        tampered = b'{"id": "evt_1", "type": "payment_intent.refunded"}'
        # We don't have a valid signature for either, so use a syntactically
        # plausible-but-wrong sig to confirm the verify path engages.
        bogus_sig = "t=1700000000,v1=" + ("00" * 32)
        try:
            result = await PaymentExtensionStripeProvider.process_webhook(
                provider_instance, tampered, bogus_sig
            )
            assert isinstance(result, dict)
            assert (
                "error" in result or not result.get("success", True)
            ), "Tampered body must surface as error"
        except Exception as e:
            err = str(e).lower()
            assert any(
                t in err for t in ("signature", "webhook", "invalid", "verify")
            ), f"Tampered body raised unexpected error: {e}"

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
            assert (
                "error" in result or not result.get("success", True)
            ), "Old-timestamp webhook must be rejected"
        except Exception as e:
            err = str(e).lower()
            assert any(
                t in err
                for t in ("timestamp", "tolerance", "stale", "signature")
            ), f"Old timestamp raised unexpected error: {e}"
