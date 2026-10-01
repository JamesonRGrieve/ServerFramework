# SPDX-License-Identifier: AGPL-3.0-or-later
"""OAuth2 authorization-server entities, managers, and flow.

The authorization-server half of the OAuth2 split (the SSO-client half lives in
``auth_oauth2_client``). This module owns:

- ``OAuth2ClientModel`` / ``OAuth2ClientManager`` — registered third-party
  clients (CRUD at ``/v1/oauth2/client``). ``client_id`` + ``client_secret``
  are generated server-side on create.
- ``OAuth2AuthCodeModel`` / ``OAuth2AuthCodeManager`` — single-use authorization
  codes carrying the PKCE challenge (internal; no HTTP CRUD).
- ``OAuth2TokenModel`` / ``OAuth2TokenManager`` — opaque, revocable access and
  refresh tokens. ``OAuth2TokenManager`` also **hosts the authorization-server
  verbs** as ``custom_routes`` at ``/v1/oauth2`` (``/authorize``, ``/token``,
  ``/introspect``, ``/revoke``); it registers no token CRUD
  (``routes_to_register = []``) so tokens are never listable over HTTP.

Security posture: opaque, DB-stored, revocable tokens; PKCE **S256 required for
public clients** (``plain`` rejected for them); constant-time
``hmac.compare_digest`` client-secret comparison; single-use codes with a short
TTL. Clients are referenced by their protocol ``client_id`` string; resource
owners are a FK to the core ``users`` table via ``UserModel.Reference``.
"""

import base64
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, ClassVar, Dict, List, Optional, Tuple, Type

from fastapi import HTTPException
from pydantic import BaseModel as _PydBaseModel
from pydantic import Field

from zephyrex.lib.DateTimeUtils import ensure_utc
from zephyrex.lib.Environment import env
from zephyrex.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    BooleanSearchModel,
    DateSearchModel,
    ModelMeta,
    StringSearchModel,
    UpdateMixinModel,
)
from zephyrex.logic.BLL_Auth import TeamModel, UserModel
from zephyrex.pydantic2.fastapi import AuthType, RouterMixin
from zephyrex.pydantic2.registry import BaseModel

# Prefix for generated public client identifiers.
_CLIENT_ID_PREFIX = "oauth_client_"
_ACCESS_PREFIX = "oauth_access_"
_REFRESH_PREFIX = "oauth_refresh_"

# Lifetimes (seconds): short single-use codes, hourly access, monthly refresh.
_AUTH_CODE_TTL_SECONDS = 600
_ACCESS_TOKEN_TTL_SECONDS = 3600
_REFRESH_TOKEN_TTL_SECONDS = 60 * 60 * 24 * 30


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------


class OAuth2ClientModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    UserModel.Reference.Optional,
    TeamModel.Reference.Optional,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["OAuth2ClientManager"]] = None  # type: ignore[assignment]
    name: Optional[str] = Field(None, description="Human-readable client name")
    client_id: Optional[str] = Field(
        None, description="Public OAuth2 client identifier (server-generated)"
    )
    client_secret: Optional[str] = Field(
        None, description="Client secret for confidential clients (server-generated)"
    )
    is_confidential: Optional[bool] = Field(
        True, description="Confidential (secret-bearing) vs public client"
    )
    redirect_uris: Optional[str] = Field(
        None, description="JSON array of registered redirect URIs"
    )
    allowed_scopes: Optional[str] = Field(
        None, description="Space-delimited scopes this client may request"
    )

    table_comment: ClassVar[str] = (
        "Registered OAuth2 clients (third-party applications)"
    )

    class Create(
        BaseModel,
        TeamModel.Reference.ID.Optional,
        UserModel.Reference.ID.Optional,
    ):
        name: str = Field(..., description="Human-readable client name")
        is_confidential: Optional[bool] = Field(
            True, description="Confidential (secret-bearing) vs public client"
        )
        redirect_uris: str = Field(
            ..., description="JSON array of registered redirect URIs"
        )
        allowed_scopes: Optional[str] = Field(
            None, description="Space-delimited grantable scopes"
        )

    class Update(BaseModel):
        name: Optional[str] = Field(None, description="Human-readable client name")
        redirect_uris: Optional[str] = Field(
            None, description="JSON array of registered redirect URIs"
        )
        allowed_scopes: Optional[str] = Field(
            None, description="Space-delimited grantable scopes"
        )
        is_confidential: Optional[bool] = Field(None)

    class Search(ApplicationModel.Search):
        name: Optional[StringSearchModel] = None
        client_id: Optional[StringSearchModel] = None
        is_confidential: Optional[BooleanSearchModel] = None


class OAuth2AuthCodeModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    UserModel.Reference.Optional,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["OAuth2AuthCodeManager"]] = None  # type: ignore[assignment]
    client_id: Optional[str] = Field(
        None, description="OAuth2 client_id this code was issued to"
    )
    code: Optional[str] = Field(None, description="The authorization code value")
    redirect_uri: Optional[str] = Field(
        None, description="Redirect URI bound to this code"
    )
    scopes: Optional[str] = Field(None, description="Space-delimited granted scopes")
    code_challenge: Optional[str] = Field(
        None, description="PKCE code challenge (base64url)"
    )
    code_challenge_method: Optional[str] = Field(
        None, description="PKCE method: 'S256' or 'plain'"
    )
    expires_at: Optional[datetime] = Field(None, description="Code expiry")
    is_used: Optional[bool] = Field(
        False, description="Single-use flag; set once the code is redeemed"
    )

    table_comment: ClassVar[str] = (
        "Single-use OAuth2 authorization codes (carry the PKCE challenge)"
    )

    class Create(BaseModel, UserModel.Reference.ID.Optional):
        client_id: str = Field(..., description="OAuth2 client_id this code is for")
        code: str = Field(..., description="The authorization code value")
        redirect_uri: str = Field(..., description="Redirect URI bound to this code")
        scopes: Optional[str] = Field(
            None, description="Space-delimited granted scopes"
        )
        code_challenge: Optional[str] = Field(None, description="PKCE code challenge")
        code_challenge_method: Optional[str] = Field(
            None, description="PKCE method: 'S256' or 'plain'"
        )
        expires_at: Optional[datetime] = Field(None, description="Code expiry")
        is_used: Optional[bool] = Field(False)

    class Update(BaseModel):
        is_used: Optional[bool] = Field(None)

    class Search(ApplicationModel.Search, UserModel.Reference.ID.Search):
        client_id: Optional[StringSearchModel] = None
        code: Optional[StringSearchModel] = None
        is_used: Optional[BooleanSearchModel] = None
        expires_at: Optional[DateSearchModel] = None


class OAuth2TokenModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    UserModel.Reference.Optional,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["OAuth2TokenManager"]] = None  # type: ignore[assignment]
    client_id: Optional[str] = Field(
        None, description="OAuth2 client_id this token was issued to"
    )
    token: Optional[str] = Field(None, description="Opaque token value")
    token_type: Optional[str] = Field(None, description="'access' or 'refresh'")
    scopes: Optional[str] = Field(None, description="Space-delimited granted scopes")
    expires_at: Optional[datetime] = Field(None, description="Token expiry")
    is_revoked: Optional[bool] = Field(
        False, description="Revocation flag; immediate on revoke"
    )
    parent_id: Optional[str] = Field(
        None, description="Linking id for the refresh->access token lineage"
    )
    last_used_at: Optional[datetime] = Field(
        None, description="Last successful validation timestamp"
    )

    table_comment: ClassVar[str] = "Opaque, revocable OAuth2 access/refresh tokens"

    class Create(BaseModel, UserModel.Reference.ID.Optional):
        client_id: str = Field(..., description="OAuth2 client_id this token is for")
        token: str = Field(..., description="Opaque token value")
        token_type: str = Field(..., description="'access' or 'refresh'")
        scopes: Optional[str] = Field(
            None, description="Space-delimited granted scopes"
        )
        expires_at: Optional[datetime] = Field(None, description="Token expiry")
        parent_id: Optional[str] = Field(None, description="refresh->access lineage id")

    class Update(BaseModel):
        is_revoked: Optional[bool] = Field(None)
        last_used_at: Optional[datetime] = Field(None)

    class Search(ApplicationModel.Search, UserModel.Reference.ID.Search):
        client_id: Optional[StringSearchModel] = None
        token: Optional[StringSearchModel] = None
        token_type: Optional[StringSearchModel] = None
        is_revoked: Optional[BooleanSearchModel] = None
        expires_at: Optional[DateSearchModel] = None


# ---------------------------------------------------------------------------
# Request bodies for the authorization-server verbs
# ---------------------------------------------------------------------------


class AuthorizeRequest(_PydBaseModel):
    client_id: str
    redirect_uri: str
    scope: Optional[str] = None
    state: Optional[str] = None
    code_challenge: Optional[str] = None
    code_challenge_method: Optional[str] = None


class TokenRequest(_PydBaseModel):
    grant_type: str
    client_id: str
    client_secret: Optional[str] = None
    code: Optional[str] = None
    redirect_uri: Optional[str] = None
    code_verifier: Optional[str] = None
    refresh_token: Optional[str] = None


class IntrospectRequest(_PydBaseModel):
    token: str
    client_id: str
    client_secret: Optional[str] = None


class RevokeRequest(_PydBaseModel):
    token: str
    client_id: str
    client_secret: Optional[str] = None


# ---------------------------------------------------------------------------
# Flow helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _verify_pkce(
    code_verifier: str, code_challenge: str, method: Optional[str]
) -> bool:
    """Constant-time PKCE verification. ``plain`` compares directly; ``S256``
    (the default) compares base64url(sha256(verifier)) against the challenge."""
    if not code_verifier or not code_challenge:
        return False
    if method == "plain":
        return hmac.compare_digest(code_verifier, code_challenge)
    digest = hashlib.sha256(code_verifier.encode()).digest()
    computed = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return hmac.compare_digest(computed, code_challenge)


def _server_managers(model_registry: Any) -> Tuple[Any, Any, Any]:
    """Return (client, auth-code, token) managers with a ROOT requester — the
    authorization server owns this state; the verbs enforce the real checks."""
    rid = env("ROOT_ID")
    return (
        OAuth2ClientManager(requester_id=rid, model_registry=model_registry),
        OAuth2AuthCodeManager(requester_id=rid, model_registry=model_registry),
        OAuth2TokenManager(requester_id=rid, model_registry=model_registry),
    )


def _authenticate_client(
    client_mgr: Any, client_id: str, client_secret: Optional[str]
) -> Any:
    """Resolve the client and, for confidential clients, verify the secret with a
    constant-time comparison. ``client_id`` is public, so its lookup need not be
    constant-time; only the secret comparison must be. Raises 401 on failure."""
    clients = client_mgr.list(filters=[client_mgr.DB.client_id == client_id])
    if not clients:
        raise HTTPException(status_code=401, detail="invalid_client")
    client = clients[0]
    if client.is_confidential:
        stored = client.client_secret or ""
        presented = client_secret or ""
        if not stored or not hmac.compare_digest(presented, stored):
            raise HTTPException(status_code=401, detail="invalid_client")
    return client


def _issue_tokens(
    client_id: str, user_id: Optional[str], scopes: Optional[str], token_mgr: Any
) -> Dict[str, Any]:
    """Mint and persist an opaque access + refresh token pair."""
    access = _ACCESS_PREFIX + secrets.token_urlsafe(32)
    refresh = _REFRESH_PREFIX + secrets.token_urlsafe(32)
    now = _now()
    access_rec = token_mgr.create(
        client_id=client_id,
        token=access,
        token_type="access",
        scopes=scopes,
        expires_at=now + timedelta(seconds=_ACCESS_TOKEN_TTL_SECONDS),
        user_id=user_id,
    )
    token_mgr.create(
        client_id=client_id,
        token=refresh,
        token_type="refresh",
        scopes=scopes,
        expires_at=now + timedelta(seconds=_REFRESH_TOKEN_TTL_SECONDS),
        user_id=user_id,
        parent_id=access_rec.id,
    )
    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "Bearer",
        "expires_in": _ACCESS_TOKEN_TTL_SECONDS,
        "scope": scopes,
    }


def _do_authorize(
    req: AuthorizeRequest, user_id: str, model_registry: Any
) -> Dict[str, Any]:
    client_mgr, code_mgr, _ = _server_managers(model_registry)
    clients = client_mgr.list(filters=[client_mgr.DB.client_id == req.client_id])
    if not clients:
        raise HTTPException(status_code=400, detail="unauthorized_client")
    client = clients[0]

    try:
        allowed_uris = json.loads(client.redirect_uris or "[]")
    except (ValueError, TypeError):
        allowed_uris = []
    if req.redirect_uri not in allowed_uris:
        raise HTTPException(status_code=400, detail="invalid_redirect_uri")

    allowed_scopes = set((client.allowed_scopes or "").split())
    requested_scopes = set((req.scope or "").split())
    if requested_scopes and not requested_scopes.issubset(allowed_scopes):
        raise HTTPException(status_code=400, detail="invalid_scope")

    method = req.code_challenge_method
    if not client.is_confidential:
        if not req.code_challenge:
            raise HTTPException(
                status_code=400,
                detail="code_challenge is required for public clients",
            )
        if (method or "S256") != "S256":
            raise HTTPException(
                status_code=400,
                detail="code_challenge_method must be S256 for public clients",
            )
    if req.code_challenge and method is None:
        method = "S256"

    code_value = secrets.token_urlsafe(32)
    code_mgr.create(
        client_id=req.client_id,
        code=code_value,
        redirect_uri=req.redirect_uri,
        scopes=req.scope,
        code_challenge=req.code_challenge,
        code_challenge_method=method,
        expires_at=_now() + timedelta(seconds=_AUTH_CODE_TTL_SECONDS),
        user_id=user_id,
    )
    return {"code": code_value, "state": req.state}


def _grant_authorization_code(
    req: TokenRequest, client: Any, code_mgr: Any, token_mgr: Any
) -> Dict[str, Any]:
    if not req.code:
        raise HTTPException(status_code=400, detail="invalid_request")
    codes = code_mgr.list(filters=[code_mgr.DB.code == req.code])
    if not codes:
        raise HTTPException(status_code=400, detail="invalid_grant")
    code = codes[0]
    if code.is_used:
        raise HTTPException(status_code=400, detail="invalid_grant")
    if code.client_id != client.client_id:
        raise HTTPException(status_code=400, detail="invalid_grant")
    if code.expires_at and ensure_utc(code.expires_at) < _now():
        raise HTTPException(status_code=400, detail="invalid_grant")
    if req.redirect_uri and code.redirect_uri and req.redirect_uri != code.redirect_uri:
        raise HTTPException(status_code=400, detail="invalid_grant")
    if code.code_challenge:
        if not req.code_verifier or not _verify_pkce(
            req.code_verifier, code.code_challenge, code.code_challenge_method
        ):
            raise HTTPException(status_code=400, detail="invalid_grant")
    code_mgr.update(id=code.id, is_used=True)
    return _issue_tokens(client.client_id, code.user_id, code.scopes, token_mgr)


def _grant_refresh_token(
    req: TokenRequest, client: Any, token_mgr: Any
) -> Dict[str, Any]:
    if not req.refresh_token:
        raise HTTPException(status_code=400, detail="invalid_request")
    toks = token_mgr.list(filters=[token_mgr.DB.token == req.refresh_token])
    if not toks:
        raise HTTPException(status_code=400, detail="invalid_grant")
    tok = toks[0]
    if tok.token_type != "refresh" or tok.is_revoked:
        raise HTTPException(status_code=400, detail="invalid_grant")
    if tok.client_id != client.client_id:
        raise HTTPException(status_code=400, detail="invalid_grant")
    if tok.expires_at and ensure_utc(tok.expires_at) < _now():
        raise HTTPException(status_code=400, detail="invalid_grant")
    # Rotate: the presented refresh token is single-use — revoke it, issue anew.
    token_mgr.update(id=tok.id, is_revoked=True)
    return _issue_tokens(client.client_id, tok.user_id, tok.scopes, token_mgr)


def _do_token(req: TokenRequest, model_registry: Any) -> Dict[str, Any]:
    client_mgr, code_mgr, token_mgr = _server_managers(model_registry)
    client = _authenticate_client(client_mgr, req.client_id, req.client_secret)
    if req.grant_type == "authorization_code":
        return _grant_authorization_code(req, client, code_mgr, token_mgr)
    if req.grant_type == "refresh_token":
        return _grant_refresh_token(req, client, token_mgr)
    raise HTTPException(status_code=400, detail="unsupported_grant_type")


def _do_introspect(req: IntrospectRequest, model_registry: Any) -> Dict[str, Any]:
    client_mgr, _, token_mgr = _server_managers(model_registry)
    client = _authenticate_client(client_mgr, req.client_id, req.client_secret)
    toks = token_mgr.list(filters=[token_mgr.DB.token == req.token])
    if not toks:
        return {"active": False}
    tok = toks[0]
    active = (
        tok.client_id == client.client_id
        and not tok.is_revoked
        and (not tok.expires_at or ensure_utc(tok.expires_at) >= _now())
    )
    if not active:
        return {"active": False}
    return {
        "active": True,
        "client_id": tok.client_id,
        "scope": tok.scopes,
        "token_type": tok.token_type,
        "user_id": tok.user_id,
        "exp": int(ensure_utc(tok.expires_at).timestamp()) if tok.expires_at else None,
    }


def _do_revoke(req: RevokeRequest, model_registry: Any) -> Dict[str, Any]:
    client_mgr, _, token_mgr = _server_managers(model_registry)
    client = _authenticate_client(client_mgr, req.client_id, req.client_secret)
    toks = token_mgr.list(filters=[token_mgr.DB.token == req.token])
    # RFC 7009: revocation is idempotent; an unknown token is still a success.
    for tok in toks or []:
        if tok.client_id == client.client_id and not tok.is_revoked:
            token_mgr.update(id=tok.id, is_revoked=True)
    return {"revoked": True}


# ---------------------------------------------------------------------------
# Managers
# ---------------------------------------------------------------------------


class OAuth2ClientManager(AbstractBLLManager, RouterMixin):
    _model = OAuth2ClientModel

    prefix: ClassVar[Optional[str]] = "/v1/oauth2/client"
    tags: ClassVar[Optional[List[str]]] = ["OAuth2 Server"]
    auth_type: ClassVar[AuthType] = AuthType.JWT

    def create(self, **kwargs: Any) -> Any:
        # ``client_id`` and ``client_secret`` are server authority: always
        # generate them, ignoring any client-supplied value. A public client
        # (``is_confidential=False``) carries no secret.
        kwargs["client_id"] = _CLIENT_ID_PREFIX + secrets.token_urlsafe(16)
        if kwargs.get("is_confidential", True):
            kwargs["client_secret"] = secrets.token_urlsafe(32)
        else:
            kwargs["client_secret"] = None
        return super().create(**kwargs)


class OAuth2AuthCodeManager(AbstractBLLManager):
    _model = OAuth2AuthCodeModel


class OAuth2TokenManager(AbstractBLLManager, RouterMixin):
    """Token storage **and** the host for the authorization-server verbs.

    ``routes_to_register = []`` disables token CRUD over HTTP (tokens are never
    listable); the four ``custom_routes`` expose the OAuth2 flow at ``/v1/oauth2``.
    ``/authorize`` is non-static + JWT (the resource owner is ``self.requester``);
    the rest are public (the client authenticates by id/secret).
    """

    _model = OAuth2TokenModel

    prefix: ClassVar[Optional[str]] = "/v1/oauth2"
    tags: ClassVar[Optional[List[str]]] = ["OAuth2 Server"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    auth_dependency: ClassVar[Optional[str]] = "get_auth_user"
    routes_to_register: ClassVar[Optional[List[Any]]] = []
    custom_routes: ClassVar[List[Dict[str, Any]]] = [
        {
            "path": "/authorize",
            "method": "post",
            "function": "authorize_route",
            "auth_type": AuthType.JWT,
            "is_static": False,
            "summary": "OAuth2 authorization endpoint",
            "description": "Resource-owner authorization: validates client, "
            "redirect URI and scopes, enforces PKCE for public clients, and "
            "returns a single-use authorization code.",
            "status_code": 200,
        },
        {
            "path": "/token",
            "method": "post",
            "function": "token_route",
            "auth_type": AuthType.NONE,
            "is_static": True,
            "summary": "OAuth2 token endpoint",
            "description": "Exchanges an authorization code (with PKCE) or a "
            "refresh token for opaque access/refresh tokens.",
            "status_code": 200,
        },
        {
            "path": "/introspect",
            "method": "post",
            "function": "introspect_route",
            "auth_type": AuthType.NONE,
            "is_static": True,
            "summary": "OAuth2 token introspection",
            "description": "Reports whether a token is active plus its metadata.",
            "status_code": 200,
        },
        {
            "path": "/revoke",
            "method": "post",
            "function": "revoke_route",
            "auth_type": AuthType.NONE,
            "is_static": True,
            "summary": "OAuth2 token revocation",
            "description": "Revokes a token immediately (idempotent per RFC 7009).",
            "status_code": 200,
        },
    ]

    def authorize_route(self, body: Dict[str, Any]) -> Dict[str, Any]:
        req = AuthorizeRequest(**(body or {}))
        return _do_authorize(req, self.requester.id, self.model_registry)

    @classmethod
    def token_route(
        cls, body: Optional[Dict[str, Any]] = None, model_registry: Any = None
    ) -> Dict[str, Any]:
        return _do_token(TokenRequest(**(body or {})), model_registry)

    @classmethod
    def introspect_route(
        cls, body: Optional[Dict[str, Any]] = None, model_registry: Any = None
    ) -> Dict[str, Any]:
        return _do_introspect(IntrospectRequest(**(body or {})), model_registry)

    @classmethod
    def revoke_route(
        cls, body: Optional[Dict[str, Any]] = None, model_registry: Any = None
    ) -> Dict[str, Any]:
        return _do_revoke(RevokeRequest(**(body or {})), model_registry)


OAuth2ClientModel.Manager = OAuth2ClientManager
OAuth2AuthCodeModel.Manager = OAuth2AuthCodeManager
OAuth2TokenModel.Manager = OAuth2TokenManager
