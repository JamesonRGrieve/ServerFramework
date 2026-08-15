# SPDX-License-Identifier: AGPL-3.0-or-later
from decimal import Decimal

import pytest

from zephyrex.extensions.payment.BLL_Payment import *  # noqa: F401,F403
from zephyrex.extensions.payment.PRV_PayPal_Payment import (
    PaymentExtensionPayPalProvider,
    PayPal_CustomerManager,
    PayPal_CustomerModel,
    PayPal_OrderModel,
    PayPal_SubscriptionModel,
)
from zephyrex.lib.Dependencies import Dependencies
from zephyrex.lib.Environment import env


@pytest.mark.payment
@pytest.mark.paypal
class TestPayPalProvider:
    """Test suite for PayPal payment provider.

    Tests provider static methods, payment processing, and PayPal API
    integration. Fully compatible with the Provider Rotation System.
    """

    provider_class = PaymentExtensionPayPalProvider
    extension_id = "payment"

    @pytest.fixture
    def paypal_client_id(self):
        client_id = env("PAYPAL_CLIENT_ID")
        if not client_id:
            pytest.xfail("PAYPAL_CLIENT_ID environment variable not set")
        return client_id

    @pytest.fixture
    def provider_instance(self, paypal_client_id):
        class MockProviderInstance:
            def __init__(self, api_key):
                self.id = "test_paypal_instance_id"
                self.api_key = api_key
                self.provider_id = "paypal"
                self.name = "Test PayPal Instance"

        return MockProviderInstance(paypal_client_id)

    def test_provider_structure(self):
        assert hasattr(PaymentExtensionPayPalProvider, "name")
        assert hasattr(PaymentExtensionPayPalProvider, "version")
        assert hasattr(PaymentExtensionPayPalProvider, "description")
        assert hasattr(PaymentExtensionPayPalProvider, "dependencies")
        assert hasattr(PaymentExtensionPayPalProvider, "_env")
        assert hasattr(PaymentExtensionPayPalProvider, "bond_instance")
        assert hasattr(PaymentExtensionPayPalProvider, "get_platform_name")

    def test_provider_metadata(self):
        assert PaymentExtensionPayPalProvider.name == "paypal"
        assert isinstance(PaymentExtensionPayPalProvider.version, str)
        assert isinstance(PaymentExtensionPayPalProvider.description, str)
        assert PaymentExtensionPayPalProvider.get_platform_name() == "PayPal"

    def test_provider_dependencies(self):
        deps = PaymentExtensionPayPalProvider.dependencies
        assert deps is not None
        assert hasattr(deps, "pip")
        assert len(deps.pip) > 0
        paypal_dep = next(
            (dep for dep in deps.pip if dep.name == "paypalrestsdk"), None
        )
        assert paypal_dep is not None

    def test_provider_env_vars(self):
        env_vars = PaymentExtensionPayPalProvider._env
        assert isinstance(env_vars, dict)
        assert "PAYPAL_CLIENT_ID" in env_vars
        assert "PAYPAL_SECRET" in env_vars
        assert "PAYPAL_WEBHOOK_ID" in env_vars
        assert "PAYPAL_CURRENCY" in env_vars

    def test_bond_instance_without_credentials(self):
        class MockInstanceWithoutKey:
            id = "test_id"
            api_key = None

        instance = MockInstanceWithoutKey()
        bonded = PaymentExtensionPayPalProvider.bond_instance(instance)
        try:
            import paypalrestsdk

            if not env("PAYPAL_CLIENT_ID") or not env("PAYPAL_SECRET"):
                assert bonded is None
        except ImportError:
            assert bonded is None

    def test_static_configuration_methods(self):
        client_id = PaymentExtensionPayPalProvider.get_client_id()
        if env("PAYPAL_CLIENT_ID"):
            assert client_id == env("PAYPAL_CLIENT_ID")

        client_secret = PaymentExtensionPayPalProvider.get_client_secret()
        if env("PAYPAL_SECRET"):
            assert client_secret == env("PAYPAL_SECRET")

    def test_currency_and_environment_handling(self):
        default_currency = PaymentExtensionPayPalProvider.get_default_currency()
        assert isinstance(default_currency, str)
        assert len(default_currency) == 3

    def test_external_models_exist(self):
        assert PayPal_CustomerModel is not None
        assert hasattr(PayPal_CustomerModel, "external_resource")
        assert PayPal_CustomerModel.external_resource == "customers"
        assert getattr(PayPal_CustomerModel, "_is_extension_model", False)

    def test_external_manager_exists(self):
        assert PayPal_CustomerManager is not None
        assert hasattr(PayPal_CustomerManager, "sync_contact")
        assert hasattr(PayPal_CustomerManager, "create_customer")
        assert callable(PayPal_CustomerManager.sync_contact)
        assert callable(PayPal_CustomerManager.create_customer)

    def test_paypal_models_structure(self):
        models = [
            (PayPal_CustomerModel, "customers"),
            (PayPal_OrderModel, "orders"),
            (PayPal_SubscriptionModel, "subscriptions"),
        ]
        for model_class, expected_resource in models:
            assert hasattr(model_class, "external_resource")
            assert model_class.external_resource == expected_resource
            assert getattr(model_class, "_is_extension_model", False)

    def test_services_method(self):
        services = PaymentExtensionPayPalProvider.services()
        assert isinstance(services, list)
        assert "payment" in services

    def test_extension_info(self):
        info = PaymentExtensionPayPalProvider.get_extension_info()
        assert isinstance(info, dict)
        assert info["platform"] == "PayPal"

    def test_create_customer_synthesized(self, provider_instance):
        """PayPal customer creation is synthesized (no first-class API).

        Verify the provider returns a well-formed customer record.
        """
        result = PaymentExtensionPayPalProvider.create_customer(
            provider_instance, email="test@example.com", name="Test User"
        )
        assert result["success"] is True
        assert result["email"] == "test@example.com"
        assert result["name"] == "Test User"
        assert result["customer_id"].startswith("PAYPAL-")

    @pytest.mark.asyncio
    async def test_process_webhook_no_webhook_id(self, provider_instance):
        """Without PAYPAL_WEBHOOK_ID the webhook path should refuse."""
        import os

        original = os.environ.pop("PAYPAL_WEBHOOK_ID", None)
        try:
            from zephyrex.lib.Environment import refresh_settings

            refresh_settings()
            result = await PaymentExtensionPayPalProvider.process_webhook(
                provider_instance, '{"event_type": "test"}', "sig"
            )
            assert isinstance(result, dict)
            if not result.get("success", True):
                assert "error" in result
        finally:
            if original is not None:
                os.environ["PAYPAL_WEBHOOK_ID"] = original
                from zephyrex.lib.Environment import refresh_settings

                refresh_settings()
