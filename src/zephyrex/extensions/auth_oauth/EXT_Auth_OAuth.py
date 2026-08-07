import json
import secrets
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urlencode, urlparse

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension, ability

# from extensions.auth_oauth.DB_Auth_OAuth import (
#     OAuth2AuthCode,
#     OAuth2Client,
#     OAuth2Token,
#     OAuthExternalScope,
#     UserOAuth,
# )
from zephyrex.lib.Dependencies import EXT_Dependency, PIP_Dependency
from zephyrex.lib.Environment import env
from zephyrex.lib.Logging import logger


class EXT_Auth_OAuth(AbstractStaticExtension):
    """
    OAuth authentication extension for AGInfrastructure.

    Provides comprehensive OAuth 2.0 authentication capabilities including client
    registration, authorization code flow, token management, and third-party provider
    integration. This extension provides static functionality and metadata to organize
    OAuth-related components and manage authentication workflows.

    The extension focuses on:
    - OAuth 2.0 authorization code flow implementation
    - Client registration and management
    - Token generation, validation, and refresh
    - Third-party OAuth provider integration (Google, GitHub, etc.)
    - Scope-based access control
    - PKCE (Proof Key for Code Exchange) support
    - Security and compliance features

    Component loading (DB, BLL, EP) is handled automatically by the import system
    based on file naming conventions.
    """

    # Extension metadata
    name = "auth_oauth"
    version = "1.0.0"
    description = (
        "OAuth authentication extension for OAuth 2.0 authorization flows, "
        "client management, and third-party provider integration"
    )

    # Define dependencies
    ext_dependencies = [
        EXT_Dependency(
            name="core",
            friendly_name="Core Extension",
            optional=False,
            reason="Required for base authentication functionality and database operations",
        ),
        EXT_Dependency(
            name="auth",
            friendly_name="Authentication Extension",
            optional=False,
            reason="Required for integration with user authentication system",
        ),
        EXT_Dependency(
            name="auth_api_keys",
            friendly_name="API Keys Extension",
            optional=True,
            reason="Integration with API key authentication for OAuth client authentication",
        ),
    ]

    pip_dependencies = [
        PIP_Dependency(
            name="requests",
            friendly_name="HTTP Requests Library",
            optional=False,
            reason="Required for OAuth provider communication and token exchange",
            semver=">=2.31.0",
        ),
        PIP_Dependency(
            name="cryptography",
            friendly_name="Cryptography Library",
            optional=False,
            reason="Required for secure token generation and PKCE implementation",
            semver=">=41.0.0",
        ),
        PIP_Dependency(
            name="pyjwt",
            friendly_name="PyJWT Library",
            optional=True,
            reason="JWT token support for OAuth providers that use JWT",
            semver=">=2.8.0",
        ),
        PIP_Dependency(
            name="redis",
            friendly_name="Redis Client",
            optional=True,
            reason="Session management and temporary storage for OAuth flows",
            semver=">=4.5.0",
        ),
    ]

    sys_dependencies = []  # type: ignore[var-annotated]

    # Define database tables
    # db_tables = [OAuth2Client, OAuth2AuthCode, OAuth2Token, UserOAuth, OAuthExternalScope]
    db_tables = []  # type: ignore[var-annotated]

    # Define what capabilities this extension provides
    capabilities = [
        "oauth_authorization",
        "oauth_token_exchange",
        "oauth_client_management",
        "oauth_provider_integration",
        "scope_validation",
        "pkce_support",
        "token_refresh",
    ]

    # OAuth 2.0 grant types
    GRANT_TYPES = {
        "authorization_code": "Authorization Code Grant",
        "client_credentials": "Client Credentials Grant",
        "refresh_token": "Refresh Token Grant",
        "device_code": "Device Authorization Grant",
    }

    # OAuth 2.0 response types
    RESPONSE_TYPES = {
        "code": "Authorization Code",
        "token": "Implicit Grant (deprecated)",
    }

    # Supported OAuth providers
    OAUTH_PROVIDERS = {
        "google": {
            "name": "Google",
            "auth_url": "https://accounts.google.com/o/oauth2/v2/auth",
            "token_url": "https://oauth2.googleapis.com/token",
            "userinfo_url": "https://www.googleapis.com/oauth2/v2/userinfo",
            "scopes": ["openid", "email", "profile"],
        },
        "github": {
            "name": "GitHub",
            "auth_url": "https://github.com/login/oauth/authorize",
            "token_url": "https://github.com/login/oauth/access_token",
            "userinfo_url": "https://api.github.com/user",
            "scopes": ["user:email", "read:user"],
        },
        "microsoft": {
            "name": "Microsoft",
            "auth_url": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
            "token_url": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
            "userinfo_url": "https://graph.microsoft.com/v1.0/me",
            "scopes": ["openid", "email", "profile"],
        },
    }

    def __init__(
        self,
        default_providers: List[str] | None = None,
        oauth_callback_url: str = "",
        enable_state_validation: bool = True,
        session_timeout_minutes: int = 30,
        enable_pkce: bool = True,
        max_concurrent_flows: int = 100,
        cleanup_interval_minutes: int = 5,
        enable_jwt_tokens: bool = False,
        jwt_secret_key: Optional[str] | None = None,
        default_token_expiry_minutes: int = 60,
        authorization_code_expiry_minutes: int = 10,
        **kwargs,
    ):
        super().__init__(**kwargs)

        # Store configuration
        self.default_providers = default_providers or ["google", "github", "microsoft"]
        self.oauth_callback_url = oauth_callback_url
        self.enable_state_validation = enable_state_validation
        self.session_timeout_minutes = session_timeout_minutes
        self.enable_pkce = enable_pkce
        self.max_concurrent_flows = max_concurrent_flows
        self.cleanup_interval_minutes = cleanup_interval_minutes
        self.enable_jwt_tokens = enable_jwt_tokens
        self.jwt_secret_key = jwt_secret_key
        self.default_token_expiry_minutes = default_token_expiry_minutes
        self.authorization_code_expiry_minutes = authorization_code_expiry_minutes
        self.require_state_parameter = enable_state_validation

        # Provider instance reference
        self.provider = None
        self.oauth_clients = {}  # type: ignore[var-annotated]
        self.registered_clients = {}  # type: ignore[var-annotated]
        self.oauth_sessions = {}  # type: ignore[var-annotated]
        self.active_flows = {}  # type: ignore[var-annotated]
        self.session_store = {}  # type: ignore[var-annotated]
        self.active_tokens = {}  # type: ignore[var-annotated]
        self.active_auth_codes = {}  # type: ignore[var-annotated]
        self.max_clients_per_user = kwargs.get("max_clients_per_user", 10)
        self.redis_client = None

    def on_initialize(self) -> bool:
        """
        Initialize the OAuth extension.
        """
        logger.debug("Initializing OAuth Extension...")

        try:
            # Create provider instance
            self._create_provider()

            # Initialize OAuth clients for configured providers
            self._initialize_oauth_clients()

            # Register OAuth hooks
            self._register_oauth_hooks()

            # Schedule cleanup tasks
            self._schedule_cleanup_tasks()

            # Register capabilities
            for capability in self.capabilities:
                self.register_capability(capability)

            logger.debug("OAuth extension initialized successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to initialize OAuth extension: {str(e)}")
            return False

    def _create_provider(self):
        """
        Create and configure the OAuth provider instance.
        """
        try:
            self.provider = {
                "type": "auth_oauth",
                "oauth_clients": self.oauth_clients,
                "active_flows": self.active_flows,
                "session_store": self.session_store,
                "supported_providers": self.default_providers,
            }

            logger.debug("OAuth provider created successfully")

        except Exception as e:
            logger.error(f"Failed to create OAuth provider: {str(e)}")
            self.provider = None

    def _initialize_oauth_clients(self):
        """Initialize OAuth clients for configured providers."""
        for provider_name in self.default_providers:
            if provider_name in self.OAUTH_PROVIDERS:
                self.oauth_clients[provider_name] = self.OAUTH_PROVIDERS[provider_name]
                self.oauth_clients[provider_name]["enabled"] = True
                logger.debug(f"OAuth client for {provider_name} configured")
            else:
                logger.warning(f"OAuth provider {provider_name} not supported")

    def _register_oauth_hooks(self):
        """Register hooks for OAuth-related operations."""
        try:

            @AbstractExtension.bll_hook("Auth", "UserManager", "login", "after")
            def oauth_login_hook(manager, result, create_args=None):
                """Hook to handle OAuth-based logins."""
                try:
                    # Check if this is an OAuth login
                    if create_args and hasattr(create_args, "oauth_provider"):
                        self._handle_oauth_login_success(result, create_args)
                        logger.debug("OAuth login hook executed")
                except Exception as e:
                    logger.error(f"Error in OAuth login hook: {e}")

            @AbstractExtension.bll_hook("Auth", "UserManager", "create", "after")
            def oauth_user_creation_hook(manager, result, create_args=None):
                """Hook to handle OAuth user creation."""
                try:
                    if create_args and hasattr(create_args, "oauth_data"):
                        self._handle_oauth_user_creation(result, create_args)
                        logger.debug("OAuth user creation hook executed")
                except Exception as e:
                    logger.error(f"Error in OAuth user creation hook: {e}")

        except Exception as e:
            logger.error(f"Error registering OAuth hooks: {e}")

    def _schedule_cleanup_tasks(self):
        """Schedule cleanup tasks for expired sessions and active flows."""

    def _initialize_oauth_providers(self):
        """Initialize OAuth provider configurations."""
        for provider_name, config in self.OAUTH_PROVIDERS.items():
            # Check for environment-specific configuration
            client_id_key = f"{provider_name.upper()}_CLIENT_ID"
            client_secret_key = f"{provider_name.upper()}_CLIENT_SECRET"

            client_id = env(client_id_key)
            client_secret = env(client_secret_key)

            if client_id and client_secret:
                config["client_id"] = client_id
                config["client_secret"] = client_secret
                config["enabled"] = True
                logger.debug(f"OAuth provider {provider_name} configured")
            else:
                config["enabled"] = False
                logger.debug(f"OAuth provider {provider_name} not configured")

    def register_capability(self, capability: str):
        """Register a new capability."""
        if capability not in self.capabilities:
            self.capabilities.append(capability)

    def get_registered_capabilities(self) -> Set[str]:
        """Return currently registered capabilities."""
        return set(self.capabilities)

    def get_capabilities(self) -> Set[str]:
        """Return the capabilities this extension provides."""
        return set(self.capabilities)

    @ability("register_oauth_client")
    async def register_oauth_client(
        self,
        client_name: str,
        redirect_uris: List[str],
        scopes: List[str],
        user_id: Optional[str] | None = None,
        team_id: Optional[str] | None = None,
        grant_types: Optional[List[str]] | None = None,
    ) -> Dict[str, Any]:
        """
        Register a new OAuth 2.0 client.
        """
        try:
            # Validate redirect URIs
            for uri in redirect_uris:
                if not self._validate_redirect_uri(uri):
                    return {"success": False, "message": f"Invalid redirect URI: {uri}"}

            # Check client limits
            if user_id:
                user_client_count = sum(
                    1
                    for client in self.registered_clients.values()
                    if client.get("user_id") == user_id
                )
                if user_client_count >= self.max_clients_per_user:
                    return {
                        "success": False,
                        "message": "Maximum number of clients reached",
                    }

            # Generate client credentials
            client_id = self._generate_client_id()
            client_secret = self._generate_client_secret()

            # Create client record
            client_record = {
                "client_id": client_id,
                "client_secret": client_secret,
                "client_name": client_name,
                "redirect_uris": redirect_uris,
                "scopes": scopes,
                "grant_types": grant_types or ["authorization_code", "refresh_token"],
                "user_id": user_id,
                "team_id": team_id,
                "created_at": datetime.utcnow().isoformat(),
                "is_active": True,
            }

            # Store client
            self.registered_clients[client_id] = client_record

            logger.debug(f"Registered OAuth client: {client_name} ({client_id})")
            return {
                "success": True,
                "client_id": client_id,
                "client_secret": client_secret,
                "client_record": {
                    k: v for k, v in client_record.items() if k != "client_secret"
                },
            }

        except Exception as e:
            logger.error(f"Error registering OAuth client: {e}")
            return {"success": False, "message": f"Failed to register client: {str(e)}"}

    @ability("create_authorization_url")
    async def create_authorization_url(
        self,
        client_id: str,
        redirect_uri: str,
        scopes: List[str],
        state: Optional[str] | None = None,
        code_challenge: Optional[str] | None = None,
        code_challenge_method: Optional[str] | None = None,
    ) -> Dict[str, Any]:
        """
        Create an OAuth 2.0 authorization URL.
        """
        try:
            # Validate client
            client = self.registered_clients.get(client_id)
            if not client or not client.get("is_active"):
                return {"success": False, "message": "Invalid or inactive client"}

            # Validate redirect URI
            if redirect_uri not in client["redirect_uris"]:
                return {"success": False, "message": "Invalid redirect URI"}

            # Validate scopes
            invalid_scopes = [
                scope for scope in scopes if scope not in client["scopes"]
            ]
            if invalid_scopes:
                return {
                    "success": False,
                    "message": f"Invalid scopes: {', '.join(invalid_scopes)}",
                }

            # Generate state if required
            if self.require_state_parameter and not state:
                state = secrets.token_urlsafe(32)

            # Validate PKCE if enabled
            if self.enable_pkce:
                if not code_challenge:
                    return {"success": False, "message": "PKCE code challenge required"}
                if code_challenge_method not in ["S256", "plain"]:
                    return {
                        "success": False,
                        "message": "Invalid PKCE challenge method",
                    }

            # Create authorization session
            session_id = secrets.token_urlsafe(32)
            session_data = {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "scopes": scopes,
                "state": state,
                "code_challenge": code_challenge,
                "code_challenge_method": code_challenge_method,
                "created_at": datetime.utcnow().isoformat(),
                "expires_at": (
                    datetime.utcnow()
                    + timedelta(minutes=self.authorization_code_expiry_minutes)
                ).isoformat(),
            }

            self.oauth_sessions[session_id] = session_data

            # Build authorization URL
            auth_params = {
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "scope": " ".join(scopes),
                "session_id": session_id,
            }

            if state:
                auth_params["state"] = state

            if code_challenge:
                auth_params["code_challenge"] = code_challenge
                auth_params["code_challenge_method"] = code_challenge_method  # type: ignore[assignment]

            base_url = env("SERVER_URI", "http://localhost:8000")
            authorization_url = f"{base_url}/oauth/authorize?" + urlencode(auth_params)

            logger.debug(f"Created authorization URL for client {client_id}")
            return {
                "success": True,
                "authorization_url": authorization_url,
                "session_id": session_id,
                "state": state,
                "expires_in": self.authorization_code_expiry_minutes * 60,
            }

        except Exception as e:
            logger.error(f"Error creating authorization URL: {e}")
            return {
                "success": False,
                "message": f"Failed to create authorization URL: {str(e)}",
            }

    @ability("exchange_authorization_code")
    async def exchange_authorization_code(
        self,
        client_id: str,
        client_secret: str,
        authorization_code: str,
        redirect_uri: str,
        code_verifier: Optional[str] | None = None,
    ) -> Dict[str, Any]:
        """
        Exchange authorization code for access token.
        """
        try:
            # Validate client credentials
            client = self.registered_clients.get(client_id)
            if not client or client.get("client_secret") != client_secret:
                return {"success": False, "message": "Invalid client credentials"}

            # Get authorization code
            auth_code_data = self.active_auth_codes.get(authorization_code)
            if not auth_code_data:
                return {"success": False, "message": "Invalid authorization code"}

            # Check expiration
            expires_at = datetime.fromisoformat(auth_code_data["expires_at"])
            if datetime.utcnow() > expires_at:
                del self.active_auth_codes[authorization_code]
                return {"success": False, "message": "Authorization code expired"}

            # Validate redirect URI
            if auth_code_data["redirect_uri"] != redirect_uri:
                return {"success": False, "message": "Invalid redirect URI"}

            # Validate PKCE if required
            if self.enable_pkce and auth_code_data.get("code_challenge"):
                if not code_verifier:
                    return {"success": False, "message": "PKCE code verifier required"}

                if not self._verify_pkce_challenge(
                    code_verifier,
                    auth_code_data["code_challenge"],
                    auth_code_data.get("code_challenge_method", "S256"),
                ):
                    return {"success": False, "message": "Invalid PKCE code verifier"}

            # Generate tokens
            access_token = self._generate_access_token(auth_code_data)
            refresh_token = self._generate_refresh_token(auth_code_data)

            # Create token record
            token_record = {
                "access_token": access_token,
                "refresh_token": refresh_token,
                "token_type": "Bearer",
                "expires_in": self.default_token_expiry_minutes * 60,
                "scopes": auth_code_data["scopes"],
                "user_id": auth_code_data.get("user_id"),
                "client_id": client_id,
                "created_at": datetime.utcnow().isoformat(),
                "expires_at": (
                    datetime.utcnow()
                    + timedelta(minutes=self.default_token_expiry_minutes)
                ).isoformat(),
            }

            # Store token
            self.active_tokens[access_token] = token_record

            # Remove used authorization code
            del self.active_auth_codes[authorization_code]

            logger.debug(f"Exchanged authorization code for token: {client_id}")
            return {
                "success": True,
                "access_token": access_token,
                "refresh_token": refresh_token,
                "token_type": "Bearer",
                "expires_in": self.default_token_expiry_minutes * 60,
                "scope": " ".join(auth_code_data["scopes"]),
            }

        except Exception as e:
            logger.error(f"Error exchanging authorization code: {e}")
            return {"success": False, "message": f"Token exchange failed: {str(e)}"}

    @ability("validate_access_token")
    async def validate_access_token(
        self,
        access_token: str,
        required_scopes: Optional[List[str]] | None = None,
    ) -> Dict[str, Any]:
        """
        Validate an OAuth access token.
        """
        try:
            # Get token data
            token_data = self.active_tokens.get(access_token)
            if not token_data:
                return {"success": False, "message": "Invalid access token"}

            # Check expiration
            expires_at = datetime.fromisoformat(token_data["expires_at"])
            if datetime.utcnow() > expires_at:
                del self.active_tokens[access_token]
                return {"success": False, "message": "Access token expired"}

            # Check scopes
            if required_scopes:
                token_scopes = token_data.get("scopes", [])
                missing_scopes = [
                    scope for scope in required_scopes if scope not in token_scopes
                ]
                if missing_scopes:
                    return {
                        "success": False,
                        "message": f"Missing required scopes: {', '.join(missing_scopes)}",
                    }

            # Update last used
            token_data["last_used_at"] = datetime.utcnow().isoformat()

            return {
                "success": True,
                "token_data": {
                    k: v
                    for k, v in token_data.items()
                    if k not in ["access_token", "refresh_token"]
                },
                "user_id": token_data.get("user_id"),
                "client_id": token_data.get("client_id"),
                "scopes": token_data.get("scopes", []),
            }

        except Exception as e:
            logger.error(f"Error validating access token: {e}")
            return {"success": False, "message": f"Token validation failed: {str(e)}"}

    @ability("refresh_access_token")
    async def refresh_access_token(
        self,
        refresh_token: str,
        client_id: str,
        client_secret: str,
    ) -> Dict[str, Any]:
        """
        Refresh an OAuth access token using refresh token.
        """
        try:
            # Validate client credentials
            client = self.registered_clients.get(client_id)
            if not client or client.get("client_secret") != client_secret:
                return {"success": False, "message": "Invalid client credentials"}

            # Find token by refresh token
            token_data = None
            access_token_key = None
            for key, data in self.active_tokens.items():
                if data.get("refresh_token") == refresh_token:
                    token_data = data
                    access_token_key = key
                    break

            if not token_data:
                return {"success": False, "message": "Invalid refresh token"}

            # Generate new tokens
            new_access_token = self._generate_access_token(token_data)
            new_refresh_token = self._generate_refresh_token(token_data)

            # Update token record
            new_token_record = token_data.copy()
            new_token_record.update(
                {
                    "access_token": new_access_token,
                    "refresh_token": new_refresh_token,
                    "created_at": datetime.utcnow().isoformat(),
                    "expires_at": (
                        datetime.utcnow()
                        + timedelta(minutes=self.default_token_expiry_minutes)
                    ).isoformat(),
                }
            )

            # Replace old token with new one
            if access_token_key:
                del self.active_tokens[access_token_key]
            self.active_tokens[new_access_token] = new_token_record

            logger.debug(f"Refreshed access token for client {client_id}")
            return {
                "success": True,
                "access_token": new_access_token,
                "refresh_token": new_refresh_token,
                "token_type": "Bearer",
                "expires_in": self.default_token_expiry_minutes * 60,
                "scope": " ".join(token_data.get("scopes", [])),
            }

        except Exception as e:
            logger.error(f"Error refreshing access token: {e}")
            return {"success": False, "message": f"Token refresh failed: {str(e)}"}

    @ability("revoke_token")
    async def revoke_token(
        self,
        token: str,
        token_type_hint: str = "access_token",
        client_id: Optional[str] | None = None,
    ) -> Dict[str, Any]:
        """
        Revoke an OAuth token.
        """
        try:
            revoked = False

            if token_type_hint == "access_token" or not token_type_hint:
                # Try to revoke as access token
                if token in self.active_tokens:
                    del self.active_tokens[token]
                    revoked = True

            if not revoked and (
                token_type_hint == "refresh_token" or not token_type_hint
            ):
                # Try to revoke as refresh token
                for key, data in list(self.active_tokens.items()):
                    if data.get("refresh_token") == token:
                        del self.active_tokens[key]
                        revoked = True
                        break

            if revoked:
                logger.debug(f"Revoked token: {token[:10]}...")
                return {"success": True, "message": "Token revoked successfully"}
            else:
                return {"success": False, "message": "Token not found"}

        except Exception as e:
            logger.error(f"Error revoking token: {e}")
            return {"success": False, "message": f"Token revocation failed: {str(e)}"}

    @ability("oauth_provider_login")
    async def oauth_provider_login(
        self,
        provider: str,
        redirect_uri: str,
        scopes: Optional[List[str]] | None = None,
        state: Optional[str] | None = None,
    ) -> Dict[str, Any]:
        """
        Initiate OAuth login with third-party provider.
        """
        try:
            provider_config = self.OAUTH_PROVIDERS.get(provider)
            if not provider_config or not provider_config.get("enabled"):
                return {
                    "success": False,
                    "message": f"OAuth provider {provider} not available",
                }

            # Use default scopes if none provided
            scopes = scopes or provider_config["scopes"]  # type: ignore[assignment]

            # Generate state for security
            if not state:
                state = secrets.token_urlsafe(32)

            # Build authorization URL
            auth_params = {
                "response_type": "code",
                "client_id": provider_config["client_id"],
                "redirect_uri": redirect_uri,
                "scope": " ".join(scopes),  # type: ignore[arg-type]
                "state": state,
            }

            authorization_url = (
                provider_config["auth_url"] + "?" + urlencode(auth_params)  # type: ignore[operator]
            )

            # Store OAuth session
            session_data = {
                "provider": provider,
                "redirect_uri": redirect_uri,
                "scopes": scopes,
                "state": state,
                "created_at": datetime.utcnow().isoformat(),
            }

            # Use Redis or memory for session storage
            if self.redis_client:
                self.redis_client.setex(
                    f"oauth_session:{state}", 600, json.dumps(session_data)
                )
            else:
                self.oauth_sessions[state] = session_data

            logger.debug(f"Created OAuth provider login URL for {provider}")
            return {
                "success": True,
                "authorization_url": authorization_url,
                "state": state,
                "provider": provider,
            }

        except Exception as e:
            logger.error(f"Error creating OAuth provider login: {e}")
            return {
                "success": False,
                "message": f"OAuth provider login failed: {str(e)}",
            }

    # Helper methods
    def _validate_redirect_uri(self, uri: str) -> bool:
        """Validate redirect URI format."""
        try:
            parsed = urlparse(uri)
            return bool(parsed.scheme and parsed.netloc)
        except Exception:
            return False

    def _generate_client_id(self) -> str:
        """Generate OAuth client ID."""
        return f"oauth_client_{secrets.token_urlsafe(16)}"

    def _generate_client_secret(self) -> str:
        """Generate OAuth client secret."""
        return secrets.token_urlsafe(32)

    def _generate_access_token(self, auth_data: Dict[str, Any]) -> str:
        """Generate access token."""
        if self.enable_jwt_tokens and self.jwt_secret_key:
            try:
                import jwt

                payload = {
                    "sub": auth_data.get("user_id"),
                    "client_id": auth_data.get("client_id"),
                    "scopes": auth_data.get("scopes", []),
                    "iat": datetime.utcnow().timestamp(),
                    "exp": (
                        datetime.utcnow()
                        + timedelta(minutes=self.default_token_expiry_minutes)
                    ).timestamp(),
                }

                return jwt.encode(payload, self.jwt_secret_key, algorithm="HS256")

            except ImportError:
                logger.warning("PyJWT not available - using random tokens")

        return f"oauth_access_{secrets.token_urlsafe(32)}"

    def _generate_refresh_token(self, auth_data: Dict[str, Any]) -> str:
        """Generate refresh token."""
        return f"oauth_refresh_{secrets.token_urlsafe(32)}"

    def _verify_pkce_challenge(
        self, verifier: str, challenge: str, method: str
    ) -> bool:
        """Verify PKCE challenge."""
        try:
            if method == "plain":
                return verifier == challenge
            elif method == "S256":
                import base64
                import hashlib

                digest = hashlib.sha256(verifier.encode()).digest()
                encoded = base64.urlsafe_b64encode(digest).decode().rstrip("=")
                return encoded == challenge
            else:
                return False

        except Exception:
            return False

    def _handle_oauth_login_success(self, user_data: Any, create_args: Any):
        """Handle OAuth login success (used in hooks)."""
        # Implementation for hook
        pass

    def _handle_oauth_user_creation(self, user_data: Any, create_args: Any):
        """Handle OAuth user creation (used in hooks)."""
        # Implementation for hook
        pass

    # Extension lifecycle methods
    def on_start(self) -> bool:
        """Start the OAuth extension."""
        try:
            # Reconnect to Redis if needed
            if not self.redis_client:
                self._initialize_redis()

            logger.debug("OAuth extension started successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to start OAuth extension: {e}")
            return False

    def on_stop(self) -> bool:
        """Stop the OAuth extension."""
        try:
            # Clean up resources
            self.registered_clients.clear()
            self.active_auth_codes.clear()
            self.active_tokens.clear()
            self.oauth_sessions.clear()

            # Close Redis connection
            if self.redis_client:
                self.redis_client.close()

            logger.debug("OAuth extension stopped successfully")
            return True

        except Exception as e:
            logger.error(f"Error stopping OAuth extension: {e}")
            return False

    def on_startup(self):
        """Called during application startup."""
        logger.debug("OAuth extension startup hook called")

    def on_shutdown(self):
        """Called during application shutdown."""
        logger.debug("OAuth extension shutdown hook called")

    def validate_config(self) -> List[str]:
        """Validate the extension configuration."""
        issues = []

        # Check for required Python packages
        try:
            import requests
        except ImportError:
            issues.append(
                "Requests library not installed - OAuth provider communication will not work"
            )

        try:
            import cryptography
        except ImportError:
            issues.append(
                "Cryptography library not installed - secure token generation will not work"
            )

        # Check JWT configuration
        if self.enable_jwt_tokens:
            if not self.jwt_secret_key:
                issues.append("JWT tokens enabled but no secret key configured")
            else:
                try:
                    import jwt
                except ImportError:
                    issues.append(
                        "PyJWT library not available but JWT tokens are enabled"
                    )

        # Check OAuth provider configurations
        configured_providers = []
        for provider_name in self.OAUTH_PROVIDERS:
            if self.OAUTH_PROVIDERS[provider_name].get("enabled"):
                configured_providers.append(provider_name)

        if not configured_providers:
            issues.append(
                "No OAuth providers configured - third-party authentication will not work"
            )

        return issues

    def get_required_permissions(self) -> List[str]:
        """Return the list of permissions required by this extension."""
        return [
            "oauth:client:create",
            "oauth:client:read",
            "oauth:client:update",
            "oauth:client:delete",
            "oauth:authorize",
            "oauth:token:create",
            "oauth:token:refresh",
            "oauth:token:revoke",
        ]

    def has_capability(self, capability: str) -> bool:
        """Check if this extension has a specific capability."""
        return capability in self.capabilities

    def get_grant_types(self) -> Dict[str, str]:
        """Get supported OAuth grant types."""
        return self.GRANT_TYPES.copy()

    def get_response_types(self) -> Dict[str, str]:
        """Get supported OAuth response types."""
        return self.RESPONSE_TYPES.copy()

    def get_oauth_providers(self) -> Dict[str, Dict[str, Any]]:
        """Get configured OAuth providers."""
        return {
            name: {k: v for k, v in config.items() if k != "client_secret"}
            for name, config in self.OAUTH_PROVIDERS.items()
            if config.get("enabled")
        }

    def get_oauth_stats(self) -> Dict[str, Any]:
        """Get OAuth statistics."""
        return {
            "registered_clients": len(self.registered_clients),
            "active_tokens": len(self.active_tokens),
            "active_auth_codes": len(self.active_auth_codes),
            "oauth_sessions": len(self.oauth_sessions),
            "configured_providers": len(
                [p for p in self.OAUTH_PROVIDERS.values() if p.get("enabled")]
            ),
            "pkce_enabled": self.enable_pkce,
            "jwt_tokens_enabled": self.enable_jwt_tokens,
        }
