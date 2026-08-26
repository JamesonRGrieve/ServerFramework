# SPDX-License-Identifier: AGPL-3.0-or-later
"""Keyless conformance + behavior tests for the ported email providers.

Covers the six providers salvaged from the pre-zephyrex AGInfrastructure fork
and rewritten into the current static ``AbstractEmailProvider`` format —
Mailgun, IMAP, Yahoo, POP3, Google, Microsoft — plus the extracted Stalwart
provider. No API keys are required: the tests exercise metadata, capabilities,
Settings, config validation, input validation, discovery, and the graceful
no-credential paths.
"""

import pytest
from pydantic import BaseModel

from zephyrex.extensions.email.EXT_EMail import (
    AbstractEmailProvider,
    Capability,
    EXT_EMail,
)
from zephyrex.extensions.email.PRV_Google_EMail import GoogleProvider
from zephyrex.extensions.email.PRV_IMAP_EMail import IMAPProvider
from zephyrex.extensions.email.PRV_Mailgun_EMail import MailgunProvider
from zephyrex.extensions.email.PRV_Microsoft_EMail import MicrosoftProvider
from zephyrex.extensions.email.PRV_POP3_EMail import POP3Provider
from zephyrex.extensions.email.PRV_Stalwart_EMail import StalwartProvider
from zephyrex.extensions.email.PRV_Yahoo_EMail import YahooProvider

SEND_PROVIDERS = [
    MailgunProvider,
    IMAPProvider,
    YahooProvider,
    POP3Provider,
    GoogleProvider,
    MicrosoftProvider,
    StalwartProvider,
]
RECEIVE_PROVIDERS = [
    IMAPProvider,
    YahooProvider,
    POP3Provider,
    GoogleProvider,
    MicrosoftProvider,
]
ALL_IDS = [c.name for c in SEND_PROVIDERS]


@pytest.mark.parametrize("cls", SEND_PROVIDERS, ids=ALL_IDS)
class TestSalvagedEmailProviderConformance:
    def test_is_email_provider(self, cls):
        assert issubclass(cls, AbstractEmailProvider)

    def test_metadata(self, cls):
        assert cls.name
        assert cls.version
        assert cls.description
        assert isinstance(cls.get_platform_name(), str) and cls.get_platform_name()

    def test_services_list(self, cls):
        services = cls.services()
        assert isinstance(services, list) and "email" in services

    def test_capabilities_are_send_capable(self, cls):
        assert isinstance(cls.capabilities, frozenset)
        assert cls.capabilities  # non-empty
        assert Capability.SEND in cls.capabilities

    def test_settings_model_with_env_map(self, cls):
        assert issubclass(cls.Settings, BaseModel)
        env_map = cls.Settings.env_field_map()
        assert isinstance(env_map, dict) and env_map  # provider-specific mapping

    def test_settings_not_configured_when_empty(self, cls):
        assert cls.Settings.is_configured({}) is False

    def test_validate_config_false_without_credentials(self, cls):
        # No credentials in the environment -> provider reports unconfigured.
        assert cls.validate_config() is False

    def test_bond_instance_returns_none_without_credentials(self, cls):
        assert cls.bond_instance(None) is None

    async def test_send_email_graceful_without_credentials(self, cls):
        result = await cls.send_email(None, "user@example.com", "Subject", "Body text")
        assert isinstance(result, str)
        assert result.lower().startswith("failed")

    async def test_send_email_rejects_crlf_injection(self, cls):
        result = await cls.send_email(
            None, "user@example.com\r\nBcc: evil@example.com", "Subj", "Body"
        )
        assert isinstance(result, str)
        # Assert the specific CRLF-rejection outcome, not a generic failure: with
        # the old `or startswith("failed")` a provider that never checked CRLF
        # (e.g. failing later for missing creds) still passed.
        assert "rejected CRLF" in result

    async def test_send_email_rejects_nul_byte(self, cls):
        result = await cls.send_email(None, "user@example.com", "Sub\x00ject", "Body")
        assert isinstance(result, str)
        # Specific NUL-rejection outcome — a stub returning any "failed" string
        # must not satisfy this.
        assert "rejected NUL byte" in result


@pytest.mark.parametrize(
    "cls", RECEIVE_PROVIDERS, ids=[c.name for c in RECEIVE_PROVIDERS]
)
class TestReceiveCapability:
    def test_declares_read_and_list(self, cls):
        assert Capability.READ in cls.capabilities
        assert Capability.LIST in cls.capabilities

    async def test_get_emails_graceful_without_credentials(self, cls):
        result = await cls.get_emails(None)
        assert isinstance(result, list)
        assert result == []


class TestDiscovery:
    """All ported providers (and Stalwart) must be discovered by EXT_EMail."""

    def test_all_providers_discovered(self):
        names = {getattr(p, "name", p.__name__) for p in EXT_EMail.providers}
        for expected in (
            "mailgun",
            "imap",
            "yahoo",
            "pop3",
            "google",
            "microsoft",
            "stalwart",
            "sendgrid",
            "smtp2go",
        ):
            assert expected in names, f"{expected} not discovered: {sorted(names)}"

    def test_stalwart_is_own_module(self):
        # Stalwart now lives in its own provider module, not embedded in SendGrid.
        assert StalwartProvider.__module__.endswith("PRV_Stalwart_EMail")
        assert StalwartProvider.name == "stalwart"
        assert StalwartProvider.get_platform_name() == "Stalwart"


class TestYahooIsImapSubclass:
    def test_yahoo_reuses_imap(self):
        # DRY: Yahoo Mail speaks IMAP/SMTP, so it is a thin IMAP subclass.
        assert issubclass(YahooProvider, IMAPProvider)
        assert YahooProvider.default_imap_host == "imap.mail.yahoo.com"


class TestPop3IsImapSubclass:
    def test_pop3_reuses_imap_send(self):
        assert issubclass(POP3Provider, IMAPProvider)
        # POP3 has no server-side search.
        assert Capability.SEARCH not in POP3Provider.capabilities
