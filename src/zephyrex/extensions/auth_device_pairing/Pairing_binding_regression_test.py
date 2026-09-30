# SPDX-License-Identifier: AGPL-3.0-or-later
"""An approved pairing's session reaches only the device that requested it.

Before the binding, GET /v1/auth/pairing/{id}/status handed the approved
session's bearer token to anyone holding the pairing id: no credential, no
proof of being the requesting device. The id is not a secret (the approver
gets it back, it sits in logs and URLs), so it must never be enough.
"""

from fastapi.testclient import TestClient

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.auth_device_pairing.BLL_Auth_DevicePairing import (
    PAIRING_COOKIE,
)
from zephyrex.extensions.auth_device_pairing.EXT_Auth_DevicePairing import (
    EXT_Auth_DevicePairing,
)
from zephyrex.lib.SessionCookies import CSRF_COOKIE, SESSION_COOKIE

_PAIRING = "/v1/auth/pairing"


def _raw_token(qr_payload: str) -> str:
    return qr_payload.split("?token=", 1)[-1]


class TestPairingBinding(ExtensionServerMixin):
    extension_class = EXT_Auth_DevicePairing

    def _device(self, server) -> TestClient:
        # Secure cookies only travel over https.
        return TestClient(server.app, base_url="https://testserver")

    def _request(self, device: TestClient, **extra) -> dict:
        response = device.post(
            f"{_PAIRING}/request",
            json={"requesting_device_type": "web", **extra},
        )
        assert response.status_code == 200, response.text
        body: dict = response.json()
        return body

    def _approve(self, server, admin, qr_payload: str) -> dict:
        response = server.post(
            f"{_PAIRING}/approve",
            json={"token": _raw_token(qr_payload)},
            headers={"Authorization": f"Bearer {admin.jwt}"},
        )
        assert response.status_code == 200, response.text
        body: dict = response.json()
        return body

    def _status(self, device: TestClient, pairing_id: str):
        return device.get(f"{_PAIRING}/{pairing_id}/status")

    def test_request_binds_the_pairing_to_the_requesting_browser(self, server):
        device = self._device(server)
        response = device.post(
            f"{_PAIRING}/request", json={"requesting_device_type": "web"}
        )
        binding = next(
            c
            for c in response.headers.get_list("set-cookie")
            if c.startswith(f"{PAIRING_COOKIE}=")
        ).lower()
        for attribute in ("httponly", "secure", "samesite=lax", f"path={_PAIRING}"):
            assert attribute in binding, binding
        assert "binding" not in response.json() or response.json()["binding"] is None

    def test_the_pairing_id_alone_never_yields_a_session(self, server, admin_a):
        requester = self._device(server)
        pairing = self._request(requester)
        approved = self._approve(server, admin_a, pairing["qr_payload"])
        assert "session_key" not in approved

        stranger = self._device(server)
        stolen = self._status(stranger, pairing["pairing_id"])
        assert stolen.status_code == 200
        assert stolen.json()["state"] == "approved"
        assert stolen.json().get("token") is None
        assert "session_key" not in stolen.json()
        assert SESSION_COOKIE not in stranger.cookies

        # The stranger's read consumed nothing: the requester still signs in.
        delivered = self._status(requester, pairing["pairing_id"])
        assert delivered.json()["user_id"] == admin_a.id
        assert delivered.json().get("token") is None
        assert SESSION_COOKIE in requester.cookies
        assert CSRF_COOKIE in requester.cookies
        assert PAIRING_COOKIE not in requester.cookies

        me = requester.get("/v1/user")
        assert me.status_code == 200, me.text

    def test_the_session_is_delivered_once(self, server, admin_a):
        requester = self._device(server)
        pairing = self._request(requester)
        self._approve(server, admin_a, pairing["qr_payload"])
        binding = requester.cookies[PAIRING_COOKIE]

        assert self._status(requester, pairing["pairing_id"]).status_code == 200
        assert SESSION_COOKIE in requester.cookies

        replay = self._device(server)
        replay.cookies.set(PAIRING_COOKIE, binding, path=_PAIRING)
        again = self._status(replay, pairing["pairing_id"])
        assert again.json()["state"] == "approved"
        assert again.json().get("token") is None
        assert SESSION_COOKIE not in replay.cookies

    def test_a_forged_binding_yields_nothing(self, server, admin_a):
        requester = self._device(server)
        pairing = self._request(requester)
        self._approve(server, admin_a, pairing["qr_payload"])

        forger = self._device(server)
        forger.cookies.set(PAIRING_COOKIE, "guessed", path=_PAIRING)
        assert self._status(forger, pairing["pairing_id"]).json().get("token") is None
        assert SESSION_COOKIE not in forger.cookies

    def test_a_pending_read_consumes_nothing(self, server, admin_a):
        requester = self._device(server)
        pairing = self._request(requester)
        pending = self._status(requester, pairing["pairing_id"])
        assert pending.json()["state"] == "pending"
        assert SESSION_COOKIE not in requester.cookies

        self._approve(server, admin_a, pairing["qr_payload"])
        self._status(requester, pairing["pairing_id"])
        assert SESSION_COOKIE in requester.cookies

    def test_a_native_client_opts_in_to_a_bearer_token(self, server, admin_a):
        native = self._device(server)
        pairing = self._request(native, token_in_body=True)
        assert pairing["binding"]
        self._approve(server, admin_a, pairing["qr_payload"])

        # A native client presents its binding itself, cookie jar or not.
        native.cookies.clear()
        response = native.get(
            f"{_PAIRING}/{pairing['pairing_id']}/status",
            headers={"Cookie": f"{PAIRING_COOKIE}={pairing['binding']}"},
        )
        token = response.json()["token"]
        assert token
        assert SESSION_COOKIE not in native.cookies
        me = self._device(server).get(
            "/v1/user", headers={"Authorization": f"Bearer {token}"}
        )
        assert me.status_code == 200, me.text
