# SPDX-License-Identifier: AGPL-3.0-or-later
"""The authorization server's HTTP surface (see ``AuthorizationServer``).

``/v1/oauth2``:

- ``GET /authorize``: the authorization endpoint (front channel).
- ``GET /authorize/requests/{request_id}`` and
  ``POST /authorize/requests/{request_id}/decision``: the signed-in UI's
  consent screen and the user's decision.
- ``POST /token``, ``POST /introspect``, ``POST /revoke``: client
  authenticated; answered in the RFC 6749 §5 shape, never cached.
- ``GET``/``POST /userinfo``: bearer access token.
- ``GET /jwks``: the ID-token signing keys.
- ``POST /keys/rotate``: the operator rotates the signing keys.

``/.well-known/openid-configuration`` and
``/.well-known/oauth-authorization-server``: discovery metadata.
"""

from datetime import datetime
from typing import Any, Callable, ClassVar, Dict, List, Optional

from fastapi import HTTPException, Request
from pydantic import BaseModel as RouteModel
from pydantic import ConfigDict, Field
from starlette.responses import JSONResponse, Response

from zephyrex.extensions.oauth_provider.AuthorizationServer import (
    AuthorizationServer,
    SignedInUser,
)
from zephyrex.extensions.oauth_provider.BLL_OAuthProvider import TAGS, server_side
from zephyrex.extensions.oauth_provider.OAuthProtocol import (
    NO_STORE_HEADERS,
    OAuthError,
)
from zephyrex.lib.CustomRoute import ExposeIn, custom_route
from zephyrex.lib.ContentNegotiation import accept_form_bodies
from zephyrex.lib.SessionCookies import accept_cross_site_writes
from zephyrex.lib.InboundSecurity import (
    DEFAULT_AUTH_RATE_LIMIT,
    DEFAULT_MUTATING_RATE_LIMIT,
    DEFAULT_READ_RATE_LIMIT,
    rate_limit,
)
from zephyrex.logic.AbstractLogicManager import AbstractBLLManager
from zephyrex.pydantic2.fastapi import AuthType, RouterMixin

REST = (ExposeIn.REST,)
PROTOCOL_TAGS = tuple(TAGS)


class OAuthParameters(RouteModel):
    """A token, introspection or revocation request's parameters, checked by
    the server itself so a refusal takes the RFC 6749 §5.2 shape."""

    model_config = ConfigDict(extra="allow")


class NoParameters(RouteModel):
    """A POST that carries nothing in its body."""


class PendingAuthorization(RouteModel):
    request_id: str
    client_id: str
    client_name: str
    redirect_uri: str
    scopes: List[str]
    granted_scopes: List[str] = Field(
        ..., description="What the user already consented to for this client"
    )
    prompt: List[str]
    max_age: Optional[int]
    expires_at: datetime


class AuthorizationDecision(RouteModel):
    approve: bool
    scopes: Optional[List[str]] = Field(
        None, description="The requested scopes the user grants (default: all)"
    )


class AuthorizationRedirect(RouteModel):
    redirect_to: str = Field(..., description="Where to send the user's browser")


class RotatedKeys(RouteModel):
    kids: List[str]


def _answer(call: Callable[[], Optional[Dict[str, Any]]]) -> Response:
    """The JSON answer of a protocol verb, or its OAuth error, uncached."""
    try:
        body = call()
    except OAuthError as error:
        return JSONResponse(
            error.body(),
            status_code=error.status_code,
            headers={**NO_STORE_HEADERS, **error.headers},
        )
    return JSONResponse(body or {}, headers=dict(NO_STORE_HEADERS))


class OauthProtocolManager(AbstractBLLManager, RouterMixin):
    """Custom routes only; the protocol state is the server's."""

    prefix: ClassVar[Optional[str]] = "/v1/oauth2"
    tags: ClassVar[Optional[List[str]]] = TAGS
    auth_type: ClassVar[AuthType] = AuthType.NONE
    routes_to_register: ClassVar[Optional[List]] = []

    @property
    def server(self) -> AuthorizationServer:
        return AuthorizationServer(self.model_registry)

    def _signed_in(self, request: Request) -> SignedInUser:
        """The requester's own signed-in session: consent is never given
        with an API key or another user's token."""
        signed_in = self.server.signed_in(request.headers.get("authorization"))
        if signed_in is None or signed_in.user_id != self.requester.id:
            raise HTTPException(
                status_code=401,
                detail="login_required: a signed-in session is required",
            )
        return signed_in

    @custom_route(
        method="GET",
        path="/authorize",
        authentication_type="none",
        expose_in=REST,
        response_class=Response,
        openapi_tags=PROTOCOL_TAGS,
        summary="Authorization endpoint (authorization code flow with PKCE)",
    )
    @rate_limit(DEFAULT_MUTATING_RATE_LIMIT, scope="ip")
    def authorize_route(self, request: Request) -> Response:
        return self.server.authorize(
            request.query_params.multi_items(), request.headers.get("authorization")
        )

    @custom_route(
        method="GET",
        path="/authorize/requests/{request_id}",
        output_model=PendingAuthorization,
        authentication_type="jwt",
        expose_in=REST,
        openapi_tags=PROTOCOL_TAGS,
        summary="A pending authorization request, for the consent screen",
    )
    @rate_limit(DEFAULT_READ_RATE_LIMIT, scope="ip")
    def pending_route(self, request_id: str) -> PendingAuthorization:
        return PendingAuthorization(
            **self.server.pending(request_id, self.requester.id)
        )

    @custom_route(
        method="POST",
        path="/authorize/requests/{request_id}/decision",
        input_model=AuthorizationDecision,
        output_model=AuthorizationRedirect,
        authentication_type="jwt",
        expose_in=REST,
        openapi_tags=PROTOCOL_TAGS,
        summary="The signed-in user approves or denies an authorization request",
    )
    @rate_limit(DEFAULT_AUTH_RATE_LIMIT, scope="ip")
    def decision_route(
        self, request_id: str, body: AuthorizationDecision, request: Request
    ) -> AuthorizationRedirect:
        target = self.server.decide(
            request_id, self._signed_in(request), body.approve, body.scopes
        )
        return AuthorizationRedirect(redirect_to=target)

    @custom_route(
        method="POST",
        path="/token",
        input_model=OAuthParameters,
        authentication_type="none",
        expose_in=REST,
        response_class=Response,
        openapi_tags=PROTOCOL_TAGS,
        summary="Token endpoint (authorization_code and refresh_token grants)",
    )
    @rate_limit(DEFAULT_MUTATING_RATE_LIMIT, scope="ip")
    def token_route(self, body: OAuthParameters, request: Request) -> Response:
        return _answer(
            lambda: self.server.token(
                body.model_dump(), request.headers.get("authorization")
            )
        )

    @custom_route(
        method="POST",
        path="/introspect",
        input_model=OAuthParameters,
        authentication_type="none",
        expose_in=REST,
        response_class=Response,
        openapi_tags=PROTOCOL_TAGS,
        summary="Token introspection (RFC 7662)",
    )
    @rate_limit(DEFAULT_READ_RATE_LIMIT, scope="ip")
    def introspect_route(self, body: OAuthParameters, request: Request) -> Response:
        return _answer(
            lambda: self.server.introspect(
                body.model_dump(), request.headers.get("authorization")
            )
        )

    @custom_route(
        method="POST",
        path="/revoke",
        input_model=OAuthParameters,
        authentication_type="none",
        expose_in=REST,
        response_class=Response,
        openapi_tags=PROTOCOL_TAGS,
        summary="Token revocation (RFC 7009)",
    )
    @rate_limit(DEFAULT_MUTATING_RATE_LIMIT, scope="ip")
    def revoke_route(self, body: OAuthParameters, request: Request) -> Response:
        return _answer(
            lambda: self.server.revoke(
                body.model_dump(), request.headers.get("authorization")
            )
        )

    @custom_route(
        method="GET",
        path="/userinfo",
        authentication_type="none",
        expose_in=REST,
        response_class=Response,
        openapi_tags=PROTOCOL_TAGS,
        summary="OpenID Connect UserInfo",
    )
    @rate_limit(DEFAULT_READ_RATE_LIMIT, scope="ip")
    def userinfo_route(self, request: Request) -> Response:
        return self._userinfo(request)

    def _userinfo(self, request: Request) -> Response:
        return _answer(
            lambda: self.server.userinfo(request.headers.get("authorization"))
        )

    @custom_route(
        method="POST",
        path="/userinfo",
        input_model=NoParameters,
        authentication_type="none",
        expose_in=REST,
        response_class=Response,
        openapi_tags=PROTOCOL_TAGS,
        summary="OpenID Connect UserInfo",
    )
    @rate_limit(DEFAULT_READ_RATE_LIMIT, scope="ip")
    def userinfo_post_route(self, body: NoParameters, request: Request) -> Response:
        return self._userinfo(request)

    @custom_route(
        method="GET",
        path="/jwks",
        authentication_type="none",
        expose_in=REST,
        response_class=Response,
        openapi_tags=PROTOCOL_TAGS,
        summary="The ID-token signing keys (JWK Set)",
    )
    @rate_limit(DEFAULT_READ_RATE_LIMIT, scope="ip")
    def jwks_route(self) -> Response:
        return JSONResponse(self.server.keys.jwks())

    @custom_route(
        method="POST",
        path="/keys/rotate",
        input_model=NoParameters,
        output_model=RotatedKeys,
        authentication_type="jwt",
        expose_in=REST,
        openapi_tags=PROTOCOL_TAGS,
        summary="Rotate the ID-token signing keys (operator only)",
    )
    @rate_limit(DEFAULT_AUTH_RATE_LIMIT, scope="ip")
    def rotate_keys_route(self, body: NoParameters) -> RotatedKeys:
        if not server_side(self.requester.id):
            raise HTTPException(
                status_code=403, detail="Only the operator rotates keys"
            )
        return RotatedKeys(kids=self.server.keys.rotate())


class OauthDiscoveryManager(AbstractBLLManager, RouterMixin):
    """The issuer's metadata at the well-known locations."""

    prefix: ClassVar[Optional[str]] = "/.well-known"
    tags: ClassVar[Optional[List[str]]] = TAGS
    auth_type: ClassVar[AuthType] = AuthType.NONE
    routes_to_register: ClassVar[Optional[List]] = []

    def _metadata(self) -> Response:
        return JSONResponse(AuthorizationServer(self.model_registry).metadata())

    @custom_route(
        method="GET",
        path="/openid-configuration",
        authentication_type="none",
        expose_in=REST,
        response_class=Response,
        openapi_tags=PROTOCOL_TAGS,
        summary="OpenID Connect Discovery metadata",
    )
    @rate_limit(DEFAULT_READ_RATE_LIMIT, scope="ip")
    def openid_configuration_route(self) -> Response:
        return self._metadata()

    @custom_route(
        method="GET",
        path="/oauth-authorization-server",
        authentication_type="none",
        expose_in=REST,
        response_class=Response,
        openapi_tags=PROTOCOL_TAGS,
        summary="OAuth 2.0 Authorization Server Metadata (RFC 8414)",
    )
    @rate_limit(DEFAULT_READ_RATE_LIMIT, scope="ip")
    def authorization_server_route(self) -> Response:
        return self._metadata()


# Browser apps (public clients) call these from their own origin; they take
# client credentials or PKCE, never the session cookie.
accept_cross_site_writes(r"/v1/oauth2/(token|introspect|revoke)")
# RFC 6749 sends these as form bodies.
accept_form_bodies(r"/v1/oauth2/(token|introspect|revoke)")
