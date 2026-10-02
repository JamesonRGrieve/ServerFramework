# SPDX-License-Identifier: AGPL-3.0-or-later
"""TokenCache: a token is reused until near expiry, renewed when the
credentials change or it is dropped, and fetched once for concurrent
callers."""

import asyncio

from zephyrex.lib.TokenCache import RENEW_MARGIN_SECONDS, TokenCache, fingerprint


class Issuer:
    def __init__(self, lifetime: float) -> None:
        self.lifetime, self.issued = lifetime, 0

    async def __call__(self):
        self.issued += 1
        await asyncio.sleep(0.01)
        return f"token-{self.issued}", self.lifetime


async def test_reused_until_near_expiry():
    cache, issuer = TokenCache(), Issuer(3600)
    assert await cache.obtain("i", "c", issuer) == "token-1"
    assert await cache.obtain("i", "c", issuer) == "token-1"
    short_lived = Issuer(RENEW_MARGIN_SECONDS)
    await cache.obtain("j", "c", short_lived)
    await cache.obtain("j", "c", short_lived)
    assert short_lived.issued == 2


async def test_new_credentials_or_a_drop_renew():
    cache, issuer = TokenCache(), Issuer(3600)
    await cache.obtain("i", fingerprint("a"), issuer)
    assert await cache.obtain("i", fingerprint("b"), issuer) == "token-2"
    cache.drop("i")
    assert await cache.obtain("i", fingerprint("b"), issuer) == "token-3"


async def test_concurrent_callers_share_one_fetch():
    cache, issuer = TokenCache(), Issuer(3600)
    tokens = await asyncio.gather(*(cache.obtain("i", "c", issuer) for _ in range(5)))
    assert set(tokens) == {"token-1"} and issuer.issued == 1


def test_fingerprints_separate_their_parts():
    assert fingerprint("ab", "c") != fingerprint("a", "bc")
