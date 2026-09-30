# SPDX-License-Identifier: AGPL-3.0-or-later
"""GET /v1/provider/root/status: environment configuration of every loaded
provider, root only, with secret values never returned."""

import os
from typing import Any, Dict, List, Tuple

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.AbstractExtensionProvider import HealthReport, HealthStatus
from zephyrex.extensions.email.EXT_EMail import EXT_EMail
from zephyrex.extensions.secret_vault.EXT_Secret_Vault import EXT_Secret_Vault

STATUS = "/v1/provider/root/status"


def _root() -> Dict[str, str]:
    return {"X-API-Key": os.environ["ROOT_API_KEY"]}


def _providers(server: Any) -> List[Dict[str, Any]]:
    response = server.get(STATUS, headers=_root())
    assert response.status_code == 200, response.text
    providers: List[Dict[str, Any]] = response.json()["providers"]
    return providers


def _setting(server: Any, secret: bool) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    return next(
        (p, s)
        for p in _providers(server)
        for s in p["settings"]
        if s["secret"] is secret
    )


def _reported(server: Any, provider: str, key: str) -> Dict[str, Any]:
    return next(
        s
        for p in _providers(server)
        if p["provider"] == provider
        for s in p["settings"]
        if s["key"] == key
    )


def _email_provider(server: Any) -> Any:
    """One provider class the email extension loaded into this app."""
    registry = server.app.state.model_registry.extension_registry
    return registry.extension_providers["email"][0]


class TestRootProviderStatus(ExtensionServerMixin):
    """Against an app that loads the email extension and its providers."""

    extension_class = EXT_EMail

    def test_non_root_is_refused(self, server, admin_a):
        response = server.get(
            STATUS, headers={"Authorization": f"Bearer {admin_a.jwt}"}
        )
        assert response.status_code == 403, response.text

    def test_lists_loaded_providers_with_their_settings(self, server):
        providers = _providers(server)
        assert {p["extension"] for p in providers} >= {"email"}
        for provider in providers:
            assert set(provider) == {
                "provider",
                "extension",
                "configured",
                "settings",
                "health",
            }
            assert provider["health"] is None
            for setting in provider["settings"]:
                assert set(setting) == {"key", "secret", "set", "value"}

    def test_secret_values_are_reported_only_as_set(self, server, monkeypatch):
        provider, setting = _setting(server, secret=True)
        secret_value = "zx-status-probe-secret-value"
        monkeypatch.setenv(setting["key"], secret_value)

        response = server.get(STATUS, headers=_root())
        assert response.status_code == 200, response.text
        assert secret_value not in response.text

        reported = _reported(server, provider["provider"], setting["key"])
        assert reported["set"] is True
        assert reported["value"] is None

    def test_extension_narrows_the_list(self, server):
        response = server.get(STATUS, params={"extension": "email"}, headers=_root())
        providers = response.json()["providers"]
        assert providers
        assert {p["extension"] for p in providers} == {"email"}

        none = server.get(STATUS, params={"extension": "nonesuch"}, headers=_root())
        assert none.json() == {"providers": []}

    def test_health_is_checked_only_when_asked(self, server, monkeypatch):
        provider_cls = _email_provider(server)
        calls: List[str] = []

        def counted(cls: Any) -> HealthReport:
            calls.append(cls.name)
            return HealthReport(HealthStatus.DEGRADED, detail="probe")

        monkeypatch.setattr(provider_cls, "health_check", classmethod(counted))
        monkeypatch.setattr(provider_cls, "_cached_health", None)

        server.get(STATUS, params={"extension": "email"}, headers=_root())
        assert calls == []

        checked = server.get(
            STATUS, params={"extension": "email", "health": "true"}, headers=_root()
        )
        reported = next(
            p for p in checked.json()["providers"] if p["provider"] == provider_cls.name
        )
        assert reported["health"] == {"status": "degraded", "detail": "probe"}
        assert calls == [provider_cls.name]

    def test_a_health_check_that_raises_reports_down(self, server, monkeypatch):
        provider_cls = _email_provider(server)

        def failing(cls: Any) -> HealthReport:
            raise RuntimeError("upstream exploded with a secret-ish message")

        monkeypatch.setattr(provider_cls, "health_check", classmethod(failing))
        monkeypatch.setattr(provider_cls, "_cached_health", None)

        response = server.get(
            STATUS, params={"extension": "email", "health": "true"}, headers=_root()
        )
        assert "secret-ish" not in response.text
        reported = next(
            p
            for p in response.json()["providers"]
            if p["provider"] == provider_cls.name
        )
        assert reported["health"] == {
            "status": "down",
            "detail": "health check failed: RuntimeError",
        }

    def test_plain_values_are_returned(self, server, monkeypatch):
        provider, setting = _setting(server, secret=False)
        monkeypatch.setenv(setting["key"], "plain-visible-value")

        assert _reported(server, provider["provider"], setting["key"]) == {
            "key": setting["key"],
            "secret": False,
            "set": True,
            "value": "plain-visible-value",
        }


class TestSecretVaultStatus(ExtensionServerMixin):
    """The secret-vault admin page reads ?extension=secret_vault&health=true."""

    extension_class = EXT_Secret_Vault

    def test_openbao_reports_its_settings_and_health(self, server, monkeypatch):
        monkeypatch.setenv("OPENBAO_ADDR", "https://bao.example.invalid:8200")
        monkeypatch.setenv("OPENBAO_TOKEN", "zx-probe-vault-token")
        monkeypatch.setenv("OPENBAO_MOUNT_POINT", "kv")
        registry = server.app.state.model_registry.extension_registry
        (openbao,) = registry.extension_providers["secret_vault"]
        monkeypatch.setattr(openbao, "_cached_health", None)

        response = server.get(
            STATUS,
            params={"extension": "secret_vault", "health": "true"},
            headers=_root(),
        )
        assert response.status_code == 200, response.text
        assert "zx-probe-vault-token" not in response.text
        (provider,) = response.json()["providers"]
        assert provider["provider"] == "openbao"
        settings = {s["key"]: s for s in provider["settings"]}
        assert settings["OPENBAO_ADDR"]["value"] == "https://bao.example.invalid:8200"
        assert settings["OPENBAO_MOUNT_POINT"]["value"] == "kv"
        assert settings["OPENBAO_TOKEN"] == {
            "key": "OPENBAO_TOKEN",
            "secret": True,
            "set": True,
            "value": None,
        }
        # The address does not resolve: a live check, reported, not raised.
        assert provider["health"]["status"] in ("down", "degraded")
