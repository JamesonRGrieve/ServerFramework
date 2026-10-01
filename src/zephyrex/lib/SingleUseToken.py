# SPDX-License-Identifier: AGPL-3.0-or-later
"""Short-lived, single-use signed tokens.

A token is an HS256 JWT signed with ``JWT_SECRET``, scoped to one purpose by
its ``aud`` and carrying a random ``jti``. Reading a token checks signature,
audience and expiry; redeeming it burns the ``jti`` in the replay cache so it
cannot be used twice. The two steps are separate so a caller can validate
the rest of a request (a subject match, a second-factor code) before the
token is spent.

A session token is a different JWT: it requires ``iss``, ``nbf`` and the
session audience, none of which these carry, so a single-use token can never
authenticate a request.
"""

import secrets
import time
from typing import Any, Dict, Optional

import jwt
from zephyrex.lib.Environment import env
from zephyrex.lib.ReplayCache import get_replay_cache

_ALGORITHM = "HS256"
_CLOCK_SKEW_SECONDS = 30
_REQUIRED_CLAIMS = ["sub", "aud", "exp", "iat", "jti"]


def issue_single_use_token(
    *,
    audience: str,
    subject: str,
    ttl_seconds: int,
    claims: Optional[Dict[str, Any]] = None,
) -> str:
    now = int(time.time())
    token: str = jwt.encode(
        {
            **(claims or {}),
            "sub": subject,
            "aud": audience,
            "iat": now,
            "exp": now + ttl_seconds,
            "jti": secrets.token_urlsafe(24),
        },
        env("JWT_SECRET"),
        algorithm=_ALGORITHM,
    )
    return token


def read_single_use_token(token: str, *, audience: str) -> Dict[str, Any]:
    """The claims of a genuine, unexpired token for ``audience``. Raises
    ``jwt.InvalidTokenError`` otherwise; says nothing about prior use."""
    claims: Dict[str, Any] = jwt.decode(
        token,
        env("JWT_SECRET"),
        algorithms=[_ALGORITHM],
        audience=audience,
        leeway=_CLOCK_SKEW_SECONDS,
        options={"require": _REQUIRED_CLAIMS},
    )
    return claims


def redeem_single_use_token(claims: Dict[str, Any]) -> bool:
    """Spend a token read by :func:`read_single_use_token`. True for the
    first caller only; the mark outlives the token's own expiry."""
    remaining = int(claims["exp"]) - int(time.time())
    return get_replay_cache().mark_if_unused(
        f"single_use_token:{claims['aud']}:{claims['jti']}",
        max(remaining, 0) + 2 * _CLOCK_SKEW_SECONDS,
    )
