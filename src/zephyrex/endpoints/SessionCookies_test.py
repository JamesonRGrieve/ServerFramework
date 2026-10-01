# SPDX-License-Identifier: AGPL-3.0-or-later
"""Browser sessions end to end: password login sets the session cookies,
the cookie alone authenticates, cookie-authenticated writes need the CSRF
token, and logout revokes the session and clears both cookies."""

import base64
import uuid
from typing import Any

from fastapi.testclient import TestClient

from zephyrex.lib.SessionCookies import CSRF_COOKIE, SESSION_COOKIE
from zephyrex.testing.factories import TEST_PASSWORD

PASSWORD = TEST_PASSWORD


def _browser(server: Any) -> TestClient:
    # A fresh cookie jar; Secure cookies only travel over https.
    return TestClient(server.app, base_url="https://testserver")


def _signed_in(server: Any) -> TestClient:
    from conftest import create_user

    user = create_user(
        server, email=f"cookie_{uuid.uuid4().hex[:8]}@example.com", password=PASSWORD
    )
    browser = _browser(server)
    credentials = base64.b64encode(f"{user.email}:{PASSWORD}".encode()).decode()
    login = browser.post(
        "/v1/user/authorize", headers={"Authorization": f"Basic {credentials}"}
    )
    assert login.status_code == 200, login.text
    assert browser.cookies[SESSION_COOKIE] == login.json()["token"]
    assert browser.cookies[CSRF_COOKIE]
    return browser


def test_the_session_cookie_authenticates_reads(server):
    browser = _signed_in(server)
    me = browser.get("/v1/user")
    assert me.status_code == 200, me.text


def test_cookie_writes_need_the_csrf_token(server):
    browser = _signed_in(server)
    body = {"user": {"display_name": "Cookie Person"}}

    refused = browser.put("/v1/user", json=body)
    assert refused.status_code == 403, refused.text

    allowed = browser.put(
        "/v1/user", json=body, headers={"X-CSRF-Token": browser.cookies[CSRF_COOKIE]}
    )
    assert allowed.status_code == 200, allowed.text


def test_logout_revokes_the_session_and_clears_the_cookies(server):
    browser = _signed_in(server)
    token = browser.cookies[SESSION_COOKIE]

    logout = browser.post(
        "/v1/user/logout", headers={"X-CSRF-Token": browser.cookies[CSRF_COOKIE]}
    )
    assert logout.status_code == 204, logout.text
    assert SESSION_COOKIE not in browser.cookies
    assert CSRF_COOKIE not in browser.cookies

    replayed = _browser(server).get(
        "/v1/user", headers={"Authorization": f"Bearer {token}"}
    )
    assert replayed.status_code == 401, replayed.text


def test_a_revoked_session_clears_the_browser_cookies(server):
    """The session is ended elsewhere (another device logs it out): the
    browser's next request is a 401 and its cookies are cleared."""
    browser = _signed_in(server)
    token = browser.cookies[SESSION_COOKIE]
    ended = _browser(server).post(
        "/v1/user/logout", headers={"Authorization": f"Bearer {token}"}
    )
    assert ended.status_code == 204, ended.text

    stale = browser.get("/v1/user")
    assert stale.status_code == 401, stale.text
    assert SESSION_COOKIE not in browser.cookies
    assert CSRF_COOKIE not in browser.cookies


def test_an_explicit_bearer_ignores_the_cookie(server):
    browser = _signed_in(server)
    # A bad explicit credential is not rescued by a good cookie.
    response = browser.get("/v1/user", headers={"Authorization": "Bearer not-a-jwt"})
    assert response.status_code == 401, response.text
