# SPDX-License-Identifier: AGPL-3.0-or-later
"""The wire rules of the authorization server, free of storage.

What RFC 6749, RFC 7636 (PKCE), RFC 8252 (native apps), RFC 9700 (the
OAuth 2.0 Security BCP) and OpenID Connect Core say a parameter, a
redirect URI, a client credential or a token must look like, and the
error a refusal carries."""

import base64
import hashlib
import hmac
import ipaddress
import json
import re
import secrets
from typing import Dict, List, Mapping, Optional, Tuple
from urllib.parse import parse_qsl, unquote_plus, urlencode, urlsplit, urlunsplit

# RFC 7636 §4.1: 43-128 characters of [A-Z] / [a-z] / [0-9] / "-" / "." /
# "_" / "~". An S256 challenge is the 43-character base64url of a SHA-256.
_VERIFIER = re.compile(r"^[A-Za-z0-9\-._~]{43,128}$")
_S256_CHALLENGE = re.compile(r"^[A-Za-z0-9_-]{43}$")
# RFC 6749 §3.3: scope-token = 1*( %x21 / %x23-5B / %x5D-7E ).
_SCOPE_TOKEN = re.compile(r"^[\x21\x23-\x5B\x5D-\x7E]+$")
# No parameter the server accepts is longer than this.
MAX_PARAMETER_LENGTH = 2048
# Bytes of entropy in every secret the server mints (codes, tokens, secrets).
SECRET_BYTES = 32

PKCE_METHOD = "S256"
TOKEN_TYPE_BEARER = "Bearer"

AUTH_METHOD_BASIC = "client_secret_basic"
AUTH_METHOD_POST = "client_secret_post"
AUTH_METHOD_NONE = "none"
CONFIDENTIAL_AUTH_METHODS = (AUTH_METHOD_BASIC, AUTH_METHOD_POST)

APPLICATION_WEB = "web"
APPLICATION_NATIVE = "native"
APPLICATION_TYPES = (APPLICATION_WEB, APPLICATION_NATIVE)

SCOPE_OPENID = "openid"
SCOPE_PROFILE = "profile"
SCOPE_EMAIL = "email"
OIDC_SCOPES = (SCOPE_OPENID, SCOPE_PROFILE, SCOPE_EMAIL)

# Cache-Control for every response that carries a credential (RFC 6749 §5.1).
NO_STORE_HEADERS: Mapping[str, str] = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
}


class OAuthError(Exception):
    """A refusal in the shape RFC 6749 §5.2 gives it: an ``error`` code, an
    optional human-readable description, the HTTP status and headers."""

    def __init__(
        self,
        error: str,
        description: str = "",
        status_code: int = 400,
        headers: Optional[Mapping[str, str]] = None,
    ) -> None:
        super().__init__(f"{error}: {description}" if description else error)
        self.error = error
        self.description = description
        self.status_code = status_code
        self.headers: Dict[str, str] = dict(headers or {})

    def body(self) -> Dict[str, str]:
        body = {"error": self.error}
        if self.description:
            body["error_description"] = self.description
        return body


def invalid_request(description: str) -> OAuthError:
    return OAuthError("invalid_request", description)


def mint_secret(prefix: str) -> str:
    """A new opaque credential: ``prefix`` and 256 random bits."""
    return prefix + secrets.token_urlsafe(SECRET_BYTES)


def digest(raw: str) -> str:
    """The stored form of a server-minted credential. Every one carries 256
    random bits, so a plain SHA-256 cannot be reversed or brute-forced and
    lets the row be found by an indexed equality lookup."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def digest_matches(raw: str, stored: str) -> bool:
    """Constant-time comparison of ``raw`` against a stored digest."""
    return hmac.compare_digest(digest(raw), stored)


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def s256(verifier: str) -> str:
    return b64url(hashlib.sha256(verifier.encode("ascii")).digest())


def is_s256_challenge(challenge: str) -> bool:
    return bool(_S256_CHALLENGE.match(challenge))


def pkce_verifies(verifier: Optional[str], challenge: str) -> bool:
    """Whether ``verifier`` proves the S256 ``challenge`` (RFC 7636 §4.6)."""
    if not verifier or not _VERIFIER.match(verifier):
        return False
    return hmac.compare_digest(s256(verifier), challenge)


def half_hash(value: str) -> str:
    """OIDC Core §3.1.3.6 ``at_hash``: the base64url of the left half of the
    SHA-256 of ``value`` (RS256 and ES256 both hash with SHA-256)."""
    full = hashlib.sha256(value.encode("ascii")).digest()
    return b64url(full[: len(full) // 2])


def parse_scope(raw: Optional[str]) -> List[str]:
    """The scope tokens of a ``scope`` parameter, in order, each once."""
    if raw is None:
        return []
    tokens: List[str] = []
    for token in raw.split(" "):
        if not token:
            raise OAuthError("invalid_scope", "scope tokens are separated by one space")
        if not _SCOPE_TOKEN.match(token):
            raise OAuthError("invalid_scope", "scope holds a forbidden character")
        if token not in tokens:
            tokens.append(token)
    return tokens


def join_scope(tokens: List[str]) -> str:
    return " ".join(tokens)


def _is_loopback_host(host: Optional[str]) -> bool:
    if not host:
        return False
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_redirect_uri(uri: str, application_type: str) -> str:
    """A redirect URI a client may register (RFC 9700 §2.1, §4.1.1 and
    RFC 8252 §7.3): absolute, without a fragment or user info, over HTTPS,
    except a native client's ``http`` loopback IP literal. Raises
    ``ValueError`` naming what is wrong."""
    if not uri or len(uri) > MAX_PARAMETER_LENGTH:
        raise ValueError("a redirect URI is 1-2048 characters")
    parts = urlsplit(uri)
    if parts.fragment or "#" in uri:
        raise ValueError(f"{uri}: a redirect URI has no fragment")
    if not parts.scheme or not parts.hostname:
        raise ValueError(f"{uri}: a redirect URI is absolute")
    if parts.username is not None or parts.password is not None:
        raise ValueError(f"{uri}: a redirect URI carries no user info")
    if "*" in uri:
        raise ValueError(f"{uri}: a redirect URI has no wildcard")
    if parts.scheme == "https":
        return uri
    if (
        parts.scheme == "http"
        and application_type == APPLICATION_NATIVE
        and _is_loopback_host(parts.hostname)
    ):
        return uri
    raise ValueError(
        f"{uri}: a redirect URI uses https (a native client may use an "
        "http loopback IP literal)"
    )


def _without_port(uri: str) -> str:
    parts = urlsplit(uri)
    host = parts.hostname or ""
    netloc = f"[{host}]" if ":" in host else host
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, ""))


def redirect_uri_registered(registered: List[str], presented: str) -> bool:
    """Exact string matching (RFC 9700 §4.1.3), with the one exception RFC
    8252 §7.3 makes: an ``http`` loopback redirect matches on any port."""
    if presented in registered:
        return True
    parts = urlsplit(presented)
    if parts.scheme != "http" or not _is_loopback_host(parts.hostname):
        return False
    bare = _without_port(presented)
    return any(
        urlsplit(uri).scheme == "http"
        and _is_loopback_host(urlsplit(uri).hostname)
        and _without_port(uri) == bare
        for uri in registered
    )


def with_query(uri: str, params: Mapping[str, Optional[str]]) -> str:
    """``uri`` with ``params`` added to its query, keeping the query it
    already has (RFC 6749 §3.1.2)."""
    parts = urlsplit(uri)
    query = parse_qsl(parts.query, keep_blank_values=True)
    query.extend((key, value) for key, value in params.items() if value is not None)
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
    )


def parse_basic_credentials(header: Optional[str]) -> Optional[Tuple[str, str]]:
    """The client id and secret of an HTTP Basic ``Authorization`` header,
    each form-urlencoded first as RFC 6749 §2.3.1 requires, or None when
    the header is not Basic. Raises ``OAuthError`` for a malformed one."""
    if not header:
        return None
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "basic":
        return None
    try:
        decoded = base64.b64decode(value.strip(), validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        raise OAuthError(
            "invalid_client", "malformed Basic credentials", status_code=401
        ) from None
    client_id, separator, secret = decoded.partition(":")
    if not separator:
        raise OAuthError(
            "invalid_client", "malformed Basic credentials", status_code=401
        )
    return unquote_plus(client_id), unquote_plus(secret)


def string_parameters(raw: Mapping[str, object]) -> Dict[str, str]:
    """A request's parameters as strings, refusing any other JSON type and
    any parameter past the length cap. Empty values count as absent
    (RFC 6749 §3.1)."""
    parameters: Dict[str, str] = {}
    for name, value in raw.items():
        if value is None:
            continue
        if not isinstance(value, str):
            raise invalid_request(f"{name} is a string")
        if len(value) > MAX_PARAMETER_LENGTH:
            raise invalid_request(f"{name} is too long")
        if value != "":
            parameters[name] = value
    return parameters


def json_list(raw: Optional[str]) -> List[str]:
    """A JSON array of strings stored in a text column."""
    if not raw:
        return []
    loaded = json.loads(raw)
    if not isinstance(loaded, list) or not all(isinstance(v, str) for v in loaded):
        raise ValueError("expected a JSON array of strings")
    return loaded
