# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest

from zephyrex.extensions.payment.BLL_Payment import *  # noqa: F401,F403
from zephyrex.extensions.payment.PRV_Moneris_Payment import (
    Moneris_CustomerManager,
    Moneris_CustomerModel,
    Moneris_PaymentModel,
    Moneris_SubscriptionModel,
    PaymentExtensionMonerisProvider,
)
from zephyrex.lib.Dependencies import Dependencies
from zephyrex.lib.Environment import env


@pytest.mark.payment
@pytest.mark.moneris
class TestMonerisProvider:
    """Test suite for Moneris payment provider.

    Tests provider static methods, configuration, and model/manager
    structure. Fully compatible with the Provider Rotation System.
    """

    provider_class = PaymentExtensionMonerisProvider
    extension_id = "payment"

    @pytest.fixture
    def moneris_store_id(self):
        store_id = env("MONERIS_STORE_ID")
        if not store_id:
            pytest.xfail("MONERIS_STORE_ID environment variable not set")
        return store_id

    @pytest.fixture
    def provider_instance(self, moneris_store_id):
        class MockProviderInstance:
            def __init__(self, api_key):
                self.id = "test_moneris_instance_id"
                self.api_key = api_key
                self.provider_id = "moneris"
                self.name = "Test Moneris Instance"

        return MockProviderInstance(moneris_store_id)

    def test_provider_structure(self):
        assert hasattr(PaymentExtensionMonerisProvider, "name")
        assert hasattr(PaymentExtensionMonerisProvider, "version")
        assert hasattr(PaymentExtensionMonerisProvider, "description")
        assert hasattr(PaymentExtensionMonerisProvider, "dependencies")
        assert hasattr(PaymentExtensionMonerisProvider, "_env")
        assert hasattr(PaymentExtensionMonerisProvider, "bond_instance")
        assert hasattr(PaymentExtensionMonerisProvider, "get_platform_name")

    def test_provider_metadata(self):
        assert PaymentExtensionMonerisProvider.name == "moneris"
        assert isinstance(PaymentExtensionMonerisProvider.version, str)
        assert isinstance(PaymentExtensionMonerisProvider.description, str)
        assert PaymentExtensionMonerisProvider.get_platform_name() == "Moneris"

    def test_provider_dependencies(self):
        deps = PaymentExtensionMonerisProvider.dependencies
        assert deps is not None
        assert hasattr(deps, "pip")
        assert len(deps.pip) > 0
        httpx_dep = next((dep for dep in deps.pip if dep.name == "httpx"), None)
        assert httpx_dep is not None

    def test_provider_env_vars(self):
        env_vars = PaymentExtensionMonerisProvider._env
        assert isinstance(env_vars, dict)
        assert "MONERIS_STORE_ID" in env_vars
        assert "MONERIS_MERCHANT_ID" in env_vars
        assert "MONERIS_ENVIRONMENT" in env_vars
        assert "MONERIS_CURRENCY" in env_vars

    def test_bond_instance_without_api_key(self):
        class MockInstanceWithoutKey:
            id = "test_id"
            api_key = None

        import os

        original_store = os.environ.pop("MONERIS_STORE_ID", None)
        original_key = os.environ.pop("MONERIS_API_KEY", None)
        try:
            from zephyrex.lib.Environment import refresh_settings

            refresh_settings()
            instance = MockInstanceWithoutKey()
            bonded = PaymentExtensionMonerisProvider.bond_instance(instance)
            assert bonded is None
        finally:
            if original_store is not None:
                os.environ["MONERIS_STORE_ID"] = original_store
            if original_key is not None:
                os.environ["MONERIS_API_KEY"] = original_key
            from zephyrex.lib.Environment import refresh_settings

            refresh_settings()

    def test_bond_instance_with_api_key(self, provider_instance):
        bonded = PaymentExtensionMonerisProvider.bond_instance(provider_instance)
        try:
            import httpx

            assert bonded is not None
            assert hasattr(bonded, "sdk")
        except ImportError:
            assert bonded is None

    def test_static_configuration_methods(self):
        store_id = PaymentExtensionMonerisProvider.get_store_id()
        if env("MONERIS_STORE_ID"):
            assert store_id == env("MONERIS_STORE_ID")

        merchant_id = PaymentExtensionMonerisProvider.get_merchant_id()
        if env("MONERIS_MERCHANT_ID"):
            assert merchant_id == env("MONERIS_MERCHANT_ID")

    def test_validate_config(self):
        has_creds = bool(env("MONERIS_STORE_ID") and env("MONERIS_MERCHANT_ID"))
        assert PaymentExtensionMonerisProvider.validate_config() == has_creds

    def test_currency_defaults_to_cad(self):
        default_currency = PaymentExtensionMonerisProvider.get_default_currency()
        assert isinstance(default_currency, str)
        assert len(default_currency) == 3
        if not env("MONERIS_CURRENCY"):
            assert default_currency == "CAD"

    def test_external_models_exist(self):
        assert Moneris_CustomerModel is not None
        assert hasattr(Moneris_CustomerModel, "external_resource")
        assert Moneris_CustomerModel.external_resource == "customers"
        assert getattr(Moneris_CustomerModel, "_is_extension_model", False)

    def test_external_manager_exists(self):
        assert Moneris_CustomerManager is not None
        assert hasattr(Moneris_CustomerManager, "sync_contact")
        assert hasattr(Moneris_CustomerManager, "create_customer")
        assert callable(Moneris_CustomerManager.sync_contact)
        assert callable(Moneris_CustomerManager.create_customer)

    def test_moneris_models_structure(self):
        models = [
            (Moneris_CustomerModel, "customers"),
            (Moneris_PaymentModel, "payments"),
            (Moneris_SubscriptionModel, "subscriptions"),
        ]
        for model_class, expected_resource in models:
            assert hasattr(model_class, "external_resource")
            assert model_class.external_resource == expected_resource
            assert getattr(model_class, "_is_extension_model", False)

    def test_services_method(self):
        services = PaymentExtensionMonerisProvider.services()
        assert isinstance(services, list)
        assert "payment" in services

    def test_extension_info(self):
        info = PaymentExtensionMonerisProvider.get_extension_info()
        assert isinstance(info, dict)
        assert info["platform"] == "Moneris"

    @pytest.mark.asyncio
    async def test_process_webhook_valid_json(self, provider_instance):
        result = await PaymentExtensionMonerisProvider.process_webhook(
            provider_instance,
            '{"type": "RECURRING_PAYMENT_CONFIRMED", "id": "evt_123"}',
            "sig",
        )
        assert isinstance(result, dict)
        assert result["success"] is True
        assert result["event_type"] == "RECURRING_PAYMENT_CONFIRMED"
        assert result["event_id"] == "evt_123"

    @pytest.mark.asyncio
    async def test_process_webhook_invalid_json(self, provider_instance):
        result = await PaymentExtensionMonerisProvider.process_webhook(
            provider_instance, "not json", "sig"
        )
        assert isinstance(result, dict)
        assert not result.get("success", True)
        assert "error" in result
