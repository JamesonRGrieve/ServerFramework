from fastapi import Body, Depends, HTTPException, Path, Query, status

from serverframework.endpoints.AbstractEPRouter import AbstractEPRouter, ExampleGenerator, MessageModel
from serverframework.extensions.auth_oauth.BLL_Auth_OAuth import (
    OAuth2AuthCodeManager,
    OAuth2ClientManager,
    OAuth2TokenManager,
    OAuthExternalScopeManager,
    OAuthProviderManager,
    UserOAuthManager,
)
from serverframework.logic.BLL_Auth import User, UserManager


# Manager factories
def get_user_oauth_manager(user: User = Depends(UserManager.auth)):
    """Get an initialized UserOAuth manager instance."""
    return UserOAuthManager(requester_id=user.id)


def get_oauth_provider_manager(user: User = Depends(UserManager.auth)):
    """Get an initialized OAuthProvider manager instance."""
    return OAuthProviderManager(requester_id=user.id)


def get_oauth2_client_manager(user: User = Depends(UserManager.auth)):
    """Get an initialized OAuth2Client manager instance."""
    return OAuth2ClientManager(requester_id=user.id)


def get_oauth2_auth_code_manager(user: User = Depends(UserManager.auth)):
    """Get an initialized OAuth2AuthCode manager instance."""
    return OAuth2AuthCodeManager(requester_id=user.id)


def get_oauth2_token_manager(user: User = Depends(UserManager.auth)):
    """Get an initialized OAuth2Token manager instance."""
    return OAuth2TokenManager(requester_id=user.id)


def get_oauth_external_scope_manager(user: User = Depends(UserManager.auth)):
    """Get an initialized OAuthExternalScope manager instance."""
    return OAuthExternalScopeManager(requester_id=user.id)


# Create custom examples for documentation
user_oauth_examples = {
    "get": {
        "user_oauth": ExampleGenerator.generate_example_for_model(
            UserOAuthManager.NetworkModel.ResponseSingle.model_fields[
                "user_oauth"
            ].annotation
        )
    },
    "create": {
        "user_oauth": ExampleGenerator.generate_example_for_model(
            UserOAuthManager.NetworkModel.POST.model_fields["user_oauth"].annotation
        )
    },
    "list": {
        "user_oauths": [
            ExampleGenerator.generate_example_for_model(UserOAuthManager.Model)
        ]
    },
}

# Customize examples to make them more realistic
user_oauth_examples["get"]["user_oauth"][
    "provider_id"
] = "d8f9e25c-5f78-4b41-8c24-b1e86a2a3d71"
user_oauth_examples["get"]["user_oauth"]["provider_user_id"] = "109876543210987654321"
user_oauth_examples["get"]["user_oauth"]["account_name"] = "user@example.com"
user_oauth_examples["get"]["user_oauth"]["account_email"] = "user@example.com"
user_oauth_examples["get"]["user_oauth"][
    "access_token"
] = "ya29.a0AbVbY1Xrqazywkl34moprstuvwxyz123456789"
user_oauth_examples["get"]["user_oauth"][
    "refresh_token"
] = "1//0aAbCdEfGh987654321abcdefghijklmnopqrstuvwxyz"

oauth_provider_examples = {
    "get": {
        "oauth_provider": ExampleGenerator.generate_example_for_model(
            OAuthProviderManager.NetworkModel.ResponseSingle.model_fields[
                "oauth_provider"
            ].annotation
        )
    },
    "create": {
        "oauth_provider": ExampleGenerator.generate_example_for_model(
            OAuthProviderManager.NetworkModel.POST.model_fields[
                "oauth_provider"
            ].annotation
        )
    },
}

# Customize examples
oauth_provider_examples["get"]["oauth_provider"]["name"] = "google"
oauth_provider_examples["get"]["oauth_provider"][
    "client_id"
] = "123456789012-abcdefghijklmnopqrstuvwxyz123456.apps.googleusercontent.com"
oauth_provider_examples["get"]["oauth_provider"][
    "auth_url"
] = "https://accounts.google.com/o/oauth2/auth"
oauth_provider_examples["get"]["oauth_provider"][
    "token_url"
] = "https://oauth2.googleapis.com/token"
oauth_provider_examples["get"]["oauth_provider"][
    "userinfo_url"
] = "https://www.googleapis.com/oauth2/v3/userinfo"

oauth2_client_examples = {
    "get": {
        "oauth2_client": ExampleGenerator.generate_example_for_model(
            OAuth2ClientManager.NetworkModel.ResponseSingle.model_fields[
                "oauth2_client"
            ].annotation
        )
    },
    "create": {
        "oauth2_client": ExampleGenerator.generate_example_for_model(
            OAuth2ClientManager.NetworkModel.POST.model_fields[
                "oauth2_client"
            ].annotation
        )
    },
}

# Customize examples
oauth2_client_examples["get"]["oauth2_client"]["name"] = "Example Client Application"
oauth2_client_examples["get"]["oauth2_client"]["client_id"] = "client_123456789"
oauth2_client_examples["get"]["oauth2_client"][
    "redirect_uris"
] = '["https://app.example.com/callback"]'
oauth2_client_examples["get"]["oauth2_client"]["allowed_scopes"] = "profile email"

# Create routers using AbstractEPRouter for standard CRUD endpoints
user_oauth_router = AbstractEPRouter(
    prefix="/v1/oauth",
    tags=["OAuth"],
    manager_factory=get_user_oauth_manager,
    network_model_cls=UserOAuthManager.NetworkModel,
    example_overrides=user_oauth_examples,
)

oauth_provider_router = AbstractEPRouter(
    prefix="/provider",
    tags=["OAuth"],
    manager_factory=get_oauth_provider_manager,
    network_model_cls=OAuthProviderManager.NetworkModel,
    example_overrides=oauth_provider_examples,
)

oauth2_client_router = AbstractEPRouter(
    prefix="/client",
    tags=["OAuth"],
    manager_factory=get_oauth2_client_manager,
    network_model_cls=OAuth2ClientManager.NetworkModel,
    example_overrides=oauth2_client_examples,
)

oauth2_auth_code_router = AbstractEPRouter(
    prefix="/auth-code",
    tags=["OAuth"],
    manager_factory=get_oauth2_auth_code_manager,
    network_model_cls=OAuth2AuthCodeManager.NetworkModel,
)

oauth2_token_router = AbstractEPRouter(
    prefix="/token",
    tags=["OAuth"],
    manager_factory=get_oauth2_token_manager,
    network_model_cls=OAuth2TokenManager.NetworkModel,
)

oauth_external_scope_router = AbstractEPRouter(
    prefix="/external-scope",
    tags=["OAuth"],
    manager_factory=get_oauth_external_scope_manager,
    network_model_cls=OAuthExternalScopeManager.NetworkModel,
)


# Add custom routes for OAuth connections
@user_oauth_router.post(
    "/connect/{provider}",
    summary="Connect OAuth provider",
    description="""
    Connects an OAuth provider to the user's account.

    This endpoint allows users to authenticate with an external OAuth provider
    and connect it to their account. For new users, this can also create an account.

    ## Path Parameters
    - `provider`: OAuth provider name (e.g., google, microsoft, github)

    ## Request Body
    - `code`: Authorization code from OAuth flow
    - `redirect_uri`: Optional redirect URI
    - `invitation_id`: Optional invitation ID for new users

    ## Response
    Returns connection status and authenticated user details.
    """,
    response_model=dict,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_200_OK: {
            "description": "OAuth connection established successfully",
            "content": {
                "application/json": {
                    "example": {
                        "message": "Account connected with google",
                        "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                        "email": "user@example.com",
                        "is_new_user": False,
                    }
                }
            },
        },
        status.HTTP_400_BAD_REQUEST: {
            "description": "Invalid OAuth request or authorization code"
        },
        status.HTTP_409_CONFLICT: {
            "description": "OAuth account already connected to another user"
        },
    },
)
async def connect_oauth(
    provider: str = Path(
        ...,
        description="OAuth provider name",
        examples=["google", "microsoft", "github"],
    ),
    code: str = Body(..., embed=True, description="Authorization code from OAuth flow"),
    redirect_uri: str = Body(None, embed=True, description="Redirect URI"),
    invitation_id: str = Body(
        None, embed=True, description="Invitation ID for new users"
    ),
    manager=Depends(get_user_oauth_manager),
):
    """Connect an OAuth provider to a user account"""
    return manager.connect_oauth(
        provider_name=provider,
        code=code,
        redirect_uri=redirect_uri,
        invitation_id=invitation_id,
    )


@user_oauth_router.post(
    "/refresh/{provider}",
    summary="Refresh OAuth token",
    description="""
    Refreshes the access token for an OAuth provider.

    This endpoint attempts to refresh the access token using the
    stored refresh token. Not all providers support token refresh.

    ## Path Parameters
    - `provider`: OAuth provider name (e.g., google, microsoft)

    ## Response
    Returns updated token information including the new access token and expiration time.
    """,
    response_model=dict,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_200_OK: {
            "description": "Token refreshed successfully",
            "content": {
                "application/json": {
                    "example": {
                        "message": "Token refreshed successfully",
                        "access_token": "ya29.a0AbVbY1Xrqazywkl34moprstuvwxyz123456789",
                        "expires_at": "2025-04-01T12:00:00.000Z",
                    }
                }
            },
        },
        status.HTTP_400_BAD_REQUEST: {
            "description": "Invalid refresh token or provider doesn't support token refresh"
        },
        status.HTTP_404_NOT_FOUND: {
            "description": "OAuth provider connection not found"
        },
    },
)
async def refresh_oauth_token(
    provider: str = Path(
        ..., description="OAuth provider name", examples=["google", "microsoft"]
    ),
    manager=Depends(get_user_oauth_manager),
):
    """Refresh an OAuth access token"""
    return manager.refresh_oauth_token(provider_name=provider)


@user_oauth_router.delete(
    "/disconnect/{provider}",
    summary="Disconnect OAuth provider",
    description="""
    Disconnects an OAuth provider from the user's account.

    This endpoint allows authenticated users to remove the connection
    to an OAuth provider from their account. Users must have at least
    one authentication method remaining.

    ## Path Parameters
    - `provider`: OAuth provider name (e.g., google, microsoft, github)

    ## Response
    Returns a success message upon successful disconnection.
    """,
    response_model=MessageModel,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_200_OK: {
            "description": "OAuth provider disconnected successfully",
            "content": {
                "application/json": {"example": {"message": "Disconnected from google"}}
            },
        },
        status.HTTP_400_BAD_REQUEST: {
            "description": "Cannot disconnect the only authentication method"
        },
        status.HTTP_404_NOT_FOUND: {
            "description": "OAuth provider connection not found"
        },
    },
)
async def disconnect_oauth(
    provider: str = Path(
        ...,
        description="OAuth provider name",
        examples=["google", "microsoft", "github"],
    ),
    manager=Depends(get_user_oauth_manager),
):
    """Disconnect an OAuth provider from user account"""
    result = manager.disconnect_oauth(provider_name=provider)
    return MessageModel(message=result["message"])


@oauth_provider_router.get(
    "/available",
    summary="List available OAuth providers",
    description="""
    Returns a list of available OAuth providers that users can connect to.

    This endpoint provides information about which OAuth providers are
    supported by the system, including any configuration details needed
    for the OAuth flow.

    ## Response
    Returns a list of provider names and their configurations.
    """,
    response_model=list,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_200_OK: {
            "description": "List of available OAuth providers",
            "content": {
                "application/json": {
                    "example": [
                        {
                            "name": "google",
                            "display_name": "Google",
                            "auth_url": "https://accounts.google.com/o/oauth2/auth",
                            "scopes": ["profile", "email"],
                        },
                        {
                            "name": "microsoft",
                            "display_name": "Microsoft",
                            "auth_url": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
                            "scopes": ["profile", "email", "User.Read"],
                        },
                    ]
                }
            },
        }
    },
)
async def list_available_providers(
    manager=Depends(get_oauth_provider_manager),
):
    """List available OAuth providers"""
    providers = manager.search()
    result = []

    for provider in providers:
        # Get available scopes for this provider
        scopes_manager = OAuthExternalScopeManager(
            requester_id=manager.requester.id, db=manager.db
        )
        scopes = scopes_manager.search(provider_id=provider.id)

        result.append(
            {
                "name": provider.name,
                "display_name": provider.name.title(),
                "auth_url": provider.auth_url,
                "scopes": [scope.scope_name for scope in scopes],
            }
        )

    return result


# OAuth2 Authorization Server routes
@oauth2_client_router.post(
    "/authorize",
    summary="OAuth 2.0 Authorization Endpoint",
    description="""
    OAuth 2.0 Authorization endpoint to obtain authorization code.

    This endpoint implements the authorization code flow for OAuth 2.0.
    It validates the client's request and generates an authorization code
    that can be exchanged for an access token.

    ## Query Parameters
    - `client_id`: OAuth 2.0 client ID
    - `redirect_uri`: URI to redirect to after authorization
    - `response_type`: Must be "code"
    - `scope`: Space-delimited list of requested scopes
    - `state`: Optional client state for CSRF protection

    ## Response
    Redirects to the redirect_uri with an authorization code.
    """,
    status_code=status.HTTP_302_FOUND,
    responses={
        status.HTTP_302_FOUND: {
            "description": "Redirect to client with authorization code"
        },
        status.HTTP_400_BAD_REQUEST: {"description": "Invalid request parameters"},
        status.HTTP_401_UNAUTHORIZED: {"description": "User authentication required"},
    },
)
async def authorize(
    client_id: str = Query(..., description="OAuth 2.0 client ID"),
    redirect_uri: str = Query(..., description="Redirect URI"),
    response_type: str = Query(..., description="Response type (must be 'code')"),
    scope: str = Query(..., description="Space-delimited list of requested scopes"),
    state: str = Query(None, description="Optional client state for CSRF protection"),
    manager=Depends(get_oauth2_auth_code_manager),
    user=Depends(UserManager.auth),
):
    """OAuth 2.0 Authorization endpoint"""
    if response_type != "code":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid response_type. Must be 'code'.",
        )

    # Validate client and redirect URI
    clients = OAuth2ClientManager(requester_id=user.id, db=manager.db)
    client_list = clients.search(client_id=client_id)

    if not client_list:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid client_id"
        )

    client = client_list[0]

    # Create authorization code
    import json

    redirect_uris = json.loads(client.redirect_uris)

    if redirect_uri not in redirect_uris:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid redirect_uri"
        )

    # Create auth code
    import secrets
    from datetime import datetime, timedelta

    auth_code = secrets.token_urlsafe(32)
    expires_at = datetime.utcnow() + timedelta(minutes=10)

    manager.create(
        user_id=user.id,
        client_id=client.id,
        code=auth_code,
        redirect_uri=redirect_uri,
        scopes=scope,
        expires_at=expires_at,
        is_used=False,
    )

    # Build redirect URL
    from urllib.parse import urlencode

    params = {"code": auth_code}

    if state:
        params["state"] = state

    redirect_url = f"{redirect_uri}?{urlencode(params)}"

    from fastapi.responses import RedirectResponse

    return RedirectResponse(url=redirect_url)


@oauth2_client_router.post(
    "/token",
    summary="OAuth 2.0 Token Endpoint",
    description="""
    OAuth 2.0 Token endpoint to obtain access tokens.

    This endpoint implements the token exchange flow for OAuth 2.0.
    It validates the client's request and issues access tokens.

    ## Request Body
    - `grant_type`: Must be "authorization_code" or "refresh_token"
    - `code`: Required for "authorization_code" grant type
    - `refresh_token`: Required for "refresh_token" grant type
    - `redirect_uri`: Required for "authorization_code" grant type
    - `client_id`: OAuth 2.0 client ID
    - `client_secret`: Required for confidential clients

    ## Response
    Returns an access token, token type, expiration, and optionally a refresh token.
    """,
    response_model=dict,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_200_OK: {
            "description": "Access token issued successfully",
            "content": {
                "application/json": {
                    "example": {
                        "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                        "token_type": "bearer",
                        "expires_in": 3600,
                        "refresh_token": "abcdefghijklmnopqrstuvwxyz123456789",
                        "scope": "profile email",
                    }
                }
            },
        },
        status.HTTP_400_BAD_REQUEST: {"description": "Invalid request parameters"},
        status.HTTP_401_UNAUTHORIZED: {"description": "Invalid client credentials"},
    },
)
async def token(
    grant_type: str = Body(..., description="Grant type"),
    code: str = Body(None, description="Authorization code"),
    refresh_token: str = Body(None, description="Refresh token"),
    redirect_uri: str = Body(None, description="Redirect URI"),
    client_id: str = Body(..., description="OAuth 2.0 client ID"),
    client_secret: str = Body(None, description="Client secret"),
    manager=Depends(get_oauth2_token_manager),
):
    """OAuth 2.0 Token endpoint"""
    # Validate client
    clients_manager = OAuth2ClientManager(
        requester_id=manager.requester.id, db=manager.db
    )
    client_list = clients_manager.search(client_id=client_id)

    if not client_list:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid client_id"
        )

    client = client_list[0]

    # Validate client secret for confidential clients
    if client.is_confidential and (
        not client_secret or client_secret != client.client_secret
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid client credentials",
        )

    # Process based on grant type
    if grant_type == "authorization_code":
        if not code or not redirect_uri:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Missing required parameters",
            )

        # Validate auth code
        auth_codes_manager = OAuth2AuthCodeManager(
            requester_id=manager.requester.id, db=manager.db
        )
        auth_code_list = auth_codes_manager.search(code=code, client_id=client.id)

        if not auth_code_list or auth_code_list[0].is_used:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid authorization code",
            )

        auth_code = auth_code_list[0]

        # Check if code is expired
        from datetime import datetime

        if auth_code.expires_at < datetime.utcnow():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Authorization code expired",
            )

        # Mark code as used
        auth_codes_manager.update(id=auth_code.id, is_used=True)

        # Create tokens
        import secrets
        from datetime import datetime, timedelta

        access_token = secrets.token_urlsafe(32)
        expires_at = datetime.utcnow() + timedelta(hours=1)
        refresh_token_value = secrets.token_urlsafe(32)

        # Create access token
        access_token_record = manager.create(
            user_id=auth_code.user_id,
            client_id=client.id,
            token_type="access",
            token=access_token,
            scopes=auth_code.scopes,
            expires_at=expires_at,
            is_revoked=False,
        )

        # Create refresh token
        refresh_token_record = manager.create(
            user_id=auth_code.user_id,
            client_id=client.id,
            token_type="refresh",
            token=refresh_token_value,
            scopes=auth_code.scopes,
            expires_at=datetime.utcnow() + timedelta(days=30),
            is_revoked=False,
            parent_id=access_token_record.id,
        )

        return {
            "access_token": access_token,
            "token_type": "bearer",
            "expires_in": 3600,  # 1 hour in seconds
            "refresh_token": refresh_token_value,
            "scope": auth_code.scopes,
        }

    elif grant_type == "refresh_token":
        if not refresh_token:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Missing refresh token"
            )

        # Find existing refresh token
        refresh_tokens = manager.search(
            token=refresh_token, token_type="refresh", client_id=client.id
        )

        if not refresh_tokens or refresh_tokens[0].is_revoked:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid refresh token"
            )

        refresh_token_record = refresh_tokens[0]

        # Check if token is expired
        from datetime import datetime

        if refresh_token_record.expires_at < datetime.utcnow():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Refresh token expired"
            )

        # Create new access token
        import secrets
        from datetime import datetime, timedelta

        access_token = secrets.token_urlsafe(32)
        expires_at = datetime.utcnow() + timedelta(hours=1)

        access_token_record = manager.create(
            user_id=refresh_token_record.user_id,
            client_id=client.id,
            token_type="access",
            token=access_token,
            scopes=refresh_token_record.scopes,
            expires_at=expires_at,
            is_revoked=False,
        )

        return {
            "access_token": access_token,
            "token_type": "bearer",
            "expires_in": 3600,  # 1 hour in seconds
            "scope": refresh_token_record.scopes,
        }

    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported grant type: {grant_type}",
        )


# Export the router
router = user_oauth_router

# Include all other OAuth-related routers
router.include_router(oauth_provider_router)
router.include_router(oauth2_client_router)
router.include_router(oauth2_auth_code_router)
router.include_router(oauth2_token_router)
router.include_router(oauth_external_scope_router)
