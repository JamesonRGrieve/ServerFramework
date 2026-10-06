# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Tests for EXT_EMail extension.
Tests static extension functionality with Provider Rotation System.

Per AGENTS.md no-mock pillar: tests that exercise real provider rotation
or upstream-API calls are tagged ``@pytest.mark.external_api(provider="sendgrid")``
and run live against the sandbox when ``SENDGRID_API_KEY`` is set, or
auto-xfail otherwise. Tests of the extension's metadata and its providers
run against the providers it ships, with no stand-ins.
"""

import pytest

from zephyrex.extensions.AbstractEXTTest import (
    ExtensionServerMixin,
    ExtensionTestConfig,
    ExtensionTestType,
)
from zephyrex.extensions.email.EXT_EMail import EXT_EMail
from zephyrex.extensions.email.PRV_IMAP_EMail import IMAPProvider


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
    )

    def test_extension_metadata(self):
        """Test static extension metadata"""
        assert EXT_EMail.name == "email"
        # 1.1.0: inbound mail sources (signed endpoint, IMAP poller).
        assert EXT_EMail.version == "1.1.0"
        assert "email" in EXT_EMail.description.lower()

    def test_every_provider_belongs_to_the_extension(self):
        """Each provider the extension ships names it, and is offered by
        name."""
        providers = EXT_EMail.providers
        assert providers
        for provider in providers:
            assert provider.extension is EXT_EMail
            assert provider.extension_type == "email"
        assert EXT_EMail.get_provider_names() == [p.name for p in providers]
        assert EXT_EMail.get_provider_class("IMAP") is IMAPProvider
        with pytest.raises(ValueError):
            EXT_EMail.get_provider_class("nonesuch")

    def test_get_abilities_gathers_every_providers(self):
        """The extension offers its own abilities and each provider's."""
        abilities = EXT_EMail.get_abilities()
        assert EXT_EMail._abilities <= abilities
        for provider in EXT_EMail.providers:
            assert provider._abilities <= abilities, provider.name

    def test_has_ability(self):
        assert EXT_EMail.has_ability("email_send")
        # Read by the IMAP-backed providers only.
        assert EXT_EMail.has_ability("email_search")
        assert not EXT_EMail.has_ability("nonexistent_ability")

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
