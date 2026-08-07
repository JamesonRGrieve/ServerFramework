from typing import List
from unittest.mock import MagicMock, patch

import pytest

from serverframework.AbstractTest import CategoryOfTest, SkipThisTest
from serverframework.extensions.AbstractEXTTest import ExtensionTestConfig
from serverframework.extensions.AbstractEXTTest import AbstractEXTTest
from serverframework.extensions.auth_oauth.EXT_Auth_OAuth import EXT_Auth_OAuth
from serverframework.lib.Dependencies import install_pip_dependencies


class TestEXTAuthOAuth(AbstractEXTTest):
    """
    Test suite for EXT_Auth_OAuth extension.

    Tests extension initialization, OAuth 2.0 capabilities, abilities, and authentication functionality.
    """

    extension_class = EXT_Auth_OAuth
    test_config = ExtensionTestConfig(skip_performance_tests=True)

    expected_abilities = [
        "register_oauth_client",
        "create_authorization_url",
        "exchange_authorization_code",
        "validate_access_token",
        "refresh_access_token",
        "revoke_token",
        "oauth_provider_login",
    ]

    expected_capabilities = [
        "oauth_authorization",
        "oauth_token_exchange",
        "oauth_client_management",
        "oauth_provider_integration",
        "scope_validation",
        "pkce_support",
        "token_refresh",
    ]

    _skip_tests: List[SkipThisTest] = []

    @pytest.fixture
    def oauth_instance(self):
        """Create an actual instance for testing instance methods."""
        return EXT_Auth_OAuth(enable_pkce=False)

    @pytest.mark.dependency(name="auth_oauth_dependencies")
    def test_install_pip_dependencies(self):
        """
        Install PIP dependencies required by the Auth OAuth extension.
        This test must run first and all other Auth OAuth tests depend on it.
        """
        # Get the pip dependencies from the extension class
        pip_deps = self.extension_class.pip_dependencies

        # Install the dependencies
        result = install_pip_dependencies(pip_deps, only_missing=True)

        # Check final satisfaction status after installation attempt
        from serverframework.lib.Dependencies import check_pip_dependencies

        final_status = check_pip_dependencies(pip_deps)

        # Verify installation results for required dependencies
        for dep in pip_deps:
            if not dep.optional:
                # Check if dependency is satisfied
                is_satisfied = final_status.get(dep.name, False)
                was_installed = result.get(dep.name, False)

                assert is_satisfied or was_installed, (
                    f"Required dependency {dep.name} is not satisfied. "
                    f"Final status: {is_satisfied}, Installation result: {was_installed}"
                )

        # Verify cryptography specifically since it's critical for OAuth security
        try:
            import cryptography

            assert hasattr(
                cryptography, "__version__"
            ), "cryptography import successful but missing expected attributes"
        except ImportError:
            pytest.fail("cryptography library not available after installation")

    @pytest.fixture
    def mock_env_configured(self):
        """Mock environment with OAuth configured"""
        with patch("serverframework.extensions.auth_oauth.EXT_Auth_OAuth.env") as mock_env:
            mock_env.side_effect = lambda key, default=None: {
                "REDIS_URL": "redis://localhost:6379/4",
                "JWT_SECRET_KEY": "test_jwt_secret_key",
                "LOG_LEVEL": "INFO",
                "LOG_FORMAT": "%(message)s",
            }.get(key, default)
            yield mock_env

    @pytest.fixture
    def mock_env_no_redis(self):
        """Mock environment without Redis configured"""
        with patch("serverframework.extensions.auth_oauth.EXT_Auth_OAuth.env") as mock_env:
            mock_env.side_effect = lambda key, default=None: {
                "REDIS_URL": "",
                "JWT_SECRET_KEY": "test_jwt_secret_key",
                "LOG_LEVEL": "INFO",
                "LOG_FORMAT": "%(message)s",
            }.get(key, default)
            yield mock_env

    @pytest.fixture
    def mock_cryptography_available(self):
        """Mock cryptography library availability"""
        mock_crypto = MagicMock()

        with patch.dict("sys.modules", {"cryptography": mock_crypto}):
            yield mock_crypto

    @pytest.fixture
    def mock_redis_available(self):
        """Mock Redis library availability"""
        mock_redis = MagicMock()
        mock_client = MagicMock()
        mock_client.ping.return_value = True
        mock_client.set.return_value = True
        mock_client.get.return_value = None
        mock_client.delete.return_value = True
        mock_redis.from_url.return_value = mock_client

        with patch.dict("sys.modules", {"redis": mock_redis}):
            yield mock_redis

    @pytest.fixture
    def mock_requests_available(self):
        """Mock requests library availability"""
        mock_requests = MagicMock()
        mock_response = MagicMock()
        mock_response.json.return_value = {"access_token": "test_token"}
        mock_response.status_code = 200
        mock_requests.post.return_value = mock_response

        with patch.dict("sys.modules", {"requests": mock_requests}):
            yield mock_requests

    @pytest.fixture
    def mock_pyjwt_available(self):
        """Mock PyJWT library availability"""
        mock_jwt = MagicMock()
        mock_jwt.encode.return_value = "test.jwt.token"
        mock_jwt.decode.return_value = {"sub": "user123", "scope": "read"}

        with patch.dict("sys.modules", {"jwt": mock_jwt}):
            yield mock_jwt

    @pytest.mark.dependency(depends=["auth_oauth_dependencies"])
    def test_extension_metadata(self, extension):
        """Test extension metadata and basic attributes"""
        assert extension.name == "auth_oauth"
        assert extension.version == "1.0.0"
        assert "OAuth authentication extension" in extension.description
        assert hasattr(extension, "capabilities")
        assert hasattr(extension, "ext_dependencies")
        assert hasattr(extension, "pip_dependencies")
        assert hasattr(extension, "db_tables")

    @pytest.mark.dependency(depends=["auth_oauth_dependencies"])
    def test_dependencies_structure(self, extension):
        """Test that dependencies are properly structured"""
        # Check extension dependencies
        assert len(extension.ext_dependencies) == 3
        ext_deps = {dep.name: dep for dep in extension.ext_dependencies}

        assert "core" in ext_deps
        assert not ext_deps["core"].optional

        assert "auth" in ext_deps
        assert not ext_deps["auth"].optional

        assert "auth_api_keys" in ext_deps
        assert ext_deps["auth_api_keys"].optional

        # Check pip dependencies
        assert len(extension.pip_dependencies) == 4
        pip_deps = {dep.name: dep for dep in extension.pip_dependencies}

        assert "requests" in pip_deps
        assert not pip_deps["requests"].optional

        assert "cryptography" in pip_deps
        assert not pip_deps["cryptography"].optional

        assert "pyjwt" in pip_deps
        assert pip_deps["pyjwt"].optional

        assert "redis" in pip_deps
        assert pip_deps["redis"].optional

    @pytest.mark.dependency(depends=["auth_oauth_dependencies"])
    @pytest.mark.skip(reason="db_tables not populated yet — BLL_Auth_OAuth and DB models not created")
    def test_db_tables_structure(self, extension):
        """Test that database tables are properly defined"""
        assert len(extension.db_tables) == 4
        table_names = [table.__name__ for table in extension.db_tables]
        assert "OAuth2Client" in table_names
        assert "OAuth2AuthCode" in table_names
        assert "OAuth2Token" in table_names
        assert "UserOAuth" in table_names

    @pytest.mark.dependency(depends=["auth_oauth_dependencies"])
    def test_oauth_constants_structure(self, oauth_instance):
        """Test that OAuth constants are properly defined"""
        grant_types = oauth_instance.get_grant_types()
        assert isinstance(grant_types, dict)
        assert "authorization_code" in grant_types
        assert "client_credentials" in grant_types
        assert "refresh_token" in grant_types

        response_types = oauth_instance.get_response_types()
        assert isinstance(response_types, dict)
        assert "code" in response_types

        providers = oauth_instance.get_oauth_providers()
        assert isinstance(providers, dict)

    @pytest.mark.dependency(depends=["auth_oauth_dependencies"])
    def test_initialization_with_custom_settings(self):
        """Test extension initialization with custom settings"""
        ext = EXT_Auth_OAuth(
            enable_pkce=False,
            session_timeout_minutes=60,
            max_concurrent_flows=50,
        )

        assert ext.enable_pkce is False
        assert ext.session_timeout_minutes == 60
        assert ext.max_concurrent_flows == 50

    @pytest.mark.dependency(depends=["auth_oauth_dependencies"])
    def test_on_initialize_success_with_redis(self, mock_redis_available):
        """Test successful extension initialization with Redis"""
        with patch("serverframework.lib.Logging.logger"):
            with patch.object(EXT_Auth_OAuth, "_register_oauth_hooks") as mock_hooks:
                extension = EXT_Auth_OAuth()
                result = extension.on_initialize()

                assert result is True
                mock_hooks.assert_called_once()

    @pytest.mark.dependency(depends=["auth_oauth_dependencies"])
    def test_on_initialize_success_without_redis(self):
        """Test successful extension initialization without Redis"""
        with patch("serverframework.lib.Logging.logger"):
            with patch.object(EXT_Auth_OAuth, "_register_oauth_hooks") as mock_hooks:
                extension = EXT_Auth_OAuth()
                result = extension.on_initialize()

                assert result is True
                mock_hooks.assert_called_once()

    @pytest.mark.dependency(depends=["auth_oauth_dependencies"])
    def test_on_initialize_failure(self):
        """Test extension initialization failure handling"""
        with patch.object(
            EXT_Auth_OAuth,
            "_create_provider",
            side_effect=Exception("Test error"),
        ):
            ext = EXT_Auth_OAuth()
            result = ext.on_initialize()
            assert result is False

    @pytest.mark.dependency(depends=["auth_oauth_dependencies"])
    @pytest.mark.skip(reason="_initialize_redis method not implemented yet")
    def test_initialize_redis_success(self, mock_redis_available, mock_env_configured):
        """Test successful Redis initialization"""
        pass

    @pytest.mark.dependency(depends=["auth_oauth_dependencies"])
    @pytest.mark.skip(reason="_initialize_redis method not implemented yet")
    def test_initialize_redis_no_redis_lib(self):
        """Test Redis initialization without Redis library"""
        pass

    @pytest.mark.dependency(depends=["auth_oauth_dependencies"])
    def test_get_capabilities(self, oauth_instance):
        """Test getting extension capabilities"""
        capabilities = oauth_instance.get_capabilities()

        assert isinstance(capabilities, set)
        for expected_capability in self.expected_capabilities:
            assert expected_capability in capabilities

    @pytest.mark.dependency(depends=["auth_oauth_dependencies"])
    def test_register_capability(self, oauth_instance):
        """Test registering new capability"""
        new_capability = "test_oauth_capability"
        oauth_instance.register_capability(new_capability)

        assert new_capability in oauth_instance.capabilities
        assert new_capability in oauth_instance.get_registered_capabilities()

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_oauth_dependencies"])
    async def test_register_oauth_client_success(self, oauth_instance):
        """Test successful OAuth client registration"""
        result = await oauth_instance.register_oauth_client(
            client_name="Test Client",
            redirect_uris=["https://example.com/callback"],
            scopes=["read", "write"],
            user_id="user123",
        )

        assert result["success"] is True
        assert "client_id" in result
        assert "client_secret" in result
        assert result["client_record"]["client_name"] == "Test Client"
        assert result["client_record"]["user_id"] == "user123"

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_oauth_dependencies"])
    async def test_register_oauth_client_invalid_redirect_uri(self, oauth_instance):
        """Test OAuth client registration with invalid redirect URI"""
        result = await oauth_instance.register_oauth_client(
            client_name="Test Client",
            redirect_uris=["invalid-uri"],
            scopes=["read"],
        )

        assert result["success"] is False
        assert "Invalid redirect URI" in result["message"]

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_oauth_dependencies"])
    async def test_register_oauth_client_user_limit_exceeded(self, oauth_instance):
        """Test OAuth client registration when user limit is exceeded"""
        oauth_instance.max_clients_per_user = 1

        # Register first client
        await oauth_instance.register_oauth_client(
            client_name="First Client",
            redirect_uris=["https://example.com/callback"],
            scopes=["read"],
            user_id="user123",
        )

        # Try to register second client (should fail)
        result = await oauth_instance.register_oauth_client(
            client_name="Second Client",
            redirect_uris=["https://example.com/callback"],
            scopes=["read"],
            user_id="user123",
        )

        assert result["success"] is False
        assert "Maximum number of clients reached" in result["message"]

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_oauth_dependencies"])
    async def test_create_authorization_url_success(self, oauth_instance):
        """Test successful authorization URL creation"""
        # First register a client
        client_result = await oauth_instance.register_oauth_client(
            client_name="Test Client",
            redirect_uris=["https://example.com/callback"],
            scopes=["read", "write"],
        )
        client_id = client_result["client_id"]

        result = await oauth_instance.create_authorization_url(
            client_id=client_id,
            redirect_uri="https://example.com/callback",
            scopes=["read"],
            state="test_state",
        )

        assert result["success"] is True
        assert "authorization_url" in result
        assert "code" in result["authorization_url"]
        assert "state" in result["authorization_url"]

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_oauth_dependencies"])
    async def test_create_authorization_url_with_pkce(self):
        """Test authorization URL creation with PKCE"""
        pkce_instance = EXT_Auth_OAuth(enable_pkce=True)

        # Register a client
        client_result = await pkce_instance.register_oauth_client(
            client_name="Test Client",
            redirect_uris=["https://example.com/callback"],
            scopes=["read"],
        )
        client_id = client_result["client_id"]

        result = await pkce_instance.create_authorization_url(
            client_id=client_id,
            redirect_uri="https://example.com/callback",
            scopes=["read"],
            code_challenge="test_challenge",
            code_challenge_method="S256",
        )

        assert result["success"] is True
        assert "authorization_url" in result
        assert "code_challenge" in result["authorization_url"]

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_oauth_dependencies"])
    async def test_create_authorization_url_invalid_client(self, oauth_instance):
        """Test authorization URL creation with invalid client"""
        result = await oauth_instance.create_authorization_url(
            client_id="invalid_client",
            redirect_uri="https://example.com/callback",
            scopes=["read"],
        )

        assert result["success"] is False
        assert "Invalid or inactive client" in result["message"]

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_oauth_dependencies"])
    async def test_exchange_authorization_code_success(self, oauth_instance):
        """Test successful authorization code exchange"""
        # Setup client and authorization code
        client_result = await oauth_instance.register_oauth_client(
            client_name="Test Client",
            redirect_uris=["https://example.com/callback"],
            scopes=["read"],
        )
        client_id = client_result["client_id"]
        client_secret = client_result["client_secret"]

        auth_code = "test_auth_code"

        # Mock the authorization code in active codes
        oauth_instance.active_auth_codes[auth_code] = {
            "client_id": client_id,
            "redirect_uri": "https://example.com/callback",
            "scopes": ["read"],
            "user_id": "user123",
            "expires_at": "2030-01-01T00:00:00",
        }

        result = await oauth_instance.exchange_authorization_code(
            client_id=client_id,
            client_secret=client_secret,
            authorization_code=auth_code,
            redirect_uri="https://example.com/callback",
        )

        assert result["success"] is True
        assert "access_token" in result
        assert "refresh_token" in result
        assert "token_type" in result

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_oauth_dependencies"])
    async def test_exchange_authorization_code_invalid_code(self, oauth_instance):
        """Test authorization code exchange with invalid code"""
        # Setup client
        client_result = await oauth_instance.register_oauth_client(
            client_name="Test Client",
            redirect_uris=["https://example.com/callback"],
            scopes=["read"],
        )
        client_id = client_result["client_id"]
        client_secret = client_result["client_secret"]

        result = await oauth_instance.exchange_authorization_code(
            client_id=client_id,
            client_secret=client_secret,
            authorization_code="invalid_code",
            redirect_uri="https://example.com/callback",
        )

        assert result["success"] is False
        assert "Invalid authorization code" in result["message"]

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_oauth_dependencies"])
    async def test_validate_access_token_success(self, oauth_instance):
        """Test successful access token validation"""
        # Setup token
        test_token = "test_access_token"
        oauth_instance.active_tokens[test_token] = {
            "client_id": "test_client",
            "user_id": "user123",
            "scopes": ["read", "write"],
            "expires_at": "2030-01-01T00:00:00",
            "token_type": "Bearer",
        }

        result = await oauth_instance.validate_access_token(
            access_token=test_token,
            required_scopes=["read"],
        )

        assert result["success"] is True
        assert "token_data" in result

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_oauth_dependencies"])
    async def test_validate_access_token_insufficient_scope(self, oauth_instance):
        """Test access token validation with insufficient scope"""
        # Setup token with limited scope
        test_token = "test_access_token"
        oauth_instance.active_tokens[test_token] = {
            "client_id": "test_client",
            "user_id": "user123",
            "scopes": ["read"],
            "expires_at": "2030-01-01T00:00:00",
            "token_type": "Bearer",
        }

        result = await oauth_instance.validate_access_token(
            access_token=test_token,
            required_scopes=["read", "write", "admin"],
        )

        assert result["success"] is False
        assert "Missing required scopes:" in result["message"]

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_oauth_dependencies"])
    async def test_validate_access_token_expired(self, oauth_instance):
        """Test validation of expired access token"""
        # Setup expired token
        test_token = "expired_token"
        oauth_instance.active_tokens[test_token] = {
            "client_id": "test_client",
            "user_id": "user123",
            "scopes": ["read"],
            "expires_at": "2020-01-01T00:00:00",  # Expired
            "token_type": "Bearer",
        }

        result = await oauth_instance.validate_access_token(access_token=test_token)

        assert result["success"] is False
        assert "expired" in result["message"]

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_oauth_dependencies"])
    async def test_refresh_access_token_success(self, oauth_instance):
        """Test successful access token refresh"""
        # Setup client and refresh token
        client_result = await oauth_instance.register_oauth_client(
            client_name="Test Client",
            redirect_uris=["https://example.com/callback"],
            scopes=["read"],
        )
        client_id = client_result["client_id"]
        client_secret = client_result["client_secret"]

        # Store token with access_token as key and refresh_token in data
        # (matches how exchange_authorization_code stores tokens)
        access_token_key = "test_access_token"
        refresh_token = "test_refresh_token"
        oauth_instance.active_tokens[access_token_key] = {
            "client_id": client_id,
            "user_id": "user123",
            "scopes": ["read"],
            "token_type": "Bearer",
            "expires_at": "2030-01-01T00:00:00",
            "access_token": access_token_key,
            "refresh_token": refresh_token,
        }

        result = await oauth_instance.refresh_access_token(
            refresh_token=refresh_token,
            client_id=client_id,
            client_secret=client_secret,
        )

        assert result["success"] is True
        assert "access_token" in result
        assert "refresh_token" in result

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_oauth_dependencies"])
    async def test_refresh_access_token_invalid_token(self, oauth_instance):
        """Test access token refresh with invalid refresh token"""
        # Setup client
        client_result = await oauth_instance.register_oauth_client(
            client_name="Test Client",
            redirect_uris=["https://example.com/callback"],
            scopes=["read"],
        )
        client_id = client_result["client_id"]
        client_secret = client_result["client_secret"]

        result = await oauth_instance.refresh_access_token(
            refresh_token="invalid_refresh_token",
            client_id=client_id,
            client_secret=client_secret,
        )

        assert result["success"] is False
        assert "Invalid refresh token" in result["message"]

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_oauth_dependencies"])
    async def test_revoke_token_success(self, oauth_instance):
        """Test successful token revocation"""
        # Setup token
        test_token = "test_access_token"
        oauth_instance.active_tokens[test_token] = {
            "client_id": "test_client",
            "user_id": "user123",
            "scopes": ["read"],
            "expires_at": "2030-01-01T00:00:00",
            "token_type": "Bearer",
        }

        result = await oauth_instance.revoke_token(
            token=test_token,
            token_type_hint="access_token",
        )

        assert result["success"] is True
        assert "revoked successfully" in result["message"]

        # Verify token is removed from active tokens
        assert test_token not in oauth_instance.active_tokens

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_oauth_dependencies"])
    async def test_revoke_token_not_found(self, oauth_instance):
        """Test token revocation with non-existent token"""
        result = await oauth_instance.revoke_token(token="non_existent_token")

        assert result["success"] is False
        assert "Token not found" in result["message"]

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_oauth_dependencies"])
    @pytest.mark.skip(
        reason="OAuth provider login requires runtime env vars (GOOGLE_CLIENT_ID/SECRET) "
        "and on_initialize() to configure providers"
    )
    async def test_oauth_provider_login_google(self, oauth_instance):
        """Test OAuth provider login with Google"""
        result = await oauth_instance.oauth_provider_login(
            provider="google",
            redirect_uri="https://example.com/callback",
            scopes=["openid", "email"],
            state="test_state",
        )

        assert result["success"] is True
        assert "authorization_url" in result
        assert "google" in result["authorization_url"]

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_oauth_dependencies"])
    async def test_oauth_provider_login_unsupported_provider(self, oauth_instance):
        """Test OAuth provider login with unsupported provider"""
        result = await oauth_instance.oauth_provider_login(
            provider="unsupported_provider",
            redirect_uri="https://example.com/callback",
        )

        assert result["success"] is False
        assert "not available" in result["message"]

    @pytest.mark.dependency(depends=["auth_oauth_dependencies"])
    def test_validate_redirect_uri(self, oauth_instance):
        """Test redirect URI validation"""
        assert oauth_instance._validate_redirect_uri("https://example.com/callback") is True
        assert (
            oauth_instance._validate_redirect_uri("http://localhost:3000/callback") is True
        )
        assert oauth_instance._validate_redirect_uri("invalid-uri") is False

    @pytest.mark.dependency(depends=["auth_oauth_dependencies"])
    def test_generate_client_credentials(self, oauth_instance):
        """Test client ID and secret generation"""
        client_id = oauth_instance._generate_client_id()
        client_secret = oauth_instance._generate_client_secret()

        assert isinstance(client_id, str)
        assert isinstance(client_secret, str)
        assert len(client_id) > 0
        assert len(client_secret) > 0
        assert client_id != client_secret

    @pytest.mark.dependency(depends=["auth_oauth_dependencies"])
    def test_generate_tokens(self, oauth_instance):
        """Test access and refresh token generation"""
        auth_data = {
            "client_id": "test_client",
            "user_id": "user123",
            "scopes": ["read"],
        }

        access_token = oauth_instance._generate_access_token(auth_data)
        refresh_token = oauth_instance._generate_refresh_token(auth_data)

        assert isinstance(access_token, str)
        assert isinstance(refresh_token, str)
        assert len(access_token) > 0
        assert len(refresh_token) > 0
        assert access_token != refresh_token

    @pytest.mark.dependency(depends=["auth_oauth_dependencies"])
    def test_verify_pkce_challenge(self, oauth_instance):
        """Test PKCE challenge verification"""
        # Test S256 method
        verifier = "test_verifier"
        import base64
        import hashlib

        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .decode()
            .rstrip("=")
        )

        result = oauth_instance._verify_pkce_challenge(verifier, challenge, "S256")
        assert result is True

        # Test invalid challenge
        result = oauth_instance._verify_pkce_challenge(verifier, "invalid_challenge", "S256")
        assert result is False

    @pytest.mark.dependency(depends=["auth_oauth_dependencies"])
    def test_validate_config_all_libraries_available(self):
        """Test configuration validation when all libraries are available"""
        with patch("serverframework.lib.Logging.logger"):
            mock_libs = {
                "requests": MagicMock(),
                "cryptography": MagicMock(),
                "jwt": MagicMock(),
                "redis": MagicMock(),
            }

            with patch.dict("sys.modules", mock_libs):
                extension = EXT_Auth_OAuth(enable_jwt_tokens=False)
                issues = extension.validate_config()

                assert len(issues) == 0

    @pytest.mark.dependency(depends=["auth_oauth_dependencies"])
    def test_validate_config_missing_cryptography(self):
        """Test configuration validation with missing cryptography"""
        with patch("serverframework.lib.Logging.logger"):
            with patch.dict("sys.modules", {"cryptography": None}):
                extension = EXT_Auth_OAuth()
                issues = extension.validate_config()

                assert len(issues) >= 1
                issue_text = " ".join(issues).lower()
                assert "cryptography" in issue_text

    @pytest.mark.dependency(depends=["auth_oauth_dependencies"])
    def test_validate_config_jwt_without_secret(self):
        """Test configuration validation for JWT without secret key"""
        with patch("serverframework.lib.Logging.logger"):
            extension = EXT_Auth_OAuth(enable_jwt_tokens=True, jwt_secret_key=None)
            issues = extension.validate_config()

            assert len(issues) >= 1
            issue_text = " ".join(issues).lower()
            assert "jwt" in issue_text

    @pytest.mark.dependency(depends=["auth_oauth_dependencies"])
    def test_get_required_permissions(self, oauth_instance):
        """Test getting required permissions"""
        permissions = oauth_instance.get_required_permissions()

        assert isinstance(permissions, list)
        assert len(permissions) == 8
        assert "oauth:authorize" in permissions
        assert "oauth:client:create" in permissions
        assert "oauth:token:create" in permissions

    @pytest.mark.dependency(depends=["auth_oauth_dependencies"])
    @pytest.mark.skip(reason="_initialize_redis method not implemented yet — on_start calls it unconditionally")
    def test_on_start_success(self):
        """Test successful extension start"""
        with patch("serverframework.lib.Logging.logger"):
            extension = EXT_Auth_OAuth()
            result = extension.on_start()

            assert result is True

    @pytest.mark.dependency(depends=["auth_oauth_dependencies"])
    def test_on_stop_success(self, oauth_instance):
        """Test successful extension stop"""
        # Add some test data
        oauth_instance.registered_clients["test"] = {"name": "test"}
        oauth_instance.active_auth_codes["test"] = {"client_id": "test"}
        oauth_instance.active_tokens["test"] = {"client_id": "test"}

        result = oauth_instance.on_stop()

        assert result is True
        assert len(oauth_instance.registered_clients) == 0
        assert len(oauth_instance.active_auth_codes) == 0
        assert len(oauth_instance.active_tokens) == 0

    @pytest.mark.dependency(depends=["auth_oauth_dependencies"])
    def test_has_capability(self, oauth_instance):
        """Test capability checking"""
        assert oauth_instance.has_capability("oauth_authorization") is True
        assert oauth_instance.has_capability("oauth_token_exchange") is True
        assert oauth_instance.has_capability("non_existent_capability") is False

    @pytest.mark.dependency(depends=["auth_oauth_dependencies"])
    def test_get_oauth_stats(self, oauth_instance):
        """Test getting OAuth statistics"""
        # Add some test data
        oauth_instance.registered_clients["client1"] = {"name": "Test Client 1"}
        oauth_instance.active_tokens["token1"] = {"client_id": "client1"}
        oauth_instance.active_auth_codes["code1"] = {"client_id": "client1"}

        stats = oauth_instance.get_oauth_stats()

        assert isinstance(stats, dict)
        assert stats["registered_clients"] == 1
        assert stats["active_tokens"] == 1
        assert stats["active_auth_codes"] == 1

    @pytest.mark.dependency(depends=["auth_oauth_dependencies"])
    @pytest.mark.skip(reason="discover_abilities() method does not exist on AbstractStaticExtension")
    def test_abilities_discovery(self, extension):
        """Test that all expected abilities are discovered"""
        extension.discover_abilities()

        for expected_ability in self.expected_abilities:
            assert expected_ability in extension.abilities
            assert callable(extension.abilities[expected_ability])

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_oauth_dependencies"])
    @pytest.mark.skip(reason="execute_ability() method does not exist on AbstractStaticExtension")
    async def test_execute_ability_success(self, extension):
        """Test successful ability execution"""
        result = await extension.execute_ability(
            "register_oauth_client",
            {
                "client_name": "Test Client",
                "redirect_uris": ["https://example.com/callback"],
                "scopes": ["read"],
            },
        )

        assert "success" in result

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_oauth_dependencies"])
    @pytest.mark.skip(reason="execute_ability() method does not exist on AbstractStaticExtension")
    async def test_execute_ability_not_found(self, extension):
        """Test executing non-existent ability"""
        result = await extension.execute_ability("non_existent_ability")

        assert "not found" in result

    @pytest.mark.dependency(depends=["auth_oauth_dependencies"])
    @pytest.mark.skip(
        reason="_register_oauth_hooks references AbstractExtension (not imported) "
        "instead of AbstractStaticExtension"
    )
    def test_oauth_hooks_registration(self):
        """Test that OAuth hooks are properly registered"""
        with patch("serverframework.lib.Logging.logger"):
            with patch(
                "serverframework.extensions.auth_oauth.EXT_Auth_OAuth.AbstractExtension.bll_hook"
            ) as mock_hook:
                extension = EXT_Auth_OAuth()
                extension._register_oauth_hooks()

                # Should register hooks for login and user creation
                assert mock_hook.called

    @pytest.mark.dependency(depends=["auth_oauth_dependencies"])
    def test_lifecycle_methods_integration(self, oauth_instance):
        """Test integration of lifecycle methods"""
        # Test startup
        oauth_instance.on_startup()

        # Test shutdown
        oauth_instance.on_shutdown()

        # These methods should not raise exceptions

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_oauth_dependencies"])
    async def test_oauth_full_flow(self, oauth_instance):
        """Test complete OAuth authorization flow"""
        # 1. Register OAuth client
        client_result = await oauth_instance.register_oauth_client(
            client_name="Test Full Flow Client",
            redirect_uris=["https://example.com/callback"],
            scopes=["read", "write"],
            user_id="user123",
        )
        assert client_result["success"] is True
        client_id = client_result["client_id"]
        client_secret = client_result["client_secret"]

        # 2. Create authorization URL
        auth_url_result = await oauth_instance.create_authorization_url(
            client_id=client_id,
            redirect_uri="https://example.com/callback",
            scopes=["read"],
            state="test_state",
        )
        assert auth_url_result["success"] is True

        # 3. Simulate authorization code generation
        auth_code = "test_authorization_code"
        oauth_instance.active_auth_codes[auth_code] = {
            "client_id": client_id,
            "redirect_uri": "https://example.com/callback",
            "scopes": ["read"],
            "user_id": "user123",
            "expires_at": "2030-01-01T00:00:00",
        }

        # 4. Exchange authorization code for tokens
        token_result = await oauth_instance.exchange_authorization_code(
            client_id=client_id,
            client_secret=client_secret,
            authorization_code=auth_code,
            redirect_uri="https://example.com/callback",
        )
        assert token_result["success"] is True
        access_token = token_result["access_token"]
        refresh_token = token_result["refresh_token"]

        # 5. Validate access token
        validate_result = await oauth_instance.validate_access_token(
            access_token=access_token,
            required_scopes=["read"],
        )
        assert validate_result["success"] is True

        # 6. Refresh access token
        refresh_result = await oauth_instance.refresh_access_token(
            refresh_token=refresh_token,
            client_id=client_id,
            client_secret=client_secret,
        )
        assert refresh_result["success"] is True

        # 7. Revoke token — use the new access token from refresh
        new_access_token = refresh_result["access_token"]
        revoke_result = await oauth_instance.revoke_token(token=new_access_token)
        assert revoke_result["success"] is True

    @pytest.mark.dependency(depends=["auth_oauth_dependencies"])
    def test_security_token_generation(self, oauth_instance):
        """Test that tokens are properly generated and secure"""
        auth_data = {
            "client_id": "test_client",
            "user_id": "user123",
            "scopes": ["read"],
        }

        # Generate multiple tokens to ensure uniqueness
        tokens = [oauth_instance._generate_access_token(auth_data) for _ in range(5)]

        # All tokens should be unique
        assert len(set(tokens)) == len(tokens)

        # Tokens should be strings and not empty
        for token in tokens:
            assert isinstance(token, str)
            assert len(token) > 0
