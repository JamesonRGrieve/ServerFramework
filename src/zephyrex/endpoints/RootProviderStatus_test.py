# SPDX-License-Identifier: AGPL-3.0-or-later
"""GET /v1/provider/root/status: environment configuration of every loaded
provider, root only, with secret values never returned."""

import os
from typing import Any, Dict, List, Tuple

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.email.EXT_EMail import EXT_EMail

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
            assert set(provider) == {"provider", "extension", "configured", "settings"}
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

    def test_plain_values_are_returned(self, server, monkeypatch):
        provider, setting = _setting(server, secret=False)
        monkeypatch.setenv(setting["key"], "plain-visible-value")

        assert _reported(server, provider["provider"], setting["key"]) == {
            "key": setting["key"],
            "secret": False,
            "set": True,
            "value": "plain-visible-value",
        }
