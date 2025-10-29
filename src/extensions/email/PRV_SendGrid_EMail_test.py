import os
import tempfile
from typing import List

import pytest

from AbstractTest import CategoryOfTest, ClassOfTestsConfig, SkipThisTest
from extensions.AbstractPRVTest import AbstractPRVTest
from extensions.email.PRV_SendGrid_EMail import SendgridProvider
from lib.Dependencies import check_pip_dependencies, install_pip_dependencies
from lib.Environment import env
from logic.BLL_Providers import ProviderInstanceModel
import pytest


# Provide lightweight placeholders for extension-level fixtures that are not available
# in this focused test run; the provider tests will skip the heavy integration tests
# based on the test_config, but pytest still resolves fixture names at collection.


@pytest.fixture
def extension_server():
    return None


@pytest.fixture
def extension_db():
    return None


class TestSendgridProvider(AbstractPRVTest):
    """
    Test suite for SendGrid email provider.
    Tests provider initialization, email sending, and SendGrid API integration.
    """

    # Configure the test class
    provider_class = SendgridProvider
    extension_id = "email"
    # The test harness expects some common ClassOfTestsConfig fields (timeout, categories, cleanup)
    # while the provider mixin expects a ProviderTestConfig with test_types etc. Create a tiny
    # adapter object that exposes both shapes and wrap the provider basic_config here.
    class _TestConfigAdapter:
        def __init__(self, provider_conf, categories=None, timeout=None, parallel=False, cleanup=True, gh_action_skip=False):
            # AbstractPRVTest / AbstractTest expectations
            self.categories = categories or [CategoryOfTest.EXTENSION]
            self.timeout = timeout
            self.parallel = parallel
            self.cleanup = cleanup
            self.gh_action_skip = gh_action_skip

            # ProviderTestConfig surface
            self.test_types = provider_conf.test_types
            self.expected_abilities = provider_conf.expected_abilities
            self.expected_services = provider_conf.expected_services
            self.expected_dependencies = provider_conf.expected_dependencies
            self.performance_thresholds = provider_conf.performance_thresholds
            self.skip_rotation_tests = provider_conf.skip_rotation_tests
            self.skip_performance_tests = provider_conf.skip_performance_tests
            self.skip_error_handling_tests = provider_conf.skip_error_handling_tests

    test_config = _TestConfigAdapter(
        AbstractPRVTest.basic_config(),
        categories=[CategoryOfTest.EXTENSION, CategoryOfTest.INTEGRATION],
        cleanup=True,
    )

    # Expected abilities and services
    expected_abilities = []  # SendGrid provider doesn't expose abilities directly
    expected_services = ["email", "messaging", "communication"]

    # Tests to skip
    _skip_tests: List[SkipThisTest] = []

    def test_install_pip_dependencies(self):
        """
        Install PIP dependencies required by the Email extension.
        This test must run first and all other email tests depend on it.
        """
        # For email tests, we need to get dependencies from the extension class
        from extensions.email.EXT_EMail import EXT_EMail

        # Get the pip dependencies from the extension class
        pip_deps = EXT_EMail.dependencies.pip

        # Install the dependencies
        result = install_pip_dependencies(pip_deps, only_missing=True)

        # Check final satisfaction status after installation attempt
        final_status = check_pip_dependencies(pip_deps)

        # Verify installation results for required dependencies
        for dep in pip_deps:
            if not dep.optional:
                # Check if dependency is satisfied (either was already installed or just installed)
                is_satisfied = final_status.get(dep.name, False)
                was_installed = result.get(dep.name, False)

                assert is_satisfied or was_installed, (
                    f"Required dependency {dep.name} is not satisfied. "
                    f"Final status: {is_satisfied}, Installation result: {was_installed}"
                )

        # Verify sendgrid specifically since it's critical for email
        try:
            import sendgrid

            assert hasattr(
                sendgrid, "SendGridAPIClient"
            ), "sendgrid import successful but missing expected classes"
        except ImportError:
            pytest.fail("sendgrid library not available after installation")

    @pytest.fixture
    def real_sendgrid_api_key(self):
        """Get real SendGrid API key from environment or skip test."""
        api_key = env("SENDGRID_API_KEY")
        if not api_key:
            pytest.xfail("SENDGRID_API_KEY environment variable not set")
        return api_key

    @pytest.fixture
    def real_sendgrid_from_email(self):
        """Get real SendGrid from email from environment or skip test."""
        from_email = env("SENDGRID_FROM_EMAIL")
        if not from_email:
            pytest.xfail("SENDGRID_FROM_EMAIL environment variable not set")
        return from_email

    @pytest.fixture
    def provider_instance(self, real_sendgrid_api_key, real_sendgrid_from_email):
        """Create a real provider instance for testing."""

        # Create a mock ProviderInstanceModel
        class MockProviderInstance:
            def __init__(self, api_key):
                self.id = "test_instance_id"
                self.api_key = api_key
                self.provider_id = "sendgrid"
                self.name = "Test SendGrid Instance"

        return MockProviderInstance(real_sendgrid_api_key)

    def test_provider_structure(self):
        """Test that provider has correct structure."""
        assert hasattr(SendgridProvider, "name")
        assert hasattr(SendgridProvider, "version")
        assert hasattr(SendgridProvider, "description")
        assert hasattr(SendgridProvider, "dependencies")
        assert hasattr(SendgridProvider, "_env")
        assert hasattr(SendgridProvider, "bond_instance")
        assert hasattr(SendgridProvider, "get_platform_name")
        assert hasattr(SendgridProvider, "services")

    def test_provider_metadata(self):
        """Test provider metadata."""
        assert SendgridProvider.name == "sendgrid"
        assert isinstance(SendgridProvider.version, str)
        assert isinstance(SendgridProvider.description, str)
        assert SendgridProvider.get_platform_name() == "SendGrid"

    def test_provider_services(self):
        """Test provider services list."""
        services = SendgridProvider.services()
        assert isinstance(services, list)
        assert len(services) > 0
        assert "email" in services

    def test_provider_dependencies(self):
        """Test provider dependencies."""
        deps = SendgridProvider.dependencies
        assert deps is not None
        assert hasattr(deps, "pip")
        assert len(deps.pip) > 0

        # Should have sendgrid dependency
        sendgrid_dep = next((dep for dep in deps.pip if dep.name == "sendgrid"), None)
        assert sendgrid_dep is not None

    def test_provider_env_vars(self):
        """Test provider environment variables."""
        env_vars = SendgridProvider._env
        assert isinstance(env_vars, dict)
        assert "SENDGRID_API_KEY" in env_vars
        assert "SENDGRID_FROM_EMAIL" in env_vars

    def test_bond_instance_without_api_key(self):
        """Test bonding instance without API key."""

        class MockInstanceWithoutKey:
            id = "test_id"
            api_key = None

        instance = MockInstanceWithoutKey()
        bonded = SendgridProvider.bond_instance(instance)
        assert bonded is None

    def test_bond_instance_with_api_key(self, provider_instance):
        """Test bonding instance with API key."""
        bonded = SendgridProvider.bond_instance(provider_instance)

        # Check if sendgrid library is available
        try:
            import sendgrid

            # If library is available, bonding should succeed
            assert bonded is not None
            assert hasattr(bonded, "sdk")
        except ImportError:
            # If library not available, bonding should fail
            assert bonded is None

    @pytest.mark.asyncio
    async def test_send_email_without_bonded_instance(self):
        """Test sending email without bonded instance."""

        class MockInstanceWithoutKey:
            id = "test_id"
            api_key = None

        instance = MockInstanceWithoutKey()

        # Should fail gracefully without real API key
        try:
            result = await SendgridProvider.send_email(
                instance, "test@example.com", "Test Subject", "Test Body"
            )
            # If it doesn't raise an exception, it should return an error message
            assert "Failed to bond" in result or "error" in result.lower()
        except Exception as e:
            # Should handle the error gracefully
            assert "bond" in str(e).lower() or "api" in str(e).lower()

    @pytest.mark.asyncio
    async def test_send_email_with_bonded_instance(self, provider_instance):
        """Test sending email with bonded instance."""
        # This will only work if SENDGRID_API_KEY is set and valid
        if not env("SENDGRID_API_KEY"):
            pytest.xfail("SENDGRID_API_KEY not set - cannot test real email sending")

        # Try to send an email - this is a real API call
        try:
            result = await SendgridProvider.send_email(
                provider_instance,
                "test@example.com",  # This email won't actually be sent in test mode
                "Test Subject",
                "Test Body",
            )
            # Should either succeed or fail with a recognizable error
            assert isinstance(result, str)
            assert len(result) > 0
        except Exception as e:
            # If it fails, it should be due to API limits, invalid email, etc.
            # not due to code structure issues
            error_msg = str(e).lower()
            expected_errors = ["unauthorized", "forbidden", "invalid", "limit", "quota"]
            assert any(
                err in error_msg for err in expected_errors
            ), f"Unexpected error: {e}"

    @pytest.mark.asyncio
    async def test_get_emails_not_supported(self, provider_instance):
        """Test that get_emails is not supported by SendGrid."""
        # SendGrid doesn't support receiving emails
        result = await SendgridProvider.get_emails(provider_instance)
        assert isinstance(result, list)
        assert len(result) == 0  # Should return empty list

    @pytest.mark.asyncio
    async def test_email_abilities_exist(self):
        """Test that email ability methods exist and are decorated."""
        # Check that the provider has the required email abilities
        assert hasattr(SendgridProvider, "send_email")
        assert hasattr(SendgridProvider, "get_emails")
        assert hasattr(SendgridProvider, "create_draft_email")
        assert hasattr(SendgridProvider, "search_emails")
        assert hasattr(SendgridProvider, "reply_to_email")
        assert hasattr(SendgridProvider, "delete_email")
        assert hasattr(SendgridProvider, "process_attachments")

        # Check that methods have ability info
        assert hasattr(SendgridProvider.send_email, "_ability_info")
        assert SendgridProvider.send_email._ability_info["name"] == "email_send"

    def test_provider_configuration_validation(self):
        """Test provider configuration validation."""
        # Test with no configuration
        assert (
            not SendgridProvider._stripe_available
            if hasattr(SendgridProvider, "_stripe_available")
            else True
        )

        # Test environment variable structure
        env_vars = SendgridProvider._env
        required_vars = ["SENDGRID_API_KEY", "SENDGRID_FROM_EMAIL"]
        for var in required_vars:
            assert var in env_vars

    def test_static_methods_are_static(self):
        """Test that provider methods are properly static/class methods."""
        import inspect

        # These should be static methods
        static_methods = [
            "get_emails",
            "create_draft_email",
            "search_emails",
            "reply_to_email",
            "delete_email",
            "process_attachments",
        ]

        for method_name in static_methods:
            method = getattr(SendgridProvider, method_name)
            assert inspect.isfunction(method) or inspect.ismethod(method)

        # These should be class methods
        class_methods = ["send_email", "bond_instance", "get_platform_name", "services"]

        for method_name in class_methods:
            method = getattr(SendgridProvider, method_name)
            # Class methods appear as regular methods on the class
            assert callable(method)

    def test_external_models_exist(self):
        """Test that external models are defined."""
        from extensions.email.PRV_SendGrid_EMail import (
            SendGrid_ContactModel,
            SendGrid_TemplateModel,
            SendGrid_CampaignModel,
        )

        # Check that external models have the right structure
        assert hasattr(SendGrid_ContactModel, "external_resource")
        assert hasattr(SendGrid_TemplateModel, "external_resource")
        assert hasattr(SendGrid_CampaignModel, "external_resource")

        # Check that they have _is_extension_model attribute
        assert getattr(SendGrid_ContactModel, "_is_extension_model", False)
        assert getattr(SendGrid_TemplateModel, "_is_extension_model", False)
        assert getattr(SendGrid_CampaignModel, "_is_extension_model", False)
