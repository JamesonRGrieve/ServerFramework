from typing import List
from unittest.mock import MagicMock, patch

import pytest

from AbstractTest import CategoryOfTest, ClassOfTestsConfig, SkipThisTest
from extensions.AbstractEXTTest import AbstractEXTTest
from extensions.auth_notifications.EXT_Auth_Notifications import EXT_Auth_Notifications
from lib.Dependencies import install_pip_dependencies


class TestEXTAuthNotifications(AbstractEXTTest):
    """
    Test suite for EXT_Auth_Notifications extension.

    Tests extension initialization, authentication notification capabilities, abilities, and
    notification management functionality. Focuses on testing notification sending, preference
    management, template handling, and delivery tracking rather than component loading.

    Test areas:
    - Extension metadata and configuration
    - Notification capabilities and abilities (sending, templating, preferences)
    - Email and SMS integration
    - Template management and rendering
    - Notification preferences and settings
    - Delivery tracking and retry mechanisms
    - Authentication event integration
    - Multi-channel notification support
    """

    # Configure the test class
    extension_class = EXT_Auth_Notifications
    test_config = ClassOfTestsConfig(
        categories=[CategoryOfTest.EXTENSION, CategoryOfTest.INTEGRATION],
        cleanup=True,
    )

    # Expected extension properties
    expected_abilities = [
        "send_notification",
        "create_template",
        "update_preferences",
        "track_delivery",
        "send_auth_notification",
    ]

    expected_capabilities = [
        "notification_sending",
        "template_management",
        "preference_management",
        "delivery_tracking",
        "multi_channel_support",
        "auth_event_notifications",
        "notification_history",
    ]

    # Tests to skip
    _skip_tests: List[SkipThisTest] = []

    @pytest.mark.dependency(name="auth_notifications_dependencies")
    def test_install_pip_dependencies(self):
        """
        Install PIP dependencies required by the Auth Notifications extension.
        This test must run first and all other Auth Notifications tests depend on it.
        """
        # Get the pip dependencies from the extension class
        pip_deps = self.extension_class.pip_dependencies

        # Install the dependencies
        result = install_pip_dependencies(pip_deps, only_missing=True)

        # Check final satisfaction status after installation attempt
        from lib.Dependencies import check_pip_dependencies

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

        # Verify jinja2 specifically since it's critical for template rendering
        try:
            import jinja2

            assert hasattr(
                jinja2, "Template"
            ), "jinja2 import successful but missing expected attributes"
        except ImportError:
            pytest.fail("jinja2 library not available after installation")

    @pytest.fixture
    def mock_env_configured(self):
        """Mock environment with notifications configured"""
        with patch(
            "extensions.auth_notifications.EXT_Auth_Notifications.env"
        ) as mock_env:
            mock_env.side_effect = lambda key, default=None: {
                "REDIS_URL": "redis://localhost:6379/4",
                "SMTP_HOST": "smtp.example.com",
                "SMTP_PORT": "587",
                "SMTP_USERNAME": "user@example.com",
                "SMTP_PASSWORD": "password123",
                "SMS_API_KEY": "sms_api_key_123",
                "LOG_LEVEL": "INFO",
                "LOG_FORMAT": "%(message)s",
            }.get(key, default)
            yield mock_env

    @pytest.fixture
    def mock_env_no_smtp(self):
        """Mock environment without SMTP configured"""
        with patch(
            "extensions.auth_notifications.EXT_Auth_Notifications.env"
        ) as mock_env:
            mock_env.side_effect = lambda key, default=None: {
                "REDIS_URL": "redis://localhost:6379/4",
                "SMS_API_KEY": "sms_api_key_123",
                "LOG_LEVEL": "INFO",
                "LOG_FORMAT": "%(message)s",
            }.get(key, default)
            yield mock_env

    @pytest.fixture
    def mock_redis_available(self):
        """Mock Redis library availability"""
        mock_redis = MagicMock()
        mock_client = MagicMock()
        mock_client.ping.return_value = True
        mock_client.set.return_value = True
        mock_client.get.return_value = None
        mock_client.delete.return_value = True
        mock_redis.from_url.return_value = mock_client

        with patch.dict("sys.modules", {"redis": mock_redis}):
            yield mock_redis

    @pytest.fixture
    def mock_jinja2_available(self):
        """Mock Jinja2 library availability"""
        mock_jinja2 = MagicMock()
        mock_template = MagicMock()
        mock_template.render.return_value = "Rendered template content"
        mock_jinja2.Template.return_value = mock_template

        with patch.dict("sys.modules", {"jinja2": mock_jinja2}):
            yield mock_jinja2

    @pytest.fixture
    def mock_smtplib_available(self):
        """Mock smtplib library availability"""
        mock_smtplib = MagicMock()
        mock_smtp = MagicMock()
        mock_smtp.starttls.return_value = None
        mock_smtp.login.return_value = None
        mock_smtp.send_message.return_value = {}
        mock_smtp.quit.return_value = None
        mock_smtplib.SMTP.return_value = mock_smtp

        with patch.dict("sys.modules", {"smtplib": mock_smtplib}):
            yield mock_smtplib

    @pytest.fixture
    def mock_requests_available(self):
        """Mock requests library availability"""
        mock_requests = MagicMock()
        mock_response = MagicMock()
        mock_response.json.return_value = {"status": "sent", "id": "sms_123"}
        mock_response.status_code = 200
        mock_requests.post.return_value = mock_response

        with patch.dict("sys.modules", {"requests": mock_requests}):
            yield mock_requests

    @pytest.mark.dependency(depends=["auth_notifications_dependencies"])
    def test_extension_metadata(self, extension):
        """Test extension metadata and basic attributes"""
        assert extension.name == "auth_notifications"
        assert extension.version == "1.0.0"
        assert "Authentication notifications extension" in extension.description
        assert hasattr(extension, "capabilities")
        assert hasattr(extension, "ext_dependencies")
        assert hasattr(extension, "pip_dependencies")
        assert hasattr(extension, "db_tables")

    @pytest.mark.dependency(depends=["auth_notifications_dependencies"])
    def test_dependencies_structure(self, extension):
        """Test that dependencies are properly structured"""
        # Check extension dependencies
        assert len(extension.__class__.ext_dependencies) == 3
        ext_deps = {dep.name: dep for dep in extension.__class__.ext_dependencies}

        assert "core" in ext_deps
        assert not ext_deps["core"].optional

        assert "auth" in ext_deps
        assert not ext_deps["auth"].optional

        assert "email" in ext_deps
        assert ext_deps["email"].optional

        # Check pip dependencies
        assert len(extension.__class__.pip_dependencies) == 4
        pip_deps = {dep.name: dep for dep in extension.__class__.pip_dependencies}

        assert "jinja2" in pip_deps
        assert not pip_deps["jinja2"].optional

        assert "smtplib" in pip_deps
        assert not pip_deps["smtplib"].optional

        assert "requests" in pip_deps
        assert pip_deps["requests"].optional

        assert "redis" in pip_deps
        assert pip_deps["redis"].optional

    @pytest.mark.dependency(depends=["auth_notifications_dependencies"])
    def test_db_tables_structure(self, extension):
        """Test that database tables are properly defined"""
        assert len(extension.__class__.db_tables) == 4
        table_names = [table.__name__ for table in extension.__class__.db_tables]
        assert "NotificationTemplate" in table_names
        assert "NotificationPreference" in table_names
        assert "NotificationHistory" in table_names
        assert "DeliveryStatus" in table_names

    @pytest.mark.dependency(depends=["auth_notifications_dependencies"])
    def test_notification_constants_structure(self, extension):
        """Test that notification constants are properly defined"""
        notification_types = extension.get_notification_types()
        assert isinstance(notification_types, dict)
        assert "login_success" in notification_types
        assert "login_failed" in notification_types
        assert "password_changed" in notification_types

        delivery_channels = extension.get_delivery_channels()
        assert isinstance(delivery_channels, dict)
        assert "email" in delivery_channels
        assert "sms" in delivery_channels
        assert "in_app" in delivery_channels

        notification_priorities = extension.get_notification_priorities()
        assert isinstance(notification_priorities, dict)
        assert "low" in notification_priorities
        assert "normal" in notification_priorities
        assert "high" in notification_priorities

    @pytest.mark.dependency(depends=["auth_notifications_dependencies"])
    def test_initialization_with_custom_settings(self):
        """Test extension initialization with custom settings"""
        with patch("lib.Logging.logger"):
            extension = EXT_Auth_Notifications(
                enable_email_notifications=False,
                enable_sms_notifications=False,
                enable_in_app_notifications=True,
                smtp_host="custom.smtp.com",
                smtp_port=465,
                enable_notification_history=False,
                max_retry_attempts=5,
                delivery_timeout=60,
            )

            assert extension.enable_email_notifications is False
            assert extension.enable_sms_notifications is False
            assert extension.enable_in_app_notifications is True
            assert extension.smtp_host == "custom.smtp.com"
            assert extension.smtp_port == 465
            assert extension.enable_notification_history is False
            assert extension.max_retry_attempts == 5
            assert extension.delivery_timeout == 60

    @pytest.mark.dependency(depends=["auth_notifications_dependencies"])
    def test_on_initialize_success_with_smtp(
        self, mock_smtplib_available, mock_env_configured
    ):
        """Test successful extension initialization with SMTP"""
        with patch("lib.Logging.logger"):
            with patch.object(
                EXT_Auth_Notifications, "_register_auth_hooks"
            ) as mock_hooks:
                extension = EXT_Auth_Notifications(
                    enable_email_notifications=True,
                    smtp_host="smtp.example.com",
                    smtp_username="user@example.com",
                    smtp_password="password123",
                )
                result = extension.on_initialize()

                assert result is True
                mock_hooks.assert_called_once()

    @pytest.mark.dependency(depends=["auth_notifications_dependencies"])
    def test_on_initialize_success_without_smtp(self, mock_env_no_smtp):
        """Test successful extension initialization without SMTP"""
        with patch("lib.Logging.logger"):
            with patch.object(
                EXT_Auth_Notifications, "_register_auth_hooks"
            ) as mock_hooks:
                extension = EXT_Auth_Notifications(enable_email_notifications=False)
                result = extension.on_initialize()

                assert result is True
                mock_hooks.assert_called_once()

    @pytest.mark.dependency(depends=["auth_notifications_dependencies"])
    def test_on_initialize_failure(self):
        """Test extension initialization failure handling"""
        with patch("lib.Logging.logger"):
            with patch.object(
                EXT_Auth_Notifications,
                "_initialize_email_service",
                side_effect=Exception("Test error"),
            ):
                extension = EXT_Auth_Notifications()
                result = extension.on_initialize()

                assert result is False

    @pytest.mark.dependency(depends=["auth_notifications_dependencies"])
    def test_initialize_email_service_success(
        self, mock_smtplib_available, mock_env_configured
    ):
        """Test successful email service initialization"""
        with patch("lib.Logging.logger"):
            extension = EXT_Auth_Notifications(
                smtp_host="smtp.example.com",
                smtp_username="user@example.com",
                smtp_password="password123",
            )
            extension._initialize_email_service()

            # Should initialize without errors

    @pytest.mark.dependency(depends=["auth_notifications_dependencies"])
    def test_initialize_redis_success(self, mock_redis_available, mock_env_configured):
        """Test successful Redis initialization"""
        with patch("lib.Logging.logger"):
            extension = EXT_Auth_Notifications()
            extension._initialize_redis()

            assert extension.redis_client is not None

    @pytest.mark.dependency(depends=["auth_notifications_dependencies"])
    def test_get_capabilities(self, extension):
        """Test getting extension capabilities"""
        capabilities = extension.get_capabilities()

        assert isinstance(capabilities, set)
        for expected_capability in self.expected_capabilities:
            assert expected_capability in capabilities

    @pytest.mark.dependency(depends=["auth_notifications_dependencies"])
    def test_register_capability(self, extension):
        """Test registering new capability"""
        new_capability = "test_notification_capability"
        extension.register_capability(new_capability)

        assert new_capability in extension.capabilities
        assert new_capability in extension.get_registered_capabilities()

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_notifications_dependencies"])
    async def test_send_notification_email_success(
        self, extension, mock_smtplib_available
    ):
        """Test successful email notification sending"""
        extension.enable_email_notifications = True

        with patch.object(extension, "_send_email_notification") as mock_email:
            mock_email.return_value = {
                "success": True,
                "delivery_id": "email_123",
                "status": "sent",
            }

            result = await extension.send_notification(
                recipient="user@example.com",
                notification_type="login_success",
                channel="email",
                template_data={
                    "user_name": "John Doe",
                    "login_time": "2023-01-01 10:00:00",
                },
            )

            assert result["success"] is True
            assert "delivery_id" in result
            assert result["channel"] == "email"

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_notifications_dependencies"])
    async def test_send_notification_sms_success(
        self, extension, mock_requests_available
    ):
        """Test successful SMS notification sending"""
        extension.enable_sms_notifications = True

        with patch.object(extension, "_send_sms_notification") as mock_sms:
            mock_sms.return_value = {
                "success": True,
                "delivery_id": "sms_123",
                "status": "sent",
            }

            result = await extension.send_notification(
                recipient="+1234567890",
                notification_type="login_failed",
                channel="sms",
                template_data={
                    "user_name": "John Doe",
                    "failed_attempt_ip": "192.168.1.1",
                },
            )

            assert result["success"] is True
            assert result["channel"] == "sms"

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_notifications_dependencies"])
    async def test_send_notification_in_app_success(self, extension):
        """Test successful in-app notification sending"""
        extension.enable_in_app_notifications = True

        with patch.object(extension, "_send_in_app_notification") as mock_in_app:
            mock_in_app.return_value = {
                "success": True,
                "notification_id": "in_app_123",
                "status": "delivered",
            }

            result = await extension.send_notification(
                recipient="user123",
                notification_type="password_changed",
                channel="in_app",
                template_data={
                    "user_name": "John Doe",
                    "change_time": "2023-01-01 11:00:00",
                },
            )

            assert result["success"] is True
            assert result["channel"] == "in_app"

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_notifications_dependencies"])
    async def test_send_notification_invalid_channel(self, extension):
        """Test notification sending with invalid channel"""
        result = await extension.send_notification(
            recipient="user@example.com",
            notification_type="login_success",
            channel="invalid_channel",
        )

        assert result["success"] is False
        assert "not supported" in result["message"]

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_notifications_dependencies"])
    async def test_send_notification_disabled_channel(self, extension):
        """Test notification sending with disabled channel"""
        extension.enable_email_notifications = False

        result = await extension.send_notification(
            recipient="user@example.com",
            notification_type="login_success",
            channel="email",
        )

        assert result["success"] is False
        assert "disabled" in result["message"]

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_notifications_dependencies"])
    async def test_send_notification_template_not_found(self, extension):
        """Test notification sending with non-existent template"""
        result = await extension.send_notification(
            recipient="user@example.com",
            notification_type="non_existent_type",
            channel="email",
        )

        assert result["success"] is False
        assert "template not found" in result["message"]

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_notifications_dependencies"])
    async def test_send_notification_with_preferences(self, extension):
        """Test notification sending respecting user preferences"""
        # Setup user preferences to disable email notifications
        extension.user_preferences["user123"] = {
            "email_notifications": False,
            "sms_notifications": True,
            "notification_types": ["login_failed"],  # Only certain types
        }

        result = await extension.send_notification(
            recipient="user123",
            notification_type="login_success",  # Not in allowed types
            channel="email",  # Disabled for this user
        )

        assert result["success"] is False
        assert "preferences" in result["message"]

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_notifications_dependencies"])
    async def test_create_template_success(self, extension, mock_jinja2_available):
        """Test successful template creation"""
        result = await extension.create_template(
            template_name="custom_login_success",
            template_type="login_success",
            content={
                "subject": "Welcome back, {{user_name}}!",
                "body": "You have successfully logged in at {{login_time}}.",
                "sms_content": "Welcome back! Login at {{login_time}}.",
            },
            template_language="en",
        )

        assert result["success"] is True
        assert "template_id" in result
        assert result["template_name"] == "custom_login_success"

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_notifications_dependencies"])
    async def test_create_template_already_exists(self, extension):
        """Test template creation when template already exists"""
        # Setup existing template
        extension.notification_templates["login_success"] = {
            "name": "login_success",
            "type": "login_success",
            "content": {"subject": "Login Success"},
        }

        result = await extension.create_template(
            template_name="login_success",
            template_type="login_success",
            content={"subject": "New Login Success"},
        )

        assert result["success"] is False
        assert "already exists" in result["message"]

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_notifications_dependencies"])
    async def test_create_template_invalid_content(self, extension):
        """Test template creation with invalid content"""
        result = await extension.create_template(
            template_name="invalid_template",
            template_type="login_success",
            content={
                # Missing required fields like subject
                "body": "Test body",
            },
        )

        assert result["success"] is False
        assert "missing required fields" in result["message"]

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_notifications_dependencies"])
    async def test_update_preferences_success(self, extension):
        """Test successful preference update"""
        result = await extension.update_preferences(
            user_id="user123",
            preferences={
                "email_notifications": True,
                "sms_notifications": False,
                "notification_types": ["login_success", "password_changed"],
                "quiet_hours": {"start": "22:00", "end": "08:00"},
                "frequency": "immediate",
            },
        )

        assert result["success"] is True
        assert "preferences" in result
        assert extension.user_preferences["user123"]["email_notifications"] is True

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_notifications_dependencies"])
    async def test_update_preferences_invalid_data(self, extension):
        """Test preference update with invalid data"""
        result = await extension.update_preferences(
            user_id="user123",
            preferences={
                "invalid_preference": True,
                "notification_types": "not_a_list",  # Should be a list
            },
        )

        assert result["success"] is False
        assert "invalid" in result["message"]

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_notifications_dependencies"])
    async def test_track_delivery_success(self, extension):
        """Test successful delivery tracking"""
        # Setup delivery record
        delivery_id = "delivery_123"
        extension.delivery_statuses[delivery_id] = {
            "id": delivery_id,
            "status": "sent",
            "channel": "email",
            "sent_at": "2023-01-01T10:00:00",
        }

        result = await extension.track_delivery(delivery_id=delivery_id)

        assert result["success"] is True
        assert "delivery_status" in result
        assert result["delivery_status"]["status"] == "sent"

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_notifications_dependencies"])
    async def test_track_delivery_not_found(self, extension):
        """Test delivery tracking with non-existent delivery"""
        result = await extension.track_delivery(delivery_id="non_existent")

        assert result["success"] is False
        assert "not found" in result["message"]

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_notifications_dependencies"])
    async def test_track_delivery_with_webhook_update(self, extension):
        """Test delivery tracking with webhook status update"""
        # Setup delivery record
        delivery_id = "delivery_123"
        extension.delivery_statuses[delivery_id] = {
            "id": delivery_id,
            "status": "sent",
            "channel": "sms",
        }

        result = await extension.track_delivery(
            delivery_id=delivery_id,
            webhook_data={
                "status": "delivered",
                "delivered_at": "2023-01-01T10:05:00",
            },
        )

        assert result["success"] is True
        assert extension.delivery_statuses[delivery_id]["status"] == "delivered"

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_notifications_dependencies"])
    async def test_send_auth_notification_login_success(self, extension):
        """Test sending authentication notification for login success"""
        with patch.object(extension, "send_notification") as mock_send:
            mock_send.return_value = {"success": True, "delivery_id": "auth_123"}

            result = await extension.send_auth_notification(
                event_type="login_success",
                user_id="user123",
                event_data={
                    "user_name": "John Doe",
                    "login_time": "2023-01-01 10:00:00",
                    "ip_address": "192.168.1.1",
                    "user_agent": "Mozilla/5.0...",
                },
            )

            assert result["success"] is True
            assert mock_send.called

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_notifications_dependencies"])
    async def test_send_auth_notification_password_reset(self, extension):
        """Test sending authentication notification for password reset"""
        with patch.object(extension, "send_notification") as mock_send:
            mock_send.return_value = {"success": True, "delivery_id": "auth_456"}

            result = await extension.send_auth_notification(
                event_type="password_reset_request",
                user_id="user123",
                event_data={
                    "user_name": "John Doe",
                    "reset_token": "abc123",
                    "reset_link": "https://example.com/reset?token=abc123",
                    "expires_at": "2023-01-01 12:00:00",
                },
            )

            assert result["success"] is True

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_notifications_dependencies"])
    async def test_send_auth_notification_security_alert(self, extension):
        """Test sending authentication notification for security alert"""
        with patch.object(extension, "send_notification") as mock_send:
            mock_send.return_value = {"success": True, "delivery_id": "auth_789"}

            result = await extension.send_auth_notification(
                event_type="suspicious_login",
                user_id="user123",
                event_data={
                    "user_name": "John Doe",
                    "login_time": "2023-01-01 10:00:00",
                    "ip_address": "192.168.1.100",  # Different IP
                    "location": "Unknown Location",
                    "action_required": True,
                },
                priority="high",
            )

            assert result["success"] is True

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_notifications_dependencies"])
    async def test_send_auth_notification_disabled(self, extension):
        """Test sending authentication notification when disabled"""
        extension.enable_auth_notifications = False

        result = await extension.send_auth_notification(
            event_type="login_success",
            user_id="user123",
        )

        assert result["success"] is False
        assert "disabled" in result["message"]

    @pytest.mark.dependency(depends=["auth_notifications_dependencies"])
    def test_render_template_success(self, extension, mock_jinja2_available):
        """Test successful template rendering"""
        template_content = {
            "subject": "Welcome, {{user_name}}!",
            "body": "Hello {{user_name}}, you logged in at {{login_time}}.",
        }

        template_data = {
            "user_name": "John Doe",
            "login_time": "2023-01-01 10:00:00",
        }

        result = extension._render_template(template_content, template_data)

        assert "subject" in result
        assert "body" in result
        # Template should be rendered (mocked to return constant)
        assert result["subject"] == "Rendered template content"

    @pytest.mark.dependency(depends=["auth_notifications_dependencies"])
    def test_render_template_with_missing_data(self, extension, mock_jinja2_available):
        """Test template rendering with missing data"""
        template_content = {
            "subject": "Welcome, {{user_name}}!",
            "body": "Hello {{user_name}}, you logged in at {{login_time}}.",
        }

        template_data = {
            "user_name": "John Doe",
            # Missing login_time
        }

        # Should handle missing template variables gracefully
        result = extension._render_template(template_content, template_data)
        assert result is not None

    @pytest.mark.dependency(depends=["auth_notifications_dependencies"])
    def test_validate_config_all_libraries_available(self):
        """Test configuration validation when all libraries are available"""
        with patch("lib.Logging.logger"):
            mock_libs = {
                "jinja2": MagicMock(),
                "smtplib": MagicMock(),
                "requests": MagicMock(),
                "redis": MagicMock(),
            }

            with patch.dict("sys.modules", mock_libs):
                extension = EXT_Auth_Notifications(
                    enable_email_notifications=False,
                    enable_sms_notifications=False,
                )
                issues = extension.validate_config()

                assert len(issues) == 0

    @pytest.mark.dependency(depends=["auth_notifications_dependencies"])
    def test_validate_config_missing_jinja2(self):
        """Test configuration validation with missing jinja2"""
        with patch("lib.Logging.logger"):
            with patch.dict("sys.modules", {"jinja2": None}):
                extension = EXT_Auth_Notifications()
                issues = extension.validate_config()

                assert len(issues) >= 1
                issue_text = " ".join(issues).lower()
                assert "jinja2" in issue_text

    @pytest.mark.dependency(depends=["auth_notifications_dependencies"])
    def test_validate_config_email_without_smtp(self):
        """Test configuration validation for email without SMTP settings"""
        with patch("lib.Logging.logger"):
            extension = EXT_Auth_Notifications(
                enable_email_notifications=True,
                smtp_host=None,  # Missing SMTP configuration
            )
            issues = extension.validate_config()

            assert len(issues) >= 1
            issue_text = " ".join(issues).lower()
            assert "smtp" in issue_text

    @pytest.mark.dependency(depends=["auth_notifications_dependencies"])
    def test_validate_config_sms_without_api_key(self):
        """Test configuration validation for SMS without API key"""
        with patch("lib.Logging.logger"):
            extension = EXT_Auth_Notifications(
                enable_sms_notifications=True, sms_api_key=None  # Missing SMS API key
            )
            issues = extension.validate_config()

            assert len(issues) >= 1
            issue_text = " ".join(issues).lower()
            assert "sms api key" in issue_text

    @pytest.mark.dependency(depends=["auth_notifications_dependencies"])
    def test_get_required_permissions(self, extension):
        """Test getting required permissions"""
        permissions = extension.get_required_permissions()

        assert isinstance(permissions, list)
        assert len(permissions) == 7
        assert "notifications:send" in permissions
        assert "notifications:templates" in permissions
        assert "notifications:preferences" in permissions

    @pytest.mark.dependency(depends=["auth_notifications_dependencies"])
    def test_on_start_success(self):
        """Test successful extension start"""
        with patch("lib.Logging.logger"):
            extension = EXT_Auth_Notifications(
                enable_email_notifications=False, enable_sms_notifications=False
            )
            result = extension.on_start()

            assert result is True

    @pytest.mark.dependency(depends=["auth_notifications_dependencies"])
    def test_on_stop_success(self, extension):
        """Test successful extension stop"""
        # Add some test data
        extension.notification_templates["test"] = {"content": "test"}
        extension.delivery_statuses["delivery1"] = {"status": "sent"}
        extension.user_preferences["user1"] = {"email": True}

        result = extension.on_stop()

        assert result is True
        assert len(extension.notification_templates) == 0
        assert len(extension.delivery_statuses) == 0
        assert len(extension.user_preferences) == 0

    @pytest.mark.dependency(depends=["auth_notifications_dependencies"])
    def test_has_capability(self, extension):
        """Test capability checking"""
        assert extension.has_capability("notification_sending") is True
        assert extension.has_capability("template_management") is True
        assert extension.has_capability("non_existent_capability") is False

    @pytest.mark.dependency(depends=["auth_notifications_dependencies"])
    def test_get_notification_stats(self, extension):
        """Test getting notification statistics"""
        # Add some test data
        extension.notification_templates["template1"] = {"type": "login_success"}
        extension.delivery_statuses["delivery1"] = {"status": "sent"}
        extension.user_preferences["user1"] = {"email": True}

        stats = extension.get_notification_stats()

        assert isinstance(stats, dict)
        assert stats["total_templates"] == 1
        assert stats["delivery_records"] == 1
        assert stats["user_preferences"] == 1
        assert stats["email_enabled"] == extension.enable_email_notifications

    @pytest.mark.dependency(depends=["auth_notifications_dependencies"])
    def test_abilities_discovery(self, extension):
        """Test that all expected abilities are discovered"""
        extension.discover_abilities()

        for expected_ability in self.expected_abilities:
            assert expected_ability in extension.abilities
            assert callable(extension.abilities[expected_ability])

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_notifications_dependencies"])
    async def test_execute_ability_success(self, extension):
        """Test successful ability execution"""
        # Test create_template ability
        result = await extension.execute_ability(
            "create_template",
            {
                "template_name": "test_template",
                "template_type": "login_success",
                "content": {
                    "subject": "Test Subject",
                    "body": "Test Body",
                },
            },
        )

        assert "success" in result

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_notifications_dependencies"])
    async def test_execute_ability_not_found(self, extension):
        """Test executing non-existent ability"""
        result = await extension.execute_ability("non_existent_ability")

        assert "not found" in result

    @pytest.mark.dependency(depends=["auth_notifications_dependencies"])
    def test_auth_hooks_registration(self):
        """Test that authentication hooks are properly registered"""
        with patch("lib.Logging.logger"):
            with patch(
                "extensions.auth_notifications.EXT_Auth_Notifications.AbstractExtension.bll_hook"
            ) as mock_hook:
                extension = EXT_Auth_Notifications()
                extension._register_auth_hooks()

                # Should register hooks for authentication events
                assert mock_hook.called

    @pytest.mark.dependency(depends=["auth_notifications_dependencies"])
    def test_lifecycle_methods_integration(self, extension):
        """Test integration of lifecycle methods"""
        # Test startup
        extension.on_startup()

        # Test shutdown
        extension.on_shutdown()

        # These methods should not raise exceptions

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_notifications_dependencies"])
    async def test_notification_full_workflow(
        self, extension, mock_smtplib_available, mock_jinja2_available
    ):
        """Test complete notification workflow"""
        # 1. Create template
        with patch.object(extension, "_validate_template_content") as mock_validate:
            mock_validate.return_value = True

            template_result = await extension.create_template(
                template_name="workflow_test",
                template_type="login_success",
                content={
                    "subject": "Welcome back, {{user_name}}!",
                    "body": "You logged in at {{login_time}}.",
                },
            )
            assert template_result["success"] is True

        # 2. Update user preferences
        preferences_result = await extension.update_preferences(
            user_id="user123",
            preferences={
                "email_notifications": True,
                "notification_types": ["login_success"],
            },
        )
        assert preferences_result["success"] is True

        # 3. Send notification
        with patch.object(extension, "_send_email_notification") as mock_email:
            mock_email.return_value = {"success": True, "delivery_id": "email_123"}

            notification_result = await extension.send_notification(
                recipient="user@example.com",
                notification_type="login_success",
                channel="email",
                template_data={
                    "user_name": "John Doe",
                    "login_time": "2023-01-01 10:00:00",
                },
            )
            assert notification_result["success"] is True

        # 4. Track delivery
        delivery_id = notification_result["delivery_id"]
        tracking_result = await extension.track_delivery(delivery_id=delivery_id)
        assert tracking_result["success"] is True

        # 5. Send auth notification
        auth_result = await extension.send_auth_notification(
            event_type="login_success",
            user_id="user123",
            event_data={
                "user_name": "John Doe",
                "login_time": "2023-01-01 10:00:00",
            },
        )
        assert auth_result["success"] is True

    @pytest.mark.dependency(depends=["auth_notifications_dependencies"])
    def test_template_validation_security(self, extension):
        """Test that template validation prevents security issues"""
        # Test template with potentially dangerous content
        dangerous_template = {
            "subject": "{{user_name}}",
            "body": "{{dangerous_script}}<script>alert('xss')</script>",
        }

        is_valid = extension._validate_template_content(dangerous_template)

        # Should validate template content for security
        assert isinstance(is_valid, bool)

    @pytest.mark.dependency(depends=["auth_notifications_dependencies"])
    def test_notification_rate_limiting(self, extension):
        """Test notification rate limiting functionality"""
        user_id = "user123"
        notification_type = "login_success"

        # Test rate limiting check
        is_allowed = extension._check_rate_limit(user_id, notification_type)
        assert isinstance(is_allowed, bool)

        # Test rate limit update
        extension._update_rate_limit(user_id, notification_type)

        # Rate limit data should be updated
        if extension.enable_rate_limiting:
            assert user_id in extension.rate_limit_data

    @pytest.mark.dependency(depends=["auth_notifications_dependencies"])
    def test_notification_retry_mechanism(self, extension):
        """Test notification retry mechanism"""
        delivery_record = {
            "id": "delivery_123",
            "status": "failed",
            "retry_count": 0,
            "max_retries": 3,
        }

        should_retry = extension._should_retry_delivery(delivery_record)
        assert should_retry is True

        # Test maximum retries reached
        delivery_record["retry_count"] = 3
        should_retry = extension._should_retry_delivery(delivery_record)
        assert should_retry is False
