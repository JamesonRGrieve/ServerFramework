# SPDX-License-Identifier: AGPL-3.0-or-later
"""Browser sessions carried in cookies.

A login sets two cookies: ``zx_session`` holds the session JWT (HttpOnly, so
page scripts cannot read it) and ``zx_csrf`` a random token the page can
read. :class:`SessionCookieMiddleware` turns the session cookie into the
``Authorization: Bearer`` header every auth path already understands, but
only when the request carries neither ``Authorization`` nor ``X-API-Key``:
explicit credentials win. A cookie-authenticated request that can change
state must echo the CSRF cookie in ``X-CSRF-Token`` (double submit), or it
is refused with 403 before reaching the app.

A write with no session to protect (register, login by body, magic-link
or pairing request) carries no cookie to check, so the middleware also
refuses, 403, any non-safe request that has neither ``Authorization`` nor
``X-API-Key`` and whose ``Origin`` is not the app's own: such a request is
exactly what another site's page can send. Behind a proxy that rewrites
``Host``, list the public origin in ``APP_CORS_ALLOWED_ORIGINS``.

A WebSocket upgrade (GraphQL subscriptions) is authenticated by the cookie
too, but only from the app's own origin: the same host, or an exact origin in
``APP_CORS_ALLOWED_ORIGINS``. Browsers send cookies on cross-site WebSocket
upgrades and CORS does not apply to them, so without the Origin check any
site could open a socket as the signed-in user.
"""

import hmac
import json
import secrets
from http.cookies import CookieError, SimpleCookie
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from fastapi import Response

from zephyrex.lib.Environment import env

SESSION_COOKIE = "zx_session"
CSRF_COOKIE = "zx_csrf"
CSRF_HEADER = "x-csrf-token"
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_CSRF_TOKEN_BYTES = 32


def set_session_cookies(response: Response, token: str, max_age: int) -> None:
    """Hand the browser a session: the JWT, and a fresh CSRF token to echo."""
    for name, value, http_only in (
        (SESSION_COOKIE, token, True),
        (CSRF_COOKIE, secrets.token_urlsafe(_CSRF_TOKEN_BYTES), False),
    ):
        response.set_cookie(
            name,
            value,
            max_age=max_age,
            path="/",
            domain=env("SESSION_COOKIE_DOMAIN") or None,
            secure=True,
            httponly=http_only,
            samesite="lax",
        )


def clear_session_cookies(response: Response) -> None:
    for name, http_only in ((SESSION_COOKIE, True), (CSRF_COOKIE, False)):
        response.delete_cookie(
            name,
            path="/",
            domain=env("SESSION_COOKIE_DOMAIN") or None,
            secure=True,
            httponly=http_only,
            samesite="lax",
        )


_UNAUTHORIZED = 401


def _clearing_cookies_on_401(
    send: Callable[[Any], Awaitable[None]],
) -> Callable[[Any], Awaitable[None]]:
    """A ``send`` that clears the session cookies when the response is a 401:
    the cookie authenticated nothing (expired, revoked, invalid), so the
    browser should stop presenting it."""
    clearing = Response()
    clear_session_cookies(clearing)
    set_cookies = [
        (name, value) for name, value in clearing.raw_headers if name == b"set-cookie"
    ]

    async def _send(message: Any) -> None:
        if (
            message["type"] == "http.response.start"
            and message["status"] == _UNAUTHORIZED
        ):
            message = {
                **message,
                "headers": [*message.get("headers", []), *set_cookies],
            }
        await send(message)

    return _send


def _same_app_origin(present: Dict[bytes, bytes]) -> bool:
    """The upgrade comes from the app itself: its Origin is this host, or an
    exact origin in APP_CORS_ALLOWED_ORIGINS (never ``*``, which grants no
    credentials). No Origin, which a browser always sends, means no cookie."""
    from urllib.parse import urlparse

    from zephyrex.lib.InboundSecurity import parse_cors_origins

    origin = present.get(b"origin", b"").decode("latin-1")
    if not origin:
        return False
    if urlparse(origin).netloc == present.get(b"host", b"").decode("latin-1"):
        return True
    allowed = parse_cors_origins(env("APP_CORS_ALLOWED_ORIGINS") or "")
    return origin != "*" and origin in allowed


def _forged_cross_site(method: str, present: Dict[bytes, bytes]) -> bool:
    """A write another site's page could have sent: no explicit credential
    (those force a CORS preflight), and an Origin that is not the app's.
    Browsers always send Origin on such a write; clients that are not
    browsers send none and pass."""
    return (
        method not in _SAFE_METHODS
        and b"authorization" not in present
        and b"x-api-key" not in present
        and b"origin" in present
        and not _same_app_origin(present)
    )


def _cookies(raw: Optional[bytes]) -> Dict[str, str]:
    if not raw:
        return {}
    jar: SimpleCookie = SimpleCookie()
    try:
        jar.load(raw.decode("latin-1"))
    except CookieError:
        return {}
    return {name: morsel.value for name, morsel in jar.items()}


class SessionCookieMiddleware:
    """ASGI middleware: session cookie to bearer header, with CSRF check."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(
        self,
        scope: Dict[str, Any],
        receive: Callable[[], Awaitable[Any]],
        send: Callable[[Any], Awaitable[None]],
    ) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        headers: List[Tuple[bytes, bytes]] = scope["headers"]
        present = {name.lower(): value for name, value in headers}
        if scope["type"] == "http" and _forged_cross_site(scope["method"], present):
            await self._refuse(send, "Cross-site request refused")
            return
        token = _cookies(present.get(b"cookie")).get(SESSION_COOKIE)
        if not token or b"authorization" in present or b"x-api-key" in present:
            await self.app(scope, receive, send)
            return
        bearer = (b"authorization", f"Bearer {token}".encode("latin-1"))
        if scope["type"] == "websocket":
            if _same_app_origin(present):
                scope = {**scope, "headers": [*headers, bearer]}
            await self.app(scope, receive, send)
            return
        if scope["method"] not in _SAFE_METHODS and not self._csrf_matches(present):
            await self._refuse(send, "CSRF token missing or invalid")
            return
        await self.app(
            {**scope, "headers": [*headers, bearer]},
            receive,
            _clearing_cookies_on_401(send),
        )

    @staticmethod
    def _csrf_matches(present: Dict[bytes, bytes]) -> bool:
        expected = _cookies(present.get(b"cookie")).get(CSRF_COOKIE, "")
        offered = present.get(CSRF_HEADER.encode(), b"").decode("latin-1")
        return bool(expected) and hmac.compare_digest(expected, offered)

    @staticmethod
    async def _refuse(send: Callable[[Any], Awaitable[None]], detail: str) -> None:
        body = json.dumps({"detail": detail}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
