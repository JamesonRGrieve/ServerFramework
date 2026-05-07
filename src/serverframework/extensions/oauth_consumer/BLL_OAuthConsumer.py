"""OAuth consumer BLL: link a local user to an external IdP identity.

This is the *consumer* side of OAuth ("login with Google/GitHub/...").
A successful upstream auth code exchange resolves to a normalized profile;
that profile is matched to a local user by ``provider_name + provider_user_id``
(creating a link record on first login). The grant is then dispatched through
``UserManager.login_via_grant("oauth_consumer", ...)`` so the issued session
carries ``grant_type="oauth_consumer"`` like every other passwordless grant.

CSRF: ``begin_authorize`` issues a cryptographic state value and records it
in the shared :class:`ReplayCache`. ``complete_callback`` insists on a
matching, single-use state. Forged callbacks (which is the canonical OAuth
CSRF) cannot reach the session-issuance path.

Token storage: ``access_token`` and ``refresh_token`` flow through
:func:`encrypt_secret` so they are persisted as Fernet ciphertext. The
shared helper raises if the deployment did not configure the key (and is
not in an opt-in plaintext-fallback environment), making it impossible to
silently store tokens in the clear.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, ClassVar, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from serverframework.lib.CustomRoute import custom_route
from serverframework.lib.Environment import env
from serverframework.lib.InboundSecurity import DEFAULT_AUTH_RATE_LIMIT, rate_limit
from serverframework.lib.Pydantic2FastAPI import AuthType, RouterMixin
from serverframework.lib.ReplayCache import get_replay_cache
from serverframework.lib.SecretEncryption import (
    decrypt_secret,
    encrypt_secret,
)
from serverframework.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    DateSearchModel,
    ModelMeta,
    StringSearchModel,
    UpdateMixinModel,
)
from serverframework.logic.BLL_Auth import (
    InvalidGrantError,
    PasswordlessGrantRegistry,
    UserManager,
    UserModel,
)

from serverframework.extensions.oauth_consumer.IdPRegistry import get_idp


# State TTL: window between /authorize and /callback. Long enough for the
# user to authenticate at the IdP, short enough to bound an attacker's
# replay window.
_STATE_TTL_SECONDS = 600
_STATE_KEY_PREFIX = "oauth-consumer-state:"


# ---------------------------------------------------------------------------
# Database model
# ---------------------------------------------------------------------------


class UserOAuthLinkModel(
    ApplicationModel,
    UpdateMixinModel,
    metaclass=ModelMeta,
):
    """Persistent link between a local user and an external IdP identity."""

    user_id: str = Field(..., description="Local user this link belongs to")
    provider_name: str = Field(
        ..., description="IdP name (e.g. 'google', 'github', 'microsoft', 'amazon')"
    )
    provider_user_id: str = Field(
        ..., description="User's stable identifier at the IdP"
    )
    account_email: Optional[str] = Field(
        None, description="Email reported by the IdP at link time"
    )
    display_name: Optional[str] = Field(
        None, description="Display name reported by the IdP at link time"
    )
    access_token: Optional[str] = Field(
        None,
        description=(
            "Latest access token from the IdP. SHOULD be encrypted at rest; "
            "see module docstring."
        ),
    )
    refresh_token: Optional[str] = Field(
        None, description="Refresh token from the IdP, if issued."
    )
    token_expires_at: Optional[datetime] = Field(
        None, description="When access_token expires"
    )
    scopes: Optional[str] = Field(
        None, description="Space-delimited scopes granted at link time"
    )
    is_primary: bool = Field(
        False, description="Whether this is the user's preferred OAuth link"
    )

    table_comment: ClassVar[str] = (
        "Links a local user to an identity at an external OAuth IdP"
    )

    class Create(BaseModel):
        user_id: str
        provider_name: str
        provider_user_id: str
        account_email: Optional[str] = None
        display_name: Optional[str] = None
        access_token: Optional[str] = None
        refresh_token: Optional[str] = None
        token_expires_at: Optional[datetime] = None
        scopes: Optional[str] = None
        is_primary: bool = False

    class Update(BaseModel):
        access_token: Optional[str] = None
        refresh_token: Optional[str] = None
        token_expires_at: Optional[datetime] = None
        scopes: Optional[str] = None
        is_primary: Optional[bool] = None
        account_email: Optional[str] = None
        display_name: Optional[str] = None

    class Search(ApplicationModel.Search, UpdateMixinModel.Search):
        user_id: Optional[StringSearchModel] = None
        provider_name: Optional[StringSearchModel] = None
        provider_user_id: Optional[StringSearchModel] = None
        account_email: Optional[StringSearchModel] = None
        token_expires_at: Optional[DateSearchModel] = None
        is_primary: Optional[bool] = None


# ---------------------------------------------------------------------------
# Public request/response shapes
# ---------------------------------------------------------------------------


class OAuthAuthorizeRequest(BaseModel):
    provider: str = Field(..., description="IdP name registered in IdPRegistry")
    redirect_uri: Optional[str] = Field(
        None, description="Redirect URI; defaults to OAUTH_CONSUMER_REDIRECT_URI"
    )


class OAuthAuthorizeResponse(BaseModel):
    authorize_url: str
    state: str


class OAuthCallbackRequest(BaseModel):
    provider: str
    code: str = Field(..., description="Authorization code from the IdP callback")
    state: str = Field(
        ..., description="State value originally returned by /authorize"
    )
    redirect_uri: Optional[str] = Field(
        None, description="Must match the URI provided to /authorize"
    )


class OAuthCallbackResponse(BaseModel):
    session_key: str
    user_id: str
    grant_type: str
    expires_at: datetime


class OAuthConsumerGrantPayload(BaseModel):
    """Payload passed to the ``oauth_consumer`` grant validator."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    user_id: str
    provider_name: str
    provider_user_id: str
    model_registry: Any = None


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class OAuthConsumerManager(AbstractBLLManager, RouterMixin):
    _model = UserOAuthLinkModel

    prefix: ClassVar[Optional[str]] = "/v1/auth/oauth"
    tags: ClassVar[Optional[List[str]]] = ["OAuth Consumer"]
    auth_type: ClassVar[AuthType] = AuthType.NONE
    routes_to_register: ClassVar[Optional[List]] = []

    @staticmethod
    def _default_redirect_uri() -> str:
        return env("OAUTH_CONSUMER_REDIRECT_URI") or env("MAGIC_LINK_BASE_URL") or ""

    def _resolve_user_by_email(self, email: str) -> Optional[UserModel]:
        UserDB = UserModel.DB(self.model_registry.DB.manager.Base)
        users = UserDB.list(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            filters=[
                UserDB.email == email,
                UserDB.deleted_at.is_(None),
            ],
            return_type="dto",
            override_dto=UserModel,
        )
        return users[0] if users else None

    def _find_link(
        self, provider_name: str, provider_user_id: str
    ) -> Optional[UserOAuthLinkModel]:
        LinkDB = UserOAuthLinkModel.DB(self.model_registry.DB.manager.Base)
        rows = LinkDB.list(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            filters=[
                LinkDB.provider_name == provider_name,
                LinkDB.provider_user_id == provider_user_id,
            ],
            return_type="dto",
            override_dto=UserOAuthLinkModel,
        )
        return rows[0] if rows else None

    async def begin_authorize(
        self, provider: str, redirect_uri: Optional[str] = None
    ) -> OAuthAuthorizeResponse:
        """Build the upstream authorization URL the client should redirect to.

        Issues a one-time CSRF state, records it in the replay cache, and
        embeds it in the IdP's authorize URL. ``complete_callback`` will
        refuse any callback that does not present this state.
        """
        import secrets

        idp_class = get_idp(provider)
        idp = idp_class()
        state = secrets.token_urlsafe(32)
        target_redirect = redirect_uri or self._default_redirect_uri()

        # Bind state to (provider, redirect_uri) so an attacker who somehow
        # observes the state cannot reuse it on a different IdP or to redirect
        # to a different URI.
        state_payload = f"{provider}|{target_redirect}|{state}"
        get_replay_cache().mark_used(
            _STATE_KEY_PREFIX + state_payload, ttl_seconds=_STATE_TTL_SECONDS
        )

        params = {
            "client_id": idp.client_id,
            "redirect_uri": target_redirect,
            "response_type": "code",
            "scope": idp.scopes or "",
            "state": state,
        }
        from urllib.parse import urlencode

        # AUTHORIZE_URL may be a class attribute (str) or a property; resolve
        # via attribute access on the instance to honour both shapes.
        idp_auth_url = getattr(idp, "AUTHORIZE_URL", None)
        if idp_auth_url is None:
            raise InvalidGrantError(
                detail=f"IdP {provider!r} does not expose an AUTHORIZE_URL"
            )
        authorize_url = str(idp_auth_url) + "?" + urlencode(params)
        return OAuthAuthorizeResponse(authorize_url=authorize_url, state=state)

    def _verify_state(
        self, provider: str, state: Optional[str], redirect_uri: str
    ) -> None:
        """Refuse a callback that does not present a valid, single-use state.

        ``ReplayCache`` semantics are mark-then-check; we use the cache as a
        TTL set: if the state is *not* already marked, the callback is
        invalid. That decouples the check from "have we seen this code".
        """
        if not state:
            raise InvalidGrantError(detail="Missing CSRF state")
        cache = get_replay_cache()
        key = _STATE_KEY_PREFIX + f"{provider}|{redirect_uri}|{state}"
        if not cache.is_used(key):
            raise InvalidGrantError(detail="Invalid or expired OAuth state")
        # Burn the state by re-marking with TTL=0; cache will return True
        # for the brief follow-up window but a deliberate "consume" key
        # prevents reuse without modifying the cache contract.
        cache.mark_used(key + ":consumed", ttl_seconds=_STATE_TTL_SECONDS)
        if cache.is_used(key + ":consumed-twice"):
            raise InvalidGrantError(detail="OAuth state already consumed")
        cache.mark_used(key + ":consumed-twice", ttl_seconds=_STATE_TTL_SECONDS)

    async def complete_callback(
        self,
        provider: str,
        code: str,
        redirect_uri: Optional[str] = None,
        state: Optional[str] = None,
    ) -> OAuthCallbackResponse:
        """Exchange ``code`` for tokens, resolve a local user, and issue a session."""
        target_redirect = redirect_uri or self._default_redirect_uri()
        self._verify_state(provider, state, target_redirect)

        idp_class = get_idp(provider)
        idp = await idp_class.sso_handler(code, target_redirect)
        if idp is None or not idp.access_token:
            raise InvalidGrantError(detail="OAuth code exchange failed")

        profile = await idp.get_user_info(idp.access_token)
        provider_user_id = profile.get("provider_user_id") or profile.get("email")
        if not provider_user_id:
            raise InvalidGrantError(detail="IdP did not return a stable user identifier")

        # Encrypt tokens before they touch the DB. The helper raises if the
        # deployment is not configured for encryption-at-rest, which is
        # exactly what we want — tokens never go to disk in plaintext.
        encrypted_access = encrypt_secret(idp.access_token)
        encrypted_refresh = encrypt_secret(idp.refresh_token) if idp.refresh_token else None

        link = self._find_link(provider, provider_user_id)
        now = datetime.now(timezone.utc)

        if link is not None:
            user_id = link.user_id
            LinkDB = UserOAuthLinkModel.DB(self.model_registry.DB.manager.Base)
            LinkDB.update(
                requester_id=user_id,
                model_registry=self.model_registry,
                id=link.id,
                new_properties={
                    "access_token": encrypted_access,
                    "refresh_token": encrypted_refresh,
                    "account_email": profile.get("email"),
                    "display_name": profile.get("display_name"),
                    "scopes": idp.scopes,
                },
            )
        else:
            # No link yet — match by email if a user exists; otherwise the
            # framework signup path is expected to create the user via a
            # separate registration flow. We only auto-link existing accounts.
            email = profile.get("email")
            existing = self._resolve_user_by_email(email) if email else None
            if existing is None:
                raise InvalidGrantError(
                    detail=(
                        "No local user matches this IdP identity. Sign up first, "
                        "then link this OAuth provider."
                    )
                )
            user_id = existing.id
            LinkDB = UserOAuthLinkModel.DB(self.model_registry.DB.manager.Base)
            LinkDB.create(
                requester_id=user_id,
                model_registry=self.model_registry,
                user_id=user_id,
                provider_name=provider,
                provider_user_id=str(provider_user_id),
                account_email=email,
                display_name=profile.get("display_name"),
                access_token=encrypted_access,
                refresh_token=encrypted_refresh,
                scopes=idp.scopes,
                is_primary=False,
            )

        session = UserManager.login_via_grant(
            grant_type="oauth_consumer",
            grant_payload=OAuthConsumerGrantPayload(
                user_id=user_id,
                provider_name=provider,
                provider_user_id=str(provider_user_id),
                model_registry=self.model_registry,
            ),
            model_registry=self.model_registry,
        )

        return OAuthCallbackResponse(
            session_key=session.session_key,
            user_id=session.user_id,
            grant_type=session.grant_type or "oauth_consumer",
            expires_at=session.expires_at,
        )

    @custom_route(
        method="POST",
        path="/authorize",
        input_model=OAuthAuthorizeRequest,
        output_model=OAuthAuthorizeResponse,
        authentication_type="none",
        openapi_tags=("OAuth Consumer",),
        summary="Begin an OAuth consumer flow against an external IdP",
    )
    @rate_limit(DEFAULT_AUTH_RATE_LIMIT, scope="ip")
    async def authorize_route(
        self, body: OAuthAuthorizeRequest
    ) -> OAuthAuthorizeResponse:
        return await self.begin_authorize(
            provider=body.provider, redirect_uri=body.redirect_uri
        )

    @custom_route(
        method="POST",
        path="/callback",
        input_model=OAuthCallbackRequest,
        output_model=OAuthCallbackResponse,
        authentication_type="none",
        openapi_tags=("OAuth Consumer",),
        summary="Complete an OAuth consumer flow and issue a session",
    )
    @rate_limit(DEFAULT_AUTH_RATE_LIMIT, scope="ip")
    async def callback_route(
        self, body: OAuthCallbackRequest
    ) -> OAuthCallbackResponse:
        return await self.complete_callback(
            provider=body.provider,
            code=body.code,
            redirect_uri=body.redirect_uri,
            state=body.state,
        )


# ---------------------------------------------------------------------------
# Grant validator + registration
# ---------------------------------------------------------------------------


def oauth_consumer_grant_validator(
    payload: OAuthConsumerGrantPayload,
) -> UserModel:
    if payload.model_registry is None:
        raise InvalidGrantError(
            detail="oauth_consumer grant payload missing model_registry"
        )
    UserDB = UserModel.DB(payload.model_registry.DB.manager.Base)
    user = UserDB.get(
        requester_id=env("ROOT_ID"),
        model_registry=payload.model_registry,
        id=payload.user_id,
        return_type="dto",
        override_dto=UserModel,
    )
    if user is None:
        raise InvalidGrantError(detail="oauth_consumer user no longer exists")
    return user


PasswordlessGrantRegistry.register("oauth_consumer", oauth_consumer_grant_validator)
