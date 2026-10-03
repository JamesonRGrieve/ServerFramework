# SPDX-License-Identifier: AGPL-3.0-or-later
"""This server as a SAML 2.0 Identity Provider: registered Service
Providers sign their users in with the users' accounts here.

Routes (all under ``/v1/saml_provider``):

- ``GET /metadata``: the IdP's metadata (entity id, signing certificate,
  SSO endpoints, NameID formats), for SPs to register.
- ``GET /sso`` (HTTP-Redirect) and ``POST /sso`` (HTTP-POST): an
  SP-initiated AuthnRequest. It is checked (a registered, enabled SP; a
  fresh, schema-valid request addressed to this IdP and not seen before;
  an ACS URL that exactly matches one registered; the signature verified
  against the SP's certificate when it carries one, and required when the
  SP is registered so) and parked, and the browser is sent on to
  ``/sso/continue``. Anything refused before the ACS is known to be the
  SP's is answered here, never sent anywhere.
- ``GET /sso/continue?ticket=…``: answers a parked request for the
  signed-in user (the session cookie, through the framework's usual auth
  path). A browser with no session is sent to the app's sign-in page
  (``SAML_PROVIDER_LOGIN_URL``) with the ``href`` return cookie the sign-in
  pages already honour, and comes back here. The answer is a page posting
  a signed Response, with a signed (and, for an SP that wants it,
  encrypted) Assertion, to the ACS URL: audience the SP, recipient and
  destination the ACS URL, InResponseTo the request, valid for
  ``SAML_PROVIDER_ASSERTION_LIFETIME_SECONDS`` (300 by default).
- ``GET /initiate?sp=<entity id>``: IdP-initiated SSO, off unless the SP's
  registration sets ``allow_idp_initiated``.
- ``/service_provider``: the SP registrations, which only root or system
  may read or change.

The NameID is pairwise and persistent by default (a random identifier per
user and SP, kept in ``saml_subjects``), or the user's email address for an
SP registered with ``name_id_format="email"``. An SP receives only the
attributes its registration lists (see ``RELEASABLE_ATTRIBUTES``).
"""

from __future__ import annotations

import asyncio
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import (
    Any,
    Callable,
    ClassVar,
    Dict,
    List,
    Literal,
    Optional,
    Tuple,
    TypeVar,
)
from urllib.parse import urlsplit

import jwt
from fastapi import HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel as RouteModel
from pydantic import Field
from saml2 import BINDING_HTTP_POST, BINDING_HTTP_REDIRECT, saml, samlp
from saml2.server import Server

from zephyrex.database.StaticPermissions import (
    is_any_internal_id,
    is_root_id,
    is_system_id,
)
from zephyrex.extensions.saml_provider import IdP
from zephyrex.extensions.saml_provider.IdP import (
    NAME_ID_FORMATS,
    RELEASABLE_ATTRIBUTES,
    IdPSite,
    RefusedMessage,
    ServiceProvider,
    SignatureRefused,
    SigningIdentity,
)
from zephyrex.extensions.saml_provider.PRV_SAMLSigning import (
    PRV_SAMLSigningKey,
    SigningKeyMissing,
)
from zephyrex.lib.AuthProvider import get_auth_provider
from zephyrex.lib.CustomRoute import ExposeIn, custom_route
from zephyrex.lib.DateTimeUtils import ensure_utc
from zephyrex.lib.Environment import env
from zephyrex.lib.ContentNegotiation import accept_form_bodies
from zephyrex.lib.SessionCookies import accept_cross_site_writes
from zephyrex.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    DateSearchModel,
    ModelMeta,
    StringSearchModel,
    UpdateMixinModel,
)
from zephyrex.logic.BLL_Auth import UserManager, UserModel
from zephyrex.logic.BLL_Providers import ProviderInstanceManager, ProviderManager
from zephyrex.pydantic2.fastapi import AuthType, RouterMixin
from zephyrex.pydantic2.registry import BaseModel

PREFIX = "/v1/saml_provider"
METADATA_PATH = "/metadata"
SSO_PATH = "/sso"
CONTINUE_PATH = "/sso/continue"
METADATA_MEDIA_TYPE = "application/samlmetadata+xml"
# The sign-in pages send the browser back to this cookie's path (a path on
# the app's own origin) once the user has signed in.
RETURN_COOKIE = "href"
DEFAULT_LOGIN_URL = "/user"
# How long a parked request waits for its user to sign in.
PENDING_REQUEST_TTL = timedelta(minutes=10)
DEFAULT_ASSERTION_LIFETIME_SECONDS = 300
ASSERTION_LIFETIME_BOUNDS = (30, 3600)
# Bounds on what a registration and a request may carry.
MAX_ACS_URLS = 20
MAX_ENTITY_ID_LENGTH = 1024
MAX_RELAY_STATE_LENGTH = 1024
TICKET_BYTES = 32
PAIRWISE_ID_BYTES = 32
# pysaml2 runs xmlsec1 without a time limit; the wait for it has one.
SIGNING_TIMEOUT_SECONDS = 30.0
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}

NameIDFormat = Literal["persistent", "email"]
T = TypeVar("T")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _administrator(requester_id: str) -> bool:
    return is_root_id(requester_id) or is_system_id(requester_id)


def _root() -> str:
    return env("ROOT_ID")


# Root, whom the IdP's lookups run as, sees deleted rows too, so each of
# its lookups asks for deleted_at=None: a deleted registration signs no
# one in, and a purged request is not purged twice.


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class SamlServiceProviderModel(ApplicationModel, UpdateMixinModel, metaclass=ModelMeta):
    """A Service Provider registered with this IdP."""

    name: str = Field(..., description="What the SP is called")
    entity_id: str = Field(..., description="The SP's SAML entity id")
    acs_urls: List[str] = Field(
        ..., description="Assertion Consumer Service URLs (HTTP-POST), matched exactly"
    )
    certificate: Optional[str] = Field(
        None,
        description="The SP's X.509 certificate (PEM): verifies its signed "
        "AuthnRequests, and encrypts assertions when encrypt_assertions is set",
    )
    require_signed_requests: bool = Field(
        False, description="Refuse an AuthnRequest that is not signed"
    )
    encrypt_assertions: bool = Field(
        False, description="Encrypt assertions to the SP's certificate"
    )
    name_id_format: NameIDFormat = Field(
        "persistent",
        description="persistent (a pairwise id per user and SP) or email",
    )
    released_attributes: List[str] = Field(
        default_factory=list,
        description="User fields released to the SP: "
        + ", ".join(sorted(RELEASABLE_ATTRIBUTES)),
    )
    allow_idp_initiated: bool = Field(
        False, description="Allow sign-in started at this IdP (unsolicited responses)"
    )
    enabled: bool = Field(True, description="Whether the SP may sign users in")

    table_comment: ClassVar[str] = "Service Providers registered with the SAML IdP"

    class Create(BaseModel):
        name: str
        entity_id: str
        acs_urls: List[str]
        certificate: Optional[str] = None
        require_signed_requests: bool = False
        encrypt_assertions: bool = False
        name_id_format: NameIDFormat = "persistent"
        released_attributes: List[str] = Field(default_factory=list)
        allow_idp_initiated: bool = False
        enabled: bool = True

    class Update(BaseModel):
        name: Optional[str] = None
        entity_id: Optional[str] = None
        acs_urls: Optional[List[str]] = None
        certificate: Optional[str] = None
        require_signed_requests: Optional[bool] = None
        encrypt_assertions: Optional[bool] = None
        name_id_format: Optional[NameIDFormat] = None
        released_attributes: Optional[List[str]] = None
        allow_idp_initiated: Optional[bool] = None
        enabled: Optional[bool] = None

    class Search(ApplicationModel.Search, UpdateMixinModel.Search):
        name: Optional[StringSearchModel] = None
        entity_id: Optional[StringSearchModel] = None
        enabled: Optional[bool] = None


class SamlSubjectModel(
    ApplicationModel,
    UpdateMixinModel,
    SamlServiceProviderModel.Reference,
    UserModel.Reference,
    metaclass=ModelMeta,
):
    """A user's pairwise persistent NameID at one SP."""

    name_id: str = Field(..., description="The opaque identifier the SP sees")

    table_comment: ClassVar[str] = "Pairwise SAML NameIDs, one per user and SP"
    permission_references: ClassVar[List[str]] = ["saml_service_provider"]

    class Create(BaseModel):
        saml_service_provider_id: str
        user_id: str
        name_id: str

    class Update(BaseModel):
        pass

    class Search(ApplicationModel.Search):
        saml_service_provider_id: Optional[StringSearchModel] = None
        user_id: Optional[StringSearchModel] = None


class SamlPendingRequestModel(
    ApplicationModel,
    UpdateMixinModel,
    SamlServiceProviderModel.Reference,
    metaclass=ModelMeta,
):
    """A checked request waiting for its user, and the request ids an SP
    has used (a replayed AuthnRequest is refused)."""

    ticket: str = Field(..., description="The secret the browser carries back")
    request_id: Optional[str] = Field(
        None, description="The AuthnRequest's ID (none when IdP-initiated)"
    )
    acs_url: str = Field(..., description="Where the Response goes")
    relay_state: Optional[str] = Field(None, description="Returned to the SP as sent")
    force_authn: bool = Field(False, description="The SP asked for a fresh sign-in")
    is_passive: bool = Field(False, description="The SP asked for no interaction")
    expires_at: datetime = Field(..., description="When the request lapses")
    login_redirected: bool = Field(
        False, description="The browser has been sent to sign in once"
    )
    consumed_at: Optional[datetime] = Field(None, description="When it was answered")

    table_comment: ClassVar[str] = "SAML requests waiting for their user to sign in"
    permission_references: ClassVar[List[str]] = ["saml_service_provider"]

    class Create(BaseModel):
        saml_service_provider_id: str
        ticket: str
        request_id: Optional[str] = None
        acs_url: str
        relay_state: Optional[str] = None
        force_authn: bool = False
        is_passive: bool = False
        expires_at: datetime

    class Update(BaseModel):
        login_redirected: Optional[bool] = None
        consumed_at: Optional[datetime] = None

    class Search(ApplicationModel.Search):
        saml_service_provider_id: Optional[StringSearchModel] = None
        ticket: Optional[StringSearchModel] = None
        request_id: Optional[StringSearchModel] = None
        expires_at: Optional[DateSearchModel] = None


# ---------------------------------------------------------------------------
# Registration rules
# ---------------------------------------------------------------------------


def _invalid(detail: str) -> HTTPException:
    return HTTPException(status_code=422, detail=detail)


def acs_url_problem(url: str) -> Optional[str]:
    """Why ``url`` cannot be an ACS URL, or None. Assertions are bearer
    credentials, so they go over https only (plain http to loopback, for
    development)."""
    parts = urlsplit(url)
    if parts.scheme not in ("https", "http") or not parts.hostname:
        return f"{url!r} is not an absolute http(s) URL"
    if parts.scheme == "http" and parts.hostname not in LOOPBACK_HOSTS:
        return f"{url!r} must use https"
    if parts.fragment or parts.username or parts.password:
        return f"{url!r} must have no fragment or credentials"
    return None


def checked_registration(fields: Dict[str, Any]) -> Dict[str, Any]:
    """A registration's fields, normalized, or 422 naming the first
    problem. ``fields`` is the whole registration (on update, the stored
    one with the change applied)."""
    checked = dict(fields)
    name = str(checked.get("name") or "").strip()
    if not name:
        raise _invalid("name is required")
    checked["name"] = name
    entity_id = str(checked.get("entity_id") or "").strip()
    if (
        not entity_id
        or len(entity_id) > MAX_ENTITY_ID_LENGTH
        or any(c.isspace() for c in entity_id)
    ):
        raise _invalid(
            f"entity_id is 1-{MAX_ENTITY_ID_LENGTH} characters without spaces"
        )
    checked["entity_id"] = entity_id
    urls = list(dict.fromkeys(str(u).strip() for u in checked.get("acs_urls") or []))
    if not urls or len(urls) > MAX_ACS_URLS:
        raise _invalid(f"acs_urls lists 1-{MAX_ACS_URLS} URLs")
    for url in urls:
        problem = acs_url_problem(url)
        if problem:
            raise _invalid(problem)
    checked["acs_urls"] = urls
    certificate = (checked.get("certificate") or "").strip()
    if certificate:
        try:
            IdP.rsa_public_key(certificate)
        except ValueError as exc:
            raise _invalid(f"certificate: {exc}") from None
        checked["certificate"] = IdP.normalized_pem(certificate)
    else:
        checked["certificate"] = None
    for flag in ("require_signed_requests", "encrypt_assertions"):
        if checked.get(flag) and not checked["certificate"]:
            raise _invalid(f"{flag} needs the SP's certificate")
    released = list(dict.fromkeys(checked.get("released_attributes") or []))
    unknown = sorted(set(released) - set(RELEASABLE_ATTRIBUTES))
    if unknown:
        raise _invalid(
            f"released_attributes: {unknown} are not releasable "
            f"({', '.join(sorted(RELEASABLE_ATTRIBUTES))} are)"
        )
    checked["released_attributes"] = released
    return checked


def protocol_view(registration: SamlServiceProviderModel) -> ServiceProvider:
    return ServiceProvider(
        entity_id=registration.entity_id,
        acs_urls=tuple(registration.acs_urls),
        certificate_pem=registration.certificate,
        encrypt_assertions=registration.encrypt_assertions,
        name_id_format=NAME_ID_FORMATS[registration.name_id_format],
    )


# ---------------------------------------------------------------------------
# Managers
# ---------------------------------------------------------------------------


class SamlServiceProviderManager(AbstractBLLManager, RouterMixin):
    """SP registrations: root or system only, on every surface."""

    _model = SamlServiceProviderModel
    prefix: ClassVar[Optional[str]] = f"{PREFIX}/service_provider"
    tags: ClassVar[Optional[List[str]]] = ["SAML Identity Provider"]
    auth_type: ClassVar[AuthType] = AuthType.JWT

    def __init__(
        self,
        model_registry: Any = None,
        requester_id: Optional[str] = None,
        target_id: Optional[str] = None,
        target_team_id: Optional[str] = None,
        parent: Optional[Any] = None,
    ) -> None:
        if requester_id is not None and not _administrator(requester_id):
            raise HTTPException(
                status_code=403,
                detail="SAML service providers are managed by the administrator",
            )
        super().__init__(
            model_registry=model_registry,
            requester_id=requester_id,
            target_id=target_id,
            target_team_id=target_team_id,
            parent=parent,
        )

    def _refuse_taken_entity_id(
        self, entity_id: str, own_id: Optional[str] = None
    ) -> None:
        for existing in self.list(entity_id=entity_id, deleted_at=None):
            if existing.id != own_id:
                raise HTTPException(
                    status_code=409, detail=f"{entity_id!r} is already registered"
                )

    def create(self, **kwargs: Any) -> Any:
        if isinstance(kwargs.get("entities"), list):
            entities = [
                checked_registration({**kwargs, **e}) for e in kwargs.pop("entities")
            ]
            for index, entity in enumerate(entities):
                self._refuse_taken_entity_id(entity["entity_id"])
                if any(
                    other["entity_id"] == entity["entity_id"]
                    for other in entities[:index]
                ):
                    raise HTTPException(
                        status_code=409,
                        detail=f"{entity['entity_id']!r} is listed twice",
                    )
            return super().create(entities=entities)
        checked = checked_registration(kwargs)
        self._refuse_taken_entity_id(checked["entity_id"])
        return super().create(**checked)

    def update(self, id: str, **kwargs: Any) -> Any:
        stored = self.get(id=id).model_dump()
        changes = {
            k: v
            for k, v in kwargs.items()
            if k in SamlServiceProviderModel.Update.model_fields
        }
        checked = checked_registration(
            {**stored, **{k: v for k, v in changes.items() if v is not None}}
        )
        if checked["entity_id"] != stored["entity_id"]:
            self._refuse_taken_entity_id(checked["entity_id"], own_id=id)
        normalized = {key: checked[key] for key in changes if changes[key] is not None}
        extra = {
            k: v
            for k, v in kwargs.items()
            if k not in SamlServiceProviderModel.Update.model_fields
        }
        return super().update(id, **normalized, **extra)

    def enabled_by_entity_id(
        self, entity_id: str
    ) -> Optional[SamlServiceProviderModel]:
        for registration in self.list(entity_id=entity_id, deleted_at=None):
            if registration.enabled:
                found: SamlServiceProviderModel = registration
                return found
        return None


class SamlSubjectManager(AbstractBLLManager):
    _model = SamlSubjectModel

    def pairwise_id(self, sp_id: str, user_id: str) -> str:
        """The user's persistent NameID at the SP, made the first time."""
        for subject in self.list(
            saml_service_provider_id=sp_id, user_id=user_id, deleted_at=None
        ):
            existing: str = subject.name_id
            return existing
        created = self.create(
            saml_service_provider_id=sp_id,
            user_id=user_id,
            name_id=secrets.token_urlsafe(PAIRWISE_ID_BYTES),
        )
        made: str = created.name_id
        return made


class SamlPendingRequestManager(AbstractBLLManager):
    _model = SamlPendingRequestModel

    def purge_expired(self, now: datetime) -> None:
        """Forget requests past their lapse; by then their AuthnRequest ids
        are also too old to be accepted again."""
        for lapsed in self.search(expires_at={"before": now}, deleted_at=None):
            self.delete(id=lapsed.id)

    def seen(self, sp_id: str, request_id: str) -> bool:
        return bool(self.list(saml_service_provider_id=sp_id, request_id=request_id))

    def by_ticket(self, ticket: str) -> Optional[SamlPendingRequestModel]:
        for pending in self.list(ticket=ticket, deleted_at=None):
            found: SamlPendingRequestModel = pending
            return found
        return None


# ---------------------------------------------------------------------------
# The Identity Provider
# ---------------------------------------------------------------------------


class PostBindingForm(RouteModel):
    """An HTTP-POST binding message, as the SP's form posts it."""

    SAMLRequest: str = Field(..., description="The base64 AuthnRequest")
    RelayState: Optional[str] = Field(None, description="Returned to the SP as sent")


def _bad_request(detail: str) -> HTTPException:
    return HTTPException(status_code=400, detail=detail)


def _forbidden(detail: str) -> HTTPException:
    return HTTPException(status_code=403, detail=detail)


def _bounded_relay_state(relay_state: Optional[str]) -> Optional[str]:
    if relay_state is not None and len(relay_state) > MAX_RELAY_STATE_LENGTH:
        raise _bad_request(f"RelayState is at most {MAX_RELAY_STATE_LENGTH} characters")
    return relay_state


def assertion_lifetime() -> timedelta:
    raw = env("SAML_PROVIDER_ASSERTION_LIFETIME_SECONDS") or str(
        DEFAULT_ASSERTION_LIFETIME_SECONDS
    )
    try:
        seconds = int(raw)
    except ValueError:
        seconds = DEFAULT_ASSERTION_LIFETIME_SECONDS
    low, high = ASSERTION_LIFETIME_BOUNDS
    return timedelta(seconds=min(max(seconds, low), high))


def idp_site() -> IdPSite:
    """Where SPs reach this IdP: ``SAML_PROVIDER_BASE_URL`` (else
    ``SERVER_URI``), and the entity id ``SAML_PROVIDER_ENTITY_ID`` (else
    the metadata URL)."""
    base = (env("SAML_PROVIDER_BASE_URL") or env("SERVER_URI")).rstrip("/")
    entity_id = env("SAML_PROVIDER_ENTITY_ID") or f"{base}{PREFIX}{METADATA_PATH}"
    return IdPSite(
        entity_id=entity_id,
        sso_url=f"{base}{PREFIX}{SSO_PATH}",
        assertion_lifetime=assertion_lifetime(),
    )


def signing_identity(model_registry: Any) -> SigningIdentity:
    """The newest enabled signing-key instance's identity, else the
    environment's. 503 when there is none or it is unusable."""
    root = _root()
    instance = None
    try:
        provider = ProviderManager(
            model_registry=model_registry, requester_id=root
        ).get(name=PRV_SAMLSigningKey.name)
    except HTTPException:
        provider = None
    if provider is not None:
        instances = ProviderInstanceManager(
            model_registry=model_registry, requester_id=root
        ).list(provider_id=provider.id)
        enabled = [i for i in instances if i.enabled]
        if enabled:
            instance = max(enabled, key=lambda i: ensure_utc(i.created_at))
    try:
        return PRV_SAMLSigningKey.identity(instance)
    except SigningKeyMissing as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None


async def off_loop(work: Callable[[], T]) -> T:
    """Run blocking pysaml2/xmlsec1 work on a worker thread, bounded."""
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(work), timeout=SIGNING_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=503, detail="SAML signing timed out") from None


def _post_page(
    acs_url: str, response_xml: str, relay_state: Optional[str]
) -> HTMLResponse:
    page = IdP.post_page(acs_url, response_xml, relay_state)
    return HTMLResponse(
        page.html,
        headers={
            **NO_STORE,
            "Content-Security-Policy": page.content_security_policy,
            "Referrer-Policy": "no-referrer",
        },
    )


class SignedInUser(RouteModel):
    id: str
    authenticated_at: datetime


class SamlIdentityProviderManager(AbstractBLLManager, RouterMixin):
    """The IdP's protocol endpoints. They authenticate for themselves: an
    SP's message carries no session, and an unauthenticated browser is sent
    to sign in rather than refused."""

    prefix: ClassVar[Optional[str]] = PREFIX
    tags: ClassVar[Optional[List[str]]] = ["SAML Identity Provider"]
    auth_type: ClassVar[AuthType] = AuthType.NONE
    routes_to_register: ClassVar[Optional[List[Any]]] = []

    # -- helpers ------------------------------------------------------------

    def _registrations(self) -> SamlServiceProviderManager:
        return SamlServiceProviderManager(
            model_registry=self.model_registry, requester_id=_root()
        )

    def _pending(self) -> SamlPendingRequestManager:
        return SamlPendingRequestManager(
            model_registry=self.model_registry, requester_id=_root()
        )

    def _signed_in(self, request: Request) -> Optional[SignedInUser]:
        """The user whose session the request carries (the session cookie
        arrives as the bearer header), and when they signed in; None for no
        session, or a credential that is not a session."""
        authorization = request.headers.get("authorization") or ""
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            return None
        try:
            user = get_auth_provider().auth(
                model_registry=self.model_registry,
                authorization=authorization,
                request=request,
            )
        except HTTPException:
            return None
        try:
            claims = jwt.decode(token.strip(), options={"verify_signature": False})
        except jwt.PyJWTError:
            return None
        if (
            user is None
            or str(claims.get("sub")) != str(user.id)
            or "iat" not in claims
        ):
            return None
        return SignedInUser(
            id=str(user.id),
            authenticated_at=datetime.fromtimestamp(
                int(claims["iat"]), tz=timezone.utc
            ),
        )

    async def _with_server(
        self,
        identity: SigningIdentity,
        sp: Optional[ServiceProvider],
        work: Callable[[Server], T],
        *,
        want_signed_post_requests: bool = False,
    ) -> T:
        site = idp_site()

        def run() -> T:
            with IdP.idp_server(
                site, identity, sp, want_signed_post_requests=want_signed_post_requests
            ) as server:
                return work(server)

        return await off_loop(run)

    async def _error_page(
        self,
        identity: SigningIdentity,
        sp: ServiceProvider,
        *,
        acs_url: str,
        in_response_to: Optional[str],
        relay_state: Optional[str],
        status: str,
        message: str,
    ) -> HTMLResponse:
        xml = await self._with_server(
            identity,
            sp,
            lambda server: IdP.error_response(
                server,
                in_response_to=in_response_to,
                acs_url=acs_url,
                status=status,
                message=message,
            ),
        )
        return _post_page(acs_url, xml, relay_state)

    def _park(
        self,
        registration: SamlServiceProviderModel,
        *,
        request_id: Optional[str],
        acs_url: str,
        relay_state: Optional[str],
        force_authn: bool = False,
        is_passive: bool = False,
    ) -> RedirectResponse:
        now = _now()
        pending = self._pending()
        pending.purge_expired(now)
        ticket = secrets.token_urlsafe(TICKET_BYTES)
        pending.create(
            saml_service_provider_id=registration.id,
            ticket=ticket,
            request_id=request_id,
            acs_url=acs_url,
            relay_state=relay_state,
            force_authn=force_authn,
            is_passive=is_passive,
            expires_at=now + PENDING_REQUEST_TTL,
        )
        return RedirectResponse(
            f"{PREFIX}{CONTINUE_PATH}?ticket={ticket}",
            status_code=303,
            headers=NO_STORE,
        )

    @staticmethod
    def _chosen_acs(
        registration: SamlServiceProviderModel, request: samlp.AuthnRequest
    ) -> str:
        """The registered ACS URL the request names exactly (by URL or by
        index), else the first registered. 400 for any other."""
        urls = registration.acs_urls
        binding = request.protocol_binding
        if binding and binding != BINDING_HTTP_POST:
            raise _bad_request("only the HTTP-POST binding is offered for responses")
        if request.assertion_consumer_service_url:
            if request.assertion_consumer_service_url not in urls:
                raise _bad_request(
                    "the AssertionConsumerServiceURL is not registered for this SP"
                )
            requested: str = request.assertion_consumer_service_url
            return requested
        if request.assertion_consumer_service_index is not None:
            try:
                return urls[int(request.assertion_consumer_service_index)]
            except (ValueError, IndexError):
                raise _bad_request(
                    "the AssertionConsumerServiceIndex is not registered"
                ) from None
        return urls[0]

    async def _receive(
        self,
        encoded: str,
        binding: str,
        relay_state: Optional[str],
        raw_query: Optional[str],
    ) -> Response:
        """Check an SP-initiated AuthnRequest and park it for its user."""
        relay_state = _bounded_relay_state(relay_state)
        try:
            issuer = IdP.request_issuer(IdP.message_xml(encoded, binding))
        except RefusedMessage as exc:
            raise _bad_request(str(exc)) from None
        registration = self._registrations().enabled_by_entity_id(issuer)
        if registration is None:
            raise _forbidden(
                "the AuthnRequest's issuer is not a registered service provider"
            )
        sp = protocol_view(registration)
        if binding == BINDING_HTTP_REDIRECT:
            signed = "Signature=" in (raw_query or "")
            if signed or registration.require_signed_requests:
                if not registration.certificate:
                    raise _forbidden(
                        "the SP has no registered certificate to verify its signature"
                    )
                try:
                    IdP.verify_redirect_signature(
                        raw_query or "", registration.certificate
                    )
                except SignatureRefused as exc:
                    raise _forbidden(str(exc)) from None
        identity = signing_identity(self.model_registry)
        try:
            request = await self._with_server(
                identity,
                sp,
                lambda server: IdP.parse_authn_request(server, encoded, binding),
                want_signed_post_requests=registration.require_signed_requests,
            )
            IdP.check_issue_instant(request, _now())
        except SignatureRefused as exc:
            raise _forbidden(str(exc)) from None
        except RefusedMessage as exc:
            raise _bad_request(str(exc)) from None
        acs_url = self._chosen_acs(registration, request)
        request_id = str(request.id)
        if self._pending().seen(registration.id, request_id):
            raise _bad_request("this AuthnRequest has already been received")
        requested_format = (
            request.name_id_policy.format if request.name_id_policy else None
        )
        if requested_format and requested_format not in (
            saml.NAMEID_FORMAT_UNSPECIFIED,
            sp.name_id_format,
        ):
            return await self._error_page(
                identity,
                sp,
                acs_url=acs_url,
                in_response_to=request_id,
                relay_state=relay_state,
                status=samlp.STATUS_INVALID_NAMEID_POLICY,
                message=f"this IdP issues {sp.name_id_format} NameIDs to this SP",
            )
        return self._park(
            registration,
            request_id=request_id,
            acs_url=acs_url,
            relay_state=relay_state,
            force_authn=request.force_authn == "true",
            is_passive=request.is_passive == "true",
        )

    def _to_sign_in(self, pending: SamlPendingRequestModel) -> RedirectResponse:
        """Send the browser to sign in, to come back to this request."""
        self._pending().update(pending.id, login_redirected=True)
        response = RedirectResponse(
            env("SAML_PROVIDER_LOGIN_URL") or DEFAULT_LOGIN_URL,
            status_code=303,
            headers=NO_STORE,
        )
        response.set_cookie(
            RETURN_COOKIE,
            f"{PREFIX}{CONTINUE_PATH}?ticket={pending.ticket}",
            max_age=int(PENDING_REQUEST_TTL.total_seconds()),
            path="/",
            domain=env("SESSION_COOKIE_DOMAIN") or None,
            secure=True,
            httponly=False,
            samesite="lax",
        )
        return response

    def _released(
        self, registration: SamlServiceProviderModel, user: Any
    ) -> Dict[str, List[str]]:
        released: Dict[str, List[str]] = {}
        for field in registration.released_attributes:
            value = getattr(user, field, None)
            if value:
                released[RELEASABLE_ATTRIBUTES[field]] = [str(value)]
        return released

    # -- routes -------------------------------------------------------------

    @custom_route(
        method="GET",
        path=METADATA_PATH,
        authentication_type="none",
        response_class=Response,
        expose_in=(ExposeIn.REST,),
        openapi_tags=("SAML Identity Provider",),
        summary="The IdP's SAML metadata",
    )
    async def metadata_route(self) -> Response:
        identity = signing_identity(self.model_registry)
        site = idp_site()
        xml = await off_loop(lambda: IdP.metadata_xml(site, identity))
        return Response(xml, media_type=METADATA_MEDIA_TYPE)

    @custom_route(
        method="GET",
        path=SSO_PATH,
        authentication_type="none",
        response_class=Response,
        expose_in=(ExposeIn.REST,),
        openapi_tags=("SAML Identity Provider",),
        summary="Single sign-on, HTTP-Redirect binding (SAMLRequest, RelayState, SigAlg, Signature)",
    )
    async def sso_redirect_route(self, request: Request) -> Response:
        encoded = request.query_params.get("SAMLRequest")
        if not encoded:
            raise _bad_request("SAMLRequest is required")
        return await self._receive(
            encoded,
            BINDING_HTTP_REDIRECT,
            request.query_params.get("RelayState"),
            request.url.query,
        )

    @custom_route(
        method="POST",
        path=SSO_PATH,
        input_model=PostBindingForm,
        authentication_type="none",
        response_class=Response,
        expose_in=(ExposeIn.REST,),
        openapi_tags=("SAML Identity Provider",),
        summary="Single sign-on, HTTP-POST binding",
    )
    async def sso_post_route(self, body: PostBindingForm) -> Response:
        return await self._receive(
            body.SAMLRequest, BINDING_HTTP_POST, body.RelayState, None
        )

    @custom_route(
        method="GET",
        path="/initiate",
        authentication_type="none",
        response_class=Response,
        expose_in=(ExposeIn.REST,),
        openapi_tags=("SAML Identity Provider",),
        summary="IdP-initiated sign-in to an SP that allows it",
    )
    async def initiate_route(
        self, sp: str, relay_state: Optional[str] = None
    ) -> Response:
        registration = self._registrations().enabled_by_entity_id(sp)
        if registration is None:
            raise _forbidden("not a registered service provider")
        if not registration.allow_idp_initiated:
            raise _forbidden(
                "this service provider does not accept IdP-initiated sign-in"
            )
        return self._park(
            registration,
            request_id=None,
            acs_url=registration.acs_urls[0],
            relay_state=_bounded_relay_state(relay_state),
        )

    @custom_route(
        method="GET",
        path=CONTINUE_PATH,
        authentication_type="none",
        response_class=Response,
        expose_in=(ExposeIn.REST,),
        openapi_tags=("SAML Identity Provider",),
        summary="Answer a parked request for the signed-in user",
    )
    async def continue_route(self, request: Request, ticket: str) -> Response:
        pending_requests = self._pending()
        found = pending_requests.by_ticket(ticket)
        now = _now()
        if (
            found is None
            or found.consumed_at is not None
            or ensure_utc(found.expires_at) <= now
        ):
            raise _bad_request(
                "this sign-in request has lapsed; start again from the application"
            )
        pending: SamlPendingRequestModel = found
        registrations = self._registrations()
        live = registrations.list(id=pending.saml_service_provider_id, deleted_at=None)
        registration = live[0] if live else None
        if (
            registration is None
            or not registration.enabled
            or pending.acs_url not in registration.acs_urls
        ):
            raise _forbidden(
                "the service provider's registration has changed; start again"
            )
        sp = protocol_view(registration)
        identity = signing_identity(self.model_registry)

        async def refusal(status: str, message: str) -> HTMLResponse:
            pending_requests.update(pending.id, consumed_at=now)
            return await self._error_page(
                identity,
                sp,
                acs_url=pending.acs_url,
                in_response_to=pending.request_id,
                relay_state=pending.relay_state,
                status=status,
                message=message,
            )

        user = self._signed_in(request)
        if user is None:
            if pending.is_passive:
                return await refusal(
                    samlp.STATUS_NO_PASSIVE, "the user is not signed in"
                )
            return self._to_sign_in(pending)
        if is_any_internal_id(user.id):
            raise _forbidden("a system account cannot sign in to a service provider")
        # A session's iat has whole seconds; one begun after the request
        # began no earlier than the request's second.
        requested_at = ensure_utc(pending.created_at).replace(microsecond=0)
        if pending.force_authn and user.authenticated_at < requested_at:
            if pending.login_redirected or pending.is_passive:
                return await refusal(
                    samlp.STATUS_AUTHN_FAILED, "a fresh sign-in was required"
                )
            return self._to_sign_in(pending)
        pending_requests.update(pending.id, consumed_at=now)
        account = UserManager(
            model_registry=self.model_registry, requester_id=user.id
        ).get(id=user.id)
        if registration.name_id_format == "email":
            if not account.email:
                return await refusal(
                    samlp.STATUS_INVALID_NAMEID_POLICY, "the user has no email address"
                )
            subject_value = str(account.email)
        else:
            subject_value = SamlSubjectManager(
                model_registry=self.model_registry, requester_id=_root()
            ).pairwise_id(registration.id, user.id)
        site = idp_site()
        attributes = self._released(registration, account)
        xml = await self._with_server(
            identity,
            sp,
            lambda server: IdP.authn_response(
                server,
                sp,
                in_response_to=pending.request_id,
                acs_url=pending.acs_url,
                subject=IdP.name_id(sp, site, subject_value),
                attributes=attributes,
                authn_instant=user.authenticated_at,
            ),
        )
        return _post_page(pending.acs_url, xml, pending.relay_state)


def metadata_document(model_registry: Any) -> Tuple[str, str]:
    """The IdP's entity id and metadata XML (blocking: xmlsec1 runs)."""
    site = idp_site()
    return site.entity_id, IdP.metadata_xml(site, signing_identity(model_registry))


# An SP's page posts its AuthnRequest here (HTTP-POST binding). It reaches
# the route without the session, which only /sso/continue (a GET) reads.
accept_cross_site_writes(re.escape(f"{PREFIX}{SSO_PATH}"))
accept_form_bodies(re.escape(f"{PREFIX}{SSO_PATH}"))
