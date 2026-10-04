# SPDX-License-Identifier: AGPL-3.0-or-later
"""This server as the forward-auth endpoint of a reverse proxy: Traefik
``ForwardAuth``, nginx ``auth_request``, Caddy ``forward_auth``.

For every request it guards, the proxy sends ``GET /v1/auth/forward/verify``
carrying the original request's cookies and headers, and the original
request itself in ``X-Forwarded-Method``, ``X-Forwarded-Proto``,
``X-Forwarded-Host`` and ``X-Forwarded-Uri``. (nginx sends its subrequest
with the original method unless told ``proxy_method GET;``; set it, and set
those headers with ``proxy_set_header``.) The answer:

- **200**, carrying the identity headers the operator configured, when the
  request carries a session of this server (the ``zx_session`` cookie a
  browser login sets, or a bearer token / API key), checked by the
  framework's own authentication: signature, expiry, a revoked session,
  a disabled account. Nothing else is sent: never the token.
- **401** without one. A browser (an original ``GET``/``HEAD`` accepting
  ``text/html``) is instead sent to ``FORWARD_AUTH_PROVIDER_LOGIN_URL`` with
  a **302**, carrying the page it asked for in the return parameter, but
  only when that page is on a host the operator allows. nginx accepts only
  2xx/401/403 from ``auth_request``, so with ``?redirect_with_401=true`` the
  answer is a 401 carrying the ``Location`` (``auth_request_set $login
  $upstream_http_location; error_page 401 =302 $login;``).
- **403** when the signed-in user may not reach that page: an access rule
  (``/v1/auth/forward/rule``, ROOT only) requires a team they are not a
  live member of, or ``FORWARD_AUTH_PROVIDER_REQUIRE_RULE`` is on and no
  rule covers the page. Also 403 for a disabled account, and for any
  caller that is not one of the trusted proxies.

The ``X-Forwarded-*`` headers say which page is being asked for, so they
decide which rule applies and where a login returns to: they are believed
only from an address in ``FORWARD_AUTH_PROVIDER_TRUSTED_PROXIES``, and
anyone else is refused rather than judged without them. The address is the
peer the app sees, after uvicorn's own proxy-header handling: keep the
proxy out of uvicorn's ``FORWARDED_ALLOW_IPS`` (or it reports the browser's
address instead), and list it in ``TRUSTED_PROXIES`` too, so the rate limit
counts each browser rather than the proxy.

A rule covers a host (or every host) and a path with everything below it.
The path is compared after decoding it once and resolving ``.``/``..`` and
empty segments, as the application behind the proxy will, so ``/%61dmin``
and ``/x/../admin`` are ``/admin``. Of the rules covering a page, the one
with the longest path wins, then one naming the host over one for every
host, then the higher priority.
"""

import ipaddress
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import quote, unquote, urlencode, urlsplit

from fastapi import HTTPException, Request, Response
from pydantic import Field, field_validator
from starlette.datastructures import Headers

from zephyrex.lib.ContentNegotiation import skip_negotiation
from zephyrex.lib.CustomRoute import ExposeIn, custom_route
from zephyrex.lib.DateTimeUtils import ensure_utc
from zephyrex.lib.Environment import env
from zephyrex.lib.InboundSecurity import (
    DEFAULT_READ_RATE_LIMIT,
    _parse_trusted_proxies,
    rate_limit,
)
from zephyrex.lib.Logging import logger
from zephyrex.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    ModelMeta,
    NameMixinModel,
    StringSearchModel,
    UpdateMixinModel,
)
from zephyrex.logic.AbstractLogicManager.ownership import server_side
from zephyrex.logic.BLL_Auth import TeamModel, UserModel, UserTeamModel
from zephyrex.pydantic2.fastapi import AuthType, RouterMixin
from zephyrex.pydantic2.fastapi.resource import create_manager_factory
from zephyrex.pydantic2.fastapi.types import RouteType
from zephyrex.pydantic2.registry import BaseModel

TRUSTED_PROXIES_SETTING = "FORWARD_AUTH_PROVIDER_TRUSTED_PROXIES"
IDENTITY_HEADERS_SETTING = "FORWARD_AUTH_PROVIDER_IDENTITY_HEADERS"
LOGIN_URL_SETTING = "FORWARD_AUTH_PROVIDER_LOGIN_URL"
RETURN_PARAMETER_SETTING = "FORWARD_AUTH_PROVIDER_RETURN_PARAMETER"
RETURN_HOSTS_SETTING = "FORWARD_AUTH_PROVIDER_RETURN_HOSTS"
REQUIRE_RULE_SETTING = "FORWARD_AUTH_PROVIDER_REQUIRE_RULE"
DEFAULT_RETURN_PARAMETER = "rd"

# Every guarded request (each page, script and image) is one subrequest, so
# login's 10/min would lock a browser out on its first page; this is the
# framework's per-address budget for reads.
FORWARD_AUTH_RATE_LIMIT = DEFAULT_READ_RATE_LIMIT

# What an identity header can carry, by the name the operator gives it.
CLAIMS = ("id", "email", "username", "name", "teams")
# Headers that would change how the proxy or browser treats the answer, or
# hand a credential on, rather than describe who is signed in.
FORBIDDEN_IDENTITY_HEADERS = frozenset(
    {
        "authorization",
        "cache-control",
        "connection",
        "content-encoding",
        "content-length",
        "content-type",
        "cookie",
        "host",
        "keep-alive",
        "location",
        "proxy-authenticate",
        "proxy-authorization",
        "set-cookie",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "www-authenticate",
        "x-api-key",
        "x-csrf-token",
    }
)
_HEADER_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*$")
_RETURN_PARAMETER = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_LABEL = r"[a-z0-9](?:[a-z0-9_-]{0,61}[a-z0-9])?"
_HOSTNAME = re.compile(rf"^{_LABEL}(?:\.{_LABEL})*$")
_METHOD = re.compile(r"^[A-Z]{1,16}$")
_MAX_PORT = 65535
# Printable ASCII but "%" (which starts an escape) and, in a list, ",".
_HEADER_SAFE = "".join(chr(c) for c in range(0x20, 0x7F) if chr(c) not in "%,")
_PAGE_METHODS = frozenset({"GET", "HEAD"})
_RETURN_SCHEMES = frozenset({"http", "https"})
_NO_STORE = {"Cache-Control": "no-store"}
_SIGN_IN = {"WWW-Authenticate": "Bearer"}
RULE_ROUTES = [
    RouteType.GET,
    RouteType.LIST,
    RouteType.SEARCH,
    RouteType.CREATE,
    RouteType.UPDATE,
    RouteType.DELETE,
]


# ---------------------------------------------------------------------------
# Hosts, paths and header values
# ---------------------------------------------------------------------------


def normalized_host(value: str) -> str:
    """A host name or address as rules hold it: lower case, no trailing dot,
    no port. ValueError for anything else."""
    host = value.strip().lower().rstrip(".")
    if host.startswith("[") and host.endswith("]"):
        return f"[{ipaddress.IPv6Address(host[1:-1])}]"
    if not _HOSTNAME.match(host):
        raise ValueError(f"not a host name: {value!r}")
    return host


def split_authority(value: str) -> Tuple[str, str]:
    """``host[:port]`` as (the normalized host, the authority to return to).
    ValueError when either part is malformed."""
    authority = value.strip().lower()
    if authority.startswith("["):
        end = authority.find("]")
        host, port = authority[: end + 1], authority[end + 1 :]
    else:
        host, colon, port = authority.partition(":")
        port = colon + port
    if port:
        number = port.removeprefix(":")
        if not number.isdigit() or not 0 < int(number) <= _MAX_PORT:
            raise ValueError(f"not a port: {port!r}")
    return normalized_host(host), authority


def _has_control_characters(text: str) -> bool:
    return any(ord(c) < 0x20 or ord(c) == 0x7F for c in text)


def normalized_path(raw: str) -> str:
    """The path of a request target as the application will resolve it:
    query and fragment dropped, decoded once, backslashes taken as slashes,
    and ``.``, ``..`` and empty segments resolved. ValueError for one that
    does not start with ``/`` or decodes to control characters."""
    path = raw.split("?", 1)[0].split("#", 1)[0]
    if not path.startswith("/"):
        raise ValueError(f"not an absolute path: {raw!r}")
    decoded = unquote(path)
    if _has_control_characters(decoded):
        raise ValueError("the path holds control characters")
    segments: List[str] = []
    for segment in decoded.replace("\\", "/").split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            if segments:
                segments.pop()
            continue
        segments.append(segment)
    return "/" + "/".join(segments)


def covers(prefix: str, path: str) -> bool:
    """Whether a rule for ``prefix`` covers ``path``: the path itself or
    anything below it, never ``/administrator`` for ``/admin``."""
    return prefix == "/" or path == prefix or path.startswith(prefix + "/")


def header_value(text: str, *, list_item: bool = False) -> str:
    """``text`` as a header value: anything but printable ASCII, and ``%``,
    percent-encoded (UTF-8), so no value can end the header or start
    another; in a list, commas too."""
    return quote(text, safe=_HEADER_SAFE if list_item else _HEADER_SAFE + ",")


# ---------------------------------------------------------------------------
# Access rules (ROOT-managed)
# ---------------------------------------------------------------------------


def _checked_host(value: Optional[str]) -> Optional[str]:
    if value is None or not value.strip():
        return None
    return normalized_host(value)


def _checked_prefix(value: str) -> str:
    if "?" in value or "#" in value:
        raise ValueError("a path prefix has no query or fragment")
    return normalized_path(value)


class ForwardAuthRuleModel(
    ApplicationModel,
    UpdateMixinModel,
    NameMixinModel,
    metaclass=ModelMeta,
):
    """Who may reach a host and path through the proxy."""

    host: Optional[str] = Field(
        None, description="Host the rule covers (no port); empty: every host"
    )
    path_prefix: str = Field(
        "/", description="Path the rule covers, with everything below it"
    )
    required_team_id: Optional[str] = Field(
        None, description="Team whose members alone may pass; empty: anyone signed in"
    )
    priority: int = Field(
        0, description="Higher wins between rules for the same host and path"
    )
    enabled: bool = Field(True, description="Whether the rule is applied")

    table_comment: ClassVar[str] = "Who may reach a host and path behind the proxy"
    is_system_entity: ClassVar[bool] = True

    class Create(BaseModel, NameMixinModel):
        host: Optional[str] = None
        path_prefix: str = "/"
        required_team_id: Optional[str] = None
        priority: int = 0
        enabled: bool = True

        @field_validator("host")
        @classmethod
        def normalized_rule_host(cls, value: Optional[str]) -> Optional[str]:
            return _checked_host(value)

        @field_validator("path_prefix")
        @classmethod
        def normalized_rule_prefix(cls, value: str) -> str:
            return _checked_prefix(value)

    class Update(BaseModel, NameMixinModel.Optional):
        host: Optional[str] = None
        path_prefix: Optional[str] = None
        required_team_id: Optional[str] = None
        priority: Optional[int] = None
        enabled: Optional[bool] = None

        @field_validator("host")
        @classmethod
        def normalized_rule_host(cls, value: Optional[str]) -> Optional[str]:
            return _checked_host(value)

        @field_validator("path_prefix")
        @classmethod
        def normalized_rule_prefix(cls, value: Optional[str]) -> str:
            if value is None:
                raise ValueError("path_prefix cannot be cleared; use /")
            return _checked_prefix(value)

    class Search(ApplicationModel.Search, NameMixinModel.Search):
        host: Optional[StringSearchModel] = None
        path_prefix: Optional[StringSearchModel] = None
        enabled: Optional[bool] = None


def _require_server_side(manager: AbstractBLLManager) -> None:
    """Rules are the server's own configuration: only ROOT or SYSTEM (the
    root API key) reads or changes them."""
    requester = manager.optional_requester
    if requester is None or not server_side(requester.id):
        raise HTTPException(
            status_code=403, detail="Only the server's administrator manages this"
        )


class ForwardAuthRuleManager(AbstractBLLManager, RouterMixin):
    _model = ForwardAuthRuleModel

    prefix: ClassVar[Optional[str]] = "/v1/auth/forward/rule"
    tags: ClassVar[Optional[List[str]]] = ["Forward Auth"]
    auth_type: ClassVar[AuthType] = AuthType.API_KEY
    routes_to_register: ClassVar[Optional[List[RouteType]]] = RULE_ROUTES

    def _require_team(self, team_id: str) -> None:
        """The team exists and the requester can see it."""
        TeamDB = TeamModel.DB(self.model_registry.DB.manager.Base)
        if not TeamDB.list(
            requester_id=self.requester.id,
            model_registry=self.model_registry,
            id=team_id,
            filters=[TeamDB.deleted_at.is_(None)],
        ):
            raise HTTPException(status_code=404, detail="No such team")

    def get(
        self,
        include: Optional[Any] = None,
        fields: Optional[Any] = None,
        **kwargs: Any,
    ) -> Any:
        _require_server_side(self)
        return super().get(include=include, fields=fields, **kwargs)

    def list(self, *args: Any, **kwargs: Any) -> Any:
        _require_server_side(self)
        return super().list(*args, **kwargs)

    def search(self, *args: Any, **kwargs: Any) -> Any:
        _require_server_side(self)
        return super().search(*args, **kwargs)

    def create_validation(self, entity: Any) -> None:
        if entity.required_team_id:
            self._require_team(entity.required_team_id)

    def create(self, **kwargs: Any) -> Any:
        _require_server_side(self)
        return super().create(**kwargs)

    def update(self, id: str, **kwargs: Any) -> Any:
        _require_server_side(self)
        if kwargs.get("required_team_id"):
            self._require_team(kwargs["required_team_id"])
        return super().update(id, **kwargs)

    def delete(self, id: str) -> None:
        _require_server_side(self)
        super().delete(id)


ForwardAuthRuleModel.Manager = ForwardAuthRuleManager


def enabled_rules(model_registry: Any) -> List[ForwardAuthRuleModel]:
    """The rules in force: enabled, and not deleted (a ROOT read includes
    deleted rows)."""
    RuleDB = ForwardAuthRuleModel.DB(model_registry.DB.manager.Base)
    rules: List[ForwardAuthRuleModel] = RuleDB.list(
        requester_id=env("ROOT_ID"),
        model_registry=model_registry,
        filters=[RuleDB.enabled.is_(True), RuleDB.deleted_at.is_(None)],
        return_type="dto",
        override_dto=ForwardAuthRuleModel,
    )
    return rules


def governing_rule(
    rules: Sequence[ForwardAuthRuleModel], host: str, path: str
) -> Optional[ForwardAuthRuleModel]:
    """The rule that decides ``host`` + ``path``: the longest covering path,
    then a rule for the host over one for every host, then priority."""
    covering = [
        rule
        for rule in rules
        if (rule.host is None or rule.host == host) and covers(rule.path_prefix, path)
    ]
    if not covering:
        return None
    return max(
        covering,
        key=lambda rule: (
            len(rule.path_prefix),
            rule.host is not None,
            rule.priority,
            str(rule.id),
        ),
    )


@dataclass(frozen=True)
class AccessDecision:
    allowed: bool
    reason: str
    rule_id: Optional[str] = None


def decide(
    rules: Sequence[ForwardAuthRuleModel],
    host: str,
    path: str,
    team_ids: Sequence[str],
    require_rule: bool,
) -> AccessDecision:
    """Whether a signed-in user who is a live member of ``team_ids`` may
    reach ``host`` + ``path``."""
    rule = governing_rule(rules, host, path)
    if rule is None:
        if require_rule:
            return AccessDecision(False, "No rule allows this page")
        return AccessDecision(True, "Signed in")
    rule_id = str(rule.id)
    if rule.required_team_id is None:
        return AccessDecision(True, "Signed in", rule_id)
    if rule.required_team_id in team_ids:
        return AccessDecision(True, "A member of the required team", rule_id)
    return AccessDecision(False, "Not a member of the required team", rule_id)


def live_teams(model_registry: Any, user_id: str) -> Dict[str, str]:
    """The teams (id to name) the user is a member of through an enabled,
    unrevoked, unexpired membership of a team that is not deleted."""
    base = model_registry.DB.manager.Base
    UserTeamDB = UserTeamModel.DB(base)
    now = datetime.now(timezone.utc)
    memberships: List[UserTeamModel] = UserTeamDB.list(
        requester_id=env("ROOT_ID"),
        model_registry=model_registry,
        user_id=user_id,
        enabled=True,
        filters=[UserTeamDB.deleted_at.is_(None)],
        return_type="dto",
        override_dto=UserTeamModel,
    )
    team_ids = [
        str(m.team_id)
        for m in memberships
        if m.expires_at is None or ensure_utc(m.expires_at) > now
    ]
    if not team_ids:
        return {}
    TeamDB = TeamModel.DB(base)
    teams: List[TeamModel] = TeamDB.list(
        requester_id=env("ROOT_ID"),
        model_registry=model_registry,
        filters=[TeamDB.id.in_(team_ids), TeamDB.deleted_at.is_(None)],
        return_type="dto",
        override_dto=TeamModel,
    )
    return {str(team.id): team.name or "" for team in teams}


def live_user(model_registry: Any, user_id: str) -> Optional[UserModel]:
    """The user, unless deleted (a ROOT read includes deleted rows)."""
    UserDB = UserModel.DB(model_registry.DB.manager.Base)
    users: List[UserModel] = UserDB.list(
        requester_id=env("ROOT_ID"),
        model_registry=model_registry,
        id=user_id,
        filters=[UserDB.deleted_at.is_(None)],
        return_type="dto",
        override_dto=UserModel,
    )
    return users[0] if users else None


# ---------------------------------------------------------------------------
# The operator's configuration
# ---------------------------------------------------------------------------


class ForwardAuthMisconfigured(ValueError):
    """A setting the operator must fix; the endpoint answers 503 until then."""


def parse_identity_headers(raw: str) -> Tuple[Tuple[str, str], ...]:
    """``Header=claim`` pairs, comma separated, e.g.
    ``X-Forwarded-User=id,X-Forwarded-Groups=teams``."""
    pairs: List[Tuple[str, str]] = []
    seen: set[str] = set()
    for entry in (part.strip() for part in raw.split(",") if part.strip()):
        name, equals, claim = (piece.strip() for piece in entry.partition("="))
        if not equals or not _HEADER_NAME.match(name):
            raise ForwardAuthMisconfigured(
                f"{IDENTITY_HEADERS_SETTING}: {entry!r} is not Header=claim"
            )
        if name.lower() in FORBIDDEN_IDENTITY_HEADERS:
            raise ForwardAuthMisconfigured(
                f"{IDENTITY_HEADERS_SETTING}: {name} cannot carry identity"
            )
        if claim not in CLAIMS:
            raise ForwardAuthMisconfigured(
                f"{IDENTITY_HEADERS_SETTING}: {claim!r} is not one of {', '.join(CLAIMS)}"
            )
        if name.lower() in seen:
            raise ForwardAuthMisconfigured(
                f"{IDENTITY_HEADERS_SETTING}: {name} is named twice"
            )
        seen.add(name.lower())
        pairs.append((name, claim))
    return tuple(pairs)


def parse_login_url(raw: str) -> Optional[str]:
    url = raw.strip()
    if not url:
        return None
    parts = urlsplit(url)
    if parts.scheme not in _RETURN_SCHEMES or not parts.netloc or parts.fragment:
        raise ForwardAuthMisconfigured(
            f"{LOGIN_URL_SETTING}: an absolute http(s) URL without a fragment"
        )
    return url


def parse_return_parameter(raw: str) -> str:
    name = raw.strip() or DEFAULT_RETURN_PARAMETER
    if not _RETURN_PARAMETER.match(name):
        raise ForwardAuthMisconfigured(
            f"{RETURN_PARAMETER_SETTING}: letters, digits, '_', '.' or '-'"
        )
    return name


def parse_return_hosts(raw: str) -> Tuple[str, ...]:
    """Hosts a login may return to: ``app.example.com`` exactly, or
    ``.example.com`` for any host below example.com."""
    hosts: List[str] = []
    for entry in (part.strip() for part in raw.split(",") if part.strip()):
        below = entry.startswith(".")
        try:
            host = normalized_host(entry[1:] if below else entry)
        except ValueError:
            raise ForwardAuthMisconfigured(
                f"{RETURN_HOSTS_SETTING}: {entry!r} is not a host name"
            ) from None
        hosts.append(f".{host}" if below else host)
    return tuple(hosts)


def parse_trusted_proxies(raw: str) -> Tuple[ipaddress._BaseNetwork, ...]:
    try:
        return _parse_trusted_proxies(raw.strip())
    except ValueError as exc:
        raise ForwardAuthMisconfigured(f"{TRUSTED_PROXIES_SETTING}: {exc}") from None


@dataclass(frozen=True)
class ForwardAuthConfig:
    trusted_proxies: Tuple[ipaddress._BaseNetwork, ...]
    identity_headers: Tuple[Tuple[str, str], ...]
    login_url: Optional[str]
    return_parameter: str
    return_hosts: Tuple[str, ...]
    require_rule: bool

    @classmethod
    def load(cls) -> "ForwardAuthConfig":
        """The settings as they stand; ForwardAuthMisconfigured names the
        first that is wrong."""
        return cls(
            trusted_proxies=parse_trusted_proxies(env(TRUSTED_PROXIES_SETTING)),
            identity_headers=parse_identity_headers(env(IDENTITY_HEADERS_SETTING)),
            login_url=parse_login_url(env(LOGIN_URL_SETTING)),
            return_parameter=parse_return_parameter(env(RETURN_PARAMETER_SETTING)),
            return_hosts=parse_return_hosts(env(RETURN_HOSTS_SETTING)),
            require_rule=env(REQUIRE_RULE_SETTING).strip().lower() == "true",
        )

    @staticmethod
    def problems() -> List[str]:
        """Every wrong setting, and a note when no proxy is trusted."""
        checks = (
            (parse_trusted_proxies, TRUSTED_PROXIES_SETTING),
            (parse_identity_headers, IDENTITY_HEADERS_SETTING),
            (parse_login_url, LOGIN_URL_SETTING),
            (parse_return_parameter, RETURN_PARAMETER_SETTING),
            (parse_return_hosts, RETURN_HOSTS_SETTING),
        )
        found: List[str] = []
        for parse, setting in checks:
            try:
                parse(env(setting))
            except ForwardAuthMisconfigured as exc:
                found.append(str(exc))
        if not env(TRUSTED_PROXIES_SETTING).strip():
            found.append(
                f"{TRUSTED_PROXIES_SETTING} is unset; every forward-auth request is refused"
            )
        return found

    def trusts(self, peer: Optional[str]) -> bool:
        if not peer:
            return False
        try:
            address = ipaddress.ip_address(peer)
        except ValueError:
            return False
        return any(address in network for network in self.trusted_proxies)

    def may_return_to(self, host: str) -> bool:
        return any(
            host.endswith(allowed) if allowed.startswith(".") else host == allowed
            for allowed in self.return_hosts
        )

    def identity(self, user: UserModel, teams: Mapping[str, str]) -> Dict[str, str]:
        """Exactly the configured identity headers, each sent (empty when
        the user has no such value) so the proxy overwrites any copy the
        client sent."""
        name = user.display_name or " ".join(
            part for part in (user.first_name, user.last_name) if part
        )
        claims = {
            "id": header_value(str(user.id)),
            "email": header_value(user.email or ""),
            "username": header_value(user.username or ""),
            "name": header_value(name),
            "teams": ",".join(
                header_value(team, list_item=True) for team in sorted(teams.values())
            ),
        }
        return {header: claims[claim] for header, claim in self.identity_headers}


# ---------------------------------------------------------------------------
# The proxied request and the answer
# ---------------------------------------------------------------------------


@dataclass
class ForwardAuthRefusal(Exception):
    """An answer other than 200, as the proxy will relay it."""

    status: int
    detail: str
    headers: Dict[str, str] = field(default_factory=dict)

    def response(self) -> Response:
        return Response(
            content=json.dumps({"detail": self.detail}),
            status_code=self.status,
            media_type="application/json",
            headers={**_NO_STORE, **self.headers},
        )


def _single(headers: Headers, name: str, *, list_allowed: bool = False) -> str:
    """The one value of a header the proxy sets; 400 when it is missing,
    repeated, or (where a value is one token) a list: a proxy that appends
    rather than overwrites leaves the client's own value first."""
    values = headers.getlist(name)
    if not values or not values[0].strip():
        raise ForwardAuthRefusal(400, f"{name} is required")
    if len(values) > 1 or (not list_allowed and "," in values[0]):
        raise ForwardAuthRefusal(400, f"{name} must carry exactly one value")
    return values[0].strip()


@dataclass(frozen=True)
class ForwardedRequest:
    """The original request, as a trusted proxy described it."""

    host: str
    authority: str
    uri: str
    path: str
    proto: Optional[str]
    method: str

    @classmethod
    def of(cls, headers: Headers) -> "ForwardedRequest":
        try:
            host, authority = split_authority(_single(headers, "X-Forwarded-Host"))
        except ValueError:
            raise ForwardAuthRefusal(400, "X-Forwarded-Host is not a host") from None
        uri = _single(headers, "X-Forwarded-Uri", list_allowed=True)
        # A path such as //evil.example/x is still a path on this host: the
        # return URL always names the forwarded host before it.
        if _has_control_characters(uri) or any(c in uri for c in " \\"):
            raise ForwardAuthRefusal(400, "X-Forwarded-Uri is not a request path")
        try:
            path = normalized_path(uri)
        except ValueError:
            raise ForwardAuthRefusal(
                400, "X-Forwarded-Uri is not a request path"
            ) from None
        proto = (
            _single(headers, "X-Forwarded-Proto").lower()
            if headers.getlist("X-Forwarded-Proto")
            else None
        )
        if proto is not None and proto not in _RETURN_SCHEMES:
            raise ForwardAuthRefusal(400, "X-Forwarded-Proto is not http or https")
        method = (
            _single(headers, "X-Forwarded-Method").upper()
            if headers.getlist("X-Forwarded-Method")
            else "GET"
        )
        if not _METHOD.match(method):
            raise ForwardAuthRefusal(400, "X-Forwarded-Method is not a method")
        return cls(host, authority, uri, path, proto, method)

    def is_page_load(self, accept: str) -> bool:
        """A browser navigating: a GET/HEAD that accepts HTML."""
        return self.method in _PAGE_METHODS and "text/html" in accept.lower()

    def return_url(self) -> Optional[str]:
        if self.proto is None:
            return None
        return f"{self.proto}://{self.authority}{self.uri}"


def sign_in_answer(
    config: ForwardAuthConfig,
    forwarded: ForwardedRequest,
    accept: str,
    redirect_with_401: bool,
) -> ForwardAuthRefusal:
    """401, or for a browser the way to the login page, returning to the
    page asked for only on a host the operator allows."""
    if config.login_url is None or not forwarded.is_page_load(accept):
        return ForwardAuthRefusal(401, "Sign in required", dict(_SIGN_IN))
    location = config.login_url
    return_url = forwarded.return_url()
    if return_url is not None and config.may_return_to(forwarded.host):
        separator = "&" if urlsplit(location).query else "?"
        location += separator + urlencode({config.return_parameter: return_url})
    if redirect_with_401:
        return ForwardAuthRefusal(
            401, "Sign in required", {**_SIGN_IN, "Location": location}
        )
    return ForwardAuthRefusal(302, "Sign in required", {"Location": location})


class ForwardAuthManager(AbstractBLLManager, RouterMixin):
    """The forward-auth endpoint: no table of its own."""

    prefix: ClassVar[Optional[str]] = "/v1/auth/forward"
    tags: ClassVar[Optional[List[str]]] = ["Forward Auth"]
    auth_type: ClassVar[AuthType] = AuthType.NONE
    routes_to_register: ClassVar[Optional[List[RouteType]]] = []

    def signed_in_user(self, request: Request) -> Optional[UserModel]:
        """The user the request's session (cookie, bearer token or API key)
        belongs to, as the framework authenticates any request; None when
        it carries none that is valid. 403 for a disabled account."""
        authenticate = create_manager_factory(
            ForwardAuthManager, self.model_registry, AuthType.JWT
        )
        try:
            requester_id = authenticate(request=request).requester_id
        except HTTPException as exc:
            if exc.status_code == 403:
                raise ForwardAuthRefusal(403, "This account is disabled") from None
            if exc.status_code in (401, 404):
                return None
            raise
        user = live_user(self.model_registry, str(requester_id))
        if user is None:
            return None
        if user.active is False:
            raise ForwardAuthRefusal(403, "This account is disabled")
        return user

    def answer(self, request: Request, redirect_with_401: bool) -> Response:
        try:
            config = ForwardAuthConfig.load()
        except ForwardAuthMisconfigured as exc:
            logger.error("forward_auth_provider: %s", exc)
            raise ForwardAuthRefusal(503, "Forward auth is misconfigured") from None
        if not config.trusts(request.client.host if request.client else None):
            raise ForwardAuthRefusal(403, "Only a trusted proxy may ask")
        forwarded = ForwardedRequest.of(request.headers)
        user = self.signed_in_user(request)
        if user is None:
            raise sign_in_answer(
                config, forwarded, request.headers.get("accept", ""), redirect_with_401
            )
        teams = live_teams(self.model_registry, str(user.id))
        decision = decide(
            enabled_rules(self.model_registry),
            forwarded.host,
            forwarded.path,
            list(teams),
            config.require_rule,
        )
        if not decision.allowed:
            raise ForwardAuthRefusal(403, decision.reason)
        return Response(
            status_code=200, headers={**_NO_STORE, **config.identity(user, teams)}
        )

    @custom_route(
        method="GET",
        path="/verify",
        authentication_type="none",
        openapi_tags=("Forward Auth",),
        summary="Decide a reverse proxy's forward-auth subrequest",
        expose_in=(ExposeIn.REST,),
        response_class=Response,
    )
    @rate_limit(FORWARD_AUTH_RATE_LIMIT, scope="ip")
    def verify_route(
        self, request: Request, redirect_with_401: bool = False
    ) -> Response:
        try:
            return self.answer(request, redirect_with_401)
        except ForwardAuthRefusal as refusal:
            return refusal.response()


# The proxy forwards the guarded page's Accept, which need not name any
# format this API negotiates; the verifier's answer carries no body to
# negotiate.
skip_negotiation(r"/v1/auth/forward/verify")
