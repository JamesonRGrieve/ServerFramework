# SPDX-License-Identifier: AGPL-3.0-or-later
"""The authorization server's records, and the two its users manage:
the clients they register and the consents (grants) they gave.

- ``OauthClientModel``: a registered application. Its ``client_id`` and
  secret are minted by the server; the secret is shown once and stored
  only as a SHA-256 digest. Its owner manages it at
  ``/v1/oauth2/clients``.
- ``OauthGrantModel``: a user's consent that a client may hold the listed
  scopes for them. The user lists and revokes their grants at
  ``/v1/oauth2/grants``; revoking one revokes every token the client holds
  for that user.
- ``OauthAuthorizationRequestModel``, ``OauthAuthorizationCodeModel``,
  ``OauthTokenModel`` and ``OauthSigningKeyModel`` are the protocol's own
  state (``AuthorizationServer``), with no CRUD surface: a pending
  authorization request, a single-use code, an access or refresh token
  (digest only), and an ID-token signing key whose private half is
  encrypted at rest.
"""

import json
from datetime import datetime, timezone
from typing import Any, Callable, ClassVar, Dict, Iterable, List, Optional, Set

from fastapi import HTTPException
from pydantic import BaseModel as RouteModel
from pydantic import Field

from zephyrex.database.StaticPermissions import is_root_id, is_system_id
from zephyrex.extensions.oauth_provider import Config
from zephyrex.extensions.oauth_provider.OAuthProtocol import (
    APPLICATION_NATIVE,
    APPLICATION_TYPES,
    APPLICATION_WEB,
    AUTH_METHOD_BASIC,
    AUTH_METHOD_NONE,
    CONFIDENTIAL_AUTH_METHODS,
    OIDC_SCOPES,
    digest,
    json_list,
    mint_secret,
    validate_redirect_uri,
)
from zephyrex.lib.CustomRoute import ExposeIn, custom_route
from zephyrex.lib.Environment import env
from zephyrex.lib.InboundSecurity import DEFAULT_AUTH_RATE_LIMIT, rate_limit
from zephyrex.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    ModelMeta,
    StringSearchModel,
    UpdateMixinModel,
)
from zephyrex.logic.BLL_Auth import UserModel
from zephyrex.logic.Permissions import (
    PermissionRegistry,
    collect_extension_permissions_into,
)
from zephyrex.pydantic2.fastapi import AuthType, RouteType, RouterMixin
from zephyrex.pydantic2.registry import BaseModel

CLIENT_ID_PREFIX = "zxc_"
CLIENT_SECRET_PREFIX = "zxcs_"
MAX_REDIRECT_URIS = 20
MAX_CLIENT_NAME = 200
TAGS = ["OAuth Provider"]
UNIQUE: Dict[str, Any] = {"unique": True}


def server_side(requester_id: Optional[str]) -> bool:
    """ROOT and SYSTEM act on others' behalf; users act as themselves."""
    if not requester_id:
        return False
    return is_root_id(requester_id) or is_system_id(requester_id)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def supported_scopes(model_registry: Any) -> Set[str]:
    """The scopes a client may be registered for: OpenID Connect's, and
    every permission a loaded extension declares (a permission is a scope
    of the same name)."""
    registry = PermissionRegistry()
    extensions = getattr(model_registry, "extension_registry", None)
    if extensions is not None:
        collect_extension_permissions_into(registry, extensions.extensions)
    return set(OIDC_SCOPES) | registry.names()


# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------


class OauthClientModel(
    ApplicationModel,
    UpdateMixinModel,
    UserModel.Reference,
    metaclass=ModelMeta,
):
    """An application registered with this authorization server."""

    name: str = Field(..., description="Name shown to users on the consent screen")
    client_id: str = Field(
        ..., description="Public client identifier", json_schema_extra=UNIQUE
    )
    client_secret_hash: Optional[str] = Field(
        None,
        exclude=True,
        description="SHA-256 of the client secret (confidential clients; write-only)",
    )
    is_confidential: bool = Field(
        True, description="Whether the client authenticates with a secret"
    )
    application_type: str = Field(APPLICATION_WEB, description="'web' or 'native'")
    token_endpoint_auth_method: str = Field(
        AUTH_METHOD_BASIC,
        description="client_secret_basic, client_secret_post, or none (public)",
    )
    redirect_uris: str = Field(
        ..., description="JSON array of registered redirect URIs"
    )
    allowed_scopes: str = Field("", description="Space-delimited scopes it may request")
    resource_uri: Optional[str] = Field(
        None,
        description="The protected resource this client is (an access token "
        "audience); set by the operator only",
    )
    id_token_signed_response_alg: str = Field(
        Config.RS256, description="Algorithm its ID tokens are signed with"
    )
    is_enabled: bool = Field(True, description="Whether the client may be used")

    table_comment: ClassVar[str] = "OAuth 2.0 / OpenID Connect registered clients"

    # Registration is POST /v1/oauth2/clients/register, which mints the
    # credentials; these fields are its server-computed output.
    class Create(BaseModel, UserModel.Reference.ID.Optional):
        name: str
        client_id: str
        client_secret_hash: Optional[str] = None
        is_confidential: bool = True
        application_type: str = APPLICATION_WEB
        token_endpoint_auth_method: str = AUTH_METHOD_BASIC
        redirect_uris: str
        allowed_scopes: str = ""
        resource_uri: Optional[str] = None
        id_token_signed_response_alg: str = Config.RS256
        is_enabled: bool = True

    class Update(BaseModel):
        name: Optional[str] = None
        redirect_uris: Optional[str] = Field(
            None, description="JSON array of registered redirect URIs"
        )
        allowed_scopes: Optional[str] = None
        is_enabled: Optional[bool] = None

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
        UserModel.Reference.ID.Search,
    ):
        name: Optional[StringSearchModel] = None
        client_id: Optional[StringSearchModel] = None
        is_enabled: Optional[bool] = None


class ClientRegistration(RouteModel):
    name: str = Field(..., min_length=1, max_length=MAX_CLIENT_NAME)
    redirect_uris: List[str] = Field(..., min_length=1, max_length=MAX_REDIRECT_URIS)
    is_confidential: bool = True
    application_type: str = APPLICATION_WEB
    allowed_scopes: List[str] = Field(default_factory=list)
    token_endpoint_auth_method: Optional[str] = Field(
        None,
        description="client_secret_basic (default) or client_secret_post for a "
        "confidential client; a public client authenticates with none",
    )
    id_token_signed_response_alg: str = Config.RS256
    resource_uri: Optional[str] = Field(
        None, description="Operator only: the resource server this client is"
    )


class ClientRegistered(RouteModel):
    """Returned once. The secret is never shown again."""

    id: str
    client_id: str
    client_secret: Optional[str]
    name: str
    is_confidential: bool
    application_type: str
    token_endpoint_auth_method: str
    redirect_uris: List[str]
    allowed_scopes: List[str]
    resource_uri: Optional[str]
    id_token_signed_response_alg: str


class ClientSecretRotated(RouteModel):
    client_id: str
    client_secret: str


class SecretRotationRequest(RouteModel):
    """No fields: the new secret is minted by the server."""


def _bad_request(detail: str) -> HTTPException:
    return HTTPException(status_code=400, detail=detail)


def _redirect_uris(raw: Iterable[str], application_type: str) -> List[str]:
    uris = list(raw)
    if not 1 <= len(uris) <= MAX_REDIRECT_URIS:
        raise _bad_request(f"a client registers 1-{MAX_REDIRECT_URIS} redirect URIs")
    try:
        return [validate_redirect_uri(uri, application_type) for uri in uris]
    except ValueError as error:
        raise _bad_request(str(error)) from None


def _scopes(raw: Iterable[str], model_registry: Any) -> List[str]:
    scopes = list(dict.fromkeys(raw))
    unknown = sorted(set(scopes) - supported_scopes(model_registry))
    if unknown:
        raise _bad_request(f"unsupported scopes: {unknown}")
    return scopes


def _resource_uri(raw: Optional[str], requester_id: str) -> Optional[str]:
    if raw is None:
        return None
    if not server_side(requester_id):
        raise HTTPException(
            status_code=403, detail="Only the operator registers a resource server"
        )
    try:
        return validate_redirect_uri(raw, APPLICATION_WEB)
    except ValueError as error:
        raise _bad_request(str(error).replace("redirect URI", "resource URI")) from None


class OauthClientManager(AbstractBLLManager, RouterMixin):
    _model = OauthClientModel

    prefix: ClassVar[Optional[str]] = "/v1/oauth2/clients"
    tags: ClassVar[Optional[List[str]]] = TAGS
    auth_type: ClassVar[AuthType] = AuthType.JWT
    routes_to_register: ClassVar[Optional[List[RouteType]]] = [
        RouteType.GET,
        RouteType.LIST,
        RouteType.SEARCH,
        RouteType.UPDATE,
        RouteType.DELETE,
    ]

    def create(self, **kwargs: Any) -> Any:
        raise HTTPException(
            status_code=403,
            detail="Clients are registered through POST /v1/oauth2/clients/register",
        )

    # Not named ``register``: the GraphQL builder reads a manager with a
    # ``register`` method as the self-scoped user manager and exposes all
    # of its CRUD.
    def register_client(self, request: ClientRegistration) -> ClientRegistered:
        """Register a client owned by the requester, minting its id and (for
        a confidential client) its secret."""
        requester_id = self.requester.id
        if request.application_type not in APPLICATION_TYPES:
            raise _bad_request("application_type is 'web' or 'native'")
        if request.application_type == APPLICATION_NATIVE and request.is_confidential:
            raise _bad_request("a native client is public (RFC 8252 §8.4)")
        method = request.token_endpoint_auth_method
        if request.is_confidential:
            method = method or AUTH_METHOD_BASIC
            if method not in CONFIDENTIAL_AUTH_METHODS:
                raise _bad_request(
                    "a confidential client authenticates with client_secret_basic "
                    "or client_secret_post"
                )
        elif (method or AUTH_METHOD_NONE) != AUTH_METHOD_NONE:
            raise _bad_request("a public client authenticates with PKCE alone (none)")
        else:
            method = AUTH_METHOD_NONE
        if request.id_token_signed_response_alg not in Config.signing_algorithms():
            raise _bad_request(
                f"id_token_signed_response_alg is one of {Config.signing_algorithms()}"
            )
        uris = _redirect_uris(request.redirect_uris, request.application_type)
        scopes = _scopes(request.allowed_scopes, self.model_registry)
        resource_uri = _resource_uri(request.resource_uri, requester_id)
        if resource_uri is not None and self._resource_taken(resource_uri):
            raise HTTPException(
                status_code=409, detail="That resource URI is already registered"
            )
        raw_secret = (
            mint_secret(CLIENT_SECRET_PREFIX) if request.is_confidential else None
        )
        client = super().create(
            name=request.name,
            client_id=mint_secret(CLIENT_ID_PREFIX),
            client_secret_hash=digest(raw_secret) if raw_secret else None,
            is_confidential=request.is_confidential,
            application_type=request.application_type,
            token_endpoint_auth_method=method,
            redirect_uris=json.dumps(uris),
            allowed_scopes=" ".join(scopes),
            resource_uri=resource_uri,
            id_token_signed_response_alg=request.id_token_signed_response_alg,
            is_enabled=True,
            user_id=requester_id,
        )
        return ClientRegistered(
            id=client.id,
            client_id=client.client_id,
            client_secret=raw_secret,
            name=client.name,
            is_confidential=client.is_confidential,
            application_type=client.application_type,
            token_endpoint_auth_method=client.token_endpoint_auth_method,
            redirect_uris=uris,
            allowed_scopes=scopes,
            resource_uri=resource_uri,
            id_token_signed_response_alg=client.id_token_signed_response_alg,
        )

    def _resource_taken(self, resource_uri: str) -> bool:
        return bool(
            self.DB.list(
                requester_id=env("ROOT_ID"),
                model_registry=self.model_registry,
                filters=[self.DB.resource_uri == resource_uri],
            )
        )

    def _owned(self, id: str) -> OauthClientModel:
        """The client, when the requester owns it (or is the operator)."""
        client: OauthClientModel = self.get(id=id)
        if not server_side(self.requester.id) and client.user_id != self.requester.id:
            raise HTTPException(
                status_code=403, detail="Only its owner manages a client"
            )
        return client

    def update(self, id: str, **kwargs: Any) -> Any:
        client = self._owned(id)
        if kwargs.get("redirect_uris") is not None:
            try:
                uris = json_list(kwargs["redirect_uris"])
            except ValueError:
                raise _bad_request("redirect_uris is a JSON array of URIs") from None
            kwargs["redirect_uris"] = json.dumps(
                _redirect_uris(uris, client.application_type)
            )
        if kwargs.get("allowed_scopes") is not None:
            kwargs["allowed_scopes"] = " ".join(
                _scopes(kwargs["allowed_scopes"].split(), self.model_registry)
            )
        updated = super().update(id, **kwargs)
        if kwargs.get("is_enabled") is False:
            revoke_tokens(self.model_registry, client_id=client.client_id)
        return updated

    def delete(self, id: str) -> None:
        client = self._owned(id)
        revoke_tokens(self.model_registry, client_id=client.client_id)
        super().delete(id)

    def rotate_secret(self, id: str) -> ClientSecretRotated:
        """A new secret for a confidential client; the old one stops working."""
        client = self._owned(id)
        if not client.is_confidential:
            raise _bad_request("a public client has no secret")
        raw_secret = mint_secret(CLIENT_SECRET_PREFIX)
        self.DB.update(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            id=client.id,
            new_properties={"client_secret_hash": digest(raw_secret)},
        )
        return ClientSecretRotated(client_id=client.client_id, client_secret=raw_secret)

    @custom_route(
        method="POST",
        path="/register",
        input_model=ClientRegistration,
        output_model=ClientRegistered,
        authentication_type="jwt",
        openapi_tags=tuple(TAGS),
        expose_in=(ExposeIn.REST,),
        summary="Register a client (its secret is returned exactly once)",
    )
    @rate_limit(DEFAULT_AUTH_RATE_LIMIT, scope="ip")
    def register_route(self, body: ClientRegistration) -> ClientRegistered:
        return self.register_client(body)

    @custom_route(
        method="POST",
        path="/{id}/secret",
        input_model=SecretRotationRequest,
        output_model=ClientSecretRotated,
        authentication_type="jwt",
        openapi_tags=tuple(TAGS),
        expose_in=(ExposeIn.REST,),
        summary="Replace a confidential client's secret (returned exactly once)",
    )
    @rate_limit(DEFAULT_AUTH_RATE_LIMIT, scope="ip")
    def rotate_secret_route(
        self, id: str, body: SecretRotationRequest
    ) -> ClientSecretRotated:
        return self.rotate_secret(id)


# ---------------------------------------------------------------------------
# Grants (consent)
# ---------------------------------------------------------------------------


class OauthGrantModel(
    ApplicationModel,
    UpdateMixinModel,
    UserModel.Reference,
    metaclass=ModelMeta,
):
    """A user's consent that a client may hold ``scopes`` on their behalf."""

    client_id: str = Field(..., description="The client the consent is for")
    client_name: str = Field(
        ..., description="The client's name when consent was given"
    )
    scopes: str = Field(..., description="Space-delimited scopes consented to")

    table_comment: ClassVar[str] = "Users' consents to OAuth clients"

    class Create(BaseModel, UserModel.Reference.ID.Optional):
        client_id: str
        client_name: str
        scopes: str

    class Update(BaseModel):
        scopes: Optional[str] = None

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
        UserModel.Reference.ID.Search,
    ):
        client_id: Optional[StringSearchModel] = None


def _owned_by(requester_id: str) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    def prepare(fields: Dict[str, Any]) -> Dict[str, Any]:
        if not server_side(requester_id) or not fields.get("user_id"):
            fields["user_id"] = requester_id
        return fields

    return prepare


class OauthGrantManager(AbstractBLLManager, RouterMixin):
    """A user's consents: listed and revoked by that user."""

    _model = OauthGrantModel

    prefix: ClassVar[Optional[str]] = "/v1/oauth2/grants"
    tags: ClassVar[Optional[List[str]]] = TAGS
    auth_type: ClassVar[AuthType] = AuthType.JWT
    routes_to_register: ClassVar[Optional[List[RouteType]]] = [
        RouteType.GET,
        RouteType.LIST,
        RouteType.SEARCH,
        RouteType.DELETE,
    ]

    def create(self, **kwargs: Any) -> Any:
        """A grant is the requester's own (ROOT and SYSTEM may name another),
        singly or in a batch."""
        prepare = _owned_by(self.requester.id)
        if isinstance(kwargs.get("entities"), list):
            kwargs = {
                **kwargs,
                "entities": [prepare(dict(e)) for e in kwargs["entities"]],
            }
        else:
            kwargs = prepare(dict(kwargs))
        return super().create(**kwargs)

    def consent(self, client: OauthClientModel, scopes: List[str]) -> OauthGrantModel:
        """Record that the requester consents to ``client`` holding
        ``scopes``, adding to any consent they already gave it."""
        existing: List[OauthGrantModel] = self.list(
            user_id=self.requester.id, client_id=client.client_id
        )
        if existing:
            grant = existing[0]
            merged = list(dict.fromkeys(grant.scopes.split() + scopes))
            updated: OauthGrantModel = self.update(grant.id, scopes=" ".join(merged))
            return updated
        created: OauthGrantModel = self.create(
            client_id=client.client_id, client_name=client.name, scopes=" ".join(scopes)
        )
        return created

    def delete(self, id: str) -> None:
        """Revoke a consent, and with it every token the client holds for
        the user."""
        grant: OauthGrantModel = self.get(id=id)
        if not server_side(self.requester.id) and grant.user_id != self.requester.id:
            raise HTTPException(status_code=403, detail="Only its user revokes a grant")
        revoke_tokens(
            self.model_registry, client_id=grant.client_id, user_id=grant.user_id
        )
        super().delete(id)


# ---------------------------------------------------------------------------
# Protocol state (no CRUD surface)
# ---------------------------------------------------------------------------


class OauthAuthorizationRequestModel(
    ApplicationModel,
    UpdateMixinModel,
    UserModel.Reference.Optional,
    metaclass=ModelMeta,
):
    """A validated authorization request waiting for the user's decision."""

    client_id: str = Field(...)
    redirect_uri: str = Field(...)
    scopes: str = Field(...)
    state: Optional[str] = Field(None)
    nonce: Optional[str] = Field(None)
    code_challenge: str = Field(..., description="PKCE S256 challenge")
    prompt: Optional[str] = Field(None)
    max_age: Optional[int] = Field(None)
    resources: str = Field(
        "[]", description="JSON array of RFC 8707 resource indicators"
    )
    expires_at: datetime = Field(...)
    decided_at: Optional[datetime] = Field(None)

    table_comment: ClassVar[str] = "Pending OAuth authorization requests"

    class Create(BaseModel):
        pass

    class Update(BaseModel):
        pass

    class Search(ApplicationModel.Search):
        pass


class OauthAuthorizationCodeModel(
    ApplicationModel,
    UpdateMixinModel,
    UserModel.Reference,
    metaclass=ModelMeta,
):
    """A single-use authorization code, bound to its client, redirect URI
    and PKCE challenge. Only its SHA-256 is stored."""

    code_hash: str = Field(..., json_schema_extra=UNIQUE)
    client_id: str = Field(...)
    redirect_uri: str = Field(...)
    scopes: str = Field(...)
    nonce: Optional[str] = Field(None)
    code_challenge: str = Field(...)
    resources: str = Field("[]")
    auth_time: datetime = Field(..., description="When the user authenticated")
    expires_at: datetime = Field(...)
    used_at: Optional[datetime] = Field(None)

    table_comment: ClassVar[str] = "OAuth authorization codes (digest only, single-use)"

    class Create(BaseModel):
        pass

    class Update(BaseModel):
        pass

    class Search(ApplicationModel.Search):
        pass


ACCESS_TOKEN = "access_token"
REFRESH_TOKEN = "refresh_token"


class OauthTokenModel(
    ApplicationModel,
    UpdateMixinModel,
    UserModel.Reference,
    metaclass=ModelMeta,
):
    """An access or refresh token (digest only). Tokens issued from one
    authorization code share its ``family_id``: a replayed code or refresh
    token revokes the family."""

    token_hash: str = Field(..., json_schema_extra=UNIQUE)
    token_type: str = Field(..., description="access_token or refresh_token")
    client_id: str = Field(...)
    scopes: str = Field(...)
    audience: str = Field(
        "[]",
        description="Access token: its audiences. Refresh token: the resources "
        "authorized (JSON arrays)",
    )
    family_id: str = Field(..., description="The authorization the token descends from")
    auth_time: datetime = Field(...)
    expires_at: datetime = Field(...)
    used_at: Optional[datetime] = Field(
        None, description="When a refresh token rotated"
    )
    revoked_at: Optional[datetime] = Field(None)

    table_comment: ClassVar[str] = "OAuth access and refresh tokens (digest only)"

    class Create(BaseModel):
        pass

    class Update(BaseModel):
        pass

    class Search(ApplicationModel.Search):
        pass


KEY_ACTIVE = "active"
KEY_RETIRING = "retiring"
KEY_RETIRED = "retired"


class OauthSigningKeyModel(
    ApplicationModel,
    UpdateMixinModel,
    metaclass=ModelMeta,
):
    """An ID-token signing key. ``active`` keys sign; ``retiring`` ones are
    still published so tokens they signed verify; ``retired`` ones are
    neither, and their private half is erased."""

    kid: str = Field(..., json_schema_extra=UNIQUE, description="RFC 7638 thumbprint")
    algorithm: str = Field(...)
    public_jwk: str = Field(..., description="The public key as a JWK (JSON)")
    private_key: Optional[str] = Field(
        None, exclude=True, description="PKCS#8 PEM, encrypted at rest (write-only)"
    )
    status: str = Field(KEY_ACTIVE)
    activated_at: datetime = Field(...)
    retiring_at: Optional[datetime] = Field(None)

    table_comment: ClassVar[str] = "OpenID Connect ID token signing keys"

    class Create(BaseModel):
        pass

    class Update(BaseModel):
        pass

    class Search(ApplicationModel.Search):
        pass


def revoke_tokens(model_registry: Any, **match: str) -> int:
    """Revoke every live token whose columns equal ``match``; how many."""
    TokenDB = OauthTokenModel.DB(model_registry.DB.manager.Base)
    filters = [getattr(TokenDB, column) == value for column, value in match.items()]
    with model_registry.DB.manager._get_db_session() as session:
        count: int = (
            session.query(TokenDB)
            .filter(*filters, TokenDB.revoked_at.is_(None))
            .update({TokenDB.revoked_at: now_utc()}, synchronize_session=False)
        )
    return count
