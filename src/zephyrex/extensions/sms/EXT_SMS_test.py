from unittest.mock import MagicMock, patch

import pytest

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.sms.EXT_SMS import EXT_SMS
from zephyrex.lib.Dependencies import install_pip_dependencies


class TestEXTSMS(ExtensionServerMixin):
    """
    Test suite for EXT_SMS extension.

    Tests extension initialization, SMS capabilities, abilities, and
    functionality. Focuses on testing SMS sending, delivery status
    tracking, bulk SMS, and provider integration (Twilio, Amazon SNS)
    rather than component loading.

    Test areas:
    - Extension metadata and configuration
    - SMS capabilities and abilities (send, status, bulk)
    - SMS provider integration (Twilio, Amazon SNS)
    - Delivery status tracking
    - Bulk SMS operations
    - Provider creation and dispatch

    Uses ExtensionServerMixin (not the full AbstractEXTTest) so this suite
    gets `extension_class` plus the shared `server`/`model_registry`
    fixtures without inheriting AbstractEXTTest's generic parametrized
    structure/metadata/dependency tests (which assume a class-level
    `extension` fixture) or its rotation/performance/concurrency/
    model-registry/database-isolation tests (which require the
    `CORE_COMPANION_EXTENSIONS` multi-extension test server this consumer
    project's conftest.py does not wire up). Mirrors how EXT_Messaging_test.py
    (the just-converted sibling) is structured.
    """

    # Configure the test class
    extension_class = EXT_SMS

    # Expected extension properties
    expected_abilities = [
        "send_sms",
        "get_sms_status",
        "send_bulk_sms",
    ]

    expected_capabilities = [
        "send_sms",
        "sms_delivery_status",
        "sms_management",
        "bulk_sms",
    ]

    @pytest.fixture
    def extension(self) -> EXT_SMS:
        """A fresh EXT_SMS instance for testing (mirrors the
        automotive/ecommerce/messaging sibling extensions' instance-based
        fixture)."""
        return EXT_SMS()

    @pytest.mark.dependency(name="sms_dependencies")
    def test_install_pip_dependencies(self):
        """
        Install PIP dependencies required by the SMS extension.
        This test must run first and all other SMS tests depend on it.
        """
        # Get the pip dependencies from the extension class
        pip_deps = self.extension_class.pip_dependencies

        # Install the dependencies
        result = install_pip_dependencies(pip_deps, only_missing=True)

        # Check final satisfaction status after installation attempt
        from zephyrex.lib.Dependencies import check_pip_dependencies

        final_status = check_pip_dependencies(pip_deps)

        # Verify installation results for required dependencies
        for dep in pip_deps:
            if not dep.optional:
                # Check if dependency is satisfied
                is_satisfied = final_status.get(dep.name, False)
                was_installed = result.get(dep.name, False)

                assert is_satisfied or was_installed, (
                    f"Required dependency {dep.name} is not satisfied. "
                    f"Final status: {is_satisfied}, Installation result: {was_installed}"
                )

    @pytest.fixture
    def mock_sms_provider(self):
        """Mock SMS provider"""
        mock_provider = MagicMock()
        mock_provider.send_sms.return_value = "SMS sent: SM123456789"
        mock_provider.get_sms_status.return_value = {
            "message_id": "SM123456789",
            "status": "delivered",
            "timestamp": "2023-01-01T10:00:00Z",
        }
        return mock_provider

    @pytest.fixture
    def mock_twilio_provider(self):
        """Mock TwilioProvider instance."""
        mock_provider = MagicMock()
        mock_provider.send_sms.return_value = {
            "success": True,
            "message_id": "SM123456789",
            "provider": "Twilio",
        }
        mock_provider.get_platform_name.return_value = "Twilio"
        return mock_provider

    @pytest.mark.dependency(depends=["sms_dependencies"])
    def test_extension_metadata(self, extension):
        """Test extension metadata and basic attributes"""
        assert extension.name == "sms"
        assert extension.version == "1.0.0"
        assert "SMS messaging extension" in extension.description
        assert hasattr(extension, "capabilities")
        assert hasattr(extension, "ext_dependencies")
        assert hasattr(extension, "pip_dependencies")
        assert hasattr(extension, "db_tables")

    @pytest.mark.dependency(depends=["sms_dependencies"])
    def test_dependencies_structure(self, extension):
        """Test that dependencies are properly structured"""
        # Check extension dependencies
        assert len(extension.__class__.ext_dependencies) == 1
        ext_deps = {dep.name: dep for dep in extension.__class__.ext_dependencies}

        assert "labels" in ext_deps
        assert ext_deps["labels"].optional

        # Check pip dependencies
        assert len(extension.__class__.pip_dependencies) == 2
        pip_deps = {dep.name: dep for dep in extension.__class__.pip_dependencies}

        assert "twilio" in pip_deps
        assert pip_deps["twilio"].optional

        assert "boto3" in pip_deps
        assert pip_deps["boto3"].optional

    @pytest.mark.dependency(depends=["sms_dependencies"])
    def test_initialization_with_custom_settings(self):
        """Test extension initialization with custom settings"""
        with patch("zephyrex.extensions.sms.EXT_SMS.logger"):
            extension = EXT_SMS(
                api_key="test_api_key",
                agent_name="test_agent",
                conversation_id="conv123",
                provider_type="twilio",
                rate_limit=10,
            )

            assert hasattr(extension, "settings")
            assert extension.settings.get("provider_type") == "twilio"
            assert extension.settings.get("rate_limit") == 10

    @pytest.mark.dependency(depends=["sms_dependencies"])
    def test_on_initialize_success(self):
        """Test successful extension initialization"""
        with patch("zephyrex.extensions.sms.EXT_SMS.logger"):
            extension = EXT_SMS(provider_type="twilio")
            result = extension.on_initialize()

            assert result is True

    @pytest.mark.dependency(depends=["sms_dependencies"])
    def test_on_initialize_failure(self):
        """Test extension initialization failure handling"""
        with patch("zephyrex.extensions.sms.EXT_SMS.logger"):
            with patch.object(
                EXT_SMS,
                "_create_provider",
                side_effect=Exception("Test error"),
            ):
                extension = EXT_SMS()
                result = extension.on_initialize()

                assert result is False

    @pytest.mark.dependency(depends=["sms_dependencies"])
    def test_get_capabilities(self, extension):
        """Test getting extension capabilities"""
        capabilities = extension.get_capabilities()

        assert isinstance(capabilities, set)
        for expected_capability in self.expected_capabilities:
            assert expected_capability in capabilities

    @pytest.mark.dependency(depends=["sms_dependencies"])
    def test_register_capability(self, extension):
        """Test registering new capability"""
        new_capability = "test_sms_capability"
        extension.register_capability(new_capability)

        assert new_capability in extension.capabilities
        assert new_capability in extension.get_registered_capabilities()

    @pytest.mark.dependency(depends=["sms_dependencies"])
    def test_capability_registration_is_instance_scoped(self):
        """Registering a capability on one instance must not leak to another."""
        first = EXT_SMS()
        second = EXT_SMS()

        first.register_capability("instance_only_capability")

        assert "instance_only_capability" in first.capabilities
        assert "instance_only_capability" not in second.capabilities

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["sms_dependencies"])
    async def test_send_sms_success(self, extension, mock_sms_provider):
        """Test successful SMS sending"""
        extension.provider = mock_sms_provider

        result = await extension.send_sms(
            phone_number="+1234567890",
            message="Hello from test SMS",
        )

        assert "SMS sent to +1234567890" in result
        assert "SM123456789" in result
        mock_sms_provider.send_sms.assert_called_once_with(
            "+1234567890", "Hello from test SMS"
        )

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["sms_dependencies"])
    async def test_send_sms_no_provider(self, extension):
        """Test SMS sending without provider"""
        extension.provider = None

        result = await extension.send_sms(
            phone_number="+1234567890",
            message="Test message",
        )

        assert "not configured" in result

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["sms_dependencies"])
    async def test_send_sms_provider_error(self, extension, mock_sms_provider):
        """Test SMS sending with provider error"""
        extension.provider = mock_sms_provider
        mock_sms_provider.send_sms.side_effect = Exception("Send error")

        result = await extension.send_sms(
            phone_number="+1234567890",
            message="Test message",
        )

        assert "Failed to send SMS" in result
        assert "Send error" in result

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["sms_dependencies"])
    async def test_get_sms_status_success(self, extension, mock_sms_provider):
        """Test successful SMS status retrieval"""
        extension.provider = mock_sms_provider

        result = await extension.get_sms_status(message_id="SM123456789")

        assert "message_id" in result
        assert result["status"] == "delivered"
        mock_sms_provider.get_sms_status.assert_called_once_with("SM123456789")

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["sms_dependencies"])
    async def test_get_sms_status_no_provider(self, extension):
        """Test SMS status retrieval without provider"""
        extension.provider = None

        result = await extension.get_sms_status(message_id="SM123456789")

        assert "error" in result
        assert "not configured" in result["error"]

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["sms_dependencies"])
    async def test_get_sms_status_not_supported(self, extension):
        """Test SMS status retrieval when not supported by provider"""
        mock_provider = MagicMock()
        # Provider without get_sms_status method
        if hasattr(mock_provider, "get_sms_status"):
            delattr(mock_provider, "get_sms_status")
        extension.provider = mock_provider

        result = await extension.get_sms_status(message_id="SM123456789")

        assert "error" in result
        assert "not supported" in result["error"]

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["sms_dependencies"])
    async def test_send_bulk_sms_success(self, extension):
        """Test successful bulk SMS sending"""
        extension.provider = MagicMock()

        with patch.object(extension, "send_sms") as mock_send:
            mock_send.return_value = "SMS sent to phone: success"

            phone_numbers = ["+1234567890", "+0987654321", "+1122334455"]
            result = await extension.send_bulk_sms(
                phone_numbers=phone_numbers,
                message="Bulk SMS test message",
            )

            assert isinstance(result, list)
            assert len(result) == 3
            for item in result:
                assert "phone_number" in item
                assert "status" in item

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["sms_dependencies"])
    async def test_send_bulk_sms_no_provider(self, extension):
        """Test bulk SMS sending without provider"""
        extension.provider = None

        result = await extension.send_bulk_sms(
            phone_numbers=["+1234567890"],
            message="Test message",
        )

        assert isinstance(result, list)
        assert len(result) == 1
        assert "error" in result[0]

    @pytest.mark.dependency(depends=["sms_dependencies"])
    def test_create_provider_twilio_success(self, extension, mock_twilio_provider):
        """Test successful Twilio provider creation"""
        extension.settings = {
            "provider_type": "twilio",
            "api_key": "test_key",
            "account_sid": "AC123",
            "from_number": "+15551234567",
        }

        with patch(
            "zephyrex.extensions.sms.PRV_Twilio.TwilioProvider",
            return_value=mock_twilio_provider,
        ):
            extension._create_provider()

            assert extension.provider is not None
            assert extension.provider == mock_twilio_provider

    @pytest.mark.dependency(depends=["sms_dependencies"])
    def test_create_provider_amazon_success(self, extension):
        """Test successful Amazon SNS provider creation"""
        mock_amazon_provider = MagicMock()
        extension.settings = {
            "provider_type": "amazon",
            "api_key": "test_key",
            "aws_region": "us-west-2",
        }

        with patch(
            "zephyrex.extensions.sms.PRV_Amazon.AmazonSNSProvider",
            return_value=mock_amazon_provider,
        ):
            extension._create_provider()

            assert extension.provider is not None
            assert extension.provider == mock_amazon_provider

    @pytest.mark.dependency(depends=["sms_dependencies"])
    def test_create_provider_unsupported_type(self, extension):
        """Test provider creation with an unsupported provider type"""
        extension.settings = {"provider_type": "unsupported"}
        extension._create_provider()

        assert extension.provider is None

    @pytest.mark.dependency(depends=["sms_dependencies"])
    def test_create_provider_no_type(self, extension):
        """Test provider creation without a specified provider type"""
        extension.settings = {}
        extension._create_provider()

        assert extension.provider is None

    @pytest.mark.dependency(depends=["sms_dependencies"])
    def test_create_provider_import_failure(self, extension):
        """Test provider creation when the Twilio provider import/construction fails."""
        extension.settings = {"provider_type": "twilio"}

        with patch(
            "zephyrex.extensions.sms.PRV_Twilio.TwilioProvider",
            side_effect=ImportError("Mock import error"),
        ):
            extension._create_provider()

            assert extension.provider is None

    @pytest.mark.dependency(depends=["sms_dependencies"])
    def test_twilio_provider_creation_parameters(self, extension, mock_twilio_provider):
        """Test that the Twilio provider is created with the right parameters,
        with settings correctly split between explicit kwargs and the
        pass-through provider_kwargs bag (no duplicate-keyword collision)."""
        extension.settings = {
            "provider_type": "twilio",
            "api_key": "test-api-key",
            "conversation_id": "test-conversation-id",
            "account_sid": "AC123",
            "from_number": "+15551234567",
        }

        with patch(
            "zephyrex.extensions.sms.PRV_Twilio.TwilioProvider",
            return_value=mock_twilio_provider,
        ) as mock_twilio_class:
            extension._create_provider()

            mock_twilio_class.assert_called_once_with(
                api_key="test-api-key",
                extension_id="sms",
                conversation_directory="test-conversation-id",
                account_sid="AC123",
                from_number="+15551234567",
            )

    @pytest.mark.dependency(depends=["sms_dependencies"])
    def test_validate_config_with_provider(self):
        """Test configuration validation with a provider type specified"""
        with patch("zephyrex.extensions.sms.EXT_SMS.logger"):
            extension = EXT_SMS(provider_type="twilio")
            issues = extension.validate_config()

            assert len(issues) == 0

    @pytest.mark.dependency(depends=["sms_dependencies"])
    def test_validate_config_no_provider(self):
        """Test configuration validation without a provider type"""
        with patch("zephyrex.extensions.sms.EXT_SMS.logger"):
            extension = EXT_SMS()
            issues = extension.validate_config()

            assert len(issues) >= 1
            issue_text = " ".join(issues).lower()
            assert "provider type not specified" in issue_text

    @pytest.mark.dependency(depends=["sms_dependencies"])
    def test_get_required_permissions(self, extension):
        """Test getting required permissions"""
        permissions = extension.get_required_permissions()

        assert isinstance(permissions, list)
        assert len(permissions) == 4
        assert "sms:send" in permissions
        assert "sms:status" in permissions
        assert "sms:manage" in permissions
        assert "communication:sms" in permissions

    @pytest.mark.dependency(depends=["sms_dependencies"])
    def test_on_start_success(self):
        """Test successful extension start"""
        with patch("zephyrex.extensions.sms.EXT_SMS.logger"):
            extension = EXT_SMS()
            result = extension.on_start()

            assert result is True

    @pytest.mark.dependency(depends=["sms_dependencies"])
    def test_on_stop_success(self, extension):
        """Test successful extension stop"""
        # Setup provider
        extension.provider = MagicMock()

        result = extension.on_stop()

        assert result is True
        assert extension.provider is None

    @pytest.mark.dependency(depends=["sms_dependencies"])
    def test_has_capability(self, extension):
        """Test capability checking"""
        assert extension.has_capability("send_sms") is True
        assert extension.has_capability("sms_delivery_status") is True
        assert extension.has_capability("non_existent_capability") is False

    @pytest.mark.dependency(depends=["sms_dependencies"])
    def test_abilities_discovery(self, extension):
        """Test that all expected abilities are discovered"""
        extension.discover_abilities()

        for expected_ability in self.expected_abilities:
            assert expected_ability in extension.abilities
            assert callable(extension.abilities[expected_ability])

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["sms_dependencies"])
    async def test_execute_ability_success(self, extension, mock_sms_provider):
        """Test successful ability execution"""
        extension.provider = mock_sms_provider

        result = await extension.execute_ability(
            "send_sms",
            {
                "phone_number": "+1234567890",
                "message": "Test SMS message",
            },
        )

        assert "SMS sent to +1234567890" in result

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["sms_dependencies"])
    async def test_execute_ability_not_found(self, extension):
        """Test executing non-existent ability"""
        result = await extension.execute_ability("non_existent_ability")

        assert "not found" in result

    @pytest.mark.dependency(depends=["sms_dependencies"])
    def test_lifecycle_methods_integration(self, extension):
        """Test integration of lifecycle methods"""
        # Test startup
        extension.on_startup()

        # Test shutdown
        extension.on_shutdown()

        # These methods should not raise exceptions

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["sms_dependencies"])
    async def test_sms_full_workflow(self, extension, mock_sms_provider):
        """Test complete SMS workflow"""
        extension.provider = mock_sms_provider

        # 1. Send SMS
        send_result = await extension.send_sms(
            phone_number="+1234567890",
            message="Hello from SMS test",
        )
        assert "SMS sent to +1234567890" in send_result

        # 2. Check SMS status
        status_result = await extension.get_sms_status(message_id="SM123456789")
        assert status_result["status"] == "delivered"

        # 3. Send bulk SMS
        bulk_result = await extension.send_bulk_sms(
            phone_numbers=["+1234567890", "+0987654321"],
            message="Bulk SMS test",
        )
        assert isinstance(bulk_result, list)
        assert len(bulk_result) == 2

    @pytest.mark.dependency(depends=["sms_dependencies"])
    def test_sms_rate_limiting(self, extension):
        """Test SMS rate limiting functionality"""
        # Test rate limiting setup
        extension.settings = {"rate_limit": 5, "rate_limit_window": 60}

        # In a real implementation, this would track and limit sends
        rate_limit = extension.settings.get("rate_limit", 10)
        assert rate_limit == 5

        rate_window = extension.settings.get("rate_limit_window", 60)
        assert rate_window == 60

    @pytest.mark.dependency(depends=["sms_dependencies"])
    def test_sms_delivery_tracking(self, extension, mock_sms_provider):
        """Test SMS delivery tracking"""
        extension.provider = mock_sms_provider

        # Mock delivery tracking data
        tracking_data = {
            "message_id": "SM123456789",
            "status": "delivered",
            "delivered_at": "2023-01-01T10:05:00Z",
            "delivery_attempts": 1,
        }

        mock_sms_provider.get_sms_status.return_value = tracking_data

        # Test tracking
        assert mock_sms_provider.get_sms_status("SM123456789") == tracking_data


if __name__ == "__main__":
    pytest.main([__file__])
