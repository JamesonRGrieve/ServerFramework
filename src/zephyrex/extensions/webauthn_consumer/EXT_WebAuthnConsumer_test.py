# SPDX-License-Identifier: AGPL-3.0-or-later
"""The extension class: what it declares and how it reports its
configuration."""

from zephyrex.extensions.webauthn_consumer.EXT_WebAuthnConsumer import (
    EXT_WebAuthnConsumer,
)
from zephyrex.extensions.webauthn_consumer.RelyingParty import SETTINGS


def test_it_declares_its_abilities() -> None:
    assert EXT_WebAuthnConsumer.get_abilities() == {
        "webauthn_consumer_list_credentials",
        "webauthn_consumer_remove_credential",
    }


def test_it_requires_py_webauthn_and_sessions() -> None:
    assert EXT_WebAuthnConsumer.pip_requirements() == ["webauthn>=3.0.1"]
    assert [
        (dep.name, dep.optional) for dep in EXT_WebAuthnConsumer.dependencies.ext
    ] == [("auth_session", False)]


def test_its_settings_are_the_relying_party_settings() -> None:
    assert EXT_WebAuthnConsumer._env == dict(SETTINGS)


def test_an_unconfigured_server_reports_what_is_missing(set_env) -> None:
    set_env("WEBAUTHN_CONSUMER_RP_ID", "")
    set_env("WEBAUTHN_CONSUMER_ORIGINS", "")
    issues = " ".join(EXT_WebAuthnConsumer.validate_config())
    assert "WEBAUTHN_CONSUMER_RP_ID" in issues
    assert "WEBAUTHN_CONSUMER_ORIGINS" in issues
