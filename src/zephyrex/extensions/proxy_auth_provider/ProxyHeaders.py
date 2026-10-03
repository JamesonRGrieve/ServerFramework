# SPDX-License-Identifier: AGPL-3.0-or-later
"""What crosses the proxy, and what never does.

Going up, a request keeps its own headers except those that belong to this
hop or to this server: the hop-by-hop headers, ``Host``, the caller's
credentials (``Authorization``, ``X-API-Key``, the CSRF header and every
``zx_`` cookie, the session cookie among them), and every identity header a
client could have forged (anything ``X-Forwarded-*``, ``Forwarded``,
``X-Real-IP``, ``Remote-*``, ``X-Remote-*``, ``X-Auth-Request-*``,
``X-WebAuth-*``). Only then are this server's own identity headers added,
so a downstream that trusts them sees exactly what this server vouches
for.

The identity a downstream receives:

- ``X-Forwarded-User``: the user's id, which never changes or passes to
  someone else;
- ``X-Forwarded-Preferred-Username``, ``X-Forwarded-Email``,
  ``X-Forwarded-Name``: when the user has them;
- ``X-Forwarded-Groups``: the ids of the teams the user is a live member
  of, comma-separated. Ids, not names: any user can create a team and name
  it ``admins``.

With a signing secret, ``X-Forwarded-Auth-Timestamp`` (Unix seconds) and
``X-Forwarded-Auth-Signature`` (``v1=`` and a hex HMAC-SHA256) let the
downstream check that this server sent them, for this method and request
target, recently. The signed text is ``signed_text``'s lines;
``verify_identity`` is the check, for a downstream written in Python.

Coming back, a response loses its hop-by-hop headers and any cookie it
tries to set under this server's ``zx_`` names, and a redirect to the
upstream's own address is pointed back through the proxy.
"""

import hashlib
import hmac
import time
from dataclasses import dataclass
from typing import Iterable, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import unquote, urlsplit, urlunsplit

from zephyrex.lib.SessionCookies import CSRF_HEADER

RawHeaders = List[Tuple[bytes, bytes]]

USER_HEADER = "X-Forwarded-User"
USERNAME_HEADER = "X-Forwarded-Preferred-Username"
EMAIL_HEADER = "X-Forwarded-Email"
NAME_HEADER = "X-Forwarded-Name"
GROUPS_HEADER = "X-Forwarded-Groups"
TIMESTAMP_HEADER = "X-Forwarded-Auth-Timestamp"
SIGNATURE_HEADER = "X-Forwarded-Auth-Signature"
SIGNATURE_VERSION = "v1"

# Every cookie this server sets is named with this prefix (the session,
# CSRF, pairing, OAuth-binding and SAML-request cookies).
FRAMEWORK_COOKIE_PREFIX = "zx_"

HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "proxy-connection",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)
# Request headers that belong to this server, never to the upstream.
_DROPPED_REQUEST = frozenset(
    {
        "host",
        "expect",
        "authorization",
        "x-api-key",
        CSRF_HEADER,
        "forwarded",
        "x-real-ip",
    }
)
# Identity header families a downstream may be configured to trust.
_FORGEABLE_PREFIXES = (
    "x-forwarded-",
    "x-remote-",
    "remote-",
    "x-auth-request-",
    "x-webauth-",
)
_CONTROL = frozenset(chr(c) for c in (*range(0x20), 0x7F))


def _names(raw: RawHeaders) -> List[str]:
    return [name.decode("latin-1").lower() for name, _ in raw]


def _connection_tokens(raw: RawHeaders) -> frozenset[str]:
    """The headers a ``Connection`` header names as this hop's own."""
    return frozenset(
        token.strip().lower()
        for name, value in raw
        if name.decode("latin-1").lower() == "connection"
        for token in value.decode("latin-1").split(",")
        if token.strip()
    )


def _is_framework_cookie(name: str) -> bool:
    return name.strip().lower().startswith(FRAMEWORK_COOKIE_PREFIX)


def _without_framework_cookies(value: bytes) -> Optional[bytes]:
    kept = [
        pair.strip()
        for pair in value.decode("latin-1").split(";")
        if pair.strip() and not _is_framework_cookie(pair.split("=", 1)[0])
    ]
    return "; ".join(kept).encode("latin-1") if kept else None


def upstream_request_headers(raw: RawHeaders) -> RawHeaders:
    """The client's headers as they may go upstream: none of this hop's,
    none of this server's credentials, no identity a client could forge."""
    dropped = HOP_BY_HOP | _DROPPED_REQUEST | _connection_tokens(raw)
    kept: RawHeaders = []
    for (name, value), lower in zip(raw, _names(raw)):
        if lower in dropped or lower.startswith(_FORGEABLE_PREFIXES):
            continue
        if lower == "cookie":
            cookies = _without_framework_cookies(value)
            if cookies is not None:
                kept.append((name, cookies))
            continue
        kept.append((name, value))
    return kept


def _set_cookie_name(value: bytes) -> str:
    return value.decode("latin-1").split(";", 1)[0].split("=", 1)[0]


def public_location(location: str, upstream_base: str, public_prefix: str) -> str:
    """``location`` as the client must follow it: a redirect to a page of
    the upstream (by its address, or by an absolute path under its base
    path) goes back through ``public_prefix``. Relative and foreign
    redirects are left as they are."""
    target = urlsplit(location)
    base = urlsplit(upstream_base)
    if target.scheme or target.netloc:
        if (target.scheme.lower(), target.netloc.lower()) != (
            base.scheme.lower(),
            base.netloc.lower(),
        ):
            return location
    elif not location.startswith("/"):
        return location
    base_path = base.path.rstrip("/")
    if target.path != base_path and not target.path.startswith(base_path + "/"):
        return location
    rest = target.path[len(base_path) :] or "/"
    return urlunsplit(("", "", public_prefix + rest, target.query, target.fragment))


def client_response_headers(
    raw: RawHeaders, upstream_base: str, public_prefix: str
) -> RawHeaders:
    """The upstream's response headers as the client may get them."""
    dropped = HOP_BY_HOP | _connection_tokens(raw)
    kept: RawHeaders = []
    for (name, value), lower in zip(raw, _names(raw)):
        if lower in dropped:
            continue
        if lower == "set-cookie" and _is_framework_cookie(_set_cookie_name(value)):
            continue
        if lower == "location":
            value = public_location(
                value.decode("latin-1"), upstream_base, public_prefix
            ).encode("latin-1")
        kept.append((name, value))
    return kept


def header_value(text: Optional[str]) -> Optional[str]:
    """``text`` as a header value, or None when it is empty or carries a
    control character (a line break would end the header)."""
    if not text or not text.strip() or any(ch in _CONTROL for ch in text):
        return None
    return text.strip()


@dataclass(frozen=True)
class Identity:
    """Who this server vouches the request is from."""

    user_id: str
    username: Optional[str]
    email: Optional[str]
    name: Optional[str]
    groups: Tuple[str, ...]

    def fields(self) -> Tuple[Tuple[str, str], ...]:
        """The identity headers, in signing order; an absent value is ``""``."""
        values = (
            (USER_HEADER, self.user_id),
            (USERNAME_HEADER, self.username),
            (EMAIL_HEADER, self.email),
            (NAME_HEADER, self.name),
            (GROUPS_HEADER, ",".join(self.groups)),
        )
        return tuple((name, header_value(value) or "") for name, value in values)


def signed_text(
    timestamp: str, method: str, target: str, fields: Iterable[Tuple[str, str]]
) -> bytes:
    """What the signature covers, one item per line: the version, the
    timestamp, the method, the request target (path and query as the
    upstream receives them), and each identity header's value in
    ``Identity.fields`` order."""
    lines = [SIGNATURE_VERSION, timestamp, method.upper(), target]
    lines.extend(value for _, value in fields)
    return "\n".join(lines).encode("utf-8")


def signature(secret: bytes, text: bytes) -> str:
    return f"{SIGNATURE_VERSION}=" + hmac.new(secret, text, hashlib.sha256).hexdigest()


def identity_headers(
    identity: Identity,
    method: str,
    target: str,
    secret: Optional[bytes],
    now: Optional[float] = None,
) -> RawHeaders:
    """The identity headers for one request, signed when there is a
    ``secret``. Values travel as UTF-8."""
    fields = identity.fields()
    headers: RawHeaders = [
        (name.encode("latin-1"), value.encode("utf-8"))
        for name, value in fields
        if value
    ]
    if secret:
        timestamp = str(int(time.time() if now is None else now))
        headers.append((TIMESTAMP_HEADER.encode("latin-1"), timestamp.encode()))
        headers.append(
            (
                SIGNATURE_HEADER.encode("latin-1"),
                signature(
                    secret, signed_text(timestamp, method, target, fields)
                ).encode(),
            )
        )
    return headers


def verify_identity(
    secret: bytes,
    method: str,
    target: str,
    headers: Mapping[str, str],
    max_age_seconds: int,
    now: Optional[float] = None,
) -> bool:
    """Whether ``headers`` (the request's, any case) carry identity this
    server signed with ``secret`` for ``method`` and ``target`` no more
    than ``max_age_seconds`` ago."""
    found = {name.lower(): value for name, value in headers.items()}
    timestamp = found.get(TIMESTAMP_HEADER.lower(), "")
    offered = found.get(SIGNATURE_HEADER.lower(), "")
    if not timestamp.isdigit() or not offered:
        return False
    current = time.time() if now is None else now
    if abs(current - int(timestamp)) > max_age_seconds:
        return False
    fields = [
        (name, found.get(name.lower(), ""))
        for name in (
            USER_HEADER,
            USERNAME_HEADER,
            EMAIL_HEADER,
            NAME_HEADER,
            GROUPS_HEADER,
        )
    ]
    expected = signature(secret, signed_text(timestamp, method, target, fields))
    return hmac.compare_digest(expected, offered)


class UnsafePath(ValueError):
    """A request path that would leave the upstream's base path."""


def upstream_target(base_url: str, raw_rest: str, query: str) -> Tuple[str, str]:
    """The upstream URL for the path ``raw_rest`` (below the upstream's
    public prefix, still percent-encoded as the client sent it) and the
    raw ``query``, and the request target the upstream will see. The host
    is always the upstream's: a path can only go below its base path, so
    a ``.``/``..`` segment is refused, encoded or not, and also when it
    hides behind an encoded slash (``a%2F..``, which a server that decodes
    slashes would climb with), as is a backslash or a control character."""
    segments = raw_rest.split("/") if raw_rest else []
    for segment in segments:
        decoded = unquote(segment)
        if (
            any(part in (".", "..") for part in decoded.split("/"))
            or "\\" in decoded
            or any(ch in _CONTROL for ch in decoded)
        ):
            raise UnsafePath(f"path segment {segment!r} is not allowed")
    base = urlsplit(base_url)
    path = base.path.rstrip("/") + "/" + "/".join(segments)
    target = path + (f"?{query}" if query else "")
    return urlunsplit((base.scheme, base.netloc, path, query, "")), target


def declared_length(raw: Sequence[Tuple[bytes, bytes]]) -> Optional[int]:
    """The ``Content-Length`` the headers declare, or None."""
    for name, value in raw:
        if name.decode("latin-1").lower() == "content-length":
            text = value.decode("latin-1").strip()
            return int(text) if text.isdigit() else None
    return None
