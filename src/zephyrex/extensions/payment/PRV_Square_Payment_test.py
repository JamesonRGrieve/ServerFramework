# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest

from zephyrex.extensions.payment.BLL_Payment import *  # noqa: F401,F403
from zephyrex.extensions.payment.PRV_Square_Payment import (
    PaymentExtensionSquareProvider,
    Square_CustomerManager,
    Square_CustomerModel,
    Square_PaymentModel,
    Square_SubscriptionModel,
)
from zephyrex.lib.Environment import env


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

    def test_bond_instance_without_api_key(self):
        class MockInstanceWithoutKey:
            id = "test_id"
            api_key = None

        instance = MockInstanceWithoutKey()
        bonded = PaymentExtensionSquareProvider.bond_instance(instance)
        assert bonded is None

    def test_bond_instance_with_api_key(self, provider_instance):
        bonded = PaymentExtensionSquareProvider.bond_instance(provider_instance)
        try:
            from square.client import Client as SquareClient

            assert SquareClient is not None
            assert bonded is not None
            assert hasattr(bonded, "sdk")
        except ImportError:
            assert bonded is None

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
    async def test_process_webhook_valid_signature_via_ssot(self, monkeypatch):
        # A correctly signed payload verifies through the shared
        # AbstractPaymentProvider.verify_hmac_sha256 SSOT and is processed.
        import hashlib
        import hmac

        secret = "whsec_square_test"
        payload = b'{"type": "payment.updated", "event_id": "evt_sq_1"}'
        monkeypatch.setattr(
            PaymentExtensionSquareProvider,
            "get_webhook_signature_key",
            classmethod(lambda cls: secret),
        )
        good_sig = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
        result = await PaymentExtensionSquareProvider.process_webhook(
            None, payload, good_sig
        )
        assert result["success"] is True
        assert result["event_type"] == "payment.updated"
        assert result["event_id"] == "evt_sq_1"

    @pytest.mark.asyncio
    async def test_process_webhook_wrong_signature_returns_failure(self, monkeypatch):
        # A non-empty but incorrect signature keeps Square's return-dict branch.
        secret = "whsec_square_test"
        payload = b'{"type": "payment.updated"}'
        monkeypatch.setattr(
            PaymentExtensionSquareProvider,
            "get_webhook_signature_key",
            classmethod(lambda cls: secret),
        )
        result = await PaymentExtensionSquareProvider.process_webhook(
            None, payload, "deadbeef_not_the_real_digest"
        )
        assert result["success"] is False
