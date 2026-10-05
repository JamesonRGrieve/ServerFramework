"""
Tests for EXT_EMail extension.
Tests static extension functionality with Provider Rotation System.

Per AGENTS.md no-mock pillar: tests that exercise real provider rotation
or upstream-API calls are tagged ``@pytest.mark.external_api(provider="sendgrid")``
and run live against the sandbox when ``SENDGRID_API_KEY`` is set, or
auto-xfail otherwise. Tests of pure static metadata/registration use real
fixtures with no mocks.
"""

from typing import Dict, List, Optional

import pytest

from zephyrex.extensions.AbstractEXTTest import (
    ExtensionServerMixin,
    ExtensionTestConfig,
    ExtensionTestType,
)
from zephyrex.extensions.email.EXT_EMail import (
    EXT_EMail,
    AbstractEmailProvider,
)
from zephyrex.lib.Dependencies import Dependencies, PIP_Dependency
from zephyrex.logic.BLL_Providers import ProviderInstanceModel


class ConcreteEmailProvider(AbstractEmailProvider):
    """Concrete implementation of AbstractEmailProvider for testing"""

    # Static provider metadata
    name = "test_email"
    friendly_name = "Test Email Provider"
    description = "Test email provider for unit tests"
    platform_name = "TestEmail"

    # Environment variables for testing
    _env = {
        "TEST_EMAIL_API_KEY": "test_api_key_12345",
        "TEST_EMAIL_FROM": "test@example.com",
    }

    # Test abilities
    _abilities = {
        "email_send",
        "email_receive",
        "email_templates",
        "email_tracking",
        "test_ability",
    }

    # Link to parent extension (REQUIRED for Provider Rotation System)
    extension = EXT_EMail

    @classmethod
    def bond_instance(cls, instance: ProviderInstanceModel) -> None:
        """The test provider has no SDK to bond."""
        return None

    @classmethod
    def services(cls) -> List[str]:
        """Return list of services provided by this provider."""
        return ["email", "messaging", "communication", "notifications"]

    @classmethod
    def get_platform_name(cls) -> str:
        """Return the platform name"""
        return cls.platform_name

    @classmethod
    async def get_emails(
        cls,
        provider_instance: ProviderInstanceModel,
        folder_name: str = "Inbox",
        max_emails: int = 10,
        page_size: int = 10,
    ) -> List[Dict[str, any]]:  # type: ignore[valid-type]
        """Mock email retrieval"""
        return [
            {
                "id": "test_email_1",
                "subject": "Test Email 1",
                "from": "sender@example.com",
                "body": "Test email body",
            }
        ]

    @classmethod
    async def send_email(
        cls,
        provider_instance: ProviderInstanceModel,
        recipient: str,
        subject: str,
        body: str,
        attachments: Optional[List[str]] = None,
        importance: str = "normal",
    ) -> str:
        """Mock email sending"""
        return f"Test email sent to {recipient}: {subject}"

    @classmethod
    async def create_draft_email(
        cls,
        provider_instance: ProviderInstanceModel,
        recipient: str,
        subject: str,
        body: str,
        attachments: Optional[List[str]] = None,
        importance: str = "normal",
    ) -> str:
        """Mock draft email creation"""
        return f"Test draft created for {recipient}: {subject}"

    @classmethod
    async def search_emails(
        cls,
        provider_instance: ProviderInstanceModel,
        query: str,
        folder_name: str = "Inbox",
        max_emails: int = 10,
    ) -> List[Dict[str, any]]:  # type: ignore[valid-type]
        """Mock email search"""
        return [
            {
                "id": "search_result_1",
                "subject": f"Search result for: {query}",
                "from": "search@example.com",
                "body": f"Email matching query: {query}",
            }
        ]

    @classmethod
    async def reply_to_email(
        cls,
        provider_instance: ProviderInstanceModel,
        email_id: str,
        body: str,
        attachments: Optional[List[str]] = None,
    ) -> str:
        """Mock email reply"""
        return f"Test reply sent to email {email_id}: {body[:50]}..."

    @classmethod
    async def delete_email(
        cls,
        provider_instance: ProviderInstanceModel,
        email_id: str,
    ) -> str:
        """Mock email deletion"""
        return f"Test email {email_id} deleted"

    @classmethod
    async def process_attachments(
        cls,
        provider_instance: ProviderInstanceModel,
        email_id: str,
    ) -> List[Dict[str, any]]:  # type: ignore[valid-type]
        """Mock attachment processing"""
        return [
            {
                "id": "attachment_1",
                "filename": "test_attachment.pdf",
                "size": 1024,
                "type": "application/pdf",
            }
        ]

    @classmethod
    def validate_config(cls) -> List[str]:
        """Mock configuration validation"""
        return []  # No issues for test provider


class TestEXTEMail(ExtensionServerMixin):
    """
    Test suite for EXT_EMail extension.
    Tests static extension functionality with Provider Rotation System.
    """

    # Extension configuration for ExtensionServerMixin
    extension_class = EXT_EMail

    # Test configuration
    test_config = ExtensionTestConfig(
        test_types={
            ExtensionTestType.STRUCTURE,
            ExtensionTestType.METADATA,
            ExtensionTestType.DEPENDENCIES,
            ExtensionTestType.ABILITIES,
            ExtensionTestType.ENVIRONMENT,
            ExtensionTestType.ROTATION,
        },
        expected_abilities={
            "email_attachment",
            "email_attachments",
            "email_delete",
            "email_draft",
            "email_flag",
            "email_get",
            "email_latest",
            "email_mark_read",
            "email_mark_unread",
            "email_move",
            "email_reply",
            "email_search",
            "email_send",
            "email_status",
            "email_thread_messages",
            "email_threads",
            "email_unflag",
        },
        expected_env_vars={
            "SENDGRID_API_KEY": "",
            "SENDGRID_FROM_EMAIL": "",
            "EMAIL_PROVIDER": "sendgrid",
            "SMTP_SERVER": "",
            "SMTP_PORT": "587",
            "IMAP_SERVER": "",
            "IMAP_PORT": "993",
        },
    )

    def test_extension_metadata(self):
        """Test static extension metadata"""
        assert EXT_EMail.name == "email"
        # 1.1.0: inbound mail sources (signed endpoint, IMAP poller).
        assert EXT_EMail.version == "1.1.0"
        assert "email" in EXT_EMail.description.lower()

    def test_provider_discovery(self):
        """Test provider auto-discovery mechanism"""
        providers = EXT_EMail.providers
        assert isinstance(providers, list)
        # The actual providers discovered depend on what PRV_*.py files exist

    def _swap_providers(self, providers):
        """Helper: replace EXT_EMail.providers with a real list, restoring after.

        Per AGENTS.md no-mock pillar — we mutate the real attribute rather
        than patching it. The test class is responsible for restoring.
        """
        # ``providers`` may be a classproperty in some builds, and a plain
        # attribute in others. Capture whatever's there now and rebind.
        original = type(EXT_EMail).__dict__.get("providers", None)
        EXT_EMail.providers = providers
        return original

    def _restore_providers(self, original):
        if original is None:
            try:
                del EXT_EMail.providers
            except AttributeError:
                pass
        else:
            EXT_EMail.providers = original

    def test_get_abilities(self):
        """Test ability aggregation from extension and providers."""
        original = self._swap_providers([ConcreteEmailProvider])
        try:
            abilities = EXT_EMail.get_abilities()

            # Should include extension abilities
            for ability in EXT_EMail._abilities:
                assert ability in abilities

            # Should include provider abilities
            for ability in ConcreteEmailProvider._abilities:
                assert ability in abilities
        finally:
            self._restore_providers(original)

    def test_has_ability(self):
        """Test ability checking."""
        original = self._swap_providers([ConcreteEmailProvider])
        try:
            assert EXT_EMail.has_ability("email_send")
            assert EXT_EMail.has_ability("email_receive")
            assert EXT_EMail.has_ability("test_ability")  # From test provider
            assert not EXT_EMail.has_ability("nonexistent_ability")
        finally:
            self._restore_providers(original)

    def test_get_provider_names(self):
        """Test provider name discovery."""
        original = self._swap_providers([ConcreteEmailProvider])
        try:
            provider_names = EXT_EMail.get_provider_names()
            assert "test_email" in provider_names
        finally:
            self._restore_providers(original)

    def test_validate_config_asks_nothing_of_the_environment(self, monkeypatch):
        """Mail goes through configured provider instances, so the extension
        needs no SendGrid (or any) variable set. It used to report SendGrid's
        variables missing on every deployment that used another provider."""
        monkeypatch.delenv("SENDGRID_API_KEY", raising=False)
        monkeypatch.delenv("SENDGRID_FROM_EMAIL", raising=False)
        assert EXT_EMail.validate_config() == []

    @pytest.mark.external_api(provider="sendgrid")
    @pytest.mark.asyncio
    async def test_send_email_static_method(self, sandbox_credentials_for):
        """Live sandbox: static email sending via rotation system.

        Auto-xfailed when ``SENDGRID_API_KEY`` is unset (Item 15 marker).
        """
        creds = sandbox_credentials_for("sendgrid")
        assert creds.get("SENDGRID_API_KEY"), "SENDGRID_API_KEY required for live test"
        result = await EXT_EMail.send_email(
            recipient="test@example.com", subject="Test Subject", body="Test Body"
        )
        assert result is not None
        assert "not configured" not in str(result).lower()
        assert "failed" not in str(result).lower()

    @pytest.mark.external_api(provider="sendgrid")
    @pytest.mark.asyncio
    async def test_get_emails_static_method(self, sandbox_credentials_for):
        """Live sandbox: static email retrieval via rotation system."""
        creds = sandbox_credentials_for("sendgrid")
        assert creds.get("SENDGRID_API_KEY"), "SENDGRID_API_KEY required for live test"
        result = await EXT_EMail.get_emails()
        assert result is not None
        assert "not configured" not in str(result).lower()

    @pytest.mark.external_api(provider="sendgrid")
    @pytest.mark.asyncio
    async def test_create_draft_email_static_method(self, sandbox_credentials_for):
        """Live sandbox: static draft creation via rotation system."""
        creds = sandbox_credentials_for("sendgrid")
        assert creds.get("SENDGRID_API_KEY"), "SENDGRID_API_KEY required for live test"
        result = await EXT_EMail.create_draft_email(
            recipient="test@example.com", subject="Test Subject", body="Test Body"
        )
        assert result is not None
        assert "not configured" not in str(result).lower()

    @pytest.mark.external_api(provider="sendgrid")
    @pytest.mark.asyncio
    async def test_search_emails_static_method(self, sandbox_credentials_for):
        """Live sandbox: static email search via rotation system."""
        creds = sandbox_credentials_for("sendgrid")
        assert creds.get("SENDGRID_API_KEY"), "SENDGRID_API_KEY required for live test"
        result = await EXT_EMail.search_emails("test query")
        assert result is not None
        assert "not configured" not in str(result).lower()

    @pytest.mark.external_api(provider="sendgrid")
    @pytest.mark.asyncio
    async def test_reply_to_email_static_method(self, sandbox_credentials_for):
        """Live sandbox: static email reply via rotation system."""
        creds = sandbox_credentials_for("sendgrid")
        assert creds.get("SENDGRID_API_KEY"), "SENDGRID_API_KEY required for live test"
        result = await EXT_EMail.reply_to_email(email_id="test_123", body="Reply body")
        assert result is not None
        assert "not configured" not in str(result).lower()

    @pytest.mark.external_api(provider="sendgrid")
    @pytest.mark.asyncio
    async def test_delete_email_static_method(self, sandbox_credentials_for):
        """Live sandbox: static email deletion via rotation system."""
        creds = sandbox_credentials_for("sendgrid")
        assert creds.get("SENDGRID_API_KEY"), "SENDGRID_API_KEY required for live test"
        result = await EXT_EMail.delete_email("test_123")
        assert result is not None
        assert "not configured" not in str(result).lower()

    @pytest.mark.external_api(provider="sendgrid")
    @pytest.mark.asyncio
    async def test_process_attachments_static_method(self, sandbox_credentials_for):
        """Live sandbox: static attachment processing via rotation system."""
        creds = sandbox_credentials_for("sendgrid")
        assert creds.get("SENDGRID_API_KEY"), "SENDGRID_API_KEY required for live test"
        result = await EXT_EMail.process_attachments("test_123")
        assert result is not None
        assert "not configured" not in str(result).lower()

    def test_env_property(self):
        """Test environment variable property"""
        env_vars = EXT_EMail.env

        assert isinstance(env_vars, dict)
        assert "SENDGRID_API_KEY" in env_vars
        assert "SENDGRID_FROM_EMAIL" in env_vars
        assert "EMAIL_PROVIDER" in env_vars

    def test_dependency_properties(self):
        """Test dependency property access"""
        pip_deps = EXT_EMail.pip_dependencies
        ext_deps = EXT_EMail.ext_dependencies
        sys_deps = EXT_EMail.sys_dependencies

        assert isinstance(pip_deps, list)
        assert isinstance(ext_deps, list)
        assert isinstance(sys_deps, list)

        # Should have email-related dependencies
        pip_names = [dep.name for dep in pip_deps]
        assert "sendgrid" in pip_names

    def test_status_reports_the_providers_and_nothing_of_the_environment(
        self, monkeypatch
    ):
        """email_status used to say "configured" whenever SENDGRID_API_KEY
        was set, whichever provider sent the mail; it now reports the version
        and the providers offered."""
        monkeypatch.setenv("SMTP_SERVER", "smtp.internal.example")
        status = EXT_EMail.get_extension_status()
        assert status == {
            "extension": "email",
            "version": EXT_EMail.version,
            "providers": sorted(EXT_EMail.get_provider_names()),
        }
        assert "smtp.internal.example" not in str(status)

    def test_no_ability_reads_out_the_servers_mail_settings(self):
        """email_config handed any caller of abilities the server's SMTP and
        IMAP hosts and ports from the environment. It is gone, and so are the
        abilities that were declared without an implementation."""
        abilities = EXT_EMail.get_abilities()
        for removed in ("email_config", "email_receive", "email_templates"):
            assert removed not in EXT_EMail._abilities
        assert "email_config" not in abilities
        assert not hasattr(EXT_EMail, "get_configuration")


class TestAbstractEmailProvider:
    """Test the abstract email provider base class"""

    def test_concrete_provider_implementation(self):
        """Test that concrete provider implements required methods"""
        # Verify all abstract methods are implemented
        provider = ConcreteEmailProvider

        assert hasattr(provider, "bond_instance")
        assert hasattr(provider, "get_emails")
        assert hasattr(provider, "send_email")
        assert hasattr(provider, "create_draft_email")

        # Test metadata
        assert provider.name == "test_email"
        assert provider.platform_name == "TestEmail"
        assert provider.extension == EXT_EMail

    def test_provider_abilities(self):
        """Test provider ability management"""
        abilities = ConcreteEmailProvider.get_abilities()

        assert isinstance(abilities, set)
        assert "email_send" in abilities
        assert "email_receive" in abilities
        assert "test_ability" in abilities

    @pytest.mark.asyncio
    async def test_provider_methods(self):
        """Test provider method implementations using a real DTO instance."""

        # No-mock pillar: build a real ProviderInstanceModel-shaped object.
        # The ConcreteEmailProvider methods don't actually inspect their
        # provider_instance argument (they're test stubs that always return
        # canned strings), so a minimal real object is sufficient.
        class _RealInstance:
            id = "test-pi-id"
            name = "test-pi"
            provider_id = "test-prov-id"

        instance = _RealInstance()

        # Test email sending
        result = await ConcreteEmailProvider.send_email(
            instance, "test@example.com", "Test Subject", "Test Body"
        )
        assert "Test email sent" in result

        # Test email retrieval
        emails = await ConcreteEmailProvider.get_emails(instance)
        assert isinstance(emails, list)
        assert len(emails) > 0

        # Test draft creation
        draft = await ConcreteEmailProvider.create_draft_email(
            instance, "test@example.com", "Test Subject", "Test Body"
        )
        assert "Test draft created" in draft

    def test_provider_metadata_attributes(self):
        """Test that provider has required metadata attributes"""
        assert hasattr(ConcreteEmailProvider, "name")
        assert hasattr(ConcreteEmailProvider, "friendly_name")
        assert hasattr(ConcreteEmailProvider, "description")
        assert hasattr(ConcreteEmailProvider, "platform_name")
        assert hasattr(ConcreteEmailProvider, "extension")
        assert ConcreteEmailProvider.extension == EXT_EMail

    def test_provider_abilities_structure(self):
        """Test provider abilities structure"""
        abilities = ConcreteEmailProvider._abilities
        assert isinstance(abilities, set)
        assert "email_send" in abilities
        assert "email_receive" in abilities
        assert "email_templates" in abilities

    def test_provider_services_method(self):
        """Test services method"""
        services = ConcreteEmailProvider.services()
        assert isinstance(services, list)
        assert "email" in services
        assert "messaging" in services

    def test_get_platform_name_method(self):
        """Test platform name retrieval method"""
        platform_name = ConcreteEmailProvider.get_platform_name()
        assert platform_name == "TestEmail"

    def test_provider_extension_linkage(self):
        """Test provider extension linkage"""
        assert ConcreteEmailProvider.extension == EXT_EMail
        assert ConcreteEmailProvider.extension_type == "email"


class _FailingEmailProvider(ConcreteEmailProvider):
    """Reports a send failure the way legacy providers do: as a string."""

    failure: str = ""

    @classmethod
    async def send_email(cls, provider_instance, recipient, subject, body, **kwargs):
        return cls.failure


def _message():
    from zephyrex.extensions.email.EXT_EMail import EmailAddress, EmailMessage

    return EmailMessage(
        to=[EmailAddress(address="to@example.com")], subject="hi", body_text="ok"
    )


class TestSendViaProviderFailureClass:
    """``send_via_provider`` validates first, so a later failure string is the
    provider's, never the caller's input: the rotation must move on."""

    @pytest.mark.asyncio
    async def test_failure_without_status_is_transient(self, monkeypatch):
        from zephyrex.extensions.ExternalErrors import TransientExternalError

        monkeypatch.setattr(
            _FailingEmailProvider, "failure", "Failed to bond SendGrid instance"
        )

        with pytest.raises(TransientExternalError) as raised:
            await _FailingEmailProvider.send_via_provider(None, _message())
        assert raised.value.provider == _FailingEmailProvider.name

    @pytest.mark.asyncio
    async def test_failure_with_status_maps_by_status(self, monkeypatch):
        from zephyrex.extensions.ExternalErrors import AuthExternalError

        monkeypatch.setattr(
            _FailingEmailProvider, "failure", "Failed to send email: 401 bad key"
        )

        with pytest.raises(AuthExternalError):
            await _FailingEmailProvider.send_via_provider(None, _message())


def test_every_email_fixture_gets_its_matrix() -> None:
    """Email's federation fixtures live in its test-only module, and the
    generator's default discovery emits a matrix class for each. (As an
    ``EXT_EMail`` classmethod they generated none: the generator walked
    ``ExtensionRegistry.extensions`` on the class, an attribute only registry
    instances have.)"""
    from zephyrex.extensions.Federation_Matrix_Generator import generate_matrix_tests
    from zephyrex.extensions.email.federation_fixtures_test import (
        MAIL_SAMPLE_ID,
        federation_matrix_fixtures,
    )

    fixtures = federation_matrix_fixtures()
    assert [fixture.name for fixture in fixtures] == ["EXT_EMail.Mail"]
    assert "Test_Federation_EXT_EMail_Mail_Matrix" in generate_matrix_tests()
    # The in-process upstream serves its seed through the real transport.
    (mail,) = fixtures
    served = mail.transport.send_sync(
        operation="get_mail", path_args={"id": MAIL_SAMPLE_ID}
    )
    assert served["id"] == MAIL_SAMPLE_ID
