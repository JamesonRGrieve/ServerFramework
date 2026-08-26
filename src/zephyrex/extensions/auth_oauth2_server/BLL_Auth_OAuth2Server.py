# SPDX-License-Identifier: AGPL-3.0-or-later
"""OAuth2 authorization-server entities + managers.

The authorization-server half of the OAuth2 split (the SSO-client half lives in
``auth_oauth2_client``). This module owns the persisted state an OAuth2 provider
needs:

- ``OAuth2ClientModel`` / ``OAuth2ClientManager`` — registered third-party
  clients. ``client_id`` and (for confidential clients) ``client_secret`` are
  generated server-side on create.
- ``OAuth2AuthCodeModel`` / ``OAuth2AuthCodeManager`` — single-use authorization
  codes carrying the PKCE ``code_challenge``.
- ``OAuth2TokenModel`` / ``OAuth2TokenManager`` — opaque, revocable access and
  refresh tokens (validated by DB lookup; revocation is immediate).

The HTTP verbs (``/authorize``, ``/token``, ``/introspect``, ``/revoke``) live
on ``EXT_Auth_OAuth2Server`` and drive these managers. Tokens are opaque and
stored here; PKCE S256 is required for public clients (enforced at the verbs).
Clients are referenced by their protocol ``client_id`` string (matching the
OAuth2 wire contract), while resource owners are a FK to the core ``users``
table via ``UserModel.Reference``.
"""

import secrets
from datetime import datetime
from typing import Any, ClassVar, List, Optional, Type

from pydantic import Field

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


class OAuth2ClientManager(AbstractBLLManager, RouterMixin):
    _model = OAuth2ClientModel

    prefix: ClassVar[Optional[str]] = "/v1/oauth2/client"
    tags: ClassVar[Optional[List[str]]] = ["OAuth2 Server"]
    auth_type: ClassVar[AuthType] = AuthType.JWT

    def create(self, **kwargs: Any) -> Any:
        # ``client_id`` and ``client_secret`` are server authority: always
        # generate them, ignoring any client-supplied value (belt-and-braces on
        # top of the audit-field stripping in the base manager). A public client
        # (``is_confidential=False``) carries no secret.
        kwargs["client_id"] = _CLIENT_ID_PREFIX + secrets.token_urlsafe(16)
        if kwargs.get("is_confidential", True):
            kwargs["client_secret"] = secrets.token_urlsafe(32)
        else:
            kwargs["client_secret"] = None
        return super().create(**kwargs)


class OAuth2AuthCodeManager(AbstractBLLManager):
    _model = OAuth2AuthCodeModel


class OAuth2TokenManager(AbstractBLLManager):
    _model = OAuth2TokenModel


OAuth2ClientModel.Manager = OAuth2ClientManager
OAuth2AuthCodeModel.Manager = OAuth2AuthCodeManager
OAuth2TokenModel.Manager = OAuth2TokenManager
