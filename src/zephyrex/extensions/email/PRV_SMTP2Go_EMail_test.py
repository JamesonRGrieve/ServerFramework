# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the SMTP2Go email provider.

No mocks. Metadata, settings, config validation, bonding and input
validation run on real code; sending runs against an SMTP2go account served
in process (MailAPITestServers), from real provider instances. Send tests
that hit the real API are gated on SMTP2GO_API_KEY.
"""

from __future__ import annotations

import os
from decimal import Decimal
from typing import Any, Iterator

import pytest

from zephyrex.extensions.AbstractExtensionProvider import HealthStatus
from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.email.EXT_EMail import (
    FROM_EMAIL_SETTING,
    EmailAddress,
    EmailMessage,
    EXT_EMail,
)
from zephyrex.extensions.email.EmailTestSupport import email_instance
from zephyrex.extensions.email.MailAPITestServers import SMTP2goTestServer
from zephyrex.extensions.email.PRV_SMTP2Go_EMail import Smtp2goProvider
from zephyrex.extensions.ExternalErrors import (
    AuthExternalError,
    TransientExternalError,
)

ACCOUNT_KEY = "smtp2go-account-key"
SENDER = "desk@example.org"


def _smtp2go_available() -> bool:
    return bool(os.environ.get("SMTP2GO_API_KEY"))


def _message(to: str = "to@example.com") -> EmailMessage:
    return EmailMessage(
        to=[EmailAddress(address=to)], subject="Hello", body_text="The body."
    )


# ---------------------------------------------------------------------------
# Provider metadata
# ---------------------------------------------------------------------------


class TestSmtp2goProviderMetadata:
    def test_name(self):
        assert Smtp2goProvider.name == "smtp2go"

    def test_version_semver(self):
        parts = Smtp2goProvider.version.split(".")
        assert len(parts) == 3
        assert all(p.isdigit() for p in parts)

    def test_platform_name(self):
        assert Smtp2goProvider.get_platform_name() == "SMTP2go"

    def test_services(self):
        svc = Smtp2goProvider.services()
        assert "email" in svc

    def test_abilities_include_send(self):
        assert "email_send" in Smtp2goProvider._abilities

    def test_capabilities_include_send(self):
        from zephyrex.extensions.email.EXT_EMail import Capability

        assert Capability.SEND in Smtp2goProvider.capabilities

    def test_capabilities_include_attachments(self):
        from zephyrex.extensions.email.EXT_EMail import Capability

        assert Capability.ATTACHMENTS in Smtp2goProvider.capabilities

    def test_cost_model(self):
        assert Smtp2goProvider.cost_model.per_call_usd == Decimal("0.0001")

    def test_rate_limit(self):
        assert Smtp2goProvider.rate_limit.rps == 100
        assert Smtp2goProvider.rate_limit.burst == 200

    def test_bulk_max_batch(self):
        assert Smtp2goProvider.SEND_BULK_MAX_BATCH == 1000

    def test_auth_strategy(self):
        assert Smtp2goProvider.default_auth_strategy == "api_key"

    def test_description_nonempty(self):
        assert len(Smtp2goProvider.description) > 5


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class TestSmtp2goSettings:
    def test_declared_settings(self):
        keys = {d.key for d in Smtp2goProvider.instance_settings}
        assert {"api_key", FROM_EMAIL_SETTING, "api_url"} <= keys
        assert Smtp2goProvider.instance_setting("api_key").secret

    def test_default_api_url(self):
        assert Smtp2goProvider.instance_setting("api_url").default == (
            "https://api.smtp2go.com/v3"
        )

    def test_the_operators_variables(self):
        assert Smtp2goProvider._env["SMTP2GO_API_KEY"] == ""
        assert Smtp2goProvider._env["SMTP2GO_API_URL"] == "https://api.smtp2go.com/v3"
        assert "SMTP2GO_FROM_EMAIL" in Smtp2goProvider._env


# ---------------------------------------------------------------------------
# The operator's environment (no instance)
# ---------------------------------------------------------------------------


class TestSmtp2goOperatorEnvironment:
    @pytest.fixture(autouse=True)
    def unset(self, set_env) -> None:
        set_env("SMTP2GO_API_KEY", "")
        set_env("SMTP2GO_FROM_EMAIL", "")

    def test_validate_config_no_key(self):
        assert Smtp2goProvider.validate_config() is False

    def test_validate_config_with_key(self, set_env):
        set_env("SMTP2GO_API_KEY", "test-key")
        assert Smtp2goProvider.validate_config() is True

    def test_health_no_api_key(self):
        report = Smtp2goProvider.health_check()
        assert report.status == HealthStatus.DOWN
        assert "not configured" in report.detail

    def test_api_key_not_in_health_report(self, set_env):
        set_env("SMTP2GO_API_KEY", "secret-key-12345")
        report = Smtp2goProvider.health_check()
        assert "secret-key-12345" not in report.detail

    def test_bond_with_key_returns_sdk(self, set_env):
        set_env("SMTP2GO_API_KEY", "bond-test-key")
        set_env("SMTP2GO_FROM_EMAIL", "test@example.com")
        bonded = Smtp2goProvider.bond_instance(None)
        assert bonded is not None
        assert bonded.sdk["api_key"] == "bond-test-key"
        assert bonded.sdk["from_email"] == "test@example.com"
        assert "api.smtp2go.com" in bonded.sdk["api_url"]
        assert "client" in bonded.sdk

    def test_bond_no_key_returns_none(self):
        assert Smtp2goProvider.bond_instance(None) is None

    @pytest.mark.asyncio
    async def test_send_no_bond_returns_failure(self):
        result = await Smtp2goProvider.send_email(
            None, "to@example.com", "Subject", "Body"
        )
        assert result == "Failed to send email: could not bond SMTP2go instance"

    @pytest.mark.asyncio
    async def test_send_no_from_email(self, set_env):
        set_env("SMTP2GO_API_KEY", "key")
        result = await Smtp2goProvider.send_email(
            None, "to@example.com", "Subject", "Body"
        )
        assert result == "Failed to send email: SMTP2go from_email not configured"


# ---------------------------------------------------------------------------
# Unsupported abilities (real code, no network)
# ---------------------------------------------------------------------------


class TestSmtp2goUnsupportedAbilities:
    @pytest.mark.asyncio
    async def test_get_emails_returns_empty(self):
        result = await Smtp2goProvider.get_emails(None)
        assert result == []

    @pytest.mark.asyncio
    async def test_create_draft_returns_unsupported(self):
        result = await Smtp2goProvider.create_draft_email(None, "r", "s", "b")
        assert "not supported" in result.lower()

    @pytest.mark.asyncio
    async def test_search_returns_empty(self):
        result = await Smtp2goProvider.search_emails(None, "query")
        assert result == []

    @pytest.mark.asyncio
    async def test_reply_returns_unsupported(self):
        result = await Smtp2goProvider.reply_to_email(None, "id", "body")
        assert "not supported" in result.lower()

    @pytest.mark.asyncio
    async def test_delete_returns_unsupported(self):
        result = await Smtp2goProvider.delete_email(None, "id")
        assert "not supported" in result.lower()

    @pytest.mark.asyncio
    async def test_process_attachments_returns_empty(self):
        result = await Smtp2goProvider.process_attachments(None, "id")
        assert result == []


# ---------------------------------------------------------------------------
# Sending through an account, from real instances
# ---------------------------------------------------------------------------


class TestSmtp2goAccount(ExtensionServerMixin):
    extension_class = EXT_EMail

    @pytest.fixture
    def account(self, monkeypatch, set_env) -> Iterator[SMTP2goTestServer]:
        set_env("SMTP2GO_API_KEY", "")
        set_env("SMTP2GO_FROM_EMAIL", "")
        with SMTP2goTestServer(ACCOUNT_KEY) as server:
            monkeypatch.setenv("EGRESS_ALLOWED_HOSTS", server.host)
            yield server

    def _instance(
        self, model_registry: Any, account: SMTP2goTestServer, **options: Any
    ) -> Any:
        settings = {"api_url": account.api_url, FROM_EMAIL_SETTING: SENDER}
        settings.update(options.pop("settings", {}))
        return email_instance(model_registry, "smtp2go", settings, **options)

    async def test_an_instance_sends_with_its_own_account(
        self, model_registry, account
    ):
        instance = self._instance(model_registry, account, api_key=ACCOUNT_KEY)
        result = await Smtp2goProvider.send_email(
            instance, "to@example.com", "Hello", "The body."
        )
        assert result == "Email sent successfully to to@example.com"
        (sent,) = account.sent
        assert sent["api_key"] == ACCOUNT_KEY
        assert sent["sender"] == SENDER
        assert sent["to"] == ["to@example.com"]
        assert (sent["subject"], sent["text_body"]) == ("Hello", "The body.")

    async def test_the_operators_instance_defaults_to_the_operators_account(
        self, model_registry, account, set_env
    ):
        set_env("SMTP2GO_API_KEY", ACCOUNT_KEY)
        set_env("SMTP2GO_FROM_EMAIL", "operator@example.org")
        instance = email_instance(
            model_registry, "smtp2go", {"api_url": account.api_url}
        )
        result = await Smtp2goProvider.send_email(
            instance, "to@example.com", "Hello", "The body."
        )
        assert result == "Email sent successfully to to@example.com"
        assert account.sent[0]["sender"] == "operator@example.org"

    async def test_a_users_instance_never_sends_on_the_operators_account(
        self, model_registry, account, set_env, admin_a
    ):
        """A user's instance without a key used to send on the operator's
        SMTP2GO_API_KEY, from the operator's address."""
        set_env("SMTP2GO_API_KEY", ACCOUNT_KEY)
        set_env("SMTP2GO_FROM_EMAIL", "operator@example.org")
        instance = self._instance(
            model_registry, account, requester_id=admin_a.id, scope="user"
        )
        result = await Smtp2goProvider.send_email(
            instance, "to@example.com", "Hello", "The body."
        )
        assert result == "Failed to send email: could not bond SMTP2go instance"
        assert account.requests == []

    async def test_send_via_provider_answers_what_was_sent(
        self, model_registry, account
    ):
        instance = self._instance(model_registry, account, api_key=ACCOUNT_KEY)
        sent = await Smtp2goProvider.send_via_provider(instance, _message())
        assert sent["provider"] == "smtp2go"
        assert sent["recipient"] == "to@example.com"
        assert len(account.sent) == 1

    async def test_a_refused_key_is_an_auth_failure(self, model_registry, account):
        """401 from the account: the rotation must not retry the same key."""
        instance = self._instance(model_registry, account, api_key="not-the-key")
        with pytest.raises(AuthExternalError) as raised:
            await Smtp2goProvider.send_via_provider(instance, _message())
        assert raised.value.provider == "smtp2go"
        assert account.sent == []

    async def test_an_unconfigured_instance_is_transient(self, model_registry, account):
        """No key is this provider's failure, not the message's: the rotation
        moves on to the next provider."""
        instance = self._instance(model_registry, account)
        with pytest.raises(TransientExternalError) as raised:
            await Smtp2goProvider.send_via_provider(instance, _message())
        assert raised.value.provider == "smtp2go"
        assert account.requests == []

    async def test_a_users_instance_sends_with_the_users_own_account(
        self, model_registry, account, admin_a
    ):
        instance = self._instance(
            model_registry,
            account,
            requester_id=admin_a.id,
            scope="user",
            api_key=ACCOUNT_KEY,
        )
        result = await Smtp2goProvider.send_email(
            instance, "to@example.com", "Hello", "The body."
        )
        assert result == "Email sent successfully to to@example.com"
        assert account.sent[0]["sender"] == SENDER


# ---------------------------------------------------------------------------
# Live send — only runs when SMTP2GO_API_KEY is set
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _smtp2go_available(), reason="SMTP2GO_API_KEY not set")
class TestSmtp2goLiveSend:
    @pytest.mark.asyncio
    async def test_send_real_email(self):
        from zephyrex.lib.Environment import env

        result = await Smtp2goProvider.send_email(
            None,
            env("SMTP2GO_FROM_EMAIL") or "test@example.com",
            "Zephyrex SMTP2Go Test",
            "This is a test email from the zephyrex test suite.",
        )
        # send_email returns "Failed to send email: ..." on failure -- also a
        # str -- so pin the success outcome.
        assert "sent successfully" in result.lower(), result

    def test_health_check_live(self):
        report = Smtp2goProvider.health_check()
        assert report.status in (HealthStatus.OK, HealthStatus.DEGRADED)

    def test_bond_instance_live(self):
        assert Smtp2goProvider.bond_instance(None) is not None
