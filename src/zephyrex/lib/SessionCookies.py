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
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers: List[Tuple[bytes, bytes]] = scope["headers"]
        present = {name.lower(): value for name, value in headers}
        token = _cookies(present.get(b"cookie")).get(SESSION_COOKIE)
        if not token or b"authorization" in present or b"x-api-key" in present:
            await self.app(scope, receive, send)
            return
        if scope["method"] not in _SAFE_METHODS and not self._csrf_matches(present):
            await self._refuse(send)
            return
        bearer = (b"authorization", f"Bearer {token}".encode("latin-1"))
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
    async def _refuse(send: Callable[[Any], Awaitable[None]]) -> None:
        body = json.dumps({"detail": "CSRF token missing or invalid"}).encode()
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
