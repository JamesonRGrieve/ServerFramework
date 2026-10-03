# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sign in with another identity provider ("Sign in with Google").

Each configured provider is a provider instance of one of this extension's
providers (``PRV_*``): Google, Microsoft, GitHub, Amazon, Forgejo, or any
OpenID provider by its issuer. A client names one by its instance name, or
by the provider's public name (``google``) when one instance of it is
configured; ``GET /v1/auth/oauth/providers`` lists them.

The flow (RFC 9700, OIDC Core):

1. ``POST /v1/auth/oauth/authorize`` records a pending sign-in: the state
   (stored hashed, single-use, ten minutes), the PKCE verifier (encrypted),
   the nonce, the provider instance and the exact redirect URI, and the hash
   of a browser-binding secret. It sets that secret in the HttpOnly, Secure,
   SameSite=Lax ``zx_oauth_binding`` cookie, scoped to these routes, and
   answers the provider's authorization URL.
2. ``POST /v1/auth/oauth/callback`` spends the state (a replay finds it
   spent), requires the binding cookie that started it (login CSRF: a code
   from another browser's flow is refused), exchanges the code with the
   verifier and resolves the account the provider vouched for.

The account is keyed on (provider instance, subject), never on email. A
first sign-in joins the local account with that email, or creates one,
only when the provider verified the email; creating one also needs
``REGISTRATION_MODE``: ``open`` creates, ``invite`` needs a pending
invitation to that address (auth_invitations), ``closed`` refuses. The
response is a password login's: the session token in the body and the
``zx_session``/``zx_csrf`` cookies, or the MFA challenge a user with a
second factor completes at ``POST /v1/user/authorize/mfa``.

A signed-in user links another provider through ``/link/authorize`` and
``/link/callback``, and lists or unlinks identities at
``/v1/auth/oauth/identity``. The provider's tokens are kept encrypted and
never returned over the API; the ``oauth_access_token`` ability hands its
owner a current access token.
"""

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import (
    Any,
    Awaitable,
    Callable,
    ClassVar,
    Dict,
    List,
    Optional,
    Tuple,
    Type,
    TypeVar,
)

from fastapi import HTTPException, Request, Response
from pydantic import BaseModel as RouteModel
from pydantic import Field
from sqlalchemy import delete, update

from zephyrex.database.StaticPermissions import is_root_id, is_system_id
from zephyrex.extensions.ExternalErrors import (
    BaseExternalError,
    InvalidInputExternalError,
    TransientExternalError,
)
from zephyrex.extensions.oauth_consumer.IdentityProvider import (
    AbstractIdentityProvider,
    Endpoints,
    Identity,
    TokenSet,
)
from zephyrex.lib import Environment
from zephyrex.lib.CustomRoute import ExposeIn, custom_route
from zephyrex.lib.DateTimeUtils import ensure_utc
from zephyrex.lib.Environment import env
from zephyrex.lib.InboundSecurity import DEFAULT_AUTH_RATE_LIMIT, rate_limit
from zephyrex.lib.Logging import logger
from zephyrex.lib.SecretEncryption import decrypt_secret, encrypt_secret
from zephyrex.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    DateSearchModel,
    ModelMeta,
    StringSearchModel,
    UpdateMixinModel,
)
from zephyrex.logic.BLL_Auth import (
    InvalidGrantError,
    PasswordlessGrantRegistry,
    UserIdGrantPayload,
    UserManager,
    UserModel,
    _invitation_hooks,
    refuse_internal_account,
)
from zephyrex.logic.BLL_Auth.user import issue_browser_session
from zephyrex.logic.BLL_Providers import (
    ProviderInstanceModel,
    ProviderManager,
)
from zephyrex.pydantic2.fastapi import AuthType, RouterMixin, RouteType
from zephyrex.pydantic2.registry import BaseModel

OAUTH_PREFIX = "/v1/auth/oauth"
GRANT_TYPE = "oauth_consumer"
# The browser that began a sign-in proves it with this HttpOnly cookie.
BINDING_COOKIE = "zx_oauth_binding"
# Window between /authorize and /callback: long enough to sign in at the
# provider, short enough to bound a leaked state's use.
STATE_TTL_SECONDS = 600
_SECRET_BYTES = 32
# 64 bytes give an 86-character verifier (RFC 7636: 43 to 128).
_VERIFIER_BYTES = 64
_PROVIDER_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_INVALID_STATE = "Invalid or expired OAuth state"
_UNVERIFIED_EMAIL = (
    "The identity provider did not confirm ownership of this email address"
)

Result = TypeVar("Result")


def _digest(secret: str) -> str:
    """SHA-256 of a random secret: what is stored in its place."""
    return hashlib.sha256(secret.encode()).hexdigest()


def _same_digest(stored: Optional[str], secret: Optional[str]) -> bool:
    return bool(stored and secret) and hmac.compare_digest(
        str(stored), _digest(str(secret))
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _server_side(requester_id: str) -> bool:
    """ROOT and SYSTEM act on others' behalf; users act as themselves."""
    return is_root_id(requester_id) or is_system_id(requester_id)


def _each(
    kwargs: Dict[str, Any], prepare: Callable[[Dict[str, Any]], Dict[str, Any]]
) -> Dict[str, Any]:
    """``kwargs`` for a create, or each of a batch's ``entities``, prepared."""
    if isinstance(kwargs.get("entities"), list):
        return {**kwargs, "entities": [prepare(dict(e)) for e in kwargs["entities"]]}
    return prepare(dict(kwargs))


# ---------------------------------------------------------------------------
# Database models
# ---------------------------------------------------------------------------


class OAuthIdentityModel(
    ApplicationModel,
    UpdateMixinModel,
    UserModel.Reference,
    metaclass=ModelMeta,
):
    """A local user's account at an identity provider. The provider's
    tokens are encrypted, and never serialized."""

    provider_instance_id: str = Field(
        ..., description="The configured provider (provider instance) it is at"
    )
    provider: str = Field(..., description="The provider's public name (google, oidc…)")
    issuer: Optional[str] = Field(None, description="The OpenID issuer, if any")
    subject: str = Field(..., description="The account's stable id at the provider")
    email: Optional[str] = Field(None, description="Email the provider reported")
    email_verified: bool = Field(
        False, description="Whether the provider said it verified the email"
    )
    display_name: Optional[str] = Field(None, description="Name the provider reported")
    access_token: Optional[str] = Field(
        None, exclude=True, description="The provider's access token (encrypted)"
    )
    refresh_token: Optional[str] = Field(
        None, exclude=True, description="The provider's refresh token (encrypted)"
    )
    token_expires_at: Optional[datetime] = Field(
        None, description="When the access token expires"
    )
    scopes: Optional[str] = Field(None, description="Scopes the provider granted")
    last_login_at: Optional[datetime] = Field(
        None, description="When it last signed in"
    )

    table_comment: ClassVar[str] = (
        "A local user's account at an external identity provider (OAuth/OIDC)"
    )

    class Create(BaseModel, UserModel.Reference.ID.Optional):
        provider_instance_id: str
        provider: str
        issuer: Optional[str] = None
        subject: str
        email: Optional[str] = None
        email_verified: bool = False
        display_name: Optional[str] = None
        access_token: Optional[str] = None
        refresh_token: Optional[str] = None
        token_expires_at: Optional[datetime] = None
        scopes: Optional[str] = None
        last_login_at: Optional[datetime] = None

    # Written by the sign-in flow only: no update route exists.
    class Update(BaseModel):
        email: Optional[str] = None
        email_verified: Optional[bool] = None
        display_name: Optional[str] = None
        access_token: Optional[str] = None
        refresh_token: Optional[str] = None
        token_expires_at: Optional[datetime] = None
        scopes: Optional[str] = None
        last_login_at: Optional[datetime] = None

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
        UserModel.Reference.ID.Search,
    ):
        provider_instance_id: Optional[StringSearchModel] = None
        provider: Optional[StringSearchModel] = None
        subject: Optional[StringSearchModel] = None
        email: Optional[StringSearchModel] = None
        last_login_at: Optional[DateSearchModel] = None


class OAuthLoginStateModel(ApplicationModel, UpdateMixinModel, metaclass=ModelMeta):
    """A sign-in begun and not yet finished. The state and the browser
    binding are stored as their SHA-256; the PKCE verifier encrypted."""

    state_hash: str = Field(..., description="SHA-256 of the state")
    binding_hash: str = Field(..., description="SHA-256 of the browser binding")
    provider_instance_id: str = Field(..., description="The provider signed in with")
    redirect_uri: str = Field(..., description="The exact redirect URI sent")
    code_verifier: str = Field(
        ..., exclude=True, description="The PKCE code verifier (encrypted)"
    )
    nonce: str = Field(..., exclude=True, description="The OpenID nonce sent")
    link_user_id: Optional[str] = Field(
        None, description="The signed-in user linking a provider, if linking"
    )
    expires_at: datetime = Field(..., description="When the state lapses")
    consumed_at: Optional[datetime] = Field(
        None, description="When a callback spent the state"
    )

    table_comment: ClassVar[str] = "OAuth sign-ins begun and not yet completed"

    class Create(BaseModel):
        state_hash: str
        binding_hash: str
        provider_instance_id: str
        redirect_uri: str
        code_verifier: str
        nonce: str
        link_user_id: Optional[str] = None
        expires_at: datetime
        consumed_at: Optional[datetime] = None

    class Update(BaseModel):
        consumed_at: Optional[datetime] = None

    class Search(ApplicationModel.Search, UpdateMixinModel.Search):
        provider_instance_id: Optional[StringSearchModel] = None
        expires_at: Optional[DateSearchModel] = None


# ---------------------------------------------------------------------------
# Route models
# ---------------------------------------------------------------------------


class OAuthProviderEntry(RouteModel):
    name: str = Field(..., description="What to pass as `provider`")
    provider: str = Field(..., description="The provider's public name")
    friendly_name: str
    kind: str = Field(..., description="oidc or oauth2")


class OAuthProviderList(RouteModel):
    providers: List[OAuthProviderEntry]


class OAuthAuthorizeRequest(RouteModel):
    provider: str = Field(..., description="A name from GET /v1/auth/oauth/providers")
    redirect_uri: Optional[str] = Field(
        None,
        description="One of the provider's registered redirect URIs, exactly; "
        "defaults to <APP_URI>/user/close/<provider>",
    )


class OAuthAuthorizeResponse(RouteModel):
    authorize_url: str = Field(..., description="Send the browser here")
    state: str = Field(..., description="The state the callback returns")


class OAuthCallbackRequest(RouteModel):
    provider: str
    code: str = Field(..., description="The authorization code from the redirect")
    state: str = Field(..., description="The state from the redirect")
    redirect_uri: Optional[str] = Field(
        None, description="The redirect URI /authorize used"
    )
    iss: Optional[str] = Field(
        None, description="The redirect's iss parameter, when it carries one (RFC 9207)"
    )


class OAuthCallbackResponse(RouteModel):
    """A password login's answer: the session (``token``, also set in the
    ``zx_session``/``zx_csrf`` cookies), or for a user with a verified
    second factor the MFA challenge to complete at POST
    /v1/user/authorize/mfa."""

    user_id: str
    token: Optional[str] = Field(
        None, description="JWT to present as Authorization: Bearer"
    )
    session_key: Optional[str] = None
    grant_type: Optional[str] = None
    expires_at: Optional[datetime] = None
    new_user: bool = Field(False, description="Whether this sign-in created the user")
    mfa_required: bool = False
    challenge_token: Optional[str] = None
    methods: Optional[List[Dict[str, str]]] = None


class OAuthIdentityView(RouteModel):
    id: str
    provider_instance_id: str
    provider: str
    subject: str
    email: Optional[str] = None
    email_verified: bool = False
    display_name: Optional[str] = None
    last_login_at: Optional[datetime] = None

    @classmethod
    def of(cls, identity: OAuthIdentityModel) -> "OAuthIdentityView":
        return cls(
            id=str(identity.id),
            provider_instance_id=identity.provider_instance_id,
            provider=identity.provider,
            subject=identity.subject,
            email=identity.email,
            email_verified=bool(identity.email_verified),
            display_name=identity.display_name,
            last_login_at=identity.last_login_at,
        )


class OAuthAccessToken(RouteModel):
    access_token: str
    expires_at: Optional[datetime] = None
    scopes: Optional[str] = None


# ---------------------------------------------------------------------------
# Configured providers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConfiguredProvider:
    """A provider instance someone can sign in with, and its provider."""

    instance: ProviderInstanceModel
    provider: Type[AbstractIdentityProvider]


def configured_providers(model_registry: Any) -> List[ConfiguredProvider]:
    """Every enabled, undeleted instance of this extension's providers that names
    enough (a client id; an issuer) to sign anyone in."""
    from zephyrex.extensions.oauth_consumer.EXT_OAuthConsumer import (
        EXT_OAuthConsumer,
    )

    root_id = env("ROOT_ID")
    providers = ProviderManager(model_registry=model_registry, requester_id=root_id)
    InstanceDB = ProviderInstanceModel.DB(model_registry.DB.manager.Base)
    found: List[ConfiguredProvider] = []
    for provider in EXT_OAuthConsumer.providers:
        if not issubclass(provider, AbstractIdentityProvider):
            continue
        try:
            record = providers.get(name=provider.name)
        except HTTPException:
            continue
        # Read as root, which sees deleted rows unless told not to.
        rows: List[ProviderInstanceModel] = InstanceDB.list(
            requester_id=root_id,
            model_registry=model_registry,
            filters=[
                InstanceDB.provider_id == record.id,
                InstanceDB.deleted_at.is_(None),
            ],
            return_type="dto",
            override_dto=ProviderInstanceModel,
        )
        for row in rows or []:
            instance = ProviderInstanceModel.model_validate(row, from_attributes=True)
            if instance.enabled is False:
                continue
            if provider.is_configured_instance(instance):
                found.append(ConfiguredProvider(instance, provider))
    return found


def public_names(configured: List[ConfiguredProvider]) -> Dict[str, str]:
    """What clients call each instance (by id): the provider's public name
    when it is the only one configured and no instance is named that,
    else its own name."""
    names = {c.instance.name for c in configured}
    counts: Dict[str, int] = {}
    for c in configured:
        counts[c.provider.public_name] = counts.get(c.provider.public_name, 0) + 1
    return {
        str(c.instance.id): (
            c.provider.public_name
            if counts[c.provider.public_name] == 1
            and c.provider.public_name not in names
            else c.instance.name
        )
        for c in configured
    }


def resolve_provider(model_registry: Any, key: str) -> ConfiguredProvider:
    """The configured provider ``key`` names: an instance's name, or a
    provider's public name when one instance of it is configured."""
    if not _PROVIDER_KEY.match(key or ""):
        raise HTTPException(status_code=400, detail="Unknown OAuth provider")
    configured = configured_providers(model_registry)
    for candidate in configured:
        if candidate.instance.name == key:
            return candidate
    by_public = [c for c in configured if c.provider.public_name == key]
    if len(by_public) == 1:
        return by_public[0]
    raise HTTPException(status_code=400, detail="Unknown OAuth provider")


async def upstream(call: Awaitable[Result]) -> Result:
    """``call`` to a provider, its typed failures as HTTP: the caller's (a
    refused code, an invalid ID token) 400 with the reason; anything else
    502, its detail logged only."""
    try:
        return await call
    except InvalidInputExternalError as exc:
        logger.info("oauth_consumer: refused: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except TransientExternalError as exc:
        logger.warning("oauth_consumer: provider unreachable: %s", exc)
        raise HTTPException(
            status_code=502, detail="The identity provider could not be reached"
        ) from None
    except BaseExternalError as exc:
        logger.error("oauth_consumer: provider failure: %s", exc)
        raise HTTPException(
            status_code=502, detail="Sign-in with this provider is not available"
        ) from None


def _binding_cookie_scope(request: Request) -> Dict[str, Any]:
    """The binding cookie is sent to these routes only and never readable
    by page scripts."""
    return {
        "path": f"{request.scope.get('root_path', '')}{OAUTH_PREFIX}",
        "domain": env("SESSION_COOKIE_DOMAIN") or None,
        "secure": True,
        "httponly": True,
        "samesite": "lax",
    }


# ---------------------------------------------------------------------------
# Managers
# ---------------------------------------------------------------------------


class OAuthIdentityManager(AbstractBLLManager, RouterMixin):
    """A user's identities at providers: listed and unlinked over REST
    (``/v1/auth/oauth/identity``); created by the sign-in flow."""

    _model = OAuthIdentityModel

    prefix: ClassVar[Optional[str]] = f"{OAUTH_PREFIX}/identity"
    tags: ClassVar[Optional[List[str]]] = ["OAuth Consumer"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    routes_to_register: ClassVar[Optional[List[RouteType]]] = [
        RouteType.GET,
        RouteType.LIST,
        RouteType.SEARCH,
        RouteType.DELETE,
    ]

    def create(self, **kwargs: Any) -> Any:
        """An identity of the requester's own; ROOT and SYSTEM name the user,
        never an internal account (403)."""
        requester_id = self.requester.id

        def owned(fields: Dict[str, Any]) -> Dict[str, Any]:
            if not _server_side(requester_id) or not fields.get("user_id"):
                fields["user_id"] = requester_id
            refuse_internal_account(fields["user_id"])
            return fields

        return super().create(**_each(kwargs, owned))

    def find(
        self, provider_instance_id: str, subject: str
    ) -> Optional[OAuthIdentityModel]:
        """The live identity for (provider instance, subject), whoever owns
        it (read as root: the sign-in has no user yet)."""
        IdentityDB = OAuthIdentityModel.DB(self.model_registry.DB.manager.Base)
        rows: List[OAuthIdentityModel] = IdentityDB.list(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            filters=[
                IdentityDB.provider_instance_id == provider_instance_id,
                IdentityDB.subject == subject,
                IdentityDB.deleted_at.is_(None),
            ],
            return_type="dto",
            override_dto=OAuthIdentityModel,
        )
        return rows[0] if rows else None


class OAuthConsumerManager(AbstractBLLManager, RouterMixin):
    """The sign-in flow's routes, over pending sign-ins."""

    _model = OAuthLoginStateModel

    prefix: ClassVar[Optional[str]] = OAUTH_PREFIX
    tags: ClassVar[Optional[List[str]]] = ["OAuth Consumer"]
    auth_type: ClassVar[AuthType] = AuthType.NONE
    routes_to_register: ClassVar[Optional[List[RouteType]]] = []

    # -- helpers -------------------------------------------------------------

    @property
    def _identities(self) -> OAuthIdentityManager:
        return OAuthIdentityManager(
            requester_id=env("ROOT_ID"), model_registry=self.model_registry
        )

    @property
    def _StateDB(self) -> Any:
        return OAuthLoginStateModel.DB(self.model_registry.DB.manager.Base)

    @staticmethod
    def _redirect_uri(
        configured: ConfiguredProvider, key: str, requested: Optional[str]
    ) -> str:
        """``requested`` when it is a registered redirect URI, exactly (RFC
        9700 §4.1.3: no prefix or pattern matching); without one, the one
        for this provider. Unregistered: 400."""
        allowed = configured.provider.redirect_uris(configured.instance) or [
            f"{env('APP_URI').rstrip('/')}/user/close/{key}"
        ]
        target = requested or next(
            (uri for uri in allowed if uri.endswith(f"/{key}")), allowed[0]
        )
        if target not in allowed:
            raise HTTPException(status_code=400, detail="redirect_uri not registered")
        return target

    def _purge_lapsed(self) -> None:
        """Drop pending sign-ins whose state lapsed (spent or not)."""
        StateDB = self._StateDB
        session = self.model_registry.DB.session()
        try:
            session.execute(delete(StateDB).where(StateDB.expires_at < _now()))
            session.commit()
        finally:
            session.close()

    def _claim(self, state: str) -> Optional[OAuthLoginStateModel]:
        """Spend ``state``: the pending sign-in it names, marked consumed by
        a single conditional update so that, of concurrent callbacks, one
        wins; None when it names none, or was spent."""
        StateDB = self._StateDB
        rows: List[OAuthLoginStateModel] = StateDB.list(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            filters=[
                StateDB.state_hash == _digest(state),
                StateDB.consumed_at.is_(None),
                StateDB.deleted_at.is_(None),
            ],
            return_type="dto",
            override_dto=OAuthLoginStateModel,
        )
        if len(rows) != 1:
            return None
        session = self.model_registry.DB.session()
        try:
            claimed = session.execute(
                update(StateDB)
                .where(StateDB.id == rows[0].id, StateDB.consumed_at.is_(None))
                .values(consumed_at=_now())
            ).rowcount
            session.commit()
        finally:
            session.close()
        return rows[0] if claimed == 1 else None

    async def begin(
        self,
        key: str,
        requested_redirect: Optional[str],
        link_user_id: Optional[str],
    ) -> Tuple[OAuthAuthorizeResponse, str]:
        """Record a pending sign-in; the authorization URL and state, and
        the browser-binding secret for its cookie."""
        configured = resolve_provider(self.model_registry, key)
        redirect_uri = self._redirect_uri(configured, key, requested_redirect)
        endpoints = await upstream(configured.provider.endpoints(configured.instance))
        state = secrets.token_urlsafe(_SECRET_BYTES)
        binding = secrets.token_urlsafe(_SECRET_BYTES)
        verifier = secrets.token_urlsafe(_VERIFIER_BYTES)
        nonce = secrets.token_urlsafe(_SECRET_BYTES)
        authorize_url = configured.provider.authorization_url(
            configured.instance,
            endpoints,
            redirect_uri=redirect_uri,
            state=state,
            nonce=nonce,
            code_verifier=verifier,
        )
        self._purge_lapsed()
        self._StateDB.create(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            return_type="dto",
            override_dto=OAuthLoginStateModel,
            state_hash=_digest(state),
            binding_hash=_digest(binding),
            provider_instance_id=str(configured.instance.id),
            redirect_uri=redirect_uri,
            code_verifier=encrypt_secret(verifier),
            nonce=nonce,
            link_user_id=link_user_id,
            expires_at=_now() + timedelta(seconds=STATE_TTL_SECONDS),
        )
        return OAuthAuthorizeResponse(authorize_url=authorize_url, state=state), binding

    async def finish(
        self,
        body: OAuthCallbackRequest,
        binding: Optional[str],
        link_user_id: Optional[str],
    ) -> Tuple[ConfiguredProvider, Identity, TokenSet]:
        """Spend the state, check it was issued for this provider, redirect
        URI, browser and purpose, and exchange the code with its verifier:
        the account the provider vouched for."""
        pending = self._claim(body.state)
        configured = resolve_provider(self.model_registry, body.provider)
        redirect_uri = self._redirect_uri(configured, body.provider, body.redirect_uri)
        if (
            pending is None
            or ensure_utc(pending.expires_at) <= _now()
            or pending.provider_instance_id != str(configured.instance.id)
            or pending.redirect_uri != redirect_uri
            or pending.link_user_id != link_user_id
            or not _same_digest(pending.binding_hash, binding)
        ):
            raise HTTPException(status_code=400, detail=_INVALID_STATE)
        provider, instance = configured.provider, configured.instance
        endpoints = await upstream(provider.endpoints(instance))
        self._check_issuer(endpoints, body.iss)
        tokens = await upstream(
            provider.exchange_code(
                instance,
                endpoints,
                code=body.code,
                redirect_uri=redirect_uri,
                code_verifier=str(decrypt_secret(pending.code_verifier)),
            )
        )
        identity = await upstream(
            provider.identity(instance, endpoints, tokens, pending.nonce)
        )
        return configured, identity, tokens

    @staticmethod
    def _check_issuer(endpoints: Endpoints, iss: Optional[str]) -> None:
        """RFC 9207 mix-up defence: a redirect naming an issuer must name
        this provider's, and a provider that promises to name itself must."""
        issuer = endpoints.issuer
        if iss is None:
            if endpoints.issuer_in_response:
                raise HTTPException(status_code=400, detail="Missing iss parameter")
            return
        if (
            issuer is not None
            and "{" not in issuer
            and not hmac.compare_digest(iss, issuer)
        ):
            raise HTTPException(status_code=400, detail="Issuer mismatch")

    @staticmethod
    def _stored_tokens(identity: Identity, tokens: TokenSet) -> Dict[str, Any]:
        """The identity's fields a sign-in refreshes. A refresh token is
        kept until the provider issues another."""
        fields: Dict[str, Any] = {
            "email": identity.email,
            "email_verified": identity.email_verified,
            "display_name": identity.display_name,
            "access_token": encrypt_secret(tokens.access_token),
            "token_expires_at": tokens.expires_at,
            "scopes": tokens.scope,
            "last_login_at": _now(),
        }
        if tokens.refresh_token is not None:
            fields["refresh_token"] = encrypt_secret(tokens.refresh_token)
        return fields

    def _link(
        self,
        configured: ConfiguredProvider,
        identity: Identity,
        tokens: TokenSet,
        user_id: str,
    ) -> OAuthIdentityModel:
        """A new identity of ``user_id``'s, created as that user: a record
        root creates only root may change, and its owner unlinks it."""
        created: OAuthIdentityModel = OAuthIdentityManager(
            requester_id=user_id, model_registry=self.model_registry
        ).create(
            provider_instance_id=str(configured.instance.id),
            provider=configured.provider.public_name,
            issuer=identity.issuer,
            subject=identity.subject,
            **self._stored_tokens(identity, tokens),
        )
        return created

    def _user(self, user_id: str) -> UserModel:
        UserDB = UserModel.DB(self.model_registry.DB.manager.Base)
        user: Optional[UserModel] = UserDB.get(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            id=user_id,
            return_type="dto",
            override_dto=UserModel,
        )
        if user is None:
            raise InvalidGrantError(detail="The linked user no longer exists")
        return user

    def _assert_signup_allowed(self, email: str) -> None:
        """``REGISTRATION_MODE``: open creates; invite needs a pending
        invitation addressed to ``email``; closed refuses."""
        mode = Environment.settings.REGISTRATION_MODE
        if mode == "open":
            return
        if mode == "invite":
            pending_for = _invitation_hooks["pending_invitations_for_user"]
            if pending_for is not None and pending_for("", email, self.model_registry):
                return
            raise HTTPException(
                status_code=403, detail="User registration requires an invitation"
            )
        raise HTTPException(
            status_code=403, detail="User registration is currently closed"
        )

    def _username_for(self, identity: Identity) -> Optional[str]:
        """The provider's username, when no local user has it."""
        if identity.username is None:
            return None
        username = UserManager._normalize_identifier(identity.username)
        UserDB = UserModel.DB(self.model_registry.DB.manager.Base)
        taken = UserDB.exists(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            username=username,
        )
        return None if taken else username

    def _create_user(self, identity: Identity, email: str) -> UserModel:
        """A passwordless local user from the provider's profile."""
        UserDB = UserModel.DB(self.model_registry.DB.manager.Base)
        user: UserModel = UserDB.create(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            override_dto=self.model_registry.apply(UserModel),
            return_type="dto",
            email=email,
            username=self._username_for(identity),
            first_name=identity.first_name,
            last_name=identity.last_name,
            display_name=identity.display_name,
            image_url=identity.picture,
        )
        return user

    def sign_in_user(
        self, configured: ConfiguredProvider, identity: Identity, tokens: TokenSet
    ) -> Tuple[str, bool]:
        """The local user ``identity`` signs in as, and whether it was
        created: the one it is linked to; else, with a verified email, the
        user with that email, or a new one where registration allows."""
        linked = self._identities.find(str(configured.instance.id), identity.subject)
        if linked is not None:
            assert_may_sign_in(self._user(linked.user_id))
            self._identities.update(
                id=str(linked.id), **self._stored_tokens(identity, tokens)
            )
            return str(linked.user_id), False
        if identity.email is None or not identity.email_verified:
            raise HTTPException(status_code=403, detail=_UNVERIFIED_EMAIL)
        email = UserManager._normalize_identifier(identity.email)
        existing_id = UserManager.user_id_for_verified_email(email, self.model_registry)
        if existing_id is not None:
            assert_may_sign_in(self._user(existing_id))
            self._link(configured, identity, tokens, existing_id)
            return existing_id, False
        self._assert_signup_allowed(email)
        user = self._create_user(identity, email)
        self._link(configured, identity, tokens, str(user.id))
        return str(user.id), True

    def session_for(self, user_id: str, new_user: bool) -> OAuthCallbackResponse:
        """A session for ``user_id``, or the MFA challenge its second
        factor stands for (the provider proves the first factor only)."""
        challenge = UserManager.mfa_challenge(user_id, self.model_registry)
        if challenge is not None:
            return OAuthCallbackResponse(
                user_id=user_id, new_user=new_user, **challenge
            )
        session = UserManager.login_via_grant(
            grant_type=GRANT_TYPE,
            grant_payload=UserIdGrantPayload(
                user_id=user_id, model_registry=self.model_registry
            ),
            model_registry=self.model_registry,
        )
        return OAuthCallbackResponse(
            user_id=str(session.user_id),
            token=UserManager.session_token(session, self.model_registry),
            session_key=session.session_key,
            grant_type=session.grant_type or GRANT_TYPE,
            expires_at=session.expires_at,
            new_user=new_user,
        )

    def link_identity(
        self,
        configured: ConfiguredProvider,
        identity: Identity,
        tokens: TokenSet,
        user_id: str,
    ) -> OAuthIdentityModel:
        """Link ``identity`` to ``user_id`` (refreshing it when it already
        is); 409 when another user has it."""
        linked = self._identities.find(str(configured.instance.id), identity.subject)
        if linked is None:
            return self._link(configured, identity, tokens, user_id)
        if str(linked.user_id) != user_id:
            raise HTTPException(
                status_code=409, detail="This identity is linked to another account"
            )
        updated: OAuthIdentityModel = self._identities.update(
            id=str(linked.id), **self._stored_tokens(identity, tokens)
        )
        return updated

    def providers_list(self) -> OAuthProviderList:
        configured = configured_providers(self.model_registry)
        names = public_names(configured)
        return OAuthProviderList(
            providers=[
                OAuthProviderEntry(
                    name=names[str(c.instance.id)],
                    provider=c.provider.public_name,
                    friendly_name=c.provider.friendly_name,
                    kind=c.provider.kind,
                )
                for c in configured
            ]
        )

    # -- routes ----------------------------------------------------------------

    @custom_route(
        method="GET",
        path="/providers",
        output_model=OAuthProviderList,
        authentication_type="none",
        expose_in=(ExposeIn.REST, ExposeIn.SDK),
        openapi_tags=("OAuth Consumer",),
        summary="The identity providers users can sign in with",
    )
    def providers_route(self) -> OAuthProviderList:
        return self.providers_list()

    @custom_route(
        method="POST",
        path="/authorize",
        input_model=OAuthAuthorizeRequest,
        output_model=OAuthAuthorizeResponse,
        authentication_type="none",
        expose_in=(ExposeIn.REST, ExposeIn.SDK),
        openapi_tags=("OAuth Consumer",),
        summary="Begin signing in with an identity provider",
    )
    @rate_limit(DEFAULT_AUTH_RATE_LIMIT, scope="ip")
    async def authorize_route(
        self, body: OAuthAuthorizeRequest, request: Request, response: Response
    ) -> OAuthAuthorizeResponse:
        """The provider's authorization URL; the browser-binding cookie."""
        begun, binding = await self.begin(body.provider, body.redirect_uri, None)
        response.set_cookie(
            BINDING_COOKIE,
            binding,
            max_age=STATE_TTL_SECONDS,
            **_binding_cookie_scope(request),
        )
        return begun

    @custom_route(
        method="POST",
        path="/callback",
        input_model=OAuthCallbackRequest,
        output_model=OAuthCallbackResponse,
        authentication_type="none",
        expose_in=(ExposeIn.REST, ExposeIn.SDK),
        openapi_tags=("OAuth Consumer",),
        summary="Finish signing in with an identity provider",
    )
    @rate_limit(DEFAULT_AUTH_RATE_LIMIT, scope="ip")
    async def callback_route(
        self, body: OAuthCallbackRequest, request: Request, response: Response
    ) -> OAuthCallbackResponse:
        """The session in the body for API clients and in the session
        cookies for the browser; the binding cookie is cleared."""
        configured, identity, tokens = await self.finish(
            body, request.cookies.get(BINDING_COOKIE), None
        )
        user_id, new_user = self.sign_in_user(configured, identity, tokens)
        signed_in = self.session_for(user_id, new_user)
        response.delete_cookie(BINDING_COOKIE, **_binding_cookie_scope(request))
        if signed_in.token is not None:
            issue_browser_session(response, signed_in.token)
        return signed_in

    @custom_route(
        method="POST",
        path="/link/authorize",
        input_model=OAuthAuthorizeRequest,
        output_model=OAuthAuthorizeResponse,
        authentication_type="jwt",
        expose_in=(ExposeIn.REST, ExposeIn.SDK),
        openapi_tags=("OAuth Consumer",),
        summary="Begin linking an identity provider to the signed-in user",
    )
    @rate_limit(DEFAULT_AUTH_RATE_LIMIT, scope="ip")
    async def link_authorize_route(
        self, body: OAuthAuthorizeRequest, request: Request, response: Response
    ) -> OAuthAuthorizeResponse:
        begun, binding = await self.begin(
            body.provider, body.redirect_uri, str(self.requester.id)
        )
        response.set_cookie(
            BINDING_COOKIE,
            binding,
            max_age=STATE_TTL_SECONDS,
            **_binding_cookie_scope(request),
        )
        return begun

    @custom_route(
        method="POST",
        path="/link/callback",
        input_model=OAuthCallbackRequest,
        output_model=OAuthIdentityView,
        authentication_type="jwt",
        expose_in=(ExposeIn.REST, ExposeIn.SDK),
        openapi_tags=("OAuth Consumer",),
        summary="Finish linking an identity provider to the signed-in user",
    )
    @rate_limit(DEFAULT_AUTH_RATE_LIMIT, scope="ip")
    async def link_callback_route(
        self, body: OAuthCallbackRequest, request: Request, response: Response
    ) -> OAuthIdentityView:
        """Only the user who began the link finishes it, in the browser
        that began it. Linking takes no email: the user is signed in."""
        user_id = str(self.requester.id)
        configured, identity, tokens = await self.finish(
            body, request.cookies.get(BINDING_COOKIE), user_id
        )
        linked = self.link_identity(configured, identity, tokens, user_id)
        response.delete_cookie(BINDING_COOKIE, **_binding_cookie_scope(request))
        return OAuthIdentityView.of(linked)


async def current_access_token(
    manager: OAuthIdentityManager, identity_id: str
) -> OAuthAccessToken:
    """A current access token for an identity the manager's requester can
    see: the stored one, refreshed with the refresh token once it lapsed."""
    identity: OAuthIdentityModel = manager.get(id=identity_id)
    configured = next(
        (
            c
            for c in configured_providers(manager.model_registry)
            if str(c.instance.id) == identity.provider_instance_id
        ),
        None,
    )
    if configured is None:
        raise HTTPException(
            status_code=409, detail="The identity's provider is no longer configured"
        )
    stored = decrypt_secret(identity.access_token) if identity.access_token else None
    expires_at = (
        ensure_utc(identity.token_expires_at) if identity.token_expires_at else None
    )
    if stored is not None and (expires_at is None or expires_at > _now()):
        return OAuthAccessToken(
            access_token=stored, expires_at=expires_at, scopes=identity.scopes
        )
    if not identity.refresh_token:
        raise HTTPException(
            status_code=409, detail="The access token lapsed and cannot be refreshed"
        )
    provider, instance = configured.provider, configured.instance
    endpoints = await upstream(provider.endpoints(instance))
    tokens = await upstream(
        provider.refresh(
            instance, endpoints, str(decrypt_secret(identity.refresh_token))
        )
    )
    fields: Dict[str, Any] = {
        "access_token": encrypt_secret(tokens.access_token),
        "token_expires_at": tokens.expires_at,
        "scopes": tokens.scope or identity.scopes,
    }
    if tokens.refresh_token is not None:
        fields["refresh_token"] = encrypt_secret(tokens.refresh_token)
    OAuthIdentityManager(
        requester_id=env("ROOT_ID"), model_registry=manager.model_registry
    ).update(id=identity_id, **fields)
    return OAuthAccessToken(
        access_token=tokens.access_token,
        expires_at=tokens.expires_at,
        scopes=fields["scopes"],
    )


# ---------------------------------------------------------------------------
# Grant validator + registration
# ---------------------------------------------------------------------------


def assert_may_sign_in(user: UserModel) -> None:
    """An internal account (ROOT, SYSTEM, the template user), or an inactive
    or deleted one, signs in by no provider."""
    refuse_internal_account(user.id)
    if user.active is False or getattr(user, "deleted_at", None) is not None:
        raise HTTPException(status_code=403, detail="User account is disabled")


def oauth_consumer_grant_validator(payload: UserIdGrantPayload) -> UserModel:
    """The user an ``oauth_consumer`` grant signs in, while it may."""
    if payload.model_registry is None:
        raise InvalidGrantError(
            detail="oauth_consumer grant payload missing model_registry"
        )
    UserDB = UserModel.DB(payload.model_registry.DB.manager.Base)
    user: Optional[UserModel] = UserDB.get(
        requester_id=env("ROOT_ID"),
        model_registry=payload.model_registry,
        id=payload.user_id,
        return_type="dto",
        override_dto=UserModel,
    )
    if user is None:
        raise InvalidGrantError(detail="oauth_consumer user no longer exists")
    assert_may_sign_in(user)
    return user


PasswordlessGrantRegistry.register(GRANT_TYPE, oauth_consumer_grant_validator)


# ---------------------------------------------------------------------------
# Merge participation
# ---------------------------------------------------------------------------


def _merge_handler(ctx: Any) -> None:
    """Move the merged-away user's identities to the surviving user. An
    identity both hold (the same provider account) stays the survivor's;
    the other copy is removed."""
    root_id = env("ROOT_ID")
    IdentityDB = OAuthIdentityModel.DB(ctx.model_registry.DB.manager.Base)

    def identities_of(user_id: str) -> List[OAuthIdentityModel]:
        rows: List[OAuthIdentityModel] = IdentityDB.list(
            requester_id=root_id,
            model_registry=ctx.model_registry,
            filters=[IdentityDB.user_id == user_id, IdentityDB.deleted_at.is_(None)],
            return_type="dto",
            override_dto=OAuthIdentityModel,
        )
        return rows or []

    kept = {
        (row.provider_instance_id, row.subject)
        for row in identities_of(ctx.initiating_user_id)
    }
    for row in identities_of(ctx.target_user_id):
        if (row.provider_instance_id, row.subject) in kept:
            IdentityDB.delete(
                requester_id=root_id, model_registry=ctx.model_registry, id=row.id
            )
        else:
            IdentityDB.update(
                requester_id=root_id,
                model_registry=ctx.model_registry,
                id=row.id,
                new_properties={"user_id": ctx.initiating_user_id},
            )


def register_merge_participation() -> None:
    """Join account merges when the auth_merge extension is installed."""
    try:
        from zephyrex.extensions.auth_merge.BLL_Auth_Merge import make_merge_registrar
    except ImportError:
        return
    make_merge_registrar("oauth_consumer", _merge_handler)()
