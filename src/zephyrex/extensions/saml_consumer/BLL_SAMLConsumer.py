# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sign-in through external SAML 2.0 identity providers (this server is
the Service Provider).

An identity provider (Okta, Entra ID, Keycloak, Shibboleth, ADFS) is a row
only ROOT or SYSTEM manages, made from its metadata (pasted, or fetched
from its URL). Each one has its own SP: an entity ID, an Assertion
Consumer Service and SP metadata under ``/v1/auth/saml/idp/{id}``. The SP
signing key is stored encrypted and, like the SP certificate, never
returned.

Signing in: ``GET /{id}/login`` sends the browser to the IdP with an
AuthnRequest whose ID is stored, bound to that browser by a cookie, for
ten minutes; the IdP posts its response to ``POST /{id}/acs``. A response
that passes every check (see ``SAMLProtocol``), answers this browser's
request (or is unsolicited, where the IdP allows it) and uses neither a
request ID nor an assertion ID seen before signs the user in exactly as a
passwordless grant does (``UserManager.login_via_grant``: a session row,
its JWT, the session cookies), unless the user has a second factor, which
still stands.

The user is the one linked to the asserted identity, keyed by the IdP's
entity ID and the NameID (or a configured attribute). Failing that, an
asserted email finds an existing account only when the IdP vouches for its
emails (``emails_verified``). Failing that, a new account follows
REGISTRATION_MODE: ``open`` makes one, ``invite`` makes one only for an
email with a pending invitation, ``closed`` makes none. An account made
for an IdP whose emails are not vouched for carries no email.
"""

import asyncio
import hashlib
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, ClassVar, Dict, List, Optional, Tuple
from urllib.parse import urlencode, urlsplit, urlunsplit

from fastapi import HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel as RouteModel
from pydantic import Field
from sqlalchemy.exc import IntegrityError

from zephyrex.database.StaticPermissions import is_root_id, is_system_id
from zephyrex.extensions.ExternalErrors import (
    BaseExternalError,
    InvalidInputExternalError,
    TransientExternalError,
)
from zephyrex.extensions.saml_consumer.SAMLProtocol import (
    DISPLAY_NAME_ATTRIBUTES,
    FIRST_NAME_ATTRIBUTES,
    LAST_NAME_ATTRIBUTES,
    MAX_METADATA_BYTES,
    MAX_RESPONSE_BYTES,
    AssertedIdentity,
    SAMLResponseRefused,
    ServiceProvider,
    check_key_pair,
    parse_idp_metadata,
)
from zephyrex.lib.CustomRoute import ExposeIn, custom_route
from zephyrex.lib import Environment
from zephyrex.lib.Environment import env
from zephyrex.lib.InboundSecurity import (
    DEFAULT_READ_RATE_LIMIT,
    parse_cors_origins,
    rate_limit,
)
from zephyrex.lib.Logging import logger
from zephyrex.lib.ProviderHTTPClient import ClientPolicy, ProviderHTTPClient
from zephyrex.lib.SecretEncryption import decrypt_secret, encrypt_secret
from zephyrex.lib.ContentNegotiation import accept_form_bodies
from zephyrex.lib.SessionCookies import accept_cross_site_writes
from zephyrex.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    DateSearchModel,
    ModelMeta,
    NameMixinModel,
    StringSearchModel,
    UpdateMixinModel,
)
from zephyrex.logic.BLL_Auth import (
    PasswordlessGrantRegistry,
    UserIdGrantPayload,
    UserManager,
    UserModel,
    _invitation_hooks,
    make_user_id_grant_validator,
    refuse_internal_account,
)
from zephyrex.logic.BLL_Auth.user import issue_browser_session
from zephyrex.pydantic2.fastapi import AuthType, RouterMixin, RouteType
from zephyrex.pydantic2.registry import BaseModel

ROUTE_PREFIX = "/v1/auth/saml/idp"
GRANT_TYPE = "saml"
REQUEST_COOKIE = "zx_saml_request"
REQUEST_TTL_SECONDS = 600
BROWSER_KEY_BYTES = 32
MAX_RETURN_TO_LENGTH = 2048
DEFAULT_RETURN_TO = "/"
MFA_FRAGMENT_KEY = "saml_mfa_challenge"
METADATA_FETCH_TIMEOUT_SECONDS = 20.0
METADATA_MEDIA_TYPE = "application/samlmetadata+xml"
_REDIRECT_STATUS = 303
# Per IP and IdP. A whole organisation behind one NAT signs in through the
# same IdP at nine o'clock, and a forged response gets nowhere however
# often it is tried, so the cap only bounds the signature-checking work.
SIGN_IN_RATE_LIMIT = DEFAULT_READ_RATE_LIMIT


def _server_side(requester_id: Optional[str]) -> bool:
    if not requester_id:
        return False
    return is_root_id(requester_id) or is_system_id(requester_id)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _bad_request(exc: BaseExternalError) -> HTTPException:
    return HTTPException(status_code=400, detail=exc.message)


def _refused(exc: SAMLResponseRefused) -> HTTPException:
    return HTTPException(status_code=401, detail=exc.message)


def _server_uri() -> str:
    return (env("SERVER_URI") or "").rstrip("/")


def _acs_path(idp_id: str) -> str:
    return f"{ROUTE_PREFIX}/{idp_id}/acs"


def _origin(url: str) -> Optional[str]:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}".lower()


def safe_return_to(value: Optional[str]) -> str:
    """Where to send the browser after signing in: a path on this server,
    or a URL on this server's or the app's origin (``SERVER_URI``,
    ``APP_URI``, ``APP_CORS_ALLOWED_ORIGINS``). Anything else is refused,
    so the sign-in cannot be used as an open redirect."""
    if not value:
        return DEFAULT_RETURN_TO
    if (
        len(value) > MAX_RETURN_TO_LENGTH
        or "\\" in value
        or any(ord(c) < 0x20 for c in value)
    ):
        raise HTTPException(
            status_code=400, detail="return_to is not an allowed address"
        )
    parts = urlsplit(value)
    if not parts.scheme and not parts.netloc:
        if value.startswith("/") and not value.startswith("//"):
            return value
        raise HTTPException(
            status_code=400, detail="return_to is not an allowed address"
        )
    allowed = {
        origin
        for origin in (
            _origin(_server_uri()),
            _origin(env("APP_URI") or ""),
            *(
                _origin(o)
                for o in parse_cors_origins(env("APP_CORS_ALLOWED_ORIGINS") or "")
                if o != "*"
            ),
        )
        if origin
    }
    if _origin(value) in allowed:
        return value
    raise HTTPException(status_code=400, detail="return_to is not an allowed address")


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class SamlIdentityProviderModel(
    ApplicationModel,
    UpdateMixinModel,
    NameMixinModel,
    metaclass=ModelMeta,
):
    """An external IdP users may sign in through, and this server's SP for it."""

    entity_id: Optional[str] = Field(
        None, description="The IdP's entity ID, read from its metadata"
    )
    metadata_xml: str = Field(..., description="The IdP's SAML 2.0 metadata")
    metadata_url: Optional[str] = Field(
        None, description="Where the metadata was fetched from, to refresh it"
    )
    sp_entity_id: Optional[str] = Field(
        None,
        description="This SP's entity ID for the IdP (default: the SP metadata URL)",
    )
    sp_certificate: Optional[str] = Field(
        None, exclude=True, description="SP certificate, PEM (write-only)"
    )
    sp_private_key: Optional[str] = Field(
        None,
        exclude=True,
        description="SP signing/decryption key, PEM (write-only, stored encrypted)",
    )
    sp_key_configured: bool = Field(
        False, description="Whether an SP key and certificate are set"
    )
    name_id_format: Optional[str] = Field(
        None, description="The NameID format to request"
    )
    identity_attribute: Optional[str] = Field(
        None, description="An attribute identifying the user, instead of the NameID"
    )
    email_attribute: Optional[str] = Field(
        None, description="The attribute carrying the email"
    )
    emails_verified: bool = Field(
        False, description="The IdP vouches for its users' emails"
    )
    allow_unsolicited: bool = Field(
        False, description="Accept IdP-initiated (unsolicited) responses"
    )
    want_assertions_signed: bool = Field(
        True, description="Require the Assertion to be signed"
    )
    want_response_signed: bool = Field(
        False, description="Require the Response to be signed"
    )
    is_enabled: bool = Field(True, description="Whether users may sign in through it")

    table_comment: ClassVar[str] = (
        "External SAML 2.0 identity providers, managed by ROOT"
    )
    is_system_entity: ClassVar[bool] = True

    class Create(BaseModel, NameMixinModel):
        metadata_xml: str
        metadata_url: Optional[str] = None
        entity_id: Optional[str] = None
        sp_entity_id: Optional[str] = None
        sp_certificate: Optional[str] = None
        sp_private_key: Optional[str] = None
        sp_key_configured: bool = False
        name_id_format: Optional[str] = None
        identity_attribute: Optional[str] = None
        email_attribute: Optional[str] = None
        emails_verified: bool = False
        allow_unsolicited: bool = False
        want_assertions_signed: bool = True
        want_response_signed: bool = False
        is_enabled: bool = True

    class Update(BaseModel, NameMixinModel.Optional):
        metadata_xml: Optional[str] = None
        metadata_url: Optional[str] = None
        entity_id: Optional[str] = None
        sp_entity_id: Optional[str] = None
        sp_certificate: Optional[str] = None
        sp_private_key: Optional[str] = None
        sp_key_configured: Optional[bool] = None
        name_id_format: Optional[str] = None
        identity_attribute: Optional[str] = None
        email_attribute: Optional[str] = None
        emails_verified: Optional[bool] = None
        allow_unsolicited: Optional[bool] = None
        want_assertions_signed: Optional[bool] = None
        want_response_signed: Optional[bool] = None
        is_enabled: Optional[bool] = None

    class Search(
        ApplicationModel.Search, UpdateMixinModel.Search, NameMixinModel.Search
    ):
        entity_id: Optional[StringSearchModel] = None
        is_enabled: Optional[bool] = None


class SamlIdentityModel(
    ApplicationModel,
    UpdateMixinModel,
    UserModel.Reference,
    metaclass=ModelMeta,
):
    """A user's identity at an IdP: who the IdP says they are."""

    identity_provider_id: str = Field(
        ..., description="The SamlIdentityProvider it was asserted through"
    )
    idp_entity_id: str = Field(..., description="The asserting IdP's entity ID")
    subject: str = Field(
        ..., description="The NameID, or the configured identity attribute"
    )
    subject_format: Optional[str] = Field(None, description="The NameID format")
    asserted_email: Optional[str] = Field(
        None, description="The email the IdP last asserted"
    )
    last_login_at: Optional[datetime] = Field(
        None, description="Last sign-in through it"
    )

    table_comment: ClassVar[str] = (
        "Links a local user to the identity a SAML IdP asserts"
    )

    class Create(BaseModel, UserModel.Reference.ID):
        identity_provider_id: str
        idp_entity_id: str
        subject: str
        subject_format: Optional[str] = None
        asserted_email: Optional[str] = None
        last_login_at: Optional[datetime] = None

    class Update(BaseModel):
        asserted_email: Optional[str] = None
        last_login_at: Optional[datetime] = None

    class Search(
        ApplicationModel.Search, UpdateMixinModel.Search, UserModel.Reference.ID.Search
    ):
        identity_provider_id: Optional[StringSearchModel] = None
        idp_entity_id: Optional[StringSearchModel] = None
        subject: Optional[StringSearchModel] = None
        last_login_at: Optional[DateSearchModel] = None


class SamlAuthnRequestModel(ApplicationModel, UpdateMixinModel, metaclass=ModelMeta):
    """An AuthnRequest awaiting its response, bound to the browser that
    started it (the hash of its request cookie)."""

    request_id: str = Field(..., description="The AuthnRequest ID")
    identity_provider_id: str = Field(..., description="The IdP it was sent to")
    return_to: str = Field(..., description="Where the browser goes after signing in")
    browser_key_hash: str = Field(
        ..., description="SHA-256 of the browser's request cookie"
    )
    expires_at: datetime = Field(
        ..., description="When the request stops being answerable"
    )

    table_comment: ClassVar[str] = "Outstanding SAML AuthnRequests"

    class Create(BaseModel):
        request_id: str
        identity_provider_id: str
        return_to: str
        browser_key_hash: str
        expires_at: datetime


class SamlSpentMessageModel(ApplicationModel, metaclass=ModelMeta):
    """A request ID answered, or an assertion ID accepted: never again."""

    spent_key: str = Field(
        ...,
        description="request:<id> or assertion:<issuer>:<id>",
        json_schema_extra={"unique": True},
    )
    expires_at: datetime = Field(
        ..., description="When the message could no longer be accepted anyway"
    )

    table_comment: ClassVar[str] = (
        "SAML request and assertion IDs already used (replay protection)"
    )

    class Create(BaseModel):
        spent_key: str
        expires_at: datetime


# ---------------------------------------------------------------------------
# Route models
# ---------------------------------------------------------------------------


class SAMLPostBinding(RouteModel):
    """The fields the IdP's HTTP-POST binding form carries."""

    SAMLResponse: str = Field(..., max_length=MAX_RESPONSE_BYTES)
    RelayState: Optional[str] = Field(None, max_length=MAX_RETURN_TO_LENGTH)


class IdentityProviderListing(RouteModel):
    id: str
    name: str
    login_url: str


class IdentityProviderList(RouteModel):
    identity_providers: List[IdentityProviderListing]


class IdentityProviderImport(RouteModel):
    """A new IdP from its metadata: fetched from ``metadata_url`` or given
    as ``metadata_xml``. The other fields are as on the IdP itself."""

    name: str = Field(..., min_length=1)
    metadata_url: Optional[str] = None
    metadata_xml: Optional[str] = None
    sp_entity_id: Optional[str] = None
    sp_certificate: Optional[str] = None
    sp_private_key: Optional[str] = None
    name_id_format: Optional[str] = None
    identity_attribute: Optional[str] = None
    email_attribute: Optional[str] = None
    emails_verified: bool = False
    allow_unsolicited: bool = False
    want_assertions_signed: bool = True
    want_response_signed: bool = False
    is_enabled: bool = True


class MetadataRefresh(RouteModel):
    """Re-read the IdP's metadata from its ``metadata_url``."""


class IdentityProviderSummary(RouteModel):
    id: str
    name: str
    entity_id: Optional[str]
    metadata_url: Optional[str]
    sp_entity_id: str
    acs_url: str
    sp_metadata_url: str
    sp_key_configured: bool
    is_enabled: bool


# ---------------------------------------------------------------------------
# Managers
# ---------------------------------------------------------------------------


class SamlIdentityProviderManager(AbstractBLLManager, RouterMixin):
    _model = SamlIdentityProviderModel

    prefix: ClassVar[Optional[str]] = ROUTE_PREFIX
    tags: ClassVar[Optional[List[str]]] = ["SAML Sign-in"]
    auth_type: ClassVar[AuthType] = AuthType.API_KEY
    # No LIST: GET on the prefix is the public listing of enabled IdPs.
    routes_to_register: ClassVar[Optional[List[RouteType]]] = [
        RouteType.GET,
        RouteType.SEARCH,
        RouteType.CREATE,
        RouteType.UPDATE,
        RouteType.DELETE,
    ]

    # -- administration -----------------------------------------------------

    def _require_server_side(self) -> None:
        requester = self.optional_requester
        if requester is None or not _server_side(str(requester.id)):
            raise HTTPException(
                status_code=403,
                detail="Only ROOT or SYSTEM manage SAML identity providers",
            )

    def stored(self, idp_id: str) -> SamlIdentityProviderModel:
        """The IdP row with its secrets (read as ROOT, past the cache)."""
        IdPDB = self.DB
        found: List[SamlIdentityProviderModel] = IdPDB.list(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            filters=[IdPDB.id == idp_id, IdPDB.deleted_at.is_(None)],
            return_type="dto",
            override_dto=SamlIdentityProviderModel,
        )
        if not found:
            raise HTTPException(
                status_code=404, detail="No such SAML identity provider"
            )
        return found[0]

    def _prepared(
        self, fields: Dict[str, Any], existing: Optional[SamlIdentityProviderModel]
    ) -> Dict[str, Any]:
        """``fields`` with the server-computed ones set: the entity ID read
        from the metadata, the key pair checked and the key encrypted."""
        try:
            metadata_xml = fields.get("metadata_xml") or (
                existing.metadata_xml if existing else None
            )
            if not metadata_xml:
                raise InvalidInputExternalError("metadata_xml is required")
            fields["entity_id"] = parse_idp_metadata(metadata_xml).entity_id
            key = fields.get("sp_private_key")
            certificate = fields.get("sp_certificate")
            if existing is not None:
                key = (
                    key
                    if "sp_private_key" in fields
                    else decrypt_secret(existing.sp_private_key)
                )
                certificate = (
                    certificate
                    if "sp_certificate" in fields
                    else existing.sp_certificate
                )
            if bool(key) != bool(certificate):
                raise InvalidInputExternalError(
                    "sp_private_key and sp_certificate are set together"
                )
            if key and certificate:
                check_key_pair(key, certificate)
            if "sp_private_key" in fields:
                fields["sp_private_key"] = encrypt_secret(key) if key else None
            fields["sp_key_configured"] = bool(key and certificate)
        except InvalidInputExternalError as exc:
            raise _bad_request(exc) from exc
        return fields

    def create(self, **kwargs: Any) -> Any:
        self._require_server_side()
        if isinstance(kwargs.get("entities"), list):
            kwargs["entities"] = [
                self._prepared(dict(e), None) for e in kwargs["entities"]
            ]
            return super().create(**kwargs)
        return super().create(**self._prepared(dict(kwargs), None))

    def update(self, id: str, **kwargs: Any) -> Any:
        self._require_server_side()
        return super().update(id, **self._prepared(dict(kwargs), self.stored(id)))

    def delete(self, id: str) -> None:
        self._require_server_side()
        super().delete(id)

    def summary(self, idp: SamlIdentityProviderModel) -> IdentityProviderSummary:
        sp = self.service_provider(idp)
        return IdentityProviderSummary(
            id=str(idp.id),
            name=idp.name,
            entity_id=idp.entity_id,
            metadata_url=idp.metadata_url,
            sp_entity_id=sp.entity_id,
            acs_url=sp.acs_url,
            sp_metadata_url=f"{_server_uri()}{ROUTE_PREFIX}/{idp.id}/metadata",
            sp_key_configured=idp.sp_key_configured,
            is_enabled=idp.is_enabled,
        )

    async def fetch_metadata(self, url: str) -> str:
        """The metadata at ``url``, over HTTPS (plain HTTP only from a host
        the operator allow-listed in EGRESS_ALLOWED_HOSTS): unsigned
        metadata is only as trustworthy as the channel it came over."""
        insecure = HTTPException(
            status_code=400, detail="IdP metadata is fetched over https"
        )
        if not self._trusted_channel(url):
            raise insecure
        client = ProviderHTTPClient(
            policy=ClientPolicy(timeout=METADATA_FETCH_TIMEOUT_SECONDS, max_retries=1),
            provider_name="saml_consumer",
        )
        try:
            fetched = await client.fetch(url, max_bytes=MAX_METADATA_BYTES)
        except TransientExternalError as exc:
            raise HTTPException(
                status_code=502, detail="The IdP metadata could not be fetched"
            ) from exc
        except BaseExternalError as exc:
            raise _bad_request(exc) from exc
        if not self._trusted_channel(fetched.url):
            raise insecure
        if fetched.truncated:
            raise HTTPException(
                status_code=400,
                detail=f"IdP metadata is over {MAX_METADATA_BYTES} bytes",
            )
        try:
            return fetched.body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(
                status_code=400, detail="IdP metadata is not UTF-8"
            ) from exc

    @staticmethod
    def _trusted_channel(url: str) -> bool:
        parts = urlsplit(url)
        if parts.scheme == "https":
            return True
        allowed = [
            h.strip().lower()
            for h in (os.environ.get("EGRESS_ALLOWED_HOSTS") or "").split(",")
            if h.strip()
        ]
        host = (parts.hostname or "").lower()
        return parts.scheme == "http" and (
            host in allowed or parts.netloc.lower() in allowed
        )

    # -- the SP for an IdP ---------------------------------------------------

    def enabled(self, idp_id: str) -> SamlIdentityProviderModel:
        idp = self.stored(idp_id)
        if not idp.is_enabled:
            raise HTTPException(
                status_code=404, detail="No such SAML identity provider"
            )
        return idp

    def service_provider(self, idp: SamlIdentityProviderModel) -> ServiceProvider:
        base = f"{_server_uri()}{ROUTE_PREFIX}/{idp.id}"
        return ServiceProvider(
            entity_id=idp.sp_entity_id or f"{base}/metadata",
            acs_url=f"{base}/acs",
            idp_metadata_xml=idp.metadata_xml,
            want_assertions_signed=idp.want_assertions_signed,
            want_response_signed=idp.want_response_signed,
            allow_unsolicited=idp.allow_unsolicited,
            name_id_format=idp.name_id_format,
            private_key_pem=decrypt_secret(idp.sp_private_key),
            certificate_pem=idp.sp_certificate,
        )

    # -- signing in -----------------------------------------------------------

    def _root_db(self, model: Any) -> Any:
        return model.DB(self.model_registry.DB.manager.Base)

    async def begin_login(
        self, idp_id: str, return_to: Optional[str]
    ) -> RedirectResponse:
        idp = self.enabled(idp_id)
        destination = safe_return_to(return_to)
        sp = self.service_provider(idp)
        try:
            request = await asyncio.to_thread(sp.authn_request)
        except InvalidInputExternalError as exc:
            logger.warning(
                "saml_consumer: %s cannot send an AuthnRequest: %s", idp_id, exc
            )
            raise HTTPException(
                status_code=503, detail="This identity provider is misconfigured"
            ) from exc
        browser_key = secrets.token_urlsafe(BROWSER_KEY_BYTES)
        self._root_db(SamlAuthnRequestModel).create(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            request_id=request.request_id,
            identity_provider_id=str(idp.id),
            return_to=destination,
            browser_key_hash=_sha256(browser_key),
            expires_at=_now() + timedelta(seconds=REQUEST_TTL_SECONDS),
        )
        redirect = RedirectResponse(request.location, status_code=_REDIRECT_STATUS)
        # The IdP's POST back is cross-site: only SameSite=None reaches it.
        redirect.set_cookie(
            REQUEST_COOKIE,
            browser_key,
            max_age=REQUEST_TTL_SECONDS,
            path=_acs_path(str(idp.id)),
            secure=True,
            httponly=True,
            samesite="none",
        )
        return redirect

    def _outstanding(
        self, idp_id: str, browser_key: Optional[str]
    ) -> Optional[SamlAuthnRequestModel]:
        """The unexpired request this browser started with this IdP."""
        if not browser_key:
            return None
        RequestDB = self._root_db(SamlAuthnRequestModel)
        rows: List[SamlAuthnRequestModel] = RequestDB.list(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            filters=[
                RequestDB.identity_provider_id == idp_id,
                RequestDB.browser_key_hash == _sha256(browser_key),
                RequestDB.deleted_at.is_(None),
            ],
            return_type="dto",
            override_dto=SamlAuthnRequestModel,
        )
        live = [row for row in rows if _aware(row.expires_at) > _now()]
        return live[0] if live else None

    def _spend(self, spent_key: str, expires_at: datetime) -> None:
        """Record ``spent_key`` as used; refused if it already was. The
        unique column makes this atomic across workers."""
        try:
            self._root_db(SamlSpentMessageModel).create(
                requester_id=env("ROOT_ID"),
                model_registry=self.model_registry,
                spent_key=spent_key,
                expires_at=expires_at,
            )
        except IntegrityError as exc:
            raise SAMLResponseRefused(
                "replayed", f"{spent_key} was already used"
            ) from exc

    async def complete_login(
        self,
        idp_id: str,
        saml_response: str,
        relay_state: Optional[str],
        browser_key: Optional[str],
    ) -> RedirectResponse:
        idp = self.enabled(idp_id)
        sp = self.service_provider(idp)
        outstanding = self._outstanding(str(idp.id), browser_key)
        try:
            identity = await asyncio.to_thread(
                sp.validate,
                saml_response,
                outstanding.request_id if outstanding else None,
            )
            if outstanding is not None and identity.in_response_to is not None:
                self._spend(
                    f"request:{outstanding.request_id}", _aware(outstanding.expires_at)
                )
            self._spend(
                f"assertion:{identity.issuer}:{identity.assertion_id}",
                identity.not_on_or_after,
            )
        except SAMLResponseRefused as exc:
            raise _refused(exc) from exc
        except InvalidInputExternalError as exc:
            raise _refused(SAMLResponseRefused("malformed", exc.message)) from exc
        if identity.in_response_to is not None and outstanding is not None:
            destination = outstanding.return_to
        else:
            destination = self._unsolicited_destination(relay_state)
        user = SamlAccounts(self.model_registry, idp).user_for(identity)
        return self._signed_in(user, destination, str(idp.id))

    @staticmethod
    def _unsolicited_destination(relay_state: Optional[str]) -> str:
        """An IdP-initiated sign-in's RelayState, where it is an allowed
        address; else the default."""
        try:
            return safe_return_to(relay_state)
        except HTTPException:
            return DEFAULT_RETURN_TO

    def _signed_in(
        self, user: UserModel, destination: str, idp_id: str
    ) -> RedirectResponse:
        """The session a passwordless grant issues, in the browser's
        cookies; or, for a user with a second factor, its challenge in the
        fragment of the destination, to complete at /v1/user/authorize/mfa."""
        challenge = UserManager.mfa_challenge(str(user.id), self.model_registry)
        if challenge is not None:
            parts = urlsplit(destination)
            fragment = urlencode({MFA_FRAGMENT_KEY: challenge["challenge_token"]})
            redirect = RedirectResponse(
                urlunsplit(parts._replace(fragment=fragment)),
                status_code=_REDIRECT_STATUS,
            )
        else:
            session = UserManager.login_via_grant(
                grant_type=GRANT_TYPE,
                grant_payload=UserIdGrantPayload(
                    user_id=str(user.id), model_registry=self.model_registry
                ),
                model_registry=self.model_registry,
            )
            redirect = RedirectResponse(destination, status_code=_REDIRECT_STATUS)
            issue_browser_session(
                redirect, UserManager.session_token(session, self.model_registry)
            )
        redirect.delete_cookie(
            REQUEST_COOKIE,
            path=_acs_path(idp_id),
            secure=True,
            httponly=True,
            samesite="none",
        )
        return redirect

    # -- routes -----------------------------------------------------------------

    @custom_route(
        method="GET",
        path="",
        output_model=IdentityProviderList,
        authentication_type="none",
        expose_in=(ExposeIn.REST,),
        summary="The SAML identity providers users can sign in through",
    )
    def list_enabled_route(self) -> IdentityProviderList:
        IdPDB = self.DB
        rows: List[SamlIdentityProviderModel] = IdPDB.list(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            filters=[IdPDB.is_enabled.is_(True), IdPDB.deleted_at.is_(None)],
            return_type="dto",
            override_dto=SamlIdentityProviderModel,
        )
        return IdentityProviderList(
            identity_providers=[
                IdentityProviderListing(
                    id=str(row.id),
                    name=row.name,
                    login_url=f"{_server_uri()}{ROUTE_PREFIX}/{row.id}/login",
                )
                for row in rows
            ]
        )

    @custom_route(
        method="GET",
        path="/{idp_id}/metadata",
        authentication_type="none",
        expose_in=(ExposeIn.REST,),
        response_class=Response,
        summary="This SP's SAML metadata, for the IdP",
    )
    async def sp_metadata_route(self, idp_id: str) -> Response:
        sp = self.service_provider(self.enabled(idp_id))
        xml = await asyncio.to_thread(sp.metadata_xml)
        return Response(content=xml, media_type=METADATA_MEDIA_TYPE)

    @custom_route(
        method="GET",
        path="/{idp_id}/login",
        authentication_type="none",
        expose_in=(ExposeIn.REST,),
        response_class=RedirectResponse,
        summary="Sign in through the IdP: redirects the browser there",
    )
    @rate_limit(SIGN_IN_RATE_LIMIT, scope="ip")
    async def login_route(
        self, idp_id: str, return_to: Optional[str] = None
    ) -> RedirectResponse:
        return await self.begin_login(idp_id, return_to)

    @custom_route(
        method="POST",
        path="/{idp_id}/acs",
        input_model=SAMLPostBinding,
        authentication_type="none",
        expose_in=(ExposeIn.REST,),
        response_class=RedirectResponse,
        summary="Assertion Consumer Service: the IdP posts its response here",
    )
    @rate_limit(SIGN_IN_RATE_LIMIT, scope="ip")
    async def acs_route(
        self, idp_id: str, body: SAMLPostBinding, request: Request
    ) -> RedirectResponse:
        return await self.complete_login(
            idp_id,
            body.SAMLResponse,
            body.RelayState,
            request.cookies.get(REQUEST_COOKIE),
        )

    @custom_route(
        method="POST",
        path="/import",
        input_model=IdentityProviderImport,
        output_model=IdentityProviderSummary,
        authentication_type="api_key",
        expose_in=(ExposeIn.REST,),
        summary="Add an IdP from its metadata URL or XML",
    )
    async def import_route(
        self, body: IdentityProviderImport
    ) -> IdentityProviderSummary:
        self._require_server_side()
        if bool(body.metadata_url) == bool(body.metadata_xml):
            raise HTTPException(
                status_code=400, detail="Give one of metadata_url or metadata_xml"
            )
        fields = body.model_dump()
        if body.metadata_url:
            fields["metadata_xml"] = await self.fetch_metadata(body.metadata_url)
        created = self.create(**{k: v for k, v in fields.items() if v is not None})
        return self.summary(self.stored(str(created.id)))

    @custom_route(
        method="POST",
        path="/{idp_id}/refresh",
        input_model=MetadataRefresh,
        output_model=IdentityProviderSummary,
        authentication_type="api_key",
        expose_in=(ExposeIn.REST,),
        summary="Re-read the IdP's metadata from its metadata_url",
    )
    async def refresh_route(
        self, idp_id: str, body: MetadataRefresh
    ) -> IdentityProviderSummary:
        self._require_server_side()
        idp = self.stored(idp_id)
        if not idp.metadata_url:
            raise HTTPException(status_code=400, detail="This IdP has no metadata_url")
        self.update(idp_id, metadata_xml=await self.fetch_metadata(idp.metadata_url))
        return self.summary(self.stored(idp_id))


def _aware(value: datetime) -> datetime:
    """A stored timestamp as an aware UTC datetime (SQLite drops the zone)."""
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


class SamlIdentityManager(AbstractBLLManager, RouterMixin):
    """The caller's SAML identities: read them, or unlink one."""

    _model = SamlIdentityModel

    prefix: ClassVar[Optional[str]] = "/v1/auth/saml/identity"
    tags: ClassVar[Optional[List[str]]] = ["SAML Sign-in"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    routes_to_register: ClassVar[Optional[List[RouteType]]] = [
        RouteType.GET,
        RouteType.LIST,
        RouteType.SEARCH,
        RouteType.DELETE,
    ]

    def create(self, **kwargs: Any) -> Any:
        """An identity belongs to its user: a user links only themselves;
        ROOT and SYSTEM (the sign-in) name the user, never an internal
        account (403)."""
        requester_id = str(self.requester.id)

        def owned(fields: Dict[str, Any]) -> Dict[str, Any]:
            if not _server_side(requester_id) or not fields.get("user_id"):
                fields["user_id"] = requester_id
            refuse_internal_account(fields["user_id"])
            return fields

        if isinstance(kwargs.get("entities"), list):
            kwargs["entities"] = [owned(dict(e)) for e in kwargs["entities"]]
            return super().create(**kwargs)
        return super().create(**owned(dict(kwargs)))

    def update(self, id: str, **kwargs: Any) -> Any:
        kwargs.pop("user_id", None)
        return super().update(id, **kwargs)


class SamlAuthnRequestManager(AbstractBLLManager):
    _model = SamlAuthnRequestModel


class SamlSpentMessageManager(AbstractBLLManager):
    _model = SamlSpentMessageModel


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------


class SamlAccounts:
    """The local user an asserted identity signs in as, made or linked as
    the module doc describes."""

    def __init__(self, model_registry: Any, idp: SamlIdentityProviderModel) -> None:
        self.model_registry = model_registry
        self.idp = idp
        self.root_id = env("ROOT_ID")

    def _db(self, model: Any) -> Any:
        return model.DB(self.model_registry.DB.manager.Base)

    def subject(self, identity: AssertedIdentity) -> Tuple[str, Optional[str]]:
        if self.idp.identity_attribute:
            value = identity.attribute([self.idp.identity_attribute])
            if not value:
                raise HTTPException(
                    status_code=401, detail="SAML response refused: no_subject"
                )
            return value, None
        if identity.is_transient:
            # A transient NameID changes every sign-in: it identifies no one.
            raise HTTPException(
                status_code=401, detail="SAML response refused: no_persistent_identity"
            )
        return identity.name_id, identity.name_id_format

    def user_for(self, identity: AssertedIdentity) -> UserModel:
        subject, subject_format = self.subject(identity)
        email = identity.email(self.idp.email_attribute)
        email = UserManager._normalize_identifier(email) if email else None
        linked = self._linked(identity.issuer, subject)
        if linked is not None:
            user = self._active_user(linked.user_id)
            self._touch(linked, email)
            return user
        user = self._by_verified_email(email) or self._registered(identity, email)
        self._link(user, identity, subject, subject_format, email)
        return user

    def _linked(self, issuer: str, subject: str) -> Optional[SamlIdentityModel]:
        IdentityDB = self._db(SamlIdentityModel)
        rows: List[SamlIdentityModel] = IdentityDB.list(
            requester_id=self.root_id,
            model_registry=self.model_registry,
            filters=[
                IdentityDB.idp_entity_id == issuer,
                IdentityDB.subject == subject,
                IdentityDB.deleted_at.is_(None),
            ],
            return_type="dto",
            override_dto=SamlIdentityModel,
        )
        return rows[0] if rows else None

    def _user(self, **filters: Any) -> Optional[UserModel]:
        UserDB = self._db(UserModel)
        rows: List[UserModel] = UserDB.list(
            requester_id=self.root_id,
            model_registry=self.model_registry,
            filters=[UserDB.deleted_at.is_(None)]
            + [getattr(UserDB, k) == v for k, v in filters.items()],
            return_type="dto",
            override_dto=UserModel,
        )
        return rows[0] if rows else None

    def _active_user(self, user_id: str) -> UserModel:
        refuse_internal_account(user_id)
        user = self._user(id=user_id)
        if user is None or user.active is False:
            raise HTTPException(status_code=403, detail="This account is disabled")
        return user

    def _by_verified_email(self, email: Optional[str]) -> Optional[UserModel]:
        """The account an asserted email names. Linking to it needs the IdP
        to vouch for its emails; otherwise the email is taken."""
        user_id = UserManager.user_id_for_verified_email(email, self.model_registry)
        if user_id is None:
            return None
        if not self.idp.emails_verified:
            raise HTTPException(
                status_code=409,
                detail="An account already uses this email; this IdP is not trusted to prove it",
            )
        return self._active_user(user_id)

    def _registered(
        self, identity: AssertedIdentity, email: Optional[str]
    ) -> UserModel:
        """A new account, as REGISTRATION_MODE allows."""
        # Read when it is needed: the settings object is replaced at boot.
        mode = Environment.settings.REGISTRATION_MODE
        verified_email = email if self.idp.emails_verified else None
        invitations: List[Dict[str, Any]] = []
        if mode == "closed":
            raise HTTPException(
                status_code=403, detail="User registration is currently closed"
            )
        if mode == "invite":
            pending_for = _invitation_hooks["pending_invitations_for_user"]
            if verified_email and pending_for is not None:
                invitations = pending_for("", verified_email, self.model_registry)
            if not invitations:
                raise HTTPException(
                    status_code=403, detail="User registration requires an invitation"
                )
        user: UserModel = self._db(UserModel).create(
            requester_id=self.root_id,
            model_registry=self.model_registry,
            return_type="dto",
            override_dto=self.model_registry.apply(UserModel),
            email=verified_email,
            first_name=identity.attribute(FIRST_NAME_ATTRIBUTES),
            last_name=identity.attribute(LAST_NAME_ATTRIBUTES),
            display_name=identity.attribute(DISPLAY_NAME_ATTRIBUTES),
        )
        apply_invitation = _invitation_hooks["apply_to_user"]
        for invitation in invitations:
            if apply_invitation is not None:
                apply_invitation(invitation, str(user.id), self.model_registry)
        logger.info(
            "saml_consumer: made user %s for a sign-in through %s", user.id, self.idp.id
        )
        return user

    def _link(
        self,
        user: UserModel,
        identity: AssertedIdentity,
        subject: str,
        subject_format: Optional[str],
        email: Optional[str],
    ) -> None:
        # Made as the user, so the user can see and unlink it.
        SamlIdentityManager(
            model_registry=self.model_registry, requester_id=str(user.id)
        ).create(
            identity_provider_id=str(self.idp.id),
            idp_entity_id=identity.issuer,
            subject=subject,
            subject_format=subject_format,
            asserted_email=email,
            last_login_at=_now(),
        )

    def _touch(self, linked: SamlIdentityModel, email: Optional[str]) -> None:
        self._db(SamlIdentityModel).update(
            requester_id=self.root_id,
            model_registry=self.model_registry,
            id=linked.id,
            new_properties={"last_login_at": _now(), "asserted_email": email},
        )


# The session a SAML sign-in issues is a passwordless grant's.
PasswordlessGrantRegistry.register(GRANT_TYPE, make_user_id_grant_validator("SAML"))

# The IdP's page posts the response here (HTTP-POST binding); it reaches the
# ACS without the session, and the browser binding cookie still arrives.
accept_cross_site_writes(f"{re.escape(ROUTE_PREFIX)}/[^/]+/acs")
accept_form_bodies(f"{re.escape(ROUTE_PREFIX)}/[^/]+/acs")
