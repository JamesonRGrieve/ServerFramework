# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest

from zephyrex.extensions.payment.BLL_Payment import *  # noqa: F401,F403
from zephyrex.extensions.payment.PRV_Helcim_Payment import (
    Helcim_CustomerManager,
    Helcim_CustomerModel,
    Helcim_InvoiceModel,
    Helcim_PaymentModel,
    PaymentExtensionHelcimProvider,
)
from zephyrex.lib.Dependencies import Dependencies
from zephyrex.lib.Environment import env


@pytest.mark.payment
@pytest.mark.helcim
class TestHelcimProvider:
    """Test suite for Helcim payment provider.

    Tests provider static methods, configuration, and model/manager
    structure. Fully compatible with the Provider Rotation System.
    """

    provider_class = PaymentExtensionHelcimProvider
    extension_id = "payment"

    @pytest.fixture
    def helcim_api_token(self):
        token = env("HELCIM_API_TOKEN")
        if not token:
            pytest.xfail("HELCIM_API_TOKEN environment variable not set")
        return token

    @pytest.fixture
    def provider_instance(self, helcim_api_token):
        class MockProviderInstance:
            def __init__(self, api_key):
                self.id = "test_helcim_instance_id"
                self.api_key = api_key
                self.provider_id = "helcim"
                self.name = "Test Helcim Instance"

        return MockProviderInstance(helcim_api_token)

    def test_provider_structure(self):
        assert hasattr(PaymentExtensionHelcimProvider, "name")
        assert hasattr(PaymentExtensionHelcimProvider, "version")
        assert hasattr(PaymentExtensionHelcimProvider, "description")
        assert hasattr(PaymentExtensionHelcimProvider, "dependencies")
        assert hasattr(PaymentExtensionHelcimProvider, "_env")
        assert hasattr(PaymentExtensionHelcimProvider, "bond_instance")
        assert hasattr(PaymentExtensionHelcimProvider, "get_platform_name")

    def test_provider_metadata(self):
        assert PaymentExtensionHelcimProvider.name == "helcim"
        assert isinstance(PaymentExtensionHelcimProvider.version, str)
        assert isinstance(PaymentExtensionHelcimProvider.description, str)
        assert PaymentExtensionHelcimProvider.get_platform_name() == "Helcim"

    def test_provider_dependencies(self):
        deps = PaymentExtensionHelcimProvider.dependencies
        assert deps is not None
        assert hasattr(deps, "pip")
        assert len(deps.pip) > 0
        httpx_dep = next((dep for dep in deps.pip if dep.name == "httpx"), None)
        assert httpx_dep is not None

    def test_provider_env_vars(self):
        env_vars = PaymentExtensionHelcimProvider._env
        assert isinstance(env_vars, dict)
        assert "HELCIM_API_TOKEN" in env_vars
        assert "HELCIM_CURRENCY" in env_vars

    def test_bond_instance_without_api_key(self):
        class MockInstanceWithoutKey:
            id = "test_id"
            api_key = None

        import os

        original = os.environ.pop("HELCIM_API_TOKEN", None)
        try:
            from zephyrex.lib.Environment import refresh_settings

            refresh_settings()
            instance = MockInstanceWithoutKey()
            bonded = PaymentExtensionHelcimProvider.bond_instance(instance)
            assert bonded is None
        finally:
            if original is not None:
                os.environ["HELCIM_API_TOKEN"] = original
                from zephyrex.lib.Environment import refresh_settings

                refresh_settings()

    def test_currency_defaults_to_cad(self):
        default_currency = PaymentExtensionHelcimProvider.get_default_currency()
        assert isinstance(default_currency, str)
        assert len(default_currency) == 3
        if not env("HELCIM_CURRENCY"):
            assert default_currency == "CAD"

    def test_external_models_exist(self):
        assert Helcim_CustomerModel is not None
        assert hasattr(Helcim_CustomerModel, "external_resource")
        assert Helcim_CustomerModel.external_resource == "customers"
        assert getattr(Helcim_CustomerModel, "_is_extension_model", False)

    def test_external_manager_exists(self):
        assert Helcim_CustomerManager is not None
        assert hasattr(Helcim_CustomerManager, "sync_contact")
        assert hasattr(Helcim_CustomerManager, "create_customer")
        assert callable(Helcim_CustomerManager.sync_contact)
        assert callable(Helcim_CustomerManager.create_customer)

    def test_helcim_models_structure(self):
        models = [
            (Helcim_CustomerModel, "customers"),
            (Helcim_PaymentModel, "payment"),
            (Helcim_InvoiceModel, "invoices"),
        ]
        for model_class, expected_resource in models:
            assert hasattr(model_class, "external_resource")
            assert model_class.external_resource == expected_resource
            assert getattr(model_class, "_is_extension_model", False)

    def test_services_method(self):
        services = PaymentExtensionHelcimProvider.services()
        assert isinstance(services, list)
        assert "payment" in services

    def test_extension_info(self):
        info = PaymentExtensionHelcimProvider.get_extension_info()
        assert isinstance(info, dict)
        assert info["platform"] == "Helcim"

    def test_subscription_not_supported(self):
        class FakeInstance:
            id = "test"
            api_key = "test"

        result = PaymentExtensionHelcimProvider.create_subscription(
            FakeInstance(), customer_id="c1", price_id="p1"
        )
        assert not result["success"]
        assert "invoice" in result["error"].lower() or "subscription" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_process_webhook_valid_json(self):
        class FakeInstance:
            id = "test"
            api_key = "test"

        result = await PaymentExtensionHelcimProvider.process_webhook(
            FakeInstance(),
            '{"eventName": "transaction.completed", "id": "evt_456"}',
            "sig",
        )
        assert isinstance(result, dict)
        assert result["success"] is True
        assert result["event_type"] == "transaction.completed"
        assert result["event_id"] == "evt_456"

    @pytest.mark.asyncio
    async def test_process_webhook_invalid_json(self):
        class FakeInstance:
            id = "test"
            api_key = "test"

        result = await PaymentExtensionHelcimProvider.process_webhook(
            FakeInstance(), "not json", "sig"
        )
        assert isinstance(result, dict)
        assert not result.get("success", True)
        assert "error" in result
