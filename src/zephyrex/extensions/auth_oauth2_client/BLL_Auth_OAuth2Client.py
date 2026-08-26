# SPDX-License-Identifier: AGPL-3.0-or-later
"""OAuth2 *client* entities, managers, and external-IdP SSO flow.

The client half of the OAuth2 split (the authorization-server half is the
separate ``auth_oauth2_server`` extension). This lets a logged-in user link
external identity providers (Google / GitHub / Microsoft / Amazon) to their
account. It owns:

- ``UserOAuthModel`` / ``UserOAuthManager`` — the link between a local user and
  an external identity (provider + provider_user_id + tokens). The manager also
  **hosts the SSO verbs** at ``/v1/oauth2_client`` (``/providers``,
  ``/connections``, ``/connect/{provider}``, ``/callback/{provider}``,
  ``/disconnect/{provider}``) as ``custom_routes``; it registers no raw CRUD
  (``routes_to_register = []``) so external tokens are never listed over HTTP.
- ``OAuthProviderModel`` / ``OAuthProviderManager`` — admin-configurable
  provider records (endpoints/ids), an extension point beyond the built-in
  adapters.
- ``OAuthExternalScopeModel`` / ``OAuthExternalScopeManager`` — per-provider
  scope records.

The code<->token<->userinfo exchange is delegated to the existing per-provider
adapters (``Google``/``GitHub``/``Microsoft``/``Amazon`` subclasses of
``AbstractOAuthProvider``); this module only builds the authorize URL and
persists the resulting link.
"""

import secrets
from typing import Any, ClassVar, Dict, List, Optional, Type
from urllib.parse import urlencode

from fastapi import HTTPException
from pydantic import Field

from zephyrex.extensions.auth_oauth2_client.Amazon import AmazonOAuthProvider
from zephyrex.extensions.auth_oauth2_client.GitHub import GitHubOAuthProvider
from zephyrex.extensions.auth_oauth2_client.Google import GoogleOAuthProvider
from zephyrex.extensions.auth_oauth2_client.Microsoft import MicrosoftOAuthProvider
from zephyrex.lib.Environment import env
from zephyrex.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    ModelMeta,
    StringSearchModel,
    UpdateMixinModel,
)
from zephyrex.logic.BLL_Auth import UserModel
from zephyrex.pydantic2.fastapi import AuthType, RouterMixin
from zephyrex.pydantic2.registry import BaseModel

# Built-in provider adapters keyed by their short public name.
PROVIDER_REGISTRY: Dict[str, Type[Any]] = {
    "google": GoogleOAuthProvider,
    "github": GitHubOAuthProvider,
    "microsoft": MicrosoftOAuthProvider,
    "amazon": AmazonOAuthProvider,
}

# Authorize-endpoint config per provider: the URL to redirect the user to, the
# default scope, and the env var holding the client id.
_PROVIDER_AUTH: Dict[str, Dict[str, str]] = {
    "google": {
        "auth_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "scope": "openid email profile",
        "client_id_env": "GOOGLE_CLIENT_ID",
    },
    "github": {
        "auth_url": "https://github.com/login/oauth/authorize",
        "scope": "read:user user:email",
        "client_id_env": "GITHUB_CLIENT_ID",
    },
    "microsoft": {
        "auth_url": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        "scope": "openid email profile",
        "client_id_env": "MICROSOFT_CLIENT_ID",
    },
    "amazon": {
        "auth_url": "https://www.amazon.com/ap/oa",
        "scope": "profile",
        "client_id_env": "AWS_CLIENT_ID",
    },
}


def _require_provider(provider: str) -> Type[Any]:
    adapter = PROVIDER_REGISTRY.get((provider or "").lower())
    if adapter is None:
        raise HTTPException(status_code=404, detail=f"unknown provider: {provider}")
    return adapter


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------


class UserOAuthModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    UserModel.Reference.Optional,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["UserOAuthManager"]] = None  # type: ignore[assignment]
    provider: Optional[str] = Field(
        None, description="External IdP short name (google/github/...)"
    )
    provider_user_id: Optional[str] = Field(
        None, description="The user's stable id at the provider"
    )
    account_email: Optional[str] = Field(
        None, description="Email reported by the provider"
    )
    account_name: Optional[str] = Field(
        None, description="Display name reported by the provider"
    )
    access_token: Optional[str] = Field(
        None, description="Current external access token"
    )
    refresh_token: Optional[str] = Field(
        None, description="External refresh token, if issued"
    )

    table_comment: ClassVar[str] = (
        "Links a local user to an external OAuth2 identity (SSO)"
    )

    class Create(BaseModel, UserModel.Reference.ID.Optional):
        provider: str = Field(..., description="External IdP short name")
        provider_user_id: Optional[str] = Field(None)
        account_email: Optional[str] = Field(None)
        account_name: Optional[str] = Field(None)
        access_token: Optional[str] = Field(None)
        refresh_token: Optional[str] = Field(None)

    class Update(BaseModel):
        provider_user_id: Optional[str] = Field(None)
        account_email: Optional[str] = Field(None)
        account_name: Optional[str] = Field(None)
        access_token: Optional[str] = Field(None)
        refresh_token: Optional[str] = Field(None)

    class Search(ApplicationModel.Search, UserModel.Reference.ID.Search):
        provider: Optional[StringSearchModel] = None
        account_email: Optional[StringSearchModel] = None


class OAuthProviderModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["OAuthProviderManager"]] = None  # type: ignore[assignment]
    name: Optional[str] = Field(None, description="Provider short name")
    client_id: Optional[str] = Field(
        None, description="OAuth client id at the provider"
    )
    auth_url: Optional[str] = Field(None, description="Authorization endpoint URL")
    token_url: Optional[str] = Field(None, description="Token endpoint URL")
    userinfo_url: Optional[str] = Field(None, description="Userinfo endpoint URL")

    table_comment: ClassVar[str] = "Admin-configurable external OAuth2 providers"

    class Create(BaseModel):
        name: str = Field(..., description="Provider short name")
        client_id: Optional[str] = Field(None)
        auth_url: Optional[str] = Field(None)
        token_url: Optional[str] = Field(None)
        userinfo_url: Optional[str] = Field(None)

    class Update(BaseModel):
        client_id: Optional[str] = Field(None)
        auth_url: Optional[str] = Field(None)
        token_url: Optional[str] = Field(None)
        userinfo_url: Optional[str] = Field(None)

    class Search(ApplicationModel.Search):
        name: Optional[StringSearchModel] = None


class OAuthExternalScopeModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["OAuthExternalScopeManager"]] = None  # type: ignore[assignment]
    provider: Optional[str] = Field(None, description="Provider short name")
    scope_name: Optional[str] = Field(None, description="Scope granted at the provider")

    table_comment: ClassVar[str] = "Per-provider external OAuth2 scopes"

    class Create(BaseModel):
        provider: str = Field(..., description="Provider short name")
        scope_name: str = Field(..., description="Scope granted at the provider")

    class Update(BaseModel):
        scope_name: Optional[str] = Field(None)

    class Search(ApplicationModel.Search):
        provider: Optional[StringSearchModel] = None
        scope_name: Optional[StringSearchModel] = None


# ---------------------------------------------------------------------------
# Managers
# ---------------------------------------------------------------------------


class UserOAuthManager(AbstractBLLManager, RouterMixin):
    """The user's external-identity links + the SSO flow verbs.

    ``routes_to_register = []`` keeps the raw CRUD (which would expose external
    tokens) off HTTP; the ``custom_routes`` below are the only surface.
    """

    _model = UserOAuthModel

    prefix: ClassVar[Optional[str]] = "/v1/oauth2_client"
    tags: ClassVar[Optional[List[str]]] = ["OAuth2 Client (SSO)"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    auth_dependency: ClassVar[Optional[str]] = "get_auth_user"
    routes_to_register: ClassVar[Optional[List[Any]]] = []
    custom_routes: ClassVar[List[Dict[str, Any]]] = [
        {
            "path": "/providers",
            "method": "get",
            "function": "providers_route",
            "auth_type": AuthType.JWT,
            "is_static": False,
            "summary": "List available SSO providers",
            "status_code": 200,
        },
        {
            "path": "/connections",
            "method": "get",
            "function": "connections_route",
            "auth_type": AuthType.JWT,
            "is_static": False,
            "summary": "List the caller's linked external identities",
            "status_code": 200,
        },
        {
            "path": "/connect/{provider}",
            "method": "get",
            "function": "connect_route",
            "auth_type": AuthType.JWT,
            "is_static": False,
            "summary": "Get the provider authorize URL to begin linking",
            "status_code": 200,
        },
        {
            "path": "/callback/{provider}",
            "method": "post",
            "function": "callback_route",
            "auth_type": AuthType.JWT,
            "is_static": False,
            "summary": "Complete linking: exchange the code and store the identity",
            "status_code": 200,
        },
        {
            "path": "/disconnect/{provider}",
            "method": "delete",
            "function": "disconnect_route",
            "auth_type": AuthType.JWT,
            "is_static": False,
            "summary": "Unlink an external identity from the caller",
            "status_code": 200,
        },
    ]

    def providers_route(self) -> Dict[str, Any]:
        return {
            "providers": [
                {"name": name, "configured": bool(env(cfg["client_id_env"]))}
                for name, cfg in _PROVIDER_AUTH.items()
            ]
        }

    def connections_route(self) -> Dict[str, Any]:
        links = self.list(
            filters=[
                self.DB.user_id == self.requester.id,
                self.DB.deleted_at.is_(None),
            ]
        )
        return {
            "connections": [
                {
                    "provider": link.provider,
                    "account_email": link.account_email,
                    "account_name": link.account_name,
                }
                for link in (links or [])
            ]
        }

    def connect_route(self, provider: str) -> Dict[str, Any]:
        provider = (provider or "").lower()
        cfg = _PROVIDER_AUTH.get(provider)
        if cfg is None:
            raise HTTPException(status_code=404, detail=f"unknown provider: {provider}")
        redirect_uri = env(f"{provider.upper()}_REDIRECT_URI") or env("APP_URI")
        params = {
            "client_id": env(cfg["client_id_env"]),
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": cfg["scope"],
            "state": secrets.token_urlsafe(16),
        }
        return {"authorize_url": f"{cfg['auth_url']}?{urlencode(params)}"}

    def callback_route(self, provider: str, body: Dict[str, Any]) -> Dict[str, Any]:
        provider = (provider or "").lower()
        adapter = _require_provider(provider)
        code = (body or {}).get("code")
        if not code:
            raise HTTPException(status_code=400, detail="code is required")
        instance = adapter.sso_handler(code, (body or {}).get("redirect_uri"))
        if instance is None:
            raise HTTPException(status_code=401, detail="provider exchange failed")
        info = instance.get_user_info() or {}
        email = info.get("email")
        provider_user_id = str(info.get("id") or email or "")

        existing = self.list(
            filters=[
                self.DB.user_id == self.requester.id,
                self.DB.provider == provider,
                self.DB.deleted_at.is_(None),
            ]
        )
        fields = dict(
            provider_user_id=provider_user_id,
            account_email=email,
            account_name=info.get("name")
            or " ".join(p for p in [info.get("first_name"), info.get("last_name")] if p)
            or None,
            access_token=getattr(instance, "access_token", None),
            refresh_token=getattr(instance, "refresh_token", None),
        )
        if existing:
            self.update(id=existing[0].id, **fields)
        else:
            self.create(user_id=self.requester.id, provider=provider, **fields)
        return {"linked": True, "provider": provider, "email": email}

    def disconnect_route(self, provider: str) -> Dict[str, Any]:
        provider = (provider or "").lower()
        existing = self.list(
            filters=[
                self.DB.user_id == self.requester.id,
                self.DB.provider == provider,
                self.DB.deleted_at.is_(None),
            ]
        )
        for link in existing or []:
            self.delete(id=link.id)
        return {"disconnected": True, "provider": provider}


class OAuthProviderManager(AbstractBLLManager, RouterMixin):
    _model = OAuthProviderModel

    prefix: ClassVar[Optional[str]] = "/v1/oauth2_provider"
    tags: ClassVar[Optional[List[str]]] = ["OAuth2 Client (SSO)"]
    auth_type: ClassVar[AuthType] = AuthType.JWT


class OAuthExternalScopeManager(AbstractBLLManager):
    _model = OAuthExternalScopeModel


UserOAuthModel.Manager = UserOAuthManager
OAuthProviderModel.Manager = OAuthProviderManager
OAuthExternalScopeModel.Manager = OAuthExternalScopeManager
