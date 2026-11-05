# SPDX-License-Identifier: AGPL-3.0-or-later
"""A forward-auth service this server asks who a browser is.

Each provider instance is one verifier endpoint: Authelia's
``/api/authz/forward-auth``, oauth2-proxy's ``/oauth2/auth``, Authentik's
``/outpost.goauthentik.io/auth/nginx`` and the like. The sign-in sends it a
GET carrying only the credentials the instance names (cookies by name,
request headers by name) from the browser's own request, and the
``X-Forwarded-*`` / ``X-Original-*`` description of the URL being reached
that those services authorize against. A 2xx answer allows and names the
user in its response headers; 401, 403 or a redirect (to the service's
login page) denies. Anything else, a timeout or an unreachable verifier
fails closed.

The request goes through the framework's ``ProviderHTTPClient``, so the
SSRF guard applies: a verifier on a private address must be listed in
``EGRESS_ALLOWED_HOSTS``. Redirects are never followed.
"""

import re
from dataclasses import dataclass
from typing import ClassVar, Dict, FrozenSet, List, Mapping, Optional, Set, Tuple
from urllib.parse import urlsplit

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractProviderInstance,
    AbstractStaticProvider,
    InstanceSetting,
)
from zephyrex.extensions.ExternalErrors import (
    AuthExternalError,
    BaseExternalError,
    PermanentExternalError,
)
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

DEFAULT_USER_HEADER = "Remote-User"
DEFAULT_EMAIL_HEADER = "Remote-Email"
DEFAULT_NAME_HEADER = "Remote-Name"
DEFAULT_TIMEOUT_SECONDS = "5"
MAX_TIMEOUT_SECONDS = 30.0
MILLISECONDS_PER_SECOND = 1000
MAX_IDENTITY_LENGTH = 256
MAX_EMAIL_LENGTH = 254
_TRUE = frozenset({"true", "1", "yes", "on"})
_FALSE = frozenset({"false", "0", "no", "off", ""})
# RFC 9110 field-name token.
_HEADER_NAME = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]+$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# Headers the sign-in sets itself, or that describe this hop rather than a
# credential: forwarding the browser's copy would let it describe the
# request (or smuggle a second one) to the verifier.
_RESERVED_HEADERS = frozenset(
    {
        "connection",
        "content-length",
        "cookie",
        "forwarded",
        "host",
        "keep-alive",
        "proxy-connection",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)
_RESERVED_HEADER_PREFIXES = ("x-forwarded-", "x-original-", "x-real-ip")
# A redirect is the service sending an unauthenticated browser to log in.
_REDIRECT_STATUS_FIRST = 300
_REDIRECT_STATUS_LAST = 399


@dataclass(frozen=True)
class VerifiedIdentity:
    """Who the verifier says the browser is: the identity (its user
    header), and the email and display name it gave, if any."""

    identity: str
    email: Optional[str]
    display_name: Optional[str]


@dataclass(frozen=True)
class OriginalRequest:
    """What the browser sent that the verifier may see: its raw Cookie
    header, its headers, its address, and the URL and method it reached."""

    cookie_header: Optional[str]
    headers: Mapping[str, str]
    client_host: Optional[str]
    url: str
    method: str


def name_list(value: Optional[str]) -> Tuple[str, ...]:
    """Names from a comma- or whitespace-separated list, in order, once."""
    seen: List[str] = []
    for name in (value or "").replace(",", " ").split():
        if name not in seen:
            seen.append(name)
    return tuple(seen)


def forwardable_headers(value: Optional[str]) -> FrozenSet[str]:
    """The request headers (lower-cased) an instance forwards. A name that
    is not a header name, or one the sign-in sets itself, is a
    misconfiguration (cookies are forwarded by name, not as a header)."""
    names = frozenset(name.lower() for name in name_list(value))
    for name in names:
        if not _HEADER_NAME.match(name):
            raise PermanentExternalError(f"{name!r} is not a header name")
        if name in _RESERVED_HEADERS or name.startswith(_RESERVED_HEADER_PREFIXES):
            raise PermanentExternalError(
                f"The {name} header cannot be forwarded to the verifier"
            )
    return names


def parse_flag(value: Optional[str], key: str) -> bool:
    flag = (value or "").strip().lower()
    if flag in _TRUE:
        return True
    if flag in _FALSE:
        return False
    raise PermanentExternalError(f"The {key} setting is not true or false")


def parse_timeout(value: Optional[str]) -> float:
    try:
        seconds = float((value or DEFAULT_TIMEOUT_SECONDS).strip())
    except ValueError:
        raise PermanentExternalError("The timeout_seconds setting is not a number")
    if not 0 < seconds <= MAX_TIMEOUT_SECONDS:
        raise PermanentExternalError(
            f"The timeout_seconds setting must be above 0 and at most "
            f"{MAX_TIMEOUT_SECONDS:g}"
        )
    return seconds


def selected_cookies(cookie_header: Optional[str], names: Tuple[str, ...]) -> str:
    """The ``name=value`` pairs of ``cookie_header`` whose (case-sensitive)
    names are in ``names``, verbatim and in the browser's order."""
    kept: List[str] = []
    for pair in (cookie_header or "").split(";"):
        name, separator, _ = pair.strip().partition("=")
        if separator and name in names:
            kept.append(pair.strip())
    return "; ".join(kept)


def _printable(value: str, limit: int) -> Optional[str]:
    value = value.strip()
    if not value or len(value) > limit or not value.isprintable():
        return None
    return value


def _single(values: List[str], header: str) -> Optional[str]:
    if len(values) > 1:
        raise PermanentExternalError(f"The verifier sent {header} more than once")
    return values[0] if values else None


@dataclass(frozen=True)
class Verifier:
    """One verifier endpoint and what the instance lets it see."""

    instance_name: str
    url: str
    cookies: Tuple[str, ...]
    headers: FrozenSet[str]
    user_header: str
    email_header: Optional[str]
    name_header: Optional[str]
    trusted_for_email: bool
    timeout_seconds: float
    original_url: Optional[str] = None

    def carries_credentials(self, original: OriginalRequest) -> bool:
        """The browser sent at least one credential this verifier reads."""
        return bool(selected_cookies(original.cookie_header, self.cookies)) or any(
            name.lower() in self.headers and value
            for name, value in original.headers.items()
        )

    def subrequest_headers(self, original: OriginalRequest) -> Dict[str, str]:
        """The headers the verifier is sent: the named credentials and the
        description of the URL being reached, nothing else of the
        browser's."""
        headers: Dict[str, str] = {
            name: value
            for name, value in original.headers.items()
            if name.lower() in self.headers
        }
        cookies = selected_cookies(original.cookie_header, self.cookies)
        if cookies:
            headers["Cookie"] = cookies
        reached = self.original_url or original.url
        parts = urlsplit(reached)
        uri = parts.path or "/"
        if parts.query:
            uri = f"{uri}?{parts.query}"
        headers.update(
            {
                "X-Forwarded-Method": original.method,
                "X-Forwarded-Proto": parts.scheme,
                "X-Forwarded-Host": parts.netloc,
                "X-Forwarded-Uri": uri,
                "X-Original-URL": reached,
                "X-Original-Method": original.method,
            }
        )
        if original.client_host:
            headers["X-Forwarded-For"] = original.client_host
        return headers

    def identity_from(self, answer: Mapping[str, List[str]]) -> VerifiedIdentity:
        """The identity in an allowing answer's headers (lower-cased names,
        each with every value sent). A missing, repeated or malformed user
        header fails closed; a bad email or name is only dropped."""

        def value(header: str, limit: int) -> Optional[str]:
            return _printable(
                _single(answer.get(header.lower(), []), header) or "", limit
            )

        identity = value(self.user_header, MAX_IDENTITY_LENGTH)
        if identity is None:
            raise PermanentExternalError(
                f"The verifier allowed the request without a usable {self.user_header}"
            )
        email = (
            value(self.email_header, MAX_EMAIL_LENGTH) if self.email_header else None
        )
        if email is not None and not _EMAIL.match(email):
            email = None
        display_name = (
            value(self.name_header, MAX_IDENTITY_LENGTH) if self.name_header else None
        )
        return VerifiedIdentity(
            identity=identity, email=email, display_name=display_name
        )


def header_values(items: List[Tuple[str, str]]) -> Dict[str, List[str]]:
    """Response header pairs as lower-cased names to every value sent."""
    values: Dict[str, List[str]] = {}
    for name, item in items:
        values.setdefault(name.lower(), []).append(item)
    return values


class PRV_ForwardAuthVerifier(AbstractStaticProvider):
    name: ClassVar[str] = "forward_auth_verifier"
    friendly_name: ClassVar[str] = "Forward-auth verifier"
    description: ClassVar[str] = (
        "A forward-auth endpoint (Authelia, oauth2-proxy, Authentik) that "
        "says who a browser is from its own cookies or headers"
    )
    _abilities: ClassVar[Set[str]] = set()
    _env: ClassVar[Dict[str, str]] = {}
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting(
            "verify_url",
            "The verifier endpoint (https://auth.example.com/api/authz/forward-auth); "
            "a private address must be in EGRESS_ALLOWED_HOSTS",
            env="FORWARD_AUTH_CONSUMER_VERIFY_URL",
        ),
        InstanceSetting(
            "forward_cookies",
            "Cookies of the browser's request sent to the verifier, by name, "
            "comma-separated (authelia_session); no others are sent",
            env="FORWARD_AUTH_CONSUMER_FORWARD_COOKIES",
        ),
        InstanceSetting(
            "forward_headers",
            "Headers of the browser's request sent to the verifier, by name, "
            "comma-separated (Authorization); no others are sent",
            env="FORWARD_AUTH_CONSUMER_FORWARD_HEADERS",
        ),
        InstanceSetting(
            "user_header",
            "The verifier's response header naming the user (Remote-User, "
            "X-Auth-Request-User, X-authentik-username)",
            env="FORWARD_AUTH_CONSUMER_USER_HEADER",
            default=DEFAULT_USER_HEADER,
        ),
        InstanceSetting(
            "email_header",
            "The verifier's response header with the user's email",
            env="FORWARD_AUTH_CONSUMER_EMAIL_HEADER",
            default=DEFAULT_EMAIL_HEADER,
        ),
        InstanceSetting(
            "name_header",
            "The verifier's response header with the user's display name",
            env="FORWARD_AUTH_CONSUMER_NAME_HEADER",
            default=DEFAULT_NAME_HEADER,
        ),
        InstanceSetting(
            "trusted_for_email",
            "true when the verifier vouches for the emails it sends: a new "
            "identity then signs in to the account with that email",
            env="FORWARD_AUTH_CONSUMER_TRUSTED_FOR_EMAIL",
            default="false",
        ),
        InstanceSetting(
            "timeout_seconds",
            "Seconds to wait for the verifier before refusing the sign-in",
            env="FORWARD_AUTH_CONSUMER_TIMEOUT_SECONDS",
            default=DEFAULT_TIMEOUT_SECONDS,
        ),
        InstanceSetting(
            "original_url",
            "The public URL the verifier is told is being reached (its access "
            "rules match it); empty means the sign-in request's own URL",
            env="FORWARD_AUTH_CONSUMER_ORIGINAL_URL",
        ),
    )

    @classmethod
    def bond_instance(cls, instance: ProviderInstanceModel) -> AbstractProviderInstance:
        return AbstractProviderInstance(instance)

    @classmethod
    def is_configured_instance(cls, instance: ProviderInstanceModel) -> bool:
        return bool((cls.setting(instance, "verify_url") or "").strip())

    @classmethod
    def verifier(cls, instance: ProviderInstanceModel) -> Verifier:
        """The instance's verifier, its settings checked. A verifier that
        would be sent no credential at all could only answer the same for
        everyone, so one must be named."""
        url = (cls.setting(instance, "verify_url") or "").strip()
        if urlsplit(url).scheme not in ("http", "https") or not urlsplit(url).netloc:
            raise PermanentExternalError("The verify_url setting is not an HTTP URL")
        cookies = name_list(cls.setting(instance, "forward_cookies"))
        headers = forwardable_headers(cls.setting(instance, "forward_headers"))
        if not cookies and not headers:
            raise PermanentExternalError(
                "Name the cookies or headers the verifier reads "
                "(forward_cookies, forward_headers)"
            )
        user_header = (cls.setting(instance, "user_header") or "").strip()
        if not _HEADER_NAME.match(user_header):
            raise PermanentExternalError("The user_header setting is not a header name")
        optional: Dict[str, Optional[str]] = {}
        for key in ("email_header", "name_header"):
            value = (cls.setting(instance, key) or "").strip()
            if value and not _HEADER_NAME.match(value):
                raise PermanentExternalError(f"The {key} setting is not a header name")
            optional[key] = value or None
        original_url = (cls.setting(instance, "original_url") or "").strip() or None
        if original_url and urlsplit(original_url).scheme not in ("http", "https"):
            raise PermanentExternalError("The original_url setting is not an HTTP URL")
        return Verifier(
            instance_name=instance.name,
            url=url,
            cookies=cookies,
            headers=headers,
            user_header=user_header,
            email_header=optional["email_header"],
            name_header=optional["name_header"],
            trusted_for_email=parse_flag(
                cls.setting(instance, "trusted_for_email"), "trusted_for_email"
            ),
            timeout_seconds=parse_timeout(cls.setting(instance, "timeout_seconds")),
            original_url=original_url,
        )

    @classmethod
    async def verify(
        cls, verifier: Verifier, original: OriginalRequest
    ) -> VerifiedIdentity:
        """Ask ``verifier`` who sent ``original``. AuthExternalError when it
        denies (401, 403, a redirect to its login); any other
        BaseExternalError when it fails (5xx, timeout, unreachable, refused
        by the SSRF guard, an allow without a usable identity).

        This is a plain call, never through the provider rotation: a
        denial is one browser's missing session, and cooling the instance
        down for it would lock everyone else out."""
        try:
            response = await cls.http().request(
                "GET",
                verifier.url,
                headers=verifier.subrequest_headers(original),
                raw=True,
                deadline_ms=int(verifier.timeout_seconds * MILLISECONDS_PER_SECOND),
            )
        except AuthExternalError:
            raise
        except BaseExternalError as exc:
            status = exc.upstream_status
            if status is not None and (
                _REDIRECT_STATUS_FIRST <= status <= _REDIRECT_STATUS_LAST
            ):
                raise AuthExternalError(
                    "The verifier sent the browser to log in",
                    provider=cls.name,
                    upstream_status=exc.upstream_status,
                ) from exc
            raise
        return verifier.identity_from(header_values(response.headers.multi_items()))
