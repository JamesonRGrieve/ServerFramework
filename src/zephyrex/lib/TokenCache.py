# SPDX-License-Identifier: AGPL-3.0-or-later
"""Short-lived access tokens, kept until shortly before they expire.

Many APIs trade a long-lived credential (a refresh token, a client
secret) for an access token that lasts minutes or hours. A token is
cached per provider instance and per credential fingerprint, so changing
an instance's credentials takes effect at once; a refused token is
dropped so the next call trades again.
"""

import asyncio
import hashlib
import time
import weakref
from typing import Awaitable, Callable, Dict, Tuple

# A token is renewed this long before it expires.
RENEW_MARGIN_SECONDS = 60

Fetch = Callable[[], Awaitable[Tuple[str, float]]]


def fingerprint(*parts: str) -> str:
    """A digest of the credentials a token was issued for."""
    return hashlib.sha256("\x00".join(parts).encode()).hexdigest()


class TokenCache:
    def __init__(self) -> None:
        self._tokens: Dict[str, Tuple[str, str, float]] = {}
        # A lock belongs to one event loop; each loop gets its own.
        self._locks: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, Dict[str, asyncio.Lock]]" = (weakref.WeakKeyDictionary())

    async def obtain(self, key: str, credentials: str, fetch: Fetch) -> str:
        """The cached token for ``key``, or a new one from ``fetch``
        (``(token, lifetime in seconds)``) when there is none, it has
        nearly expired, or the credentials changed."""
        cached = self._tokens.get(key)
        if cached and cached[0] == credentials and cached[2] > time.monotonic():
            return cached[1]
        locks = self._locks.setdefault(asyncio.get_running_loop(), {})
        lock = locks.setdefault(key, asyncio.Lock())
        async with lock:
            cached = self._tokens.get(key)
            if cached and cached[0] == credentials and cached[2] > time.monotonic():
                return cached[1]
            token, lifetime = await fetch()
            expiry = time.monotonic() + max(lifetime - RENEW_MARGIN_SECONDS, 0)
            self._tokens[key] = (credentials, token, expiry)
            return token

    def drop(self, key: str) -> None:
        """Forget ``key``'s token (it was refused)."""
        self._tokens.pop(key, None)
