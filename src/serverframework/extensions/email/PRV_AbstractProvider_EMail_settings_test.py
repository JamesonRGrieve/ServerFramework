"""Item 90 — typed Settings model tests for the email providers.

Covers:
* SendGrid Settings.from_env builds a valid model with SecretStr api_key.
* Missing required env vars raise a clear validation error naming the field.
* is_configured returns the right True/False on its env-map argument.
* Reading the legacy `_env` dict triggers a DeprecationWarning.
* The startup-banner helper logs which providers are configured / not.
"""

from __future__ import annotations

import logging
import warnings

import pytest
from pydantic import SecretStr, ValidationError

from serverframework.extensions.email.EXT_EMail import (
    AbstractEmailProvider,
    iter_configured_email_providers,
    validate_email_provider_settings_at_startup,
)
from serverframework.extensions.email.PRV_SendGrid_EMail import (
    SendgridProvider,
    Smtp2goProvider,
    StalwartProvider,
)


class TestSendgridSettings:
    def test_from_env_builds_valid_model_with_secretstr(self):
        env = {
            "SENDGRID_FROM_EMAIL": "x@y.z",
            "SENDGRID_API_KEY": "sk_test_xxx",
        }
        s = SendgridProvider.Settings.from_env(env)
        assert str(s.from_email) == "x@y.z"
        assert isinstance(s.api_key, SecretStr)
        # SecretStr redaction: print/str must NOT leak the raw key.
        assert str(s.api_key) == "**********"
        assert "sk_test_xxx" not in repr(s)
        # Reveal only via the explicit accessor.
        assert s.api_key.get_secret_value() == "sk_test_xxx"

    def test_missing_required_field_raises_clear_validation_error(self):
        # `from_email` missing — the error row must name the field so a
        # startup admin can fix the env file rather than guess.
        with pytest.raises(ValidationError) as exc:
            SendgridProvider.Settings.from_env(
                {"SENDGRID_API_KEY": "sk_test_xxx"}
            )
        rows = exc.value.errors()
        missing_fields = {tuple(r["loc"]) for r in rows}
        assert ("from_email",) in missing_fields

    def test_is_configured_true_when_all_required_present(self):
        env = {
            "SENDGRID_FROM_EMAIL": "x@y.z",
            "SENDGRID_API_KEY": "sk_test_xxx",
        }
        assert SendgridProvider.Settings.is_configured(env) is True

    def test_is_configured_false_when_required_missing(self):
        assert SendgridProvider.Settings.is_configured({}) is False
        assert (
            SendgridProvider.Settings.is_configured(
                {"SENDGRID_FROM_EMAIL": "x@y.z"}
            )
            is False
        )

    def test_is_configured_false_for_empty_string_value(self):
        env = {"SENDGRID_FROM_EMAIL": "x@y.z", "SENDGRID_API_KEY": "   "}
        assert SendgridProvider.Settings.is_configured(env) is False


class TestStalwartSettings:
    def test_from_env_with_defaults(self):
        env = {
            "STALWART_FROM_EMAIL": "from@example.com",
            "STALWART_HOST": "mail.example.com",
            "STALWART_USERNAME": "user",
            "STALWART_PASSWORD": "secret-pass",
        }
        s = StalwartProvider.Settings.from_env(env)
        assert s.host == "mail.example.com"
        assert s.port == 587
        assert s.use_tls is True
        assert isinstance(s.password, SecretStr)
        assert s.password.get_secret_value() == "secret-pass"
        assert "secret-pass" not in repr(s)

    def test_is_configured_false_without_password(self):
        env = {
            "STALWART_FROM_EMAIL": "from@example.com",
            "STALWART_HOST": "mail.example.com",
            "STALWART_USERNAME": "user",
        }
        assert StalwartProvider.Settings.is_configured(env) is False

    def test_explicit_port_and_use_tls_parsed(self):
        env = {
            "STALWART_FROM_EMAIL": "from@example.com",
            "STALWART_HOST": "mail.example.com",
            "STALWART_USERNAME": "user",
            "STALWART_PASSWORD": "p",
            "STALWART_PORT": "2525",
            "STALWART_USE_TLS": "false",
        }
        s = StalwartProvider.Settings.from_env(env)
        assert s.port == 2525
        assert s.use_tls is False


class TestSmtp2goSettings:
    def test_from_env_with_default_api_url(self):
        env = {
            "SMTP2GO_FROM_EMAIL": "a@b.com",
            "SMTP2GO_API_KEY": "api-secret",
        }
        s = Smtp2goProvider.Settings.from_env(env)
        assert isinstance(s.api_key, SecretStr)
        assert s.api_key.get_secret_value() == "api-secret"
        # HttpUrl carries a trailing slash by default; just ensure host present.
        assert "smtp2go.com" in str(s.api_url)

    def test_explicit_api_url_overrides_default(self):
        env = {
            "SMTP2GO_FROM_EMAIL": "a@b.com",
            "SMTP2GO_API_KEY": "k",
            "SMTP2GO_API_URL": "https://custom.example.com/api",
        }
        s = Smtp2goProvider.Settings.from_env(env)
        assert "custom.example.com" in str(s.api_url)

    def test_invalid_api_url_raises(self):
        env = {
            "SMTP2GO_FROM_EMAIL": "a@b.com",
            "SMTP2GO_API_KEY": "k",
            "SMTP2GO_API_URL": "not a url",
        }
        with pytest.raises(ValidationError):
            Smtp2goProvider.Settings.from_env(env)


class TestDeprecationOfEnvDict:
    def test_reading_legacy_env_dict_warns(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            _ = SendgridProvider._env["SENDGRID_API_KEY"]
        deprecations = [
            w for w in caught if issubclass(w.category, DeprecationWarning)
        ]
        assert deprecations, "expected DeprecationWarning on `_env` read"
        assert any("Settings.from_env" in str(w.message) for w in deprecations)

    def test_iterating_legacy_env_dict_warns(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            list(StalwartProvider._env.keys())
        deprecations = [
            w for w in caught if issubclass(w.category, DeprecationWarning)
        ]
        assert deprecations


class TestStartupBanner:
    def test_banner_logs_configured_and_not(self, caplog):
        env = {
            "SENDGRID_FROM_EMAIL": "x@y.z",
            "SENDGRID_API_KEY": "sk_test",
        }
        # Capture loguru via the propagation shim if available; otherwise
        # rely on the returned dict for a deterministic assertion.
        with caplog.at_level(logging.INFO):
            report = validate_email_provider_settings_at_startup(env)
        assert "sendgrid" in report
        assert report["sendgrid"] is True
        # smtp2go / stalwart present and reported as not configured.
        assert "smtp2go" in report and report["smtp2go"] is False
        assert "stalwart" in report and report["stalwart"] is False

    def test_iter_configured_returns_only_ready_providers(self):
        env = {
            "SENDGRID_FROM_EMAIL": "x@y.z",
            "SENDGRID_API_KEY": "sk_test",
        }
        configured = iter_configured_email_providers(env)
        names = {getattr(p, "name", p.__name__) for p in configured}
        assert "sendgrid" in names
        assert "smtp2go" not in names
        assert "stalwart" not in names


class TestAbstractSettingsContract:
    def test_abstract_settings_has_default_provider_name_optional(self):
        info = AbstractEmailProvider.Settings.model_fields["default_provider_name"]
        assert info.is_required() is False

    def test_abstract_env_field_map_defaults_empty(self):
        assert AbstractEmailProvider.Settings._env_field_map == {}
