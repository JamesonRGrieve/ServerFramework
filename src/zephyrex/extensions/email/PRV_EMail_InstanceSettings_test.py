# SPDX-License-Identifier: AGPL-3.0-or-later
"""An email provider is configured by its instances' settings, read from real
provider instances and their setting rows.

Each setting's environment variable (the one the provider read before its
instances carried settings) is a default for the operator's instances only:
a user's or team's instance never sends with the operator's credentials, from
the operator's address, or through the operator's servers. Their mail
servers are held to the SSRF guard; the operator's are not (a self-hosted
mail server is often on the private network)."""

from typing import Any, Dict, List, Set, Tuple

import pytest

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.email.EXT_EMail import FROM_EMAIL_SETTING, EXT_EMail
from zephyrex.extensions.email.EmailTestSupport import email_instance
from zephyrex.extensions.email.InboundEndpoint import SIGNING_SECRET_SETTING
from zephyrex.extensions.email.PRV_Google_EMail import GoogleProvider
from zephyrex.extensions.email.PRV_IMAP_EMail import IMAPProvider
from zephyrex.extensions.email.PRV_Mailgun_EMail import MailgunProvider
from zephyrex.extensions.email.PRV_Microsoft_EMail import MicrosoftProvider
from zephyrex.extensions.email.PRV_POP3_EMail import POP3Provider
from zephyrex.extensions.email.PRV_ProxmoxMailGateway_EMail import (
    ProxmoxMailGatewayProvider,
)
from zephyrex.extensions.email.PRV_SendGrid_EMail import SendgridProvider
from zephyrex.extensions.email.PRV_SMTP2Go_EMail import Smtp2goProvider
from zephyrex.extensions.email.PRV_Stalwart_EMail import StalwartProvider
from zephyrex.extensions.email.PRV_Yahoo_EMail import YahooProvider
from zephyrex.lib.Environment import env
from zephyrex.logic.BLL_Providers import (
    ProviderInstanceManager,
    ProviderInstanceModel,
    root_instance_name,
)


def _mailbox(prefix: str) -> Set[str]:
    return {
        f"{prefix}_{name}"
        for name in (
            "HOST",
            "PORT",
            "SMTP_HOST",
            "SMTP_PORT",
            "USERNAME",
            "PASSWORD",
            "FROM_EMAIL",
            "USE_SSL",
        )
    }


# The variables each provider read from the environment before its instances
# carried settings: still the operator's defaults, and what the framework
# seeds the operator's Root_<Provider> instance from (``_env``), by provider.
OPERATOR_VARIABLES: Dict[str, Set[str]] = {
    SendgridProvider.name: {"SENDGRID_API_KEY", "SENDGRID_FROM_EMAIL"},
    Smtp2goProvider.name: {
        "SMTP2GO_API_KEY",
        "SMTP2GO_FROM_EMAIL",
        "SMTP2GO_API_URL",
    },
    StalwartProvider.name: {
        "STALWART_HOST",
        "STALWART_PORT",
        "STALWART_USERNAME",
        "STALWART_PASSWORD",
        "STALWART_FROM_EMAIL",
        "STALWART_USE_TLS",
    },
    MailgunProvider.name: {
        "MAILGUN_API_KEY",
        "MAILGUN_FROM_EMAIL",
        "MAILGUN_DOMAIN",
        "MAILGUN_API_URL",
    },
    IMAPProvider.name: _mailbox("IMAP"),
    YahooProvider.name: _mailbox("YAHOO"),
    POP3Provider.name: _mailbox("POP3"),
    GoogleProvider.name: {"GOOGLE_EMAIL_ACCESS_TOKEN", "GOOGLE_EMAIL_FROM_EMAIL"},
    MicrosoftProvider.name: {
        "MICROSOFT_EMAIL_ACCESS_TOKEN",
        "MICROSOFT_EMAIL_FROM_EMAIL",
        "MICROSOFT_EMAIL_API_URL",
    },
    ProxmoxMailGatewayProvider.name: {
        "PMG_FROM_EMAIL",
        "PMG_SMTP_HOST",
        "PMG_SMTP_PORT",
        "PMG_SMTP_USERNAME",
        "PMG_SMTP_PASSWORD",
        "PMG_SMTP_USE_TLS",
        "PMG_API_URL",
        "PMG_API_TOKEN",
        "PMG_API_NODE",
        "PMG_API_TLS_VERIFY",
    },
}
PROVIDERS = [
    SendgridProvider,
    Smtp2goProvider,
    StalwartProvider,
    MailgunProvider,
    IMAPProvider,
    YahooProvider,
    POP3Provider,
    GoogleProvider,
    MicrosoftProvider,
    ProxmoxMailGatewayProvider,
]
IDS = [provider.name for provider in PROVIDERS]
# Every setting that holds a credential: stored encrypted, never returned.
CREDENTIALS = {
    SIGNING_SECRET_SETTING,
    "api_key",
    "password",
    "smtp_password",
    "api_token",
    "access_token",
    "inbound_password",
}


def env_backed(settings: Tuple[InstanceSetting, ...]) -> List[InstanceSetting]:
    """The settings an operator's environment variable backs."""
    return [declared for declared in settings if declared.env]


def operator_value(declared: InstanceSetting) -> str:
    return f"operator-{declared.key}"


@pytest.mark.parametrize("provider", PROVIDERS, ids=IDS)
class TestDeclaredSettings:
    def test_the_variables_it_read_are_its_settings_defaults(self, provider):
        variables = {
            declared.env for declared in env_backed(provider.instance_settings)
        }
        assert variables == OPERATOR_VARIABLES[provider.name]
        # The framework seeds the operator's instance from these.
        assert variables <= set(provider._env)

    def test_credentials_are_secret_and_nothing_else_is(self, provider):
        secret = {d.key for d in provider.instance_settings if d.secret}
        declared = {d.key for d in provider.instance_settings}
        assert secret == declared & CREDENTIALS
        assert secret - {SIGNING_SECRET_SETTING}, "the provider has a credential"

    def test_it_sends_from_a_declared_address(self, provider):
        assert provider.instance_setting(FROM_EMAIL_SETTING).env

    def test_no_setting_is_declared_twice(self, provider):
        keys = [declared.key for declared in provider.instance_settings]
        assert len(keys) == len(set(keys))


def test_the_extension_declares_no_mail_settings_of_its_own():
    """Mail is sent through provider instances: the extension used to declare
    SendGrid's, Stalwart's and SMTP2go's variables, and EMAIL_PROVIDER,
    SMTP_SERVER and IMAP_SERVER, none of which anything read."""
    provider_variables: Set[str] = set().union(*OPERATOR_VARIABLES.values())
    legacy = {"EMAIL_PROVIDER", "SMTP_SERVER", "SMTP_PORT", "IMAP_SERVER"}
    assert not set(EXT_EMail._env) & (provider_variables | legacy)
    assert not hasattr(EXT_EMail, "env")


class TestWhoseEnvironment(ExtensionServerMixin):
    extension_class = EXT_EMail

    @pytest.fixture
    def operator_environment(self, set_env) -> None:
        """Every provider's variables set, each to a value naming its
        setting."""
        for provider in PROVIDERS:
            for declared in env_backed(provider.instance_settings):
                set_env(declared.env or "", operator_value(declared))

    @pytest.mark.parametrize("provider", PROVIDERS, ids=IDS)
    @pytest.mark.parametrize("scope", ["root", "system"])
    def test_the_operators_instance_defaults_to_the_environment(
        self, model_registry, operator_environment, provider, scope
    ):
        instance = email_instance(model_registry, provider.name, scope=scope)
        for declared in env_backed(provider.instance_settings):
            assert provider.setting(instance, declared.key) == operator_value(declared)

    def test_the_seeded_root_instance_defaults_to_the_environment(
        self, model_registry, operator_environment
    ):
        """The framework seeds Root_<Provider> root-scoped: it is the
        operator's."""
        instance = ProviderInstanceModel.model_validate(
            ProviderInstanceManager(
                model_registry=model_registry, requester_id=env("ROOT_ID")
            ).get(name=root_instance_name(Smtp2goProvider.name)),
            from_attributes=True,
        )
        assert instance.scope == "root"
        assert Smtp2goProvider.setting(instance, "api_key") == "operator-api_key"

    def test_a_user_scoped_instance_root_made_never_reads_the_environment(
        self, model_registry, operator_environment
    ):
        """Only the scope makes an instance the operator's: one ROOT made
        for no user or team in the user scope (as Root_<Provider> once was)
        no longer borrows the operator's credentials."""
        instance = email_instance(model_registry, Smtp2goProvider.name, scope="user")
        assert instance.user_id is None and instance.team_id is None
        assert Smtp2goProvider.setting(instance, "api_key") is None

    @pytest.mark.parametrize("provider", PROVIDERS, ids=IDS)
    def test_a_users_instance_never_reads_the_operators_environment(
        self, model_registry, operator_environment, admin_a, provider
    ):
        instance = email_instance(
            model_registry, provider.name, requester_id=admin_a.id, scope="user"
        )
        for declared in env_backed(provider.instance_settings):
            assert provider.setting(instance, declared.key) == declared.default

    @pytest.mark.parametrize("provider", PROVIDERS, ids=IDS)
    def test_a_teams_instance_never_reads_the_operators_environment(
        self, model_registry, operator_environment, admin_a, team_a, provider
    ):
        instance = email_instance(
            model_registry,
            provider.name,
            requester_id=admin_a.id,
            scope="team",
            team_id=team_a.id,
        )
        for declared in env_backed(provider.instance_settings):
            assert provider.setting(instance, declared.key) == declared.default

    def test_an_instances_own_settings_come_before_the_environment(
        self, model_registry, operator_environment
    ):
        instance = email_instance(
            model_registry,
            Smtp2goProvider.name,
            {FROM_EMAIL_SETTING: "desk@example.org"},
            api_key="instance-key",
        )
        assert Smtp2goProvider.setting(instance, "api_key") == "instance-key"
        assert Smtp2goProvider.setting(instance, FROM_EMAIL_SETTING) == (
            "desk@example.org"
        )
        assert Smtp2goProvider.setting(instance, "api_url") == "operator-api_url"

    def test_a_users_account_bonds_with_its_own_credentials_only(
        self, model_registry, set_env, admin_a
    ):
        """Bonding used to fill a user's missing credentials from the
        environment: an instance with no password signed in as the
        operator's Stalwart account."""
        set_env("STALWART_HOST", "mail.example.org")
        set_env("STALWART_USERNAME", "operator")
        set_env("STALWART_PASSWORD", "operator-password")
        mine = {"host": "smtp.example.net", "username": "alice"}
        without_password = email_instance(
            model_registry, "stalwart", mine, requester_id=admin_a.id, scope="user"
        )
        assert StalwartProvider.bond_instance(without_password) is None

        with_password = email_instance(
            model_registry,
            "stalwart",
            {**mine, "password": "alice-password"},
            requester_id=admin_a.id,
            scope="user",
        )
        bonded = StalwartProvider.bond_instance(with_password)
        assert bonded is not None
        assert (bonded.sdk["host"], bonded.sdk["username"]) == (
            "smtp.example.net",
            "alice",
        )
        assert bonded.sdk["password"] == "alice-password"


class TestWhatAnInstanceMayReach(ExtensionServerMixin):
    """A user's mail servers and API addresses are held to the SSRF guard
    now that the user sets them; the operator's are not."""

    extension_class = EXT_EMail

    LOOPBACK_API = "http://127.0.0.1:9/v3"

    @pytest.fixture(autouse=True)
    def guarded(self, monkeypatch) -> None:
        monkeypatch.delenv("EGRESS_ALLOWED_HOSTS", raising=False)
        monkeypatch.delenv("DISABLE_SSRF_GUARD", raising=False)

    def _mailgun(self, model_registry: Any, **owner: Any) -> ProviderInstanceModel:
        return email_instance(
            model_registry,
            "mailgun",
            {"domain": "mg.example.org", "api_url": self.LOOPBACK_API},
            api_key="key-1",
            **owner,
        )

    def test_a_users_api_address_on_the_servers_network_is_refused(
        self, model_registry, admin_a
    ):
        user = self._mailgun(model_registry, requester_id=admin_a.id, scope="user")
        assert MailgunProvider.bond_instance(user) is None

        operator = self._mailgun(model_registry)
        bonded = MailgunProvider.bond_instance(operator)
        assert bonded is not None and bonded.sdk["api_url"] == self.LOOPBACK_API

    def test_only_the_scope_lets_an_instance_reach_the_servers_network(
        self, model_registry
    ):
        """An instance ROOT made in the user scope, for no user or team, is
        not the operator's: it is held to the guard like any user's."""
        unowned = self._mailgun(model_registry, scope="user")
        assert unowned.user_id is None and unowned.team_id is None
        assert MailgunProvider.bond_instance(unowned) is None
        system = self._mailgun(model_registry, scope="system")
        assert MailgunProvider.bond_instance(system) is not None

    def test_a_users_mail_server_on_the_servers_network_is_refused(
        self, model_registry, admin_a
    ):
        settings = {
            "imap_host": "127.0.0.1",
            "username": "alice",
            "password": "alice-password",
        }
        user = email_instance(
            model_registry, "imap", settings, requester_id=admin_a.id, scope="user"
        )
        assert IMAPProvider.bond_instance(user) is None
        operator = email_instance(model_registry, "imap", settings)
        assert IMAPProvider.bond_instance(operator) is not None

    def test_an_allowed_host_is_reached(self, model_registry, admin_a, monkeypatch):
        monkeypatch.setenv("EGRESS_ALLOWED_HOSTS", "127.0.0.1:9")
        user = self._mailgun(model_registry, requester_id=admin_a.id, scope="user")
        assert MailgunProvider.bond_instance(user) is not None
