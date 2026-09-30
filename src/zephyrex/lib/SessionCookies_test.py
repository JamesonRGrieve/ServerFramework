# SPDX-License-Identifier: AGPL-3.0-or-later
"""SessionCookieMiddleware against a bare app that echoes the Authorization
header it receives, and the cookie helpers' attributes."""

import pytest
from fastapi import FastAPI, HTTPException, Request, Response, WebSocket
from fastapi.testclient import TestClient

from zephyrex.lib.Environment import refresh_settings

from zephyrex.lib.SessionCookies import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    SessionCookieMiddleware,
    clear_session_cookies,
    set_session_cookies,
)

MAX_AGE = 3600


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(SessionCookieMiddleware)

    @app.api_route("/echo", methods=["GET", "POST", "DELETE"])
    def echo(request: Request):
        return {"authorization": request.headers.get("authorization")}

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        await websocket.accept()
        await websocket.send_json(
            {"authorization": websocket.headers.get("authorization")}
        )
        await websocket.close()

    @app.get("/stale")
    def stale():
        raise HTTPException(status_code=401, detail="Session revoked")

    @app.post("/login")
    def login(response: Response):
        set_session_cookies(response, "session-jwt", MAX_AGE)
        return {}

    @app.post("/logout")
    def logout(response: Response):
        clear_session_cookies(response)
        return {}

    return app


@pytest.fixture
def client():
    # Secure cookies are only sent back over https.
    return TestClient(_app(), base_url="https://testserver")


def _logged_in(client) -> str:
    assert client.post("/login").status_code == 200
    csrf: str = client.cookies[CSRF_COOKIE]
    return csrf


def test_login_sets_both_cookies_with_the_agreed_attributes(client):
    response = client.post("/login")
    set_cookies = response.headers.get_list("set-cookie")
    session = next(c for c in set_cookies if c.startswith(f"{SESSION_COOKIE}="))
    csrf = next(c for c in set_cookies if c.startswith(f"{CSRF_COOKIE}="))
    for cookie in (session, csrf):
        lowered = cookie.lower()
        for attribute in ("secure", "samesite=lax", "path=/", f"max-age={MAX_AGE}"):
            assert attribute in lowered, cookie
    assert "httponly" in session.lower()
    assert "httponly" not in csrf.lower()


def test_cookie_authenticates_safe_requests(client):
    _logged_in(client)
    assert client.get("/echo").json() == {"authorization": "Bearer session-jwt"}


def test_no_cookie_no_header(client):
    assert client.get("/echo").json() == {"authorization": None}


@pytest.mark.parametrize("header", ["Authorization", "X-API-Key"])
def test_explicit_credentials_win_over_the_cookie(client, header):
    _logged_in(client)
    response = client.post("/echo", headers={header: "Bearer explicit"})
    assert response.status_code == 200
    expected = "Bearer explicit" if header == "Authorization" else None
    assert response.json() == {"authorization": expected}


@pytest.mark.parametrize("method", ["post", "delete"])
def test_cookie_mutations_need_the_csrf_token(client, method):
    csrf = _logged_in(client)
    send = getattr(client, method)

    assert send("/echo").status_code == 403
    assert send("/echo", headers={"X-CSRF-Token": "forged"}).status_code == 403
    allowed = send("/echo", headers={"X-CSRF-Token": csrf})
    assert allowed.status_code == 200
    assert allowed.json() == {"authorization": "Bearer session-jwt"}


def test_a_401_on_the_cookie_clears_both_cookies(client):
    _logged_in(client)
    assert client.get("/echo").status_code == 200
    assert SESSION_COOKIE in client.cookies

    response = client.get("/stale")
    assert response.status_code == 401
    assert SESSION_COOKIE not in client.cookies
    assert CSRF_COOKIE not in client.cookies


def test_a_401_to_an_explicit_credential_leaves_the_cookies(client):
    """Only a request the cookie authenticated says the cookie is stale."""
    _logged_in(client)
    response = client.get("/stale", headers={"Authorization": "Bearer other"})
    assert response.status_code == 401
    assert SESSION_COOKIE in client.cookies


def _socket_authorization(client, origin=None):
    headers = {"cookie": f"{SESSION_COOKIE}=session-jwt"}
    if origin is not None:
        headers["origin"] = origin
    with client.websocket_connect("/ws", headers=headers) as socket:
        return socket.receive_json()["authorization"]


def test_a_same_origin_socket_is_authenticated_by_the_cookie(client):
    assert _socket_authorization(client, "https://testserver") == "Bearer session-jwt"


@pytest.mark.parametrize(
    "origin", ["https://evil.example", None], ids=["cross-site", "no-origin"]
)
def test_a_cross_site_socket_is_not_authenticated_by_the_cookie(client, origin):
    """Cross-site WebSocket hijacking: another site's page opens a socket and
    the browser attaches the session cookie; the Origin check refuses it."""
    assert _socket_authorization(client, origin) is None


@pytest.mark.parametrize(
    "allowlist, authenticated",
    [("https://app.example", True), ("*", False)],
    ids=["exact-origin", "wildcard"],
)
def test_an_allowlisted_origin_is_authenticated(
    client, monkeypatch, allowlist, authenticated
):
    monkeypatch.setenv("APP_CORS_ALLOWED_ORIGINS", allowlist)
    refresh_settings()
    try:
        authorization = _socket_authorization(client, "https://app.example")
    finally:
        monkeypatch.delenv("APP_CORS_ALLOWED_ORIGINS")
        refresh_settings()
    assert (authorization == "Bearer session-jwt") is authenticated


def test_logout_clears_both_cookies(client):
    _logged_in(client)
    csrf = client.cookies[CSRF_COOKIE]
    client.post("/logout", headers={"X-CSRF-Token": csrf})
    assert SESSION_COOKIE not in client.cookies
    assert CSRF_COOKIE not in client.cookies
    assert client.get("/echo").json() == {"authorization": None}


def test_cookie_domain_comes_from_the_environment(monkeypatch, client):
    monkeypatch.setenv("SESSION_COOKIE_DOMAIN", "example.test")
    refresh_settings()
    try:
        set_cookies = client.post("/login").headers.get_list("set-cookie")
    finally:
        monkeypatch.delenv("SESSION_COOKIE_DOMAIN")
        refresh_settings()
    assert all("domain=example.test" in c.lower() for c in set_cookies)
