# SPDX-License-Identifier: AGPL-3.0-or-later
"""The authorization server's settings, read from the environment the
extension registers (``EXT_OAuthProvider._env``)."""

from typing import Dict, Tuple
from urllib.parse import urlsplit

from fastapi import HTTPException

from zephyrex.lib.Environment import env, env_int

ISSUER = "OAUTH_PROVIDER_ISSUER"
CONSENT_URL = "OAUTH_PROVIDER_CONSENT_URL"
REQUEST_TTL = "OAUTH_PROVIDER_REQUEST_TTL_SECONDS"
CODE_TTL = "OAUTH_PROVIDER_CODE_TTL_SECONDS"
ACCESS_TOKEN_TTL = "OAUTH_PROVIDER_ACCESS_TOKEN_TTL_SECONDS"
REFRESH_TOKEN_TTL = "OAUTH_PROVIDER_REFRESH_TOKEN_TTL_SECONDS"
ID_TOKEN_TTL = "OAUTH_PROVIDER_ID_TOKEN_TTL_SECONDS"
SIGNING_ALGORITHMS = "OAUTH_PROVIDER_SIGNING_ALGORITHMS"
KEY_ROTATION_DAYS = "OAUTH_PROVIDER_KEY_ROTATION_DAYS"
KEY_RETIREMENT = "OAUTH_PROVIDER_KEY_RETIREMENT_SECONDS"

# Short-lived by default (RFC 9700 §2.1.1, §4.14): a code lives a minute,
# an access token ten, an ID token five; a refresh family lives 30 days
# from the authorization, however often it rotates.
DEFAULTS: Dict[str, str] = {
    ISSUER: "",
    CONSENT_URL: "",
    REQUEST_TTL: "600",
    CODE_TTL: "60",
    ACCESS_TOKEN_TTL: "600",
    REFRESH_TOKEN_TTL: "2592000",
    ID_TOKEN_TTL: "300",
    SIGNING_ALGORITHMS: "RS256",
    KEY_ROTATION_DAYS: "90",
    KEY_RETIREMENT: "86400",
}

RS256 = "RS256"
ES256 = "ES256"
SUPPORTED_ALGORITHMS = (RS256, ES256)


def _seconds(name: str) -> int:
    value = env_int(name, int(DEFAULTS[name]))
    if value <= 0:
        raise HTTPException(status_code=503, detail=f"{name} must be positive")
    return value


def request_ttl() -> int:
    return _seconds(REQUEST_TTL)


def code_ttl() -> int:
    return _seconds(CODE_TTL)


def access_token_ttl() -> int:
    return _seconds(ACCESS_TOKEN_TTL)


def refresh_token_ttl() -> int:
    return _seconds(REFRESH_TOKEN_TTL)


def id_token_ttl() -> int:
    return _seconds(ID_TOKEN_TTL)


def key_rotation_seconds() -> int:
    return _seconds(KEY_ROTATION_DAYS) * 86400


def key_retirement_seconds() -> int:
    return _seconds(KEY_RETIREMENT)


def issuer() -> str:
    """The issuer identifier: this server's public HTTPS base URL, with no
    query, fragment or trailing slash (RFC 8414 §2). 503 when unset, as the
    server cannot name itself in a token without it."""
    value = env(ISSUER).strip()
    if not value:
        raise HTTPException(status_code=503, detail=f"{ISSUER} is not configured")
    parts = urlsplit(value)
    if parts.scheme != "https" or not parts.hostname or parts.query or parts.fragment:
        raise HTTPException(
            status_code=503,
            detail=f"{ISSUER} is an https URL with no query or fragment",
        )
    return value.rstrip("/")


def consent_url() -> str:
    """The page (of the UI) where a signed-in user approves or denies an
    authorization request."""
    value = env(CONSENT_URL).strip()
    if not value:
        raise HTTPException(status_code=503, detail=f"{CONSENT_URL} is not configured")
    return value


def signing_algorithms() -> Tuple[str, ...]:
    """The algorithms ID tokens are signed with; RS256 is always among them
    (OpenID Connect Discovery §3)."""
    configured = tuple(
        a.strip() for a in (env(SIGNING_ALGORITHMS) or RS256).split(",") if a.strip()
    )
    unknown = [a for a in configured if a not in SUPPORTED_ALGORITHMS]
    if unknown:
        raise HTTPException(
            status_code=503,
            detail=f"{SIGNING_ALGORITHMS} names unsupported {unknown}",
        )
    return configured if RS256 in configured else (RS256, *configured)


def endpoint(path: str) -> str:
    return issuer() + path
