"""OAuth2 provider BLL: this server *is* an OAuth2 issuer.

Third-party applications register a client (``OAuth2ClientModel``), redirect
users here for consent, receive a short-lived ``OAuth2AuthCodeModel`` at the
client's redirect URI, and exchange it for an ``OAuth2TokenModel``.

Storage hardening:
- ``OAuth2ClientModel.client_secret_hash`` stores a hashed client secret;
  the raw secret is returned exactly once at creation. The hash and salt
  are NOT writable through the CRUD ``Create`` body — registration must
  flow through ``register_client`` so the server controls secret entropy.
- ``OAuth2AuthCodeModel`` extends ``OneTimeTokenMixin``: codes are stored
  hashed-at-rest, fingerprinted for indexed lookup, and marked ``is_used``
  on first redemption. PKCE (RFC 7636) is enforced via ``code_challenge``
  / ``code_challenge_method`` on the consent request and verified at
  redemption.
- ``OAuth2TokenModel.token_hash`` + ``token_fingerprint`` store opaque
  bearer tokens. Validation is constant-time after a fingerprint hit.

Scopes: this extension consumes the canonical ``PermissionRegistry`` (every
permission is also a valid OAuth scope per the framework contract) instead of
maintaining a parallel scope catalog.

Endpoint surface:
- ``OAuth2ClientManager``: GET/LIST/SEARCH/DELETE via CRUD; POST goes
  through ``/v1/oauth2/clients/register`` (custom route) so secrets are
  generated server-side.
- ``OAuth2AuthCodeManager`` and ``OAuth2TokenManager``: no CRUD surface;
  ``/v1/oauth2/authorize``, ``/token``, ``/introspect``, ``/revoke`` are
  exposed via custom routes.
"""

import base64
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timezone
from typing import ClassVar, List, Optional, Set

from fastapi import HTTPException
from pydantic import BaseModel, Field

from serverframework.lib.CustomRoute import custom_route
from serverframework.lib.Environment import env
from serverframework.lib.InboundSecurity import DEFAULT_AUTH_RATE_LIMIT, rate_limit
from serverframework.lib.Pydantic2FastAPI import AuthType, RouteType, RouterMixin
from serverframework.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    DateSearchModel,
    ModelMeta,
    StringSearchModel,
    UpdateMixinModel,
)
from serverframework.logic.BLL_Auth import (
    OneTimeTokenMixin,
    UserModel,
)
from serverframework.logic.Permissions import PermissionRegistry, has_permission


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hash_secret(raw: str, salt: str) -> str:
    return hashlib.sha256((salt + raw).encode("utf-8")).hexdigest()


def _token_fingerprint(raw: str) -> str:
    """Short HMAC of the raw token for indexed lookup. The HMAC key is the
    framework's ROOT_ID (process-stable but not user-derivable from outside)."""
    key = (env("ROOT_ID") or "oauth-provider").encode("utf-8")
    return hmac.new(key, raw.encode("utf-8"), hashlib.sha256).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class OAuth2ClientModel(
    ApplicationModel,
    UpdateMixinModel,
    metaclass=ModelMeta,
):
    """A third-party application registered with this OAuth2 issuer."""

    name: str = Field(..., description="Friendly client name")
    client_id: str = Field(..., description="Public client identifier")
    client_secret_hash: str = Field(
        ..., description="Hashed client secret (raw secret is returned once at creation)"
    )
    client_secret_salt: str = Field(..., description="Per-client salt for secret hash")
    owner_user_id: Optional[str] = Field(
        None, description="User who registered this client"
    )
    redirect_uris: str = Field(
        ..., description="JSON array of allowed redirect URIs"
    )
    allowed_scopes: str = Field(
        "", description="Space-delimited scopes the client may request"
    )
    is_confidential: bool = Field(
        True, description="Whether the client can keep its secret confidential"
    )
    is_enabled: bool = Field(True)

    table_comment: ClassVar[str] = "OAuth2 third-party client registrations"

    # ``client_id``, ``client_secret_hash``, ``client_secret_salt`` and
    # ``owner_user_id`` are server-controlled — clients submit only the
    # human-meaningful fields and the server fills in the rest. Direct
    # CRUD POST is blocked by ``OAuth2ClientManager.create_validation``.
    class Create(BaseModel):
        name: str
        redirect_uris: str = Field(..., description="JSON array of allowed redirect URIs")
        allowed_scopes: str = Field("", description="Space-delimited scopes")
        is_confidential: bool = True

    class Update(BaseModel):
        name: Optional[str] = None
        redirect_uris: Optional[str] = None
        allowed_scopes: Optional[str] = None
        is_enabled: Optional[bool] = None

    class Search(ApplicationModel.Search, UpdateMixinModel.Search):
        client_id: Optional[StringSearchModel] = None
        owner_user_id: Optional[StringSearchModel] = None
        is_enabled: Optional[bool] = None


class OAuth2AuthCodeModel(
    ApplicationModel,
    UpdateMixinModel,
    OneTimeTokenMixin,
    metaclass=ModelMeta,
):
    """Short-lived authorization code, hashed at rest, single-use.

    PKCE (RFC 7636): every authorization request must supply a
    ``code_challenge`` and ``code_challenge_method``. Public clients are
    rejected without one; confidential clients can opt out via
    ``OAUTH_PROVIDER_REQUIRE_PKCE_FOR_CONFIDENTIAL=false``.
    """

    user_id: str = Field(..., description="User who granted consent")
    client_id: str = Field(..., description="Client this code was issued for")
    redirect_uri: str = Field(..., description="Redirect URI bound to this code")
    scopes: str = Field(..., description="Space-delimited scopes the user consented to")
    code_challenge: Optional[str] = Field(
        None, description="PKCE code challenge bound to this code (RFC 7636)"
    )
    code_challenge_method: Optional[str] = Field(
        None, description="PKCE method: 'S256' (preferred) or 'plain'"
    )

    table_comment: ClassVar[str] = (
        "OAuth2 authorization codes; hashed at rest, single-use, short TTL"
    )

    # Direct CRUD CREATE/UPDATE is blocked at the manager (no public
    # routes); these schemas exist only to satisfy ``ApplicationModel``'s
    # ``ModelMeta`` requirement.
    class Create(BaseModel):
        user_id: str
        client_id: str
        redirect_uri: str
        scopes: str

    class Update(BaseModel):
        pass

    class Search(ApplicationModel.Search, UpdateMixinModel.Search):
        user_id: Optional[StringSearchModel] = None
        client_id: Optional[StringSearchModel] = None
        is_used: Optional[bool] = None
        expires_at: Optional[DateSearchModel] = None


class OAuth2TokenModel(
    ApplicationModel,
    UpdateMixinModel,
    metaclass=ModelMeta,
):
    """Access or refresh token issued by this server. Hashed at rest."""

    user_id: str = Field(..., description="User the token authorizes access for")
    client_id: str = Field(..., description="Client that holds the token")
    token_type: str = Field(..., description="'access' or 'refresh'")
    token_hash: str = Field(..., description="SHA-256 hash of (salt+raw token)")
    token_salt: str = Field(..., description="Per-token salt")
    token_fingerprint: str = Field(
        ..., description="HMAC fingerprint for indexed lookup"
    )
    scopes: str = Field(..., description="Space-delimited scopes this token grants")
    expires_at: datetime = Field(..., description="Token expiry")
    is_revoked: bool = Field(False, description="Soft-revoke flag")

    table_comment: ClassVar[str] = (
        "OAuth2 access and refresh tokens; hashed at rest"
    )

    # No public CRUD; tokens are minted by ``issue_token_pair`` and
    # revoked by ``/revoke``. These schemas exist for ``ModelMeta`` only.
    class Create(BaseModel):
        user_id: str
        client_id: str
        token_type: str

    class Update(BaseModel):
        pass

    class Search(ApplicationModel.Search, UpdateMixinModel.Search):
        user_id: Optional[StringSearchModel] = None
        client_id: Optional[StringSearchModel] = None
        token_type: Optional[StringSearchModel] = None
        token_fingerprint: Optional[StringSearchModel] = None
        is_revoked: Optional[bool] = None
        expires_at: Optional[DateSearchModel] = None


# ---------------------------------------------------------------------------
# Public response/request shapes
# ---------------------------------------------------------------------------


class OAuth2ClientCreateRequest(BaseModel):
    name: str
    redirect_uris: List[str]
    allowed_scopes: List[str] = Field(default_factory=list)
    is_confidential: bool = True


class OAuth2ClientCreateResponse(BaseModel):
    """Returned exactly once at creation. ``client_secret`` is never returned again."""

    client_id: str
    client_secret: str
    name: str
    redirect_uris: List[str]
    allowed_scopes: List[str]


class OAuth2TokenIssueResponse(BaseModel):
    access_token: str
    refresh_token: Optional[str]
    token_type: str = "Bearer"
    expires_in: int
    scope: str


class OAuth2TokenIntrospection(BaseModel):
    active: bool
    user_id: Optional[str] = None
    client_id: Optional[str] = None
    scope: Optional[str] = None
    expires_at: Optional[datetime] = None


class OAuth2AuthorizeRequest(BaseModel):
    """Body of POST /v1/oauth2/authorize.

    The user has already authenticated to the framework (JWT). The
    request asks the provider to mint an authorization code bound to
    ``client_id`` + ``redirect_uri`` + ``scopes``. PKCE is required: the
    client supplies the ``code_challenge`` it will later prove
    ownership of with ``code_verifier`` at the token exchange.

    Consent: ``consent_grant`` must be ``True``. The two-step flow is
    GET ``/preview`` (renders the consent screen) → POST ``/authorize``
    (which carries ``consent_grant=True``). Any UI/server that issues
    the POST without first showing the user what's being granted is in
    violation of the contract; the explicit boolean makes the consent
    decision auditable on the wire.
    """

    client_id: str
    redirect_uri: str
    scopes: List[str] = Field(default_factory=list)
    code_challenge: Optional[str] = Field(
        None, description="PKCE challenge (RFC 7636); required for public clients"
    )
    code_challenge_method: Optional[str] = Field(
        "S256", description="'S256' (preferred) or 'plain'"
    )
    consent_grant: bool = Field(
        False,
        description=(
            "Explicit user consent flag; must be True or the request is "
            "rejected with 400. Defaults to False so an accidental empty "
            "POST cannot mint a code."
        ),
    )


class OAuth2AuthorizePreviewRequest(BaseModel):
    client_id: str
    redirect_uri: str
    scopes: List[str] = Field(default_factory=list)


class OAuth2AuthorizePreviewResponse(BaseModel):
    """Information a UI needs to render a consent screen."""

    client_id: str
    client_name: str
    is_confidential: bool
    redirect_uri: str
    scopes: List[str]
    scopes_unknown: List[str]
    scopes_exceeds_client_allow_list: List[str]
    redirect_uri_registered: bool


class OAuth2AuthorizeResponse(BaseModel):
    code: str
    redirect_uri: str
    expires_in: int


class OAuth2TokenRequest(BaseModel):
    """Body of POST /v1/oauth2/token.

    The grant_type 'authorization_code' is the only flow supported.
    Confidential clients pass ``client_secret``; public clients omit it
    and authenticate solely via PKCE ``code_verifier``.
    """

    grant_type: str = Field("authorization_code")
    code: str
    redirect_uri: str
    client_id: str
    client_secret: Optional[str] = None
    code_verifier: Optional[str] = Field(
        None, description="PKCE verifier matching the original challenge"
    )


class OAuth2IntrospectRequest(BaseModel):
    """RFC 7662 §2.1: introspection MUST be authenticated. We require
    client credentials (the same client that holds the token) to
    prevent token-scanning attacks."""

    token: str
    client_id: str
    client_secret: Optional[str] = None


class OAuth2RevokeRequest(BaseModel):
    token: str
    client_id: str
    client_secret: Optional[str] = None


class OAuth2RevokeResponse(BaseModel):
    """RFC 7009: revocation always returns 200, regardless of whether the
    token existed. The body is empty by spec; we surface a single boolean
    so the typed contract has a non-empty schema."""

    revoked: bool = True


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class OAuth2ClientManager(AbstractBLLManager, RouterMixin):
    _model = OAuth2ClientModel

    prefix: ClassVar[Optional[str]] = "/v1/oauth2/clients"
    tags: ClassVar[Optional[List[str]]] = ["OAuth2 Provider"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    # No CRUD CREATE/UPDATE: secrets are server-generated. Operators may
    # GET/LIST/SEARCH their own client registrations and DELETE to retire one.
    routes_to_register: ClassVar[Optional[List[RouteType]]] = [
        RouteType.GET,
        RouteType.LIST,
        RouteType.SEARCH,
        RouteType.DELETE,
    ]

    def create_validation(self, entity) -> None:
        """Refuse direct CRUD CREATE — registration must flow through
        ``register_client`` so the server controls the secret material."""
        from serverframework.database.StaticPermissions import is_root_id

        if not is_root_id(self.requester.id):
            raise HTTPException(
                status_code=403,
                detail="Direct CREATE on /v1/oauth2/clients is not permitted; "
                "use POST /v1/oauth2/clients/register",
            )

    def register_client(
        self, request: OAuth2ClientCreateRequest, owner_user: UserModel
    ) -> OAuth2ClientCreateResponse:
        client_id = "cli_" + secrets.token_urlsafe(16)
        raw_secret = secrets.token_urlsafe(48)
        salt = secrets.token_hex(16)
        secret_hash = _hash_secret(raw_secret, salt)

        # Validate every requested scope is in the canonical PermissionRegistry.
        registry = PermissionRegistry()
        for scope in request.allowed_scopes:
            if registry.get(scope) is None:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unknown scope (must match a permission name): {scope}",
                )

        ClientDB = OAuth2ClientModel.DB(self.model_registry.DB.manager.Base)
        ClientDB.create(
            requester_id=owner_user.id,
            model_registry=self.model_registry,
            name=request.name,
            client_id=client_id,
            client_secret_hash=secret_hash,
            client_secret_salt=salt,
            owner_user_id=owner_user.id,
            redirect_uris=json.dumps(request.redirect_uris),
            allowed_scopes=" ".join(request.allowed_scopes),
            is_confidential=request.is_confidential,
            is_enabled=True,
        )
        return OAuth2ClientCreateResponse(
            client_id=client_id,
            client_secret=raw_secret,
            name=request.name,
            redirect_uris=request.redirect_uris,
            allowed_scopes=request.allowed_scopes,
        )

    @custom_route(
        method="POST",
        path="/register",
        input_model=OAuth2ClientCreateRequest,
        output_model=OAuth2ClientCreateResponse,
        authentication_type="jwt",
        openapi_tags=("OAuth2 Provider",),
        summary="Register a new OAuth2 client (raw secret returned exactly once)",
    )
    @rate_limit(DEFAULT_AUTH_RATE_LIMIT, scope="ip")
    async def register_route(
        self, body: OAuth2ClientCreateRequest
    ) -> OAuth2ClientCreateResponse:
        return self.register_client(body, self.requester)

    def authenticate_client(
        self, client_id: str, client_secret: Optional[str]
    ) -> OAuth2ClientModel:
        ClientDB = OAuth2ClientModel.DB(self.model_registry.DB.manager.Base)
        rows = ClientDB.list(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            filters=[
                ClientDB.client_id == client_id,
                ClientDB.is_enabled == True,  # noqa: E712
            ],
            return_type="dto",
            override_dto=OAuth2ClientModel,
        )
        if not rows:
            raise HTTPException(status_code=401, detail="Invalid client")
        client = rows[0]
        if not client.is_confidential:
            # Public clients have no secret to verify; PKCE is the binding.
            if client_secret:
                # Reject silently: a public client should never be sent a
                # secret. We don't differentiate from "wrong secret" so a
                # leaked secret-check oracle can't infer client mode.
                raise HTTPException(status_code=401, detail="Invalid client credentials")
            return client
        if not client_secret:
            raise HTTPException(status_code=401, detail="Invalid client credentials")
        expected = _hash_secret(client_secret, client.client_secret_salt)
        # Constant-time comparison to defeat timing attacks.
        if not hmac.compare_digest(expected, client.client_secret_hash):
            raise HTTPException(status_code=401, detail="Invalid client credentials")
        return client


def _verify_pkce(
    code_verifier: Optional[str],
    code_challenge: Optional[str],
    method: Optional[str],
) -> bool:
    """Verify a PKCE proof against a stored challenge (RFC 7636)."""
    if not code_challenge:
        # No PKCE bound at issuance; the caller decides whether that is
        # acceptable based on client confidentiality.
        return code_verifier is None
    if not code_verifier:
        return False
    method = (method or "S256").upper()
    if method == "PLAIN":
        return hmac.compare_digest(code_verifier, code_challenge)
    if method == "S256":
        digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
        encoded = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        return hmac.compare_digest(encoded, code_challenge)
    return False


class OAuth2AuthCodeManager(AbstractBLLManager, RouterMixin):
    _model = OAuth2AuthCodeModel

    prefix: ClassVar[Optional[str]] = "/v1/oauth2/auth-codes"
    tags: ClassVar[Optional[List[str]]] = ["OAuth2 Provider"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    # No CRUD surface — auth codes are mint-and-redeem internal artifacts.
    routes_to_register: ClassVar[Optional[List[RouteType]]] = []

    def issue_code(
        self,
        user_id: str,
        client: OAuth2ClientModel,
        redirect_uri: str,
        requested_scopes: Set[str],
        code_challenge: Optional[str] = None,
        code_challenge_method: Optional[str] = None,
        ttl_minutes: int = 10,
    ) -> str:
        whitelist = json.loads(client.redirect_uris or "[]")
        if redirect_uri not in whitelist:
            raise HTTPException(status_code=400, detail="redirect_uri not registered")

        # Verify every requested scope is permitted for the client.
        client_scopes = set((client.allowed_scopes or "").split())
        if not requested_scopes.issubset(client_scopes):
            raise HTTPException(
                status_code=400,
                detail="Requested scopes exceed client allow-list",
            )

        # PKCE policy: public clients MUST present a challenge. Confidential
        # clients SHOULD; opt-out is gated on an explicit env flag so the
        # default posture is strict.
        require_pkce_for_confidential = (
            (env("OAUTH_PROVIDER_REQUIRE_PKCE_FOR_CONFIDENTIAL") or "true").lower()
            != "false"
        )
        if not client.is_confidential and not code_challenge:
            raise HTTPException(
                status_code=400,
                detail="PKCE code_challenge is required for public clients",
            )
        if (
            client.is_confidential
            and require_pkce_for_confidential
            and not code_challenge
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    "PKCE code_challenge is required (set "
                    "OAUTH_PROVIDER_REQUIRE_PKCE_FOR_CONFIDENTIAL=false to relax)"
                ),
            )
        method = (code_challenge_method or "S256").upper()
        if code_challenge and method not in ("S256", "PLAIN"):
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported code_challenge_method: {method}",
            )

        raw_code, token = OneTimeTokenMixin.generate(ttl_minutes=ttl_minutes)
        CodeDB = OAuth2AuthCodeModel.DB(self.model_registry.DB.manager.Base)
        CodeDB.create(
            requester_id=user_id,
            model_registry=self.model_registry,
            user_id=user_id,
            client_id=client.client_id,
            redirect_uri=redirect_uri,
            scopes=" ".join(sorted(requested_scopes)),
            code_hash=token.code_hash,
            code_salt=token.code_salt,
            code_fingerprint=token.code_fingerprint,
            code_challenge=code_challenge,
            code_challenge_method=method if code_challenge else None,
            expires_at=token.expires_at,
            is_used=False,
        )
        return raw_code

    def redeem_code(
        self,
        raw_code: str,
        client: OAuth2ClientModel,
        redirect_uri: str,
        code_verifier: Optional[str] = None,
    ) -> OAuth2AuthCodeModel:
        CodeDB = OAuth2AuthCodeModel.DB(self.model_registry.DB.manager.Base)
        fingerprint = OneTimeTokenMixin.fingerprint(raw_code)
        candidates = (
            CodeDB.list(
                requester_id=env("ROOT_ID"),
                model_registry=self.model_registry,
                filters=[
                    CodeDB.code_fingerprint == fingerprint,
                    CodeDB.is_used == False,  # noqa: E712
                    CodeDB.client_id == client.client_id,
                ],
                return_type="dto",
                override_dto=OAuth2AuthCodeModel,
            )
            or []
        )
        now = datetime.now(timezone.utc)
        for candidate in candidates:
            expires_at = candidate.expires_at
            if expires_at and expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at < now:
                continue
            if candidate.redirect_uri != redirect_uri:
                continue
            if not candidate.verify(raw_code):
                continue
            if not _verify_pkce(
                code_verifier,
                candidate.code_challenge,
                candidate.code_challenge_method,
            ):
                # Burn the code on PKCE mismatch so an attacker cannot
                # brute-force the verifier against a still-valid code.
                CodeDB.update(
                    requester_id=candidate.user_id,
                    model_registry=self.model_registry,
                    id=candidate.id,
                    new_properties={"is_used": True, "used_at": now},
                )
                raise HTTPException(status_code=400, detail="PKCE verification failed")
            CodeDB.update(
                requester_id=candidate.user_id,
                model_registry=self.model_registry,
                id=candidate.id,
                new_properties={"is_used": True, "used_at": now},
            )
            return candidate
        raise HTTPException(status_code=400, detail="Invalid or expired authorization code")


class OAuth2TokenManager(AbstractBLLManager, RouterMixin):
    _model = OAuth2TokenModel

    prefix: ClassVar[Optional[str]] = "/v1/oauth2/tokens"
    tags: ClassVar[Optional[List[str]]] = ["OAuth2 Provider"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    routes_to_register: ClassVar[Optional[List]] = []

    @staticmethod
    def _generate(token_type: str) -> tuple[str, str, str, str]:
        raw = secrets.token_urlsafe(48)
        salt = secrets.token_hex(16)
        token_hash = _hash_secret(raw, salt)
        fingerprint = _token_fingerprint(raw)
        return raw, token_hash, salt, fingerprint

    def issue_token_pair(
        self,
        code: OAuth2AuthCodeModel,
        access_ttl_minutes: int = 60,
        refresh_ttl_days: int = 30,
    ) -> OAuth2TokenIssueResponse:
        from datetime import timedelta

        raw_access, access_hash, access_salt, access_fp = self._generate("access")
        raw_refresh, refresh_hash, refresh_salt, refresh_fp = self._generate("refresh")
        TokenDB = OAuth2TokenModel.DB(self.model_registry.DB.manager.Base)

        now = datetime.now(timezone.utc)
        TokenDB.create(
            requester_id=code.user_id,
            model_registry=self.model_registry,
            user_id=code.user_id,
            client_id=code.client_id,
            token_type="access",
            token_hash=access_hash,
            token_salt=access_salt,
            token_fingerprint=access_fp,
            scopes=code.scopes,
            expires_at=now + timedelta(minutes=access_ttl_minutes),
            is_revoked=False,
        )
        TokenDB.create(
            requester_id=code.user_id,
            model_registry=self.model_registry,
            user_id=code.user_id,
            client_id=code.client_id,
            token_type="refresh",
            token_hash=refresh_hash,
            token_salt=refresh_salt,
            token_fingerprint=refresh_fp,
            scopes=code.scopes,
            expires_at=now + timedelta(days=refresh_ttl_days),
            is_revoked=False,
        )
        return OAuth2TokenIssueResponse(
            access_token=raw_access,
            refresh_token=raw_refresh,
            expires_in=access_ttl_minutes * 60,
            scope=code.scopes,
        )

    def introspect(self, raw_token: str) -> OAuth2TokenIntrospection:
        TokenDB = OAuth2TokenModel.DB(self.model_registry.DB.manager.Base)
        fingerprint = _token_fingerprint(raw_token)
        candidates = (
            TokenDB.list(
                requester_id=env("ROOT_ID"),
                model_registry=self.model_registry,
                filters=[
                    TokenDB.token_fingerprint == fingerprint,
                    TokenDB.is_revoked == False,  # noqa: E712
                ],
                return_type="dto",
                override_dto=OAuth2TokenModel,
            )
            or []
        )
        now = datetime.now(timezone.utc)
        for candidate in candidates:
            expires_at = candidate.expires_at
            if expires_at and expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at < now:
                continue
            expected = _hash_secret(raw_token, candidate.token_salt)
            if hmac.compare_digest(expected, candidate.token_hash):
                return OAuth2TokenIntrospection(
                    active=True,
                    user_id=candidate.user_id,
                    client_id=candidate.client_id,
                    scope=candidate.scopes,
                    expires_at=candidate.expires_at,
                )
        return OAuth2TokenIntrospection(active=False)

    def has_scope(self, raw_token: str, required_scope: str) -> bool:
        introspection = self.introspect(raw_token)
        if not introspection.active or not introspection.scope:
            return False
        token_scopes = set(introspection.scope.split())
        return has_permission(
            required_scope,
            role_grants=set(),
            token_scopes=token_scopes,
            registry=PermissionRegistry(),
        )

    def revoke_token(self, raw_token: str, client: OAuth2ClientModel) -> bool:
        """Revoke an access or refresh token. Idempotent: revoking an
        already-revoked or non-existent token returns silently per RFC 7009.
        """
        TokenDB = OAuth2TokenModel.DB(self.model_registry.DB.manager.Base)
        fingerprint = _token_fingerprint(raw_token)
        candidates = (
            TokenDB.list(
                requester_id=env("ROOT_ID"),
                model_registry=self.model_registry,
                filters=[
                    TokenDB.token_fingerprint == fingerprint,
                    TokenDB.client_id == client.client_id,
                ],
                return_type="dto",
                override_dto=OAuth2TokenModel,
            )
            or []
        )
        for candidate in candidates:
            expected = _hash_secret(raw_token, candidate.token_salt)
            if hmac.compare_digest(expected, candidate.token_hash):
                TokenDB.update(
                    requester_id=env("ROOT_ID"),
                    model_registry=self.model_registry,
                    id=candidate.id,
                    new_properties={"is_revoked": True},
                )
                return True
        return False


# ---------------------------------------------------------------------------
# Provider-level RPC endpoint
# ---------------------------------------------------------------------------


class OAuth2ProviderManager(AbstractBLLManager, RouterMixin):
    """Standalone manager hosting the user-facing OAuth2 endpoints.

    This is a non-CRUD action manager: ``/authorize`` is JWT-authenticated
    (the user has already signed in to the framework and is granting consent
    to a third-party client); ``/token``, ``/introspect``, and ``/revoke``
    authenticate via client credentials per RFC 6749 / 7009 / 7662.
    """

    _model = OAuth2ClientModel  # arbitrary; CRUD is suppressed below

    prefix: ClassVar[Optional[str]] = "/v1/oauth2"
    tags: ClassVar[Optional[List[str]]] = ["OAuth2 Provider"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    routes_to_register: ClassVar[Optional[List[RouteType]]] = []

    def _resolve_client(self, client_id: str) -> OAuth2ClientModel:
        ClientDB = OAuth2ClientModel.DB(self.model_registry.DB.manager.Base)
        rows = ClientDB.list(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            filters=[
                ClientDB.client_id == client_id,
                ClientDB.is_enabled == True,  # noqa: E712
            ],
            return_type="dto",
            override_dto=OAuth2ClientModel,
        )
        if not rows:
            raise HTTPException(status_code=400, detail="Unknown client")
        return rows[0]

    @custom_route(
        method="POST",
        path="/authorize/preview",
        input_model=OAuth2AuthorizePreviewRequest,
        output_model=OAuth2AuthorizePreviewResponse,
        authentication_type="jwt",
        openapi_tags=("OAuth2 Provider",),
        summary="Preview what /authorize would grant, for the consent UI",
    )
    @rate_limit(DEFAULT_AUTH_RATE_LIMIT, scope="ip")
    async def authorize_preview_route(
        self, body: OAuth2AuthorizePreviewRequest
    ) -> OAuth2AuthorizePreviewResponse:
        """Returns the data a consent screen needs: which client is
        asking, what scopes they're asking for, which of those scopes
        the client is actually permitted to request, and whether the
        redirect URI is registered. Mints no code and creates no row."""
        client = self._resolve_client(body.client_id)
        registry = PermissionRegistry()
        unknown = [s for s in body.scopes if registry.get(s) is None]
        client_allowed = set((client.allowed_scopes or "").split())
        exceeds = [s for s in body.scopes if s not in client_allowed]
        whitelist = json.loads(client.redirect_uris or "[]")
        return OAuth2AuthorizePreviewResponse(
            client_id=client.client_id,
            client_name=client.name,
            is_confidential=bool(client.is_confidential),
            redirect_uri=body.redirect_uri,
            scopes=list(body.scopes),
            scopes_unknown=unknown,
            scopes_exceeds_client_allow_list=exceeds,
            redirect_uri_registered=body.redirect_uri in whitelist,
        )

    @custom_route(
        method="POST",
        path="/authorize",
        input_model=OAuth2AuthorizeRequest,
        output_model=OAuth2AuthorizeResponse,
        authentication_type="jwt",
        openapi_tags=("OAuth2 Provider",),
        summary="Issue an authorization code for the authenticated user (consent required)",
    )
    @rate_limit(DEFAULT_AUTH_RATE_LIMIT, scope="ip")
    async def authorize_route(
        self, body: OAuth2AuthorizeRequest
    ) -> OAuth2AuthorizeResponse:
        if not body.consent_grant:
            raise HTTPException(
                status_code=400,
                detail=(
                    "consent_grant=true is required. Render the consent "
                    "screen via POST /v1/oauth2/authorize/preview, then "
                    "POST /authorize once the user explicitly approves."
                ),
            )
        client = self._resolve_client(body.client_id)
        code_mgr = OAuth2AuthCodeManager(
            requester_id=self.requester.id, model_registry=self.model_registry
        )
        ttl = int(env("OAUTH_PROVIDER_CODE_TTL_MINUTES") or 10)
        raw_code = code_mgr.issue_code(
            user_id=self.requester.id,
            client=client,
            redirect_uri=body.redirect_uri,
            requested_scopes=set(body.scopes),
            code_challenge=body.code_challenge,
            code_challenge_method=body.code_challenge_method,
            ttl_minutes=ttl,
        )
        return OAuth2AuthorizeResponse(
            code=raw_code, redirect_uri=body.redirect_uri, expires_in=ttl * 60
        )

    @custom_route(
        method="POST",
        path="/token",
        input_model=OAuth2TokenRequest,
        output_model=OAuth2TokenIssueResponse,
        authentication_type="none",
        openapi_tags=("OAuth2 Provider",),
        summary="Exchange an authorization code for tokens (RFC 6749 §4.1.3)",
    )
    @rate_limit(DEFAULT_AUTH_RATE_LIMIT, scope="ip")
    async def token_route(self, body: OAuth2TokenRequest) -> OAuth2TokenIssueResponse:
        if body.grant_type != "authorization_code":
            raise HTTPException(
                status_code=400, detail="Only authorization_code grant_type is supported"
            )
        client_mgr = OAuth2ClientManager(
            requester_id=env("ROOT_ID"), model_registry=self.model_registry
        )
        client = client_mgr.authenticate_client(body.client_id, body.client_secret)
        code_mgr = OAuth2AuthCodeManager(
            requester_id=env("ROOT_ID"), model_registry=self.model_registry
        )
        code = code_mgr.redeem_code(
            raw_code=body.code,
            client=client,
            redirect_uri=body.redirect_uri,
            code_verifier=body.code_verifier,
        )
        token_mgr = OAuth2TokenManager(
            requester_id=env("ROOT_ID"), model_registry=self.model_registry
        )
        return token_mgr.issue_token_pair(
            code=code,
            access_ttl_minutes=int(env("OAUTH_PROVIDER_ACCESS_TTL_MINUTES") or 60),
            refresh_ttl_days=int(env("OAUTH_PROVIDER_REFRESH_TTL_DAYS") or 30),
        )

    @custom_route(
        method="POST",
        path="/introspect",
        input_model=OAuth2IntrospectRequest,
        output_model=OAuth2TokenIntrospection,
        authentication_type="none",
        openapi_tags=("OAuth2 Provider",),
        summary="Introspect a token (RFC 7662, client-authenticated)",
    )
    @rate_limit(DEFAULT_AUTH_RATE_LIMIT, scope="ip")
    async def introspect_route(
        self, body: OAuth2IntrospectRequest
    ) -> OAuth2TokenIntrospection:
        # RFC 7662 §2.1: the endpoint requires authentication. We use
        # client credentials and additionally bind the introspection
        # response to the calling client — a client may only introspect
        # tokens it issued, so a leaked token at one client cannot be
        # validated at another.
        client_mgr = OAuth2ClientManager(
            requester_id=env("ROOT_ID"), model_registry=self.model_registry
        )
        client = client_mgr.authenticate_client(body.client_id, body.client_secret)
        token_mgr = OAuth2TokenManager(
            requester_id=env("ROOT_ID"), model_registry=self.model_registry
        )
        result = token_mgr.introspect(body.token)
        # Inactive on cross-client introspection so token validity doesn't
        # leak across clients.
        if result.active and result.client_id != client.client_id:
            return OAuth2TokenIntrospection(active=False)
        return result

    @custom_route(
        method="POST",
        path="/revoke",
        input_model=OAuth2RevokeRequest,
        output_model=OAuth2RevokeResponse,
        authentication_type="none",
        openapi_tags=("OAuth2 Provider",),
        summary="Revoke a token (RFC 7009)",
    )
    @rate_limit(DEFAULT_AUTH_RATE_LIMIT, scope="ip")
    async def revoke_route(self, body: OAuth2RevokeRequest) -> OAuth2RevokeResponse:
        client_mgr = OAuth2ClientManager(
            requester_id=env("ROOT_ID"), model_registry=self.model_registry
        )
        client = client_mgr.authenticate_client(body.client_id, body.client_secret)
        token_mgr = OAuth2TokenManager(
            requester_id=env("ROOT_ID"), model_registry=self.model_registry
        )
        revoked = token_mgr.revoke_token(body.token, client)
        # RFC 7009: do NOT leak which tokens existed. Always report
        # success on a successful client-auth path.
        del revoked
        return OAuth2RevokeResponse(revoked=True)
