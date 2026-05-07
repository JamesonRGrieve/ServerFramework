"""OAuth2 provider BLL: this server *is* an OAuth2 issuer.

Third-party applications register a client (``OAuth2ClientModel``), redirect
users here for consent, receive a short-lived ``OAuth2AuthCodeModel`` at the
client's redirect URI, and exchange it for an ``OAuth2TokenModel``.

Storage hardening:
- ``OAuth2ClientModel.client_secret_hash`` stores a hashed client secret;
  the raw secret is returned exactly once at creation.
- ``OAuth2AuthCodeModel`` extends ``OneTimeTokenMixin``: codes are stored
  hashed-at-rest, fingerprinted for indexed lookup, and marked ``is_used``
  on first redemption.
- ``OAuth2TokenModel.token_hash`` + ``token_fingerprint`` store opaque
  bearer tokens. Validation is constant-time after a fingerprint hit.

Scopes: this extension consumes the canonical ``PermissionRegistry`` (every
permission is also a valid OAuth scope per the framework contract) instead of
maintaining a parallel scope catalog.
"""

import hashlib
import hmac
import secrets
from datetime import datetime, timezone
from typing import ClassVar, List, Optional, Set

from fastapi import HTTPException
from pydantic import BaseModel, Field

from serverframework.lib.Environment import env
from serverframework.lib.Pydantic2FastAPI import AuthType, RouterMixin
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

    class Create(BaseModel):
        name: str
        client_id: str
        client_secret_hash: str
        client_secret_salt: str
        owner_user_id: Optional[str] = None
        redirect_uris: str
        allowed_scopes: str = ""
        is_confidential: bool = True
        is_enabled: bool = True

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
    """Short-lived authorization code, hashed at rest, single-use."""

    user_id: str = Field(..., description="User who granted consent")
    client_id: str = Field(..., description="Client this code was issued for")
    redirect_uri: str = Field(..., description="Redirect URI bound to this code")
    scopes: str = Field(..., description="Space-delimited scopes the user consented to")

    table_comment: ClassVar[str] = (
        "OAuth2 authorization codes; hashed at rest, single-use, short TTL"
    )

    class Create(BaseModel):
        user_id: str
        client_id: str
        redirect_uri: str
        scopes: str
        code_hash: str
        code_salt: str
        code_fingerprint: Optional[str] = None
        expires_at: datetime
        is_used: bool = False
        used_at: Optional[datetime] = None
        created_ip: Optional[str] = None

    class Update(BaseModel):
        is_used: Optional[bool] = None
        used_at: Optional[datetime] = None

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

    class Create(BaseModel):
        user_id: str
        client_id: str
        token_type: str
        token_hash: str
        token_salt: str
        token_fingerprint: str
        scopes: str
        expires_at: datetime
        is_revoked: bool = False

    class Update(BaseModel):
        is_revoked: Optional[bool] = None

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


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class OAuth2ClientManager(AbstractBLLManager, RouterMixin):
    _model = OAuth2ClientModel

    prefix: ClassVar[Optional[str]] = "/v1/oauth2/clients"
    tags: ClassVar[Optional[List[str]]] = ["OAuth2 Provider"]
    auth_type: ClassVar[AuthType] = AuthType.JWT

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
            redirect_uris=__import__("json").dumps(request.redirect_uris),
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

    def authenticate_client(
        self, client_id: str, client_secret: str
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
        expected = _hash_secret(client_secret, client.client_secret_salt)
        # Constant-time comparison to defeat timing attacks.
        if not hmac.compare_digest(expected, client.client_secret_hash):
            raise HTTPException(status_code=401, detail="Invalid client credentials")
        return client


class OAuth2AuthCodeManager(AbstractBLLManager, RouterMixin):
    _model = OAuth2AuthCodeModel

    prefix: ClassVar[Optional[str]] = "/v1/oauth2/auth-codes"
    tags: ClassVar[Optional[List[str]]] = ["OAuth2 Provider"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    routes_to_register: ClassVar[Optional[List]] = []

    def issue_code(
        self,
        user_id: str,
        client: OAuth2ClientModel,
        redirect_uri: str,
        requested_scopes: Set[str],
        ttl_minutes: int = 10,
    ) -> str:
        # Verify redirect_uri is in the client's whitelist.
        import json

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
            expires_at=token.expires_at,
            is_used=False,
        )
        return raw_code

    def redeem_code(
        self, raw_code: str, client: OAuth2ClientModel, redirect_uri: str
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
            if candidate.verify(raw_code):
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
