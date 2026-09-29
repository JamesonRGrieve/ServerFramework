# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest

from zephyrex.lib.Dependencies import jwt
from zephyrex.lib.ReplayCache import InMemoryReplayCache, set_replay_cache
from zephyrex.lib.SingleUseToken import (
    issue_single_use_token,
    read_single_use_token,
    redeem_single_use_token,
)

AUDIENCE = "zephyrex:test:single_use"


@pytest.fixture(autouse=True)
def _fresh_replay_cache():
    set_replay_cache(InMemoryReplayCache())
    yield
    set_replay_cache(InMemoryReplayCache())


def test_claims_round_trip():
    token = issue_single_use_token(
        audience=AUDIENCE, subject="user-1", ttl_seconds=60, claims={"extra": "x"}
    )
    claims = read_single_use_token(token, audience=AUDIENCE)
    assert (claims["sub"], claims["aud"], claims["extra"]) == ("user-1", AUDIENCE, "x")


def test_redeems_once():
    claims = read_single_use_token(
        issue_single_use_token(audience=AUDIENCE, subject="u", ttl_seconds=60),
        audience=AUDIENCE,
    )
    assert redeem_single_use_token(claims) is True
    assert redeem_single_use_token(claims) is False


def test_tokens_are_distinct():
    first, second = (
        read_single_use_token(
            issue_single_use_token(audience=AUDIENCE, subject="u", ttl_seconds=60),
            audience=AUDIENCE,
        )
        for _ in range(2)
    )
    assert redeem_single_use_token(first) is True
    assert redeem_single_use_token(second) is True


@pytest.mark.parametrize(
    "token_audience, ttl_seconds",
    [("zephyrex:test:other", 60), (AUDIENCE, -120)],
    ids=["wrong-audience", "expired"],
)
def test_rejects_wrong_audience_and_expiry(token_audience, ttl_seconds):
    token = issue_single_use_token(
        audience=token_audience, subject="u", ttl_seconds=ttl_seconds
    )
    with pytest.raises(jwt.InvalidTokenError):
        read_single_use_token(token, audience=AUDIENCE)


def test_rejects_a_forged_signature():
    forged = jwt.encode(
        {"sub": "u", "aud": AUDIENCE, "iat": 0, "exp": 2**31, "jti": "j"},
        "not-the-server-secret-but-long-enough-32b",
        algorithm="HS256",
    )
    with pytest.raises(jwt.InvalidTokenError):
        read_single_use_token(forged, audience=AUDIENCE)
