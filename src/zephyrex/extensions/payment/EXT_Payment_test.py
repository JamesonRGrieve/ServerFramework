from decimal import Decimal
from typing import Dict, List, Optional

import pytest

from zephyrex.extensions.payment.EXT_Payment import AbstractPaymentProvider, EXT_Payment
from zephyrex.lib.Dependencies import Dependencies, PIP_Dependency


class ConcretePaymentProvider(AbstractPaymentProvider):
    """Concrete implementation of AbstractPaymentProvider for testing"""

    # Static provider metadata
    name = "test_payment"
    version = "1.0.0"
    description = "Test payment provider"

    # Link to parent extension (REQUIRED for Provider Rotation System)
    extension = EXT_Payment

    # Add unified dependencies using the Dependencies class
    dependencies = Dependencies(
        [
            PIP_Dependency(
                name="stripe",
                friendly_name="Stripe Python Library",
                semver=">=5.5.0",
                reason="Payment processing support",
            ),
        ]
    )

    # Initialize static abilities for testing
    abilities = {
        "payment_processing",
        "subscription_management",
        "webhook_handling",
        "customer_management",
    }

    @classmethod
    def create_payment(
        cls,
        amount: Decimal,
        currency: str = "USD",
        customer_id: Optional[str] = None,
        payment_method_id: Optional[str] = None,
        description: Optional[str] = None,
        metadata: Optional[Dict] = None,
    ) -> Dict:
        """Mock implementation for testing"""
        return {
            "success": True,
            "payment_id": "test_payment_123",
            "amount": amount,
            "currency": currency,
            "status": "succeeded",
            "customer_id": customer_id,
            "payment_method_id": payment_method_id,
            "description": description,
            "metadata": metadata or {},
        }

    @classmethod
    def get_payment(cls, payment_id: str) -> Dict:
        """Mock implementation for testing"""
        return {
            "success": True,
            "payment_id": payment_id,
            "amount": Decimal("50.00"),
            "currency": "USD",
            "status": "succeeded",
        }

    @classmethod
    def refund_payment(
        cls,
        payment_id: str,
        amount: Optional[Decimal] = None,
        reason: Optional[str] = None,
    ) -> Dict:
        """Mock implementation for testing"""
        return {
            "success": True,
            "refund_id": "test_refund_123",
            "payment_id": payment_id,
            "amount": amount or Decimal("50.00"),
            "reason": reason,
            "status": "succeeded",
        }

    @classmethod
    def create_customer(
        cls,
        email: str,
        name: Optional[str] = None,
        phone: Optional[str] = None,
        metadata: Optional[Dict] = None,
    ) -> Dict:
        """Mock implementation for testing"""
        return {
            "success": True,
            "customer_id": "test_customer_123",
            "email": email,
            "name": name,
            "phone": phone,
            "metadata": metadata or {},
        }

    @classmethod
    def get_customer(cls, customer_id: str) -> Dict:
        """Mock implementation for testing"""
        return {
            "success": True,
            "customer_id": customer_id,
            "email": "test@example.com",
            "name": "Test Customer",
        }

    @classmethod
    def create_subscription(
        cls,
        customer_id: str,
        price_id: str,
        payment_method_id: Optional[str] = None,
        trial_days: Optional[int] = None,
        metadata: Optional[Dict] = None,
    ) -> Dict:
        """Mock implementation for testing"""
        return {
            "success": True,
            "subscription_id": "test_subscription_123",
            "customer_id": customer_id,
            "price_id": price_id,
            "payment_method_id": payment_method_id,
            "trial_days": trial_days,
            "status": "active",
            "metadata": metadata or {},
        }

    @classmethod
    def cancel_subscription(
        cls, subscription_id: str, immediately: bool = False
    ) -> Dict:
        """Mock implementation for testing"""
        return {
            "success": True,
            "subscription_id": subscription_id,
            "status": "canceled" if immediately else "cancel_at_period_end",
            "canceled_at": "2023-01-01T00:00:00Z" if immediately else None,
            "cancelled_immediately": immediately,
        }

    @classmethod
    def process_webhook(cls, payload: str, signature: str) -> Dict:
        """Mock implementation for testing"""
        return {
            "success": True,
            "event_type": "payment.succeeded",
            "processed": True,
            "payload_size": len(payload),
            "signature_valid": bool(signature),
        }

    @classmethod
    def get_platform_name(cls) -> str:
        """Return the test platform name"""
        return "TestPayment"

    @classmethod
    def has_ability(cls, ability: str) -> bool:
        """Check if provider has a specific ability."""
        return super().has_ability(ability)  # type: ignore[no-any-return]

    @classmethod
    def services(cls) -> List[str]:
        """Return list of services provided by this provider."""
        return ["payment", "billing", "subscription", "commerce"]

    @classmethod
    def bond_instance(cls, instance):
        """Bond provider instance for rotation system."""
        return cls


@pytest.mark.payment
class TestEXTPayment:
    """
    Test suite for EXT_Payment extension.

    Tests basic extension metadata and abstract provider interface.
    Only tests functionality that actually exists in the implementation.

    Test areas:
    - Extension metadata (name, version, description)
    - Abstract payment provider class structure
    - Provider inheritance and linkage
    """

    def test_extension_metadata(self):
        """Test extension metadata."""
        assert EXT_Payment.name == "payment"
        assert EXT_Payment.friendly_name == "Payment Processing"
        assert EXT_Payment.version == "1.0.0"
        assert "payment" in EXT_Payment.description.lower()
        assert "rotation" in EXT_Payment.description.lower()

    def test_extension_class_structure(self):
        """Test extension class structure."""
        # Test that EXT_Payment is properly defined
        assert hasattr(EXT_Payment, "name")
        assert hasattr(EXT_Payment, "friendly_name")
        assert hasattr(EXT_Payment, "version")
        assert hasattr(EXT_Payment, "description")

        # Test inheritance
        from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension

        assert issubclass(EXT_Payment, AbstractStaticExtension)

    def test_abstract_payment_provider_class_exists(self):
        """Test that AbstractPaymentProvider class exists and is properly structured."""
        # Test that the abstract class exists
        assert AbstractPaymentProvider is not None

        # Test inheritance
        from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticProvider

        assert issubclass(AbstractPaymentProvider, AbstractStaticProvider)

        # Test extension linkage
        assert hasattr(AbstractPaymentProvider, "extension_type")
        assert AbstractPaymentProvider.extension_type == "payment"

    def test_abstract_payment_provider_cannot_be_instantiated(self):
        """Test that AbstractPaymentProvider cannot be instantiated directly."""
        with pytest.raises(TypeError):
            AbstractPaymentProvider()

    def test_concrete_provider_can_inherit_from_abstract(self):
        """Test that concrete providers can inherit from AbstractPaymentProvider."""
        # Test that ConcretePaymentProvider inherits properly
        assert issubclass(ConcretePaymentProvider, AbstractPaymentProvider)

        # Test required attributes exist
        assert hasattr(ConcretePaymentProvider, "name")
        assert hasattr(ConcretePaymentProvider, "version")
        assert hasattr(ConcretePaymentProvider, "description")
        assert hasattr(ConcretePaymentProvider, "extension")

        # Test extension linkage works
        assert ConcretePaymentProvider.extension == EXT_Payment

    def test_concrete_provider_metadata(self):
        """Test concrete provider metadata."""
        assert ConcretePaymentProvider.name == "test_payment"
        assert ConcretePaymentProvider.version == "1.0.0"
        assert ConcretePaymentProvider.description == "Test payment provider"

    def test_concrete_provider_methods_implementation(self):
        """Test that concrete provider implements required payment methods."""
        # Test that all expected payment methods exist and are callable
        payment_methods = [
            "create_payment",
            "get_payment",
            "refund_payment",
            "create_customer",
            "get_customer",
            "create_subscription",
            "cancel_subscription",
            "process_webhook",
            "get_platform_name",
        ]

        for method_name in payment_methods:
            assert hasattr(ConcretePaymentProvider, method_name)
            assert callable(getattr(ConcretePaymentProvider, method_name))

    def test_concrete_provider_payment_functionality(self):
        """Test concrete provider payment functionality works."""
        # Test create_payment
        result = ConcretePaymentProvider.create_payment(
            amount=Decimal("50.00"),
            currency="USD",
            description="Test payment",
        )
        assert isinstance(result, dict)
        assert result["success"] is True
        assert result["amount"] == Decimal("50.00")

        # Test get_payment
        result = ConcretePaymentProvider.get_payment("test_payment_123")
        assert isinstance(result, dict)
        assert result["success"] is True
        assert "payment_id" in result

    def test_concrete_provider_customer_functionality(self):
        """Test concrete provider customer functionality works."""
        # Test create_customer
        result = ConcretePaymentProvider.create_customer(
            email="test@example.com",
            name="Test Customer",
        )
        assert isinstance(result, dict)
        assert result["success"] is True
        assert result["email"] == "test@example.com"

        # Test get_customer
        result = ConcretePaymentProvider.get_customer("test_customer_123")
        assert isinstance(result, dict)
        assert result["success"] is True
        assert "customer_id" in result

    def test_concrete_provider_subscription_functionality(self):
        """Test concrete provider subscription functionality works."""
        # Test create_subscription
        result = ConcretePaymentProvider.create_subscription(
            customer_id="test_customer_123",
            price_id="price_123",
        )
        assert isinstance(result, dict)
        assert result["success"] is True
        assert result["customer_id"] == "test_customer_123"

        # Test cancel_subscription
        result = ConcretePaymentProvider.cancel_subscription(
            subscription_id="test_subscription_123",
        )
        assert isinstance(result, dict)
        assert result["success"] is True
        assert "status" in result

    def test_concrete_provider_webhook_functionality(self):
        """Test concrete provider webhook functionality works."""
        result = ConcretePaymentProvider.process_webhook(
            payload='{"event": "payment.succeeded"}',
            signature="test_signature",
        )
        assert isinstance(result, dict)
        assert result["success"] is True
        assert result["processed"] is True

    def test_concrete_provider_utility_methods(self):
        """Test concrete provider utility methods."""
        # Test get_platform_name
        platform_name = ConcretePaymentProvider.get_platform_name()
        assert platform_name == "TestPayment"

        # Test services
        services = ConcretePaymentProvider.services()
        assert isinstance(services, list)
        assert "payment" in services

    def test_concrete_provider_dependencies_structure(self):
        """Test concrete provider dependencies structure."""
        assert hasattr(ConcretePaymentProvider, "dependencies")
        assert isinstance(ConcretePaymentProvider.dependencies, Dependencies)

        # Test that it has pip dependencies
        pip_deps = ConcretePaymentProvider.dependencies.pip
        assert len(pip_deps) >= 1
        assert any(dep.name == "stripe" for dep in pip_deps)

    def test_concrete_provider_bond_instance_method(self):
        """Test concrete provider bond_instance method.

        bond_instance for the test provider simply returns the class. We
        verify that contract using a real provider-instance-shaped object
        rather than a mock (per AGENTS.md no-mock pillar).
        """

        class _FakeInstance:
            """Minimal real stand-in for a provider instance DTO.

            bond_instance only inspects the parameter when overridden by
            a real provider; this concrete test provider returns ``cls``
            unconditionally, so any plain object satisfies the contract.
            """

            id = "test-instance-id"
            name = "test-instance"

        result = ConcretePaymentProvider.bond_instance(_FakeInstance())
        assert result is not None

    def test_provider_discovery(self):
        """Test provider discovery functionality."""
        providers = EXT_Payment.providers  # Access as a property
        assert isinstance(providers, list), "Providers should be a list"
        # Providers list may be empty in test environment, which is acceptable


from decimal import Decimal
from typing import Dict, List, Optional

import pytest

from zephyrex.extensions.payment.EXT_Payment import AbstractPaymentProvider, EXT_Payment
from zephyrex.lib.Dependencies import Dependencies, PIP_Dependency
from zephyrex.lib.Environment import env


class ConcretePaymentProvider(AbstractPaymentProvider):  # type: ignore[no-redef]
    """Concrete implementation of AbstractPaymentProvider for testing"""

    # Static provider metadata
    name = "test_payment"
    version = "1.0.0"
    description = "Test payment provider"

    # Link to parent extension (REQUIRED for Provider Rotation System)
    extension = EXT_Payment

    # Add unified dependencies using the Dependencies class
    dependencies = Dependencies(
        [
            PIP_Dependency(
                name="stripe",
                friendly_name="Stripe Python Library",
                semver=">=5.5.0",
                reason="Payment processing support",
            ),
        ]
    )

    # Initialize static abilities for testing
    abilities = {
        "payment_processing",
        "subscription_management",
        "webhook_handling",
        "customer_management",
    }

    # Environment variables for testing
    env = {
        "CONCRETEPAYMENT_SECRET_KEY": "test_secret",
        "CONCRETEPAYMENT_WEBHOOK_SECRET": "test_webhook",
        "CONCRETEPAYMENT_CURRENCY": "USD",
    }

    @classmethod
    def create_payment(
        cls,
        amount: Decimal,
        currency: str = "USD",
        customer_id: Optional[str] = None,
        payment_method_id: Optional[str] = None,
        description: Optional[str] = None,
        metadata: Optional[Dict] = None,
    ) -> Dict:
        """Mock implementation for testing"""
        return {
            "success": True,
            "payment_id": "test_payment_123",
            "amount": amount,
            "currency": currency,
            "status": "succeeded",
            "customer_id": customer_id,
            "payment_method_id": payment_method_id,
            "description": description,
            "metadata": metadata or {},
        }

    @classmethod
    def get_payment(cls, payment_id: str) -> Dict:
        """Mock implementation for testing"""
        return {
            "success": True,
            "payment_id": payment_id,
            "amount": Decimal("50.00"),
            "currency": "USD",
            "status": "succeeded",
        }

    @classmethod
    def refund_payment(
        cls,
        payment_id: str,
        amount: Optional[Decimal] = None,
        reason: Optional[str] = None,
    ) -> Dict:
        """Mock implementation for testing"""
        return {
            "success": True,
            "refund_id": "test_refund_123",
            "payment_id": payment_id,
            "amount": amount or Decimal("50.00"),
            "reason": reason,
            "status": "succeeded",
        }

    @classmethod
    def create_customer(
        cls,
        email: str,
        name: Optional[str] = None,
        phone: Optional[str] = None,
        metadata: Optional[Dict] = None,
    ) -> Dict:
        """Mock implementation for testing"""
        return {
            "success": True,
            "customer_id": "test_customer_123",
            "email": email,
            "name": name,
            "phone": phone,
            "metadata": metadata or {},
        }

    @classmethod
    def get_customer(cls, customer_id: str) -> Dict:
        """Mock implementation for testing"""
        return {
            "success": True,
            "customer_id": customer_id,
            "email": "test@example.com",
            "name": "Test Customer",
        }

    @classmethod
    def create_subscription(
        cls,
        customer_id: str,
        price_id: str,
        payment_method_id: Optional[str] = None,
        trial_days: Optional[int] = None,
        metadata: Optional[Dict] = None,
    ) -> Dict:
        """Mock implementation for testing"""
        return {
            "success": True,
            "subscription_id": "test_subscription_123",
            "customer_id": customer_id,
            "price_id": price_id,
            "payment_method_id": payment_method_id,
            "trial_days": trial_days,
            "status": "active",
            "metadata": metadata or {},
        }

    @classmethod
    def cancel_subscription(
        cls, subscription_id: str, immediately: bool = False
    ) -> Dict:
        """Mock implementation for testing"""
        return {
            "success": True,
            "subscription_id": subscription_id,
            "status": "canceled" if immediately else "cancel_at_period_end",
            "canceled_at": "2023-01-01T00:00:00Z" if immediately else None,
            "cancelled_immediately": immediately,
        }

    @classmethod
    def process_webhook(cls, payload: str, signature: str) -> Dict:
        """Mock implementation for testing"""
        return {
            "success": True,
            "event_type": "payment.succeeded",
            "processed": True,
            "payload_size": len(payload),
            "signature_valid": bool(signature),
        }

    @classmethod
    def get_platform_name(cls) -> str:
        """Return the test platform name"""
        return "TestPayment"

    @classmethod
    def has_ability(cls, ability: str) -> bool:
        """Check if provider has a specific ability."""
        return super().has_ability(ability)  # type: ignore[no-any-return]

    @classmethod
    def services(cls) -> List[str]:
        """Return list of services provided by this provider."""
        return ["payment", "billing", "subscription", "commerce"]

    @classmethod
    def get_abilities(cls) -> set:
        """Return abilities for testing."""
        return cls.abilities

    @classmethod
    def get_extension_info(cls) -> dict:
        """Return extension info for testing."""
        return {
            "extension": cls.extension.name,
            "provider": cls.name,
            "name": cls.extension.friendly_name,  # Add the expected 'name' key
            "version": cls.version,
            "platform": cls.get_platform_name(),  # Add platform
            "currency": cls.get_env_value("CONCRETEPAYMENT_CURRENCY", "USD"),
        }

    @classmethod
    def get_env_value(cls, key: str, default=None):
        """Get environment value for testing."""
        return cls.env.get(key, default)

    @classmethod
    def get_api_key(cls) -> str:
        """Get API key for testing."""
        return cls.env.get("CONCRETEPAYMENT_SECRET_KEY", "")

    @classmethod
    def get_secret_key(cls) -> str:
        """Get secret key for testing."""
        return cls.get_env_value("CONCRETEPAYMENT_SECRET_KEY", "")  # type: ignore[no-any-return]

    @classmethod
    def validate_config(cls) -> bool:
        """Validate configuration for testing."""
        return bool(cls.get_secret_key())

    @classmethod
    def get_webhook_secret(cls) -> str:
        """Get webhook secret for testing."""
        return cls.get_env_value("CONCRETEPAYMENT_WEBHOOK_SECRET", "")  # type: ignore[no-any-return]

    @classmethod
    def bond_instance(cls, instance):
        """Bond provider instance for rotation system."""
        return cls


@pytest.mark.payment
class TestAbstractPaymentProvider:
    """
    Test suite for AbstractPaymentProvider base class.
    Tests the common payment provider interface and static functionality.
    Fully compatible with the Provider Rotation System.
    """

    # Configure the test class
    provider_class = ConcretePaymentProvider
    extension_id = "payment"

    @classmethod
    def _check_stripe_configured(cls) -> bool:
        """Check if Stripe is configured in environment."""
        try:
            stripe_key = env("STRIPE_SECRET_KEY")
            return stripe_key and stripe_key != ""  # type: ignore[no-any-return]
        except Exception:
            return False

    @pytest.fixture
    def skip_if_no_stripe_config(self):
        """Skip test if Stripe is not configured."""
        if not self._check_stripe_configured():
            pytest.xfail("Stripe credentials not configured in environment")

    def test_abstract_class_cannot_be_instantiated(self):
        """Test that AbstractPaymentProvider cannot be instantiated directly."""
        with pytest.raises(TypeError):
            AbstractPaymentProvider()

    def test_static_provider_metadata(self):
        """Test static provider metadata for rotation system."""
        assert ConcretePaymentProvider.name == "test_payment"
        assert ConcretePaymentProvider.version == "1.0.0"
        assert ConcretePaymentProvider.description == "Test payment provider"

    def test_extension_linkage_for_rotation_system(self):
        """Test extension linkage required for rotation system."""
        assert hasattr(ConcretePaymentProvider, "extension")
        assert ConcretePaymentProvider.extension == EXT_Payment

    def test_dependencies_structure(self):
        """Test dependencies structure using unified Dependencies system."""
        assert hasattr(ConcretePaymentProvider, "dependencies")
        assert isinstance(ConcretePaymentProvider.dependencies, Dependencies)

        # Test dependency properties
        assert hasattr(ConcretePaymentProvider.dependencies, "pip")
        pip_deps = ConcretePaymentProvider.dependencies.pip
        assert len(pip_deps) == 1
        assert pip_deps[0].name == "stripe"

    def test_abilities_registration(self):
        """Test that payment abilities are available for rotation system."""
        abilities = ConcretePaymentProvider.get_abilities()
        expected_abilities = {
            "payment_processing",
            "subscription_management",
            "webhook_handling",
            "customer_management",
        }

        for ability in expected_abilities:
            assert ability in abilities

    def test_services_method(self):
        """Test the services method returns expected services."""
        services = ConcretePaymentProvider.services()
        expected_services = ["payment", "billing", "subscription", "commerce"]

        assert isinstance(services, list)
        for service in expected_services:
            assert service in services

    def test_get_extension_info(self):
        """Test get_extension_info method."""
        info = ConcretePaymentProvider.get_extension_info()

        assert isinstance(info, dict)
        assert info["name"] == EXT_Payment.friendly_name  # Use actual extension name
        assert info["extension"] == "payment"
        assert info["provider"] == "test_payment"
        assert info["platform"] == "TestPayment"

    def test_env_property_integration(self):
        """Test .env property integration with AbstractAPIProvider."""
        # Test that .env property exists and contains payment-specific vars
        env = ConcretePaymentProvider.env
        assert isinstance(env, dict)

        # Should include payment-specific variables
        expected_vars = [
            "CONCRETEPAYMENT_SECRET_KEY",
            "CONCRETEPAYMENT_WEBHOOK_SECRET",
            "CONCRETEPAYMENT_CURRENCY",
        ]

        for var in expected_vars:
            assert var in env

    def test_static_payment_methods(self):
        """Test static payment methods for rotation system."""
        # Test create_payment
        result = ConcretePaymentProvider.create_payment(
            amount=Decimal("50.00"),
            currency="USD",
            description="Test payment",
        )

        assert isinstance(result, dict)
        assert result["success"] is True
        assert result["payment_id"] == "test_payment_123"
        assert result["amount"] == Decimal("50.00")
        assert result["currency"] == "USD"
        assert result["status"] == "succeeded"

    def test_static_customer_methods(self):
        """Test static customer methods for rotation system."""
        # Test create_customer
        result = ConcretePaymentProvider.create_customer(
            email="test@example.com",
            name="Test Customer",
            phone="+1234567890",
        )

        assert isinstance(result, dict)
        assert result["success"] is True
        assert result["email"] == "test@example.com"
        assert result["name"] == "Test Customer"
        assert result["phone"] == "+1234567890"

        # Test get_customer
        result = ConcretePaymentProvider.get_customer("test_customer_123")

        assert isinstance(result, dict)
        assert result["success"] is True
        assert result["customer_id"] == "test_customer_123"
        assert "email" in result

    def test_static_subscription_methods(self):
        """Test static subscription methods for rotation system."""
        # Test create_subscription
        result = ConcretePaymentProvider.create_subscription(
            customer_id="test_customer_123",
            price_id="price_123",
            trial_days=30,
        )

        assert isinstance(result, dict)
        assert result["success"] is True
        assert result["customer_id"] == "test_customer_123"
        assert result["price_id"] == "price_123"
        assert result["trial_days"] == 30

        # Test cancel_subscription
        result = ConcretePaymentProvider.cancel_subscription(
            subscription_id="test_subscription_123",
            immediately=True,
        )

        assert isinstance(result, dict)
        assert result["success"] is True
        assert result["subscription_id"] == "test_subscription_123"
        assert result["status"] == "canceled"

    def test_static_webhook_processing(self):
        """Test static webhook processing for rotation system."""
        result = ConcretePaymentProvider.process_webhook(
            payload='{"event": "payment.succeeded"}',
            signature="test_signature",
        )

        assert isinstance(result, dict)
        assert result["success"] is True
        assert result["processed"] is True
        assert result["signature_valid"] is True

    def test_static_refund_methods(self):
        """Test static refund methods for rotation system."""
        result = ConcretePaymentProvider.refund_payment(
            payment_id="test_payment_123",
            amount=Decimal("25.00"),
            reason="Customer request",
        )

        assert isinstance(result, dict)
        assert result["success"] is True
        assert result["payment_id"] == "test_payment_123"
        assert result["amount"] == Decimal("25.00")
        assert result["reason"] == "Customer request"

    def test_get_platform_name_static_method(self):
        """Test get_platform_name static method."""
        platform_name = ConcretePaymentProvider.get_platform_name()
        assert platform_name == "TestPayment"

    def test_abstract_methods_defined(self):
        """Test that concrete provider implements required payment methods."""
        # The abstract base class doesn't define abstract methods - providers implement them directly
        required_methods = [
            "create_payment",
            "get_payment",
            "refund_payment",
            "create_customer",
            "get_customer",
            "create_subscription",
            "cancel_subscription",
            "process_webhook",
            "get_platform_name",
        ]

        for method_name in required_methods:
            assert hasattr(ConcretePaymentProvider, method_name)
            method = getattr(ConcretePaymentProvider, method_name)
            assert callable(method)

    def test_env_var_getters(self):
        """Test environment variable getter methods using real env dict."""
        # No-mock pillar: mutate the provider's real env dict and verify the
        # getters read from it. The getters are pure dict lookups; no need
        # to patch.
        original_env = dict(ConcretePaymentProvider.env)
        try:
            ConcretePaymentProvider.env["CONCRETEPAYMENT_SECRET_KEY"] = "sk_test_123"
            ConcretePaymentProvider.env["CONCRETEPAYMENT_WEBHOOK_SECRET"] = "whsec_test_123"

            secret_key = ConcretePaymentProvider.get_secret_key()
            assert secret_key == "sk_test_123"

            webhook_secret = ConcretePaymentProvider.get_webhook_secret()
            assert webhook_secret == "whsec_test_123"
        finally:
            ConcretePaymentProvider.env.clear()
            ConcretePaymentProvider.env.update(original_env)

    def test_validate_config_static_method(self):
        """Test config validation static method using the real env dict."""
        # validate_config returns True iff get_secret_key() returns a truthy
        # value. We populate the real env dict — no patching required.
        original_env = dict(ConcretePaymentProvider.env)
        try:
            ConcretePaymentProvider.env["CONCRETEPAYMENT_SECRET_KEY"] = "sk_test_123"
            assert ConcretePaymentProvider.validate_config() is True
        finally:
            ConcretePaymentProvider.env.clear()
            ConcretePaymentProvider.env.update(original_env)

    def test_services_consistency(self):
        """Test that services method returns consistent results."""
        services1 = ConcretePaymentProvider.services()
        services2 = ConcretePaymentProvider.services()

        assert services1 == services2
        assert isinstance(services1, list)

    def test_extension_info_includes_currency(self):
        """Test that extension info includes currency information."""
        # No-mock pillar: mutate the real env dict that get_env_value reads.
        original_env = dict(ConcretePaymentProvider.env)
        try:
            ConcretePaymentProvider.env["CONCRETEPAYMENT_CURRENCY"] = "EUR"
            info = ConcretePaymentProvider.get_extension_info()
            assert "currency" in info
            assert info["currency"] == "EUR"
        finally:
            ConcretePaymentProvider.env.clear()
            ConcretePaymentProvider.env.update(original_env)

    def test_static_implementation_completeness(self):
        """Test that static implementation provides all required methods."""
        # All abstract methods should be implemented as class methods
        required_methods = [
            "create_payment",
            "get_payment",
            "refund_payment",
            "create_customer",
            "get_customer",
            "create_subscription",
            "cancel_subscription",
            "process_webhook",
            "get_platform_name",
        ]

        for method_name in required_methods:
            assert hasattr(ConcretePaymentProvider, method_name)
            assert callable(getattr(ConcretePaymentProvider, method_name))

    def test_rotation_system_compatibility(self):
        """Test compatibility with Provider Rotation System."""
        # Test that provider has required attributes for rotation
        assert hasattr(ConcretePaymentProvider, "name")
        assert hasattr(ConcretePaymentProvider, "extension")
        assert hasattr(ConcretePaymentProvider, "dependencies")

        # Test that extension linkage works
        assert ConcretePaymentProvider.extension == EXT_Payment
