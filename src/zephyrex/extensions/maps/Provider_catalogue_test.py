# SPDX-License-Identifier: AGPL-3.0-or-later
"""The settings catalogue marks a value that spans lines (Apple's PEM
private key), so a client offers a text area for it."""

from typing import Any, Dict

import pytest

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.maps.EXT_Maps import EXT_Maps
from zephyrex.extensions.maps.PRV_Apple import PRV_Apple_Maps


class TestCatalogue(ExtensionServerMixin):
    extension_class = EXT_Maps

    @pytest.fixture
    def headers(self, admin_a) -> Dict[str, str]:
        return {"Authorization": f"Bearer {admin_a.jwt}"}

    @pytest.fixture
    def apple(self, server, headers) -> Dict[str, Any]:
        providers = server.get("/v1/provider", headers=headers).json()["providers"]
        return next(p for p in providers if p["name"] == PRV_Apple_Maps.name)

    def test_a_pem_key_is_multiline(self, server, headers, apple):
        response = server.get(f"/v1/provider/{apple['id']}/settings", headers=headers)
        assert response.status_code == 200, response.text
        settings = {s["key"]: s for s in response.json()["settings"]}
        assert settings["private_key"]["multiline"] is True
        assert settings["private_key"]["write_only"] is True
        assert settings["team_id"]["multiline"] is False
