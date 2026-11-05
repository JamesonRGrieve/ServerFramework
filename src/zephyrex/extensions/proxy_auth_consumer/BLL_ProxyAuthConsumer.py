# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sign-in from the identity a trusted reverse proxy asserts.

``POST /v1/auth/proxy/login`` signs in the user an authenticating reverse
proxy (oauth2-proxy, Authelia, Authentik's outpost, Apache ``mod_auth_*``,
nginx ``auth_request``) has already authenticated, and names in a request
header.

Trust. The headers are believed only when the connection's peer is in
``PROXY_AUTH_CONSUMER_TRUSTED_PROXIES``; from anyone else they are ignored,
since any client can send them. An empty list believes no one, and a
malformed one (or a wildcard) is a misconfiguration that believes no one
(503). The proxy must set or strip each header on every request: a header
sent more than once is refused (400), since a proxy that appends rather than
replaces would put the client's own value first.

Mapping.

- ``PROXY_AUTH_CONSUMER_USER_HEADER`` (default ``X-Forwarded-User``; e.g.
  ``X-Remote-User`` or ``Remote-User``) carries the identity: the proxy's
  stable name for the user, matched exactly (case-sensitive). It may not be
  empty, longer than :data:`MAX_IDENTITY_LENGTH`, or hold control
  characters or a comma (the shape of two values joined).
- ``PROXY_AUTH_CONSUMER_NAME_HEADER`` (default ``X-Forwarded-Name``) gives
  a new account its display name.
- ``PROXY_AUTH_CONSUMER_EMAIL_HEADER`` (default ``X-Forwarded-Email``) is
  read only when ``PROXY_AUTH_CONSUMER_TRUST_EMAIL`` says the proxy vouches
  for the emails it asserts (it verified them). Then an identity with no
  link may sign in to the one account that has that email, and a new
  account gets it. Otherwise the email is ignored entirely: a new account
  has none, so an asserted email can neither reach nor shadow an existing
  account.
- Groups are not consumed: mapping them to teams or roles would let the
  proxy grant permissions, which is not this extension's to decide.

A user is found by a :class:`UserProxyAuthLinkModel` keyed by the identity.
With no link (and no trusted email match) a new account is made as
``REGISTRATION_MODE`` allows (``invite`` needs a pending invitation for a
trusted email). Links are made only by a sign-in through the trusted proxy
or by ROOT/SYSTEM, never claimed by a user; nothing links to, or signs in
as, ROOT, SYSTEM or the template user. A success issues the session
password login issues (or its MFA challenge).

The route is rate limited per client address; behind a proxy that address
is the proxy's unless ``TRUSTED_PROXIES`` also names it, so that
``X-Forwarded-For`` gives each user their own allowance.
"""

import ipaddress
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, ClassVar, Dict, List, NamedTuple, Optional

from fastapi import HTTPException, Request, Response, status
from pydantic import BaseModel as RouteModel
from pydantic import Field

from zephyrex.lib import Environment
from zephyrex.lib.CustomRoute import ExposeIn, custom_route
from zephyrex.lib.Environment import env, env_bool
from zephyrex.lib.InboundSecurity import (
    DEFAULT_AUTH_RATE_LIMIT,
    _parse_trusted_proxies,
    rate_limit,
)
from zephyrex.lib.Logging import logger
from zephyrex.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    DateSearchModel,
    ModelMeta,
    StringSearchModel,
    UpdateMixinModel,
)
from zephyrex.logic.AbstractLogicManager.ownership import server_side
from zephyrex.logic.BLL_Auth import (
    UserManager,
    UserModel,
    _invitation_hooks,
    refuse_internal_account,
)
from zephyrex.pydantic2.fastapi import AuthType, RouterMixin, RouteType
from zephyrex.pydantic2.registry import BaseModel

ROUTE_PREFIX = "/v1/auth/proxy"
PROXY_AUTH_TAGS = ("Proxy Header Sign-in",)
DEFAULT_USER_HEADER = "X-Forwarded-User"
DEFAULT_EMAIL_HEADER = "X-Forwarded-Email"
DEFAULT_NAME_HEADER = "X-Forwarded-Name"
MAX_IDENTITY_LENGTH = 256
MAX_EMAIL_LENGTH = 320
MAX_NAME_LENGTH = 256
# An RFC 7230 token restricted to letters, digits and hyphens: CGI-style
# servers fold ``X_Forwarded_User`` and ``X-Forwarded-User`` into one name,
# so a proxy stripping only one spelling would let a client's through.
_HEADER_NAME = re.compile(r"[A-Za-z0-9-]+")
_EMAIL = re.compile(r"[^@\s,]+@[^@\s,]+")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _bad_request(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


def _misconfigured() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Proxy sign-in is misconfigured",
    )


def _forbidden(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class UserProxyAuthLinkModel(
    ApplicationModel,
    UpdateMixinModel,
    UserModel.Reference,
    metaclass=ModelMeta,
):
    """An identity the trusted proxy asserts, which signs in as a user."""

    identity: str = Field(..., description="The identity the proxy asserts, exactly")
    last_login_at: Optional[datetime] = Field(
        None, description="Last successful proxy sign-in"
    )

    table_comment: ClassVar[str] = "Links a local user to a proxy-asserted identity"

    class Create(BaseModel, UserModel.Reference.ID):
        identity: str

    class Update(BaseModel):
        last_login_at: Optional[datetime] = Field(
            None, description="Set by sign-in only"
        )

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
        UserModel.Reference.ID.Search,
    ):
        identity: Optional[StringSearchModel] = None
        last_login_at: Optional[DateSearchModel] = None


class ProxyLoginRequest(RouteModel):
    """Nothing: the credential is the trusted proxy's headers."""


class ProxyLoginResponse(RouteModel):
    """The password-login response (``user``, ``token``, ``preferences``,
    ``teams``, ``session_key``) for the asserted user or, for a user with a
    verified second factor, the MFA challenge to complete at POST
    /v1/user/authorize/mfa, exactly as a password login would."""

    identity: str
    user_id: str
    user: Optional[Dict[str, Any]] = None
    token: Optional[str] = Field(
        None, description="JWT to present as Authorization: Bearer"
    )
    session_key: Optional[str] = None
    preferences: Optional[Dict[str, Any]] = None
    teams: Optional[List[Dict[str, Any]]] = None
    mfa_required: bool = False
    challenge_token: Optional[str] = None
    methods: Optional[List[Dict[str, str]]] = None


# ---------------------------------------------------------------------------
# The asserted identity
# ---------------------------------------------------------------------------


class AssertedIdentity(NamedTuple):
    """What the trusted proxy says about the user."""

    identity: str
    email: Optional[str]
    name: Optional[str]


def _header_name(key: str, default: str) -> str:
    """The configured header name; 503 when it is not a plain token."""
    name = (env(key) or default).strip()
    if not _HEADER_NAME.fullmatch(name):
        logger.error("proxy_auth_consumer: %s is not a header name", key)
        raise _misconfigured()
    return name


def _from_trusted_proxy(request: Request) -> bool:
    """The connection's peer is a configured authenticating proxy. A
    malformed allow-list is a misconfiguration that trusts no one (503)."""
    peer = request.client.host if request.client else None
    try:
        networks = _parse_trusted_proxies(
            (env("PROXY_AUTH_CONSUMER_TRUSTED_PROXIES") or "").strip()
        )
    except ValueError as exc:
        logger.error(
            "proxy_auth_consumer: PROXY_AUTH_CONSUMER_TRUSTED_PROXIES: %s", exc
        )
        raise _misconfigured() from None
    if not peer or not networks:
        return False
    try:
        address = ipaddress.ip_address(peer)
    except ValueError:
        return False
    return any(address in network for network in networks)


def _single_value(request: Request, header: str, limit: int) -> Optional[str]:
    """The header's one value, as UTF-8, stripped; None when absent or
    blank. 400 when it is sent twice, is not UTF-8, is longer than
    ``limit`` or holds a control character."""
    values = request.headers.getlist(header)
    if not values:
        return None
    if len(values) > 1:
        raise _bad_request(f"{header} was sent more than once")
    try:
        value = values[0].encode("latin-1").decode("utf-8").strip()
    except UnicodeError:
        raise _bad_request(f"{header} is not UTF-8") from None
    if len(value) > limit:
        raise _bad_request(f"{header} is too long")
    if any(unicodedata.category(char) == "Cc" for char in value):
        raise _bad_request(f"{header} holds a control character")
    return value or None


def asserted_identity(request: Request) -> AssertedIdentity:
    """The identity the trusted proxy asserts for this request. 401 when
    the peer is not a trusted proxy or asserts no one; 400 when a header
    is malformed."""
    user_header = _header_name("PROXY_AUTH_CONSUMER_USER_HEADER", DEFAULT_USER_HEADER)
    name_header = _header_name("PROXY_AUTH_CONSUMER_NAME_HEADER", DEFAULT_NAME_HEADER)
    email_header = _header_name(
        "PROXY_AUTH_CONSUMER_EMAIL_HEADER", DEFAULT_EMAIL_HEADER
    )
    if not _from_trusted_proxy(request):
        if user_header in request.headers:
            logger.warning(
                "proxy_auth_consumer: ignored %s from %s, not a trusted proxy",
                user_header,
                request.client.host if request.client else "an unknown peer",
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Proxy sign-in is accepted only through a trusted proxy",
        )
    identity = _single_value(request, user_header, MAX_IDENTITY_LENGTH)
    if identity is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="The proxy asserted no user",
        )
    validate_identity(identity)
    email: Optional[str] = None
    if env_bool("PROXY_AUTH_CONSUMER_TRUST_EMAIL"):
        email = _single_value(request, email_header, MAX_EMAIL_LENGTH)
        if email is not None and not _EMAIL.fullmatch(email):
            raise _bad_request(f"{email_header} is not an email address")
        if email is not None:
            # Stored emails are normalized at registration; match the same.
            email = UserManager._normalize_identifier(email)
    name = _single_value(request, name_header, MAX_NAME_LENGTH)
    return AssertedIdentity(identity=identity, email=email, name=name)


def validate_identity(identity: str) -> None:
    """400 for an identity no proxy should assert: empty, too long, with a
    control character, or with a comma (two values joined into one)."""
    if not identity or len(identity) > MAX_IDENTITY_LENGTH:
        raise _bad_request("The asserted identity is empty or too long")
    if "," in identity or any(unicodedata.category(c) == "Cc" for c in identity):
        raise _bad_request("The asserted identity is not a single identity")


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class UserProxyAuthLinkManager(AbstractBLLManager, RouterMixin):
    """A user's proxy identities (read and unlink), and the sign-in."""

    _model = UserProxyAuthLinkModel
    prefix: ClassVar[Optional[str]] = ROUTE_PREFIX
    tags: ClassVar[Optional[List[str]]] = list(PROXY_AUTH_TAGS)
    auth_type: ClassVar[AuthType] = AuthType.JWT
    # No CREATE or UPDATE: links come from a sign-in through the trusted
    # proxy, or from ROOT and SYSTEM through ``create``; a user could
    # otherwise claim another's identity.
    routes_to_register: ClassVar[Optional[List[RouteType]]] = [
        RouteType.GET,
        RouteType.LIST,
        RouteType.SEARCH,
        RouteType.DELETE,
    ]

    def create(self, **kwargs: Any) -> Any:
        """Link an identity to a user: ROOT and SYSTEM only. The link is
        written as its user, so they can see and remove it."""
        if not server_side(str(self.requester.id)):
            raise _forbidden("Proxy identities are linked by signing in with them")
        if isinstance(kwargs.get("entities"), list):
            return [self.create(**dict(entity)) for entity in kwargs["entities"]]
        user_id = str(kwargs.get("user_id") or "")
        identity = str(kwargs.get("identity") or "").strip()
        if not user_id:
            raise _bad_request("A link names its user and identity")
        validate_identity(identity)
        self._account(user_id)
        if self.linked(identity) is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="That identity is already linked",
            )
        return self._link(identity, user_id)

    def update(self, id: str, **kwargs: Any) -> Any:
        """Only sign-in stamps a link (its last login)."""
        if not server_side(str(self.requester.id)):
            raise _forbidden("Proxy identity links are not edited")
        kwargs.pop("user_id", None)
        kwargs.pop("identity", None)
        return super().update(id, **kwargs)

    def _link(self, identity: str, user_id: str) -> Any:
        return self.DB.create(
            requester_id=user_id,
            model_registry=self.model_registry,
            return_type="dto",
            override_dto=self.model_registry.apply(UserProxyAuthLinkModel),
            identity=identity,
            user_id=user_id,
        )

    def linked(self, identity: str) -> Optional[Any]:
        """The live link for ``identity`` (exact match)."""
        links = self.DB.list(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            return_type="dto",
            override_dto=UserProxyAuthLinkModel,
            filters=[self.DB.identity == identity, self.DB.deleted_at.is_(None)],
        )
        return links[0] if links else None

    def _users(self, **filters: Any) -> List[Dict[str, Any]]:
        UserDB = UserModel.DB(self.model_registry.DB.manager.Base)
        users: List[Dict[str, Any]] = UserDB.list(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            filters=[UserDB.deleted_at.is_(None)]
            + [getattr(UserDB, key) == value for key, value in filters.items()],
        )
        return users

    def _account(self, user_id: str) -> Dict[str, Any]:
        """The live, active account ``user_id``, which may not be an
        internal one (ROOT, SYSTEM, the template user). 403 otherwise."""
        refuse_internal_account(user_id)
        users = self._users(id=user_id)
        if len(users) != 1:
            raise _forbidden("The account no longer exists")
        if users[0].get("active") is False:
            raise _forbidden("The account is disabled")
        return users[0]

    # -- signing in ---------------------------------------------------------

    def _registered(self, asserted: AssertedIdentity) -> Dict[str, Any]:
        """A new account for the asserted identity, as REGISTRATION_MODE
        allows. Its email is set only when the proxy is trusted for it."""
        # Read through the module: the settings snapshot is replaced when
        # the environment is reloaded, so an imported name goes stale.
        mode = Environment.settings.REGISTRATION_MODE
        invitations: List[Dict[str, Any]] = []
        if mode == "closed":
            raise _forbidden("No account is linked to this identity")
        if mode == "invite":
            pending_for = _invitation_hooks["pending_invitations_for_user"]
            if asserted.email and pending_for is not None:
                invitations = pending_for("", asserted.email, self.model_registry)
            if not invitations:
                raise _forbidden("User registration requires an invitation")
        created = UserModel.DB(self.model_registry.DB.manager.Base).create(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            return_type="dto",
            override_dto=self.model_registry.apply(UserModel),
            email=asserted.email,
            display_name=asserted.name or asserted.identity,
        )
        apply_invitation = _invitation_hooks["apply_to_user"]
        for invitation in invitations:
            if apply_invitation is not None:
                apply_invitation(invitation, str(created.id), self.model_registry)
        logger.info(
            "proxy_auth_consumer: created user %s for %s", created.id, asserted.identity
        )
        return self._account(str(created.id))

    def _first_link(self, asserted: AssertedIdentity) -> Any:
        """Link an identity seen for the first time: to the one account
        with its trusted email, else to a new account."""
        user_id = UserManager.user_id_for_verified_email(
            asserted.email, self.model_registry
        )
        if user_id is not None:
            user = self._account(user_id)
        else:
            user = self._registered(asserted)
        return self._link(asserted.identity, str(user["id"]))

    def resolve_user(self, asserted: AssertedIdentity) -> Dict[str, Any]:
        """The active local user the asserted identity signs in as, linked
        (or created) on first sign-in."""
        link = self.linked(asserted.identity) or self._first_link(asserted)
        user = self._account(str(link.user_id))
        self.DB.update(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            id=link.id,
            new_properties={"last_login_at": _now()},
        )
        return user

    @custom_route(
        method="POST",
        path="/login",
        input_model=ProxyLoginRequest,
        output_model=ProxyLoginResponse,
        authentication_type="none",
        openapi_tags=PROXY_AUTH_TAGS,
        expose_in=(ExposeIn.REST,),
        summary="Sign in as the user the trusted proxy asserts",
    )
    @rate_limit(DEFAULT_AUTH_RATE_LIMIT, scope="ip")
    async def login_route(
        self, body: ProxyLoginRequest, request: Request, response: Response
    ) -> ProxyLoginResponse:
        asserted = asserted_identity(request)
        user = self.resolve_user(asserted)
        user_id = str(user["id"])
        challenge = UserManager.mfa_challenge(user_id, self.model_registry)
        if challenge is not None:
            return ProxyLoginResponse(
                identity=asserted.identity, user_id=user_id, **challenge
            )
        login = UserManager._complete_login(user, self.model_registry, response)
        return ProxyLoginResponse(identity=asserted.identity, user_id=user_id, **login)
