from typing import List
from unittest.mock import MagicMock, patch

import pytest

from AbstractTest import CategoryOfTest, ClassOfTestsConfig, SkipThisTest
from serverframework.extensions.AbstractEXTTest import AbstractEXTTest
from serverframework.extensions.auth_api_keys.EXT_Auth_APIKeys import EXT_Auth_APIKeys
from serverframework.lib.Dependencies import install_pip_dependencies


class TestEXTAuthAPIKeys(AbstractEXTTest):
    """
    Test suite for EXT_Auth_APIKeys extension.

    Tests extension initialization, API key management capabilities, abilities, and authentication functionality.
    Focuses on testing key generation, validation, rotation, and access control functionality rather than
    component loading.

    Test areas:
    - Extension metadata and configuration
    - API key capabilities and abilities (generation, validation, management)
    - Cryptography and Redis integration
    - Key lifecycle management (creation, rotation, revocation)
    - Rate limiting and usage tracking
    - Access control and permissions validation
    - Security and authentication workflows
    """

    # Configure the test class
    extension_class = EXT_Auth_APIKeys
    test_config = ClassOfTestsConfig(
        categories=[CategoryOfTest.EXTENSION, CategoryOfTest.INTEGRATION],
        cleanup=True,
    )

    # Expected extension properties
    expected_abilities = [
        "generate_api_key",
        "validate_api_key",
        "revoke_api_key",
        "rotate_api_key",
        "list_api_keys",
        "get_usage_statistics",
    ]

    expected_capabilities = [
        "api_key_generation",
        "api_key_validation",
        "api_key_management",
        "access_control",
        "rate_limiting",
        "key_rotation",
        "usage_tracking",
    ]

    # Tests to skip
    _skip_tests: List[SkipThisTest] = []

    @pytest.mark.dependency(name="auth_api_keys_dependencies")
    def test_install_pip_dependencies(self):
        """
        Install PIP dependencies required by the Auth API Keys extension.
        This test must run first and all other Auth API Keys tests depend on it.
        """
        # Get the pip dependencies from the extension class
        pip_deps = self.extension_class.pip_dependencies

        # Install the dependencies
        result = install_pip_dependencies(pip_deps, only_missing=True)

        # Check final satisfaction status after installation attempt
        from lib.Dependencies import check_pip_dependencies

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

        # Verify cryptography specifically since it's critical for key security
        try:
            import cryptography

            assert hasattr(
                cryptography, "__version__"
            ), "cryptography import successful but missing expected attributes"
        except ImportError:
            pytest.fail("cryptography library not available after installation")

    @pytest.fixture
    def mock_env_configured(self):
        """Mock environment with API keys configured"""
        with patch("extensions.auth_api_keys.EXT_Auth_APIKeys.env") as mock_env:
            mock_env.side_effect = lambda key, default=None: {
                "REDIS_URL": "redis://localhost:6379/1",
                "LOG_LEVEL": "INFO",
                "LOG_FORMAT": "%(message)s",
            }.get(key, default)
            yield mock_env

    @pytest.fixture
    def mock_env_no_redis(self):
        """Mock environment without Redis configured"""
        with patch("extensions.auth_api_keys.EXT_Auth_APIKeys.env") as mock_env:
            mock_env.side_effect = lambda key, default=None: {
                "REDIS_URL": "",
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
        mock_client.incr.return_value = 1
        mock_client.expire.return_value = True
        mock_redis.from_url.return_value = mock_client

        with patch.dict("sys.modules", {"redis": mock_redis}):
            yield mock_redis

    @pytest.fixture
    def mock_bcrypt_available(self):
        """Mock bcrypt library availability"""
        mock_bcrypt = MagicMock()

        with patch.dict("sys.modules", {"bcrypt": mock_bcrypt}):
            yield mock_bcrypt

    @pytest.mark.dependency(depends=["auth_api_keys_dependencies"])
    def test_extension_metadata(self, extension):
        """Test extension metadata and basic attributes"""
        assert extension.name == "auth_api_keys"
        assert extension.version == "1.0.0"
        assert "API Keys authentication extension" in extension.description
        assert hasattr(extension, "capabilities")
        assert hasattr(extension, "ext_dependencies")
        assert hasattr(extension, "pip_dependencies")
        assert hasattr(extension, "db_tables")

    @pytest.mark.dependency(depends=["auth_api_keys_dependencies"])
    def test_dependencies_structure(self, extension):
        """Test that dependencies are properly structured"""
        # Check extension dependencies
        assert len(extension.__class__.ext_dependencies) == 2
        ext_deps = {dep.name: dep for dep in extension.__class__.ext_dependencies}

        assert "core" in ext_deps
        assert not ext_deps["core"].optional

        assert "auth" in ext_deps
        assert not ext_deps["auth"].optional

        # Check pip dependencies
        assert len(extension.__class__.pip_dependencies) == 3
        pip_deps = {dep.name: dep for dep in extension.__class__.pip_dependencies}

        assert "cryptography" in pip_deps
        assert ">=41.0.0" in pip_deps["cryptography"].semver
        assert not pip_deps["cryptography"].optional

        assert "bcrypt" in pip_deps
        assert pip_deps["bcrypt"].optional

        assert "redis" in pip_deps
        assert pip_deps["redis"].optional

    @pytest.mark.dependency(depends=["auth_api_keys_dependencies"])
    def test_db_tables_structure(self, extension):
        """Test that database tables are properly defined"""
        assert len(extension.__class__.db_tables) == 1
        table_names = [table.__name__ for table in extension.__class__.db_tables]
        assert "APIKey" in table_names

    @pytest.mark.dependency(depends=["auth_api_keys_dependencies"])
    def test_api_key_constants_structure(self, extension):
        """Test that API key constants are properly defined"""
        key_types = extension.get_api_key_types()
        assert isinstance(key_types, dict)
        assert "read_only" in key_types
        assert "read_write" in key_types
        assert "admin" in key_types
        assert "service" in key_types

        statuses = extension.get_key_statuses()
        assert isinstance(statuses, dict)
        assert "active" in statuses
        assert "expired" in statuses
        assert "revoked" in statuses
        assert "suspended" in statuses

    @pytest.mark.dependency(depends=["auth_api_keys_dependencies"])
    def test_initialization_with_rate_limiting(self, mock_env_configured):
        """Test extension initialization with rate limiting enabled"""
        with patch("lib.Logging.logger"):
            extension = EXT_Auth_APIKeys(
                enable_rate_limiting=True,
                rate_limit_requests_per_minute=500,
                max_keys_per_user=5,
                enable_usage_tracking=True,
            )

            assert extension.enable_rate_limiting is True
            assert extension.rate_limit_requests_per_minute == 500
            assert extension.max_keys_per_user == 5
            assert extension.enable_usage_tracking is True

    @pytest.mark.dependency(depends=["auth_api_keys_dependencies"])
    def test_initialization_with_custom_settings(self):
        """Test extension initialization with custom settings"""
        with patch("lib.Logging.logger"):
            extension = EXT_Auth_APIKeys(
                key_length=64,
                key_prefix="custom_",
                default_expiry_days=180,
                hash_algorithm="sha512",
            )

            assert extension.key_length == 64
            assert extension.key_prefix == "custom_"
            assert extension.default_expiry_days == 180
            assert extension.hash_algorithm == "sha512"

    @pytest.mark.dependency(depends=["auth_api_keys_dependencies"])
    def test_on_initialize_success_with_redis(self, mock_redis_available):
        """Test successful extension initialization with Redis"""
        with patch("lib.Logging.logger"):
            with patch.object(
                EXT_Auth_APIKeys, "_register_api_key_hooks"
            ) as mock_hooks:
                extension = EXT_Auth_APIKeys(enable_rate_limiting=True)
                result = extension.on_initialize()

                assert result is True
                mock_hooks.assert_called_once()

    @pytest.mark.dependency(depends=["auth_api_keys_dependencies"])
    def test_on_initialize_success_without_redis(self):
        """Test successful extension initialization without Redis"""
        with patch("lib.Logging.logger"):
            with patch.object(
                EXT_Auth_APIKeys, "_register_api_key_hooks"
            ) as mock_hooks:
                extension = EXT_Auth_APIKeys(enable_rate_limiting=False)
                result = extension.on_initialize()

                assert result is True
                mock_hooks.assert_called_once()

    @pytest.mark.dependency(depends=["auth_api_keys_dependencies"])
    def test_on_initialize_failure(self):
        """Test extension initialization failure handling"""
        with patch("lib.Logging.logger"):
            with patch.object(
                EXT_Auth_APIKeys,
                "_initialize_redis",
                side_effect=Exception("Test error"),
            ):
                extension = EXT_Auth_APIKeys()
                result = extension.on_initialize()

                assert result is False

    @pytest.mark.dependency(depends=["auth_api_keys_dependencies"])
    def test_initialize_redis_success(self, mock_redis_available, mock_env_configured):
        """Test successful Redis initialization"""
        with patch("lib.Logging.logger"):
            extension = EXT_Auth_APIKeys()
            extension._initialize_redis()

            assert extension.redis_client is not None

    @pytest.mark.dependency(depends=["auth_api_keys_dependencies"])
    def test_initialize_redis_no_redis_lib(self):
        """Test Redis initialization without Redis library"""
        with patch("lib.Logging.logger"):
            with patch.dict("sys.modules", {"redis": None}):
                extension = EXT_Auth_APIKeys()
                extension._initialize_redis()

                assert extension.redis_client is None

    @pytest.mark.dependency(depends=["auth_api_keys_dependencies"])
    def test_initialize_redis_connection_error(self, mock_redis_available):
        """Test Redis initialization with connection error"""
        with patch("lib.Logging.logger"):
            mock_redis_available.from_url.side_effect = Exception("Connection failed")
            extension = EXT_Auth_APIKeys()
            extension._initialize_redis()

            assert extension.redis_client is None

    @pytest.mark.dependency(depends=["auth_api_keys_dependencies"])
    def test_get_capabilities(self, extension):
        """Test getting extension capabilities"""
        capabilities = extension.get_capabilities()

        assert isinstance(capabilities, set)
        for expected_capability in self.expected_capabilities:
            assert expected_capability in capabilities

    @pytest.mark.dependency(depends=["auth_api_keys_dependencies"])
    def test_register_capability(self, extension):
        """Test registering new capability"""
        new_capability = "test_api_key_capability"
        extension.register_capability(new_capability)

        assert new_capability in extension.capabilities
        assert new_capability in extension.get_registered_capabilities()

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_api_keys_dependencies"])
    async def test_generate_api_key_success(self, extension):
        """Test successful API key generation"""
        with patch("uuid.uuid4") as mock_uuid:
            mock_uuid.return_value.__str__.return_value = "test-key-id"

            result = await extension.generate_api_key(
                name="test_key",
                user_id="user123",
                key_type="read_write",
                permissions=["read", "write"],
            )

            assert result["success"] is True
            assert "key" in result
            assert result["key"].startswith(extension.key_prefix)
            assert result["key_data"]["name"] == "test_key"
            assert result["key_data"]["user_id"] == "user123"
            assert result["key_data"]["key_type"] == "read_write"

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_api_keys_dependencies"])
    async def test_generate_api_key_with_custom_expiry(self, extension):
        """Test API key generation with custom expiry"""
        from datetime import datetime, timedelta

        custom_expiry = datetime.utcnow() + timedelta(days=90)

        result = await extension.generate_api_key(
            name="custom_expiry_key",
            expires_at=custom_expiry,
        )

        assert result["success"] is True
        assert result["key_data"]["expires_at"] == custom_expiry.isoformat()

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_api_keys_dependencies"])
    async def test_generate_api_key_user_limit_exceeded(self, extension):
        """Test API key generation when user limit is exceeded"""
        extension.max_keys_per_user = 1

        # Generate first key
        await extension.generate_api_key(name="first_key", user_id="user123")

        # Try to generate second key (should fail)
        result = await extension.generate_api_key(name="second_key", user_id="user123")

        assert result["success"] is False
        assert "limit exceeded" in result["message"]

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_api_keys_dependencies"])
    async def test_validate_api_key_success(self, extension):
        """Test successful API key validation"""
        # First generate a key
        generate_result = await extension.generate_api_key(
            name="test_key",
            permissions=["read", "write"],
        )
        api_key = generate_result["key"]

        # Then validate it
        result = await extension.validate_api_key(
            api_key=api_key,
            required_permissions=["read"],
        )

        assert result["success"] is True
        assert result["message"] == "API key is valid"
        assert "key_data" in result

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_api_keys_dependencies"])
    async def test_validate_api_key_invalid(self, extension):
        """Test validation of invalid API key"""
        result = await extension.validate_api_key(api_key="invalid_key")

        assert result["success"] is False
        assert "Invalid API key" in result["message"]

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_api_keys_dependencies"])
    async def test_validate_api_key_insufficient_permissions(self, extension):
        """Test API key validation with insufficient permissions"""
        # Generate key with limited permissions
        generate_result = await extension.generate_api_key(
            name="limited_key",
            permissions=["read"],
        )
        api_key = generate_result["key"]

        # Try to validate with higher permission requirements
        result = await extension.validate_api_key(
            api_key=api_key,
            required_permissions=["read", "write", "admin"],
        )

        assert result["success"] is False
        assert "Insufficient permissions" in result["message"]

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_api_keys_dependencies"])
    async def test_validate_api_key_expired(self, extension):
        """Test validation of expired API key"""
        from datetime import datetime, timedelta

        # Generate key that expires immediately
        past_expiry = datetime.utcnow() - timedelta(days=1)
        generate_result = await extension.generate_api_key(
            name="expired_key",
            expires_at=past_expiry,
        )
        api_key = generate_result["key"]

        # Try to validate expired key
        result = await extension.validate_api_key(api_key=api_key)

        assert result["success"] is False
        assert "expired" in result["message"]

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_api_keys_dependencies"])
    async def test_validate_api_key_with_rate_limiting(self, mock_redis_available):
        """Test API key validation with rate limiting"""
        with patch("lib.Logging.logger"):
            extension = EXT_Auth_APIKeys(
                enable_rate_limiting=True,
                rate_limit_requests_per_minute=2,
            )
            extension._initialize_redis()

            # Generate key
            generate_result = await extension.generate_api_key(name="rate_limited_key")
            api_key = generate_result["key"]

            # First two validations should succeed
            result1 = await extension.validate_api_key(api_key=api_key)
            result2 = await extension.validate_api_key(api_key=api_key)

            assert result1["success"] is True
            assert result2["success"] is True

            # Third validation should be rate limited
            result3 = await extension.validate_api_key(api_key=api_key)
            assert result3["success"] is False
            assert "Rate limit exceeded" in result3["message"]

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_api_keys_dependencies"])
    async def test_revoke_api_key_success(self, extension):
        """Test successful API key revocation"""
        # Generate key
        generate_result = await extension.generate_api_key(name="revoke_test_key")
        key_id = generate_result["key_id"]

        # Revoke key
        result = await extension.revoke_api_key(
            key_id=key_id,
            reason="Test revocation",
        )

        assert result["success"] is True
        assert "revoked successfully" in result["message"]

        # Verify key is revoked
        key_data = None
        for data in extension.active_keys.values():
            if data.get("id") == key_id:
                key_data = data
                break

        assert key_data is not None
        assert key_data["status"] == "revoked"
        assert key_data["revocation_reason"] == "Test revocation"

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_api_keys_dependencies"])
    async def test_revoke_api_key_not_found(self, extension):
        """Test revocation of non-existent API key"""
        result = await extension.revoke_api_key(key_id="non-existent")

        assert result["success"] is False
        assert "not found" in result["message"]

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_api_keys_dependencies"])
    async def test_rotate_api_key_success(self, extension):
        """Test successful API key rotation"""
        # Generate original key
        generate_result = await extension.generate_api_key(name="rotate_test_key")
        original_key_id = generate_result["key_id"]
        original_key = generate_result["key"]

        # Rotate key
        result = await extension.rotate_api_key(
            key_id=original_key_id,
            new_name="rotated_test_key",
        )

        assert result["success"] is True
        assert "new_key" in result
        assert result["new_key"] != original_key
        assert result["old_key_id"] == original_key_id

        # Verify original key is marked as rotated
        old_key_data = None
        for data in extension.active_keys.values():
            if data.get("id") == original_key_id:
                old_key_data = data
                break

        assert old_key_data is not None
        assert old_key_data["status"] == "rotated"

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_api_keys_dependencies"])
    async def test_rotate_api_key_not_found(self, extension):
        """Test rotation of non-existent API key"""
        result = await extension.rotate_api_key(key_id="non-existent")

        assert result["success"] is False
        assert "not found" in result["message"]

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_api_keys_dependencies"])
    async def test_list_api_keys_success(self, extension):
        """Test successful API key listing"""
        # Generate test keys
        await extension.generate_api_key(name="key1", user_id="user1")
        await extension.generate_api_key(name="key2", user_id="user2")
        await extension.generate_api_key(name="key3", user_id="user1")

        # List all keys
        result = await extension.list_api_keys()

        assert result["success"] is True
        assert len(result["keys"]) == 3
        assert result["total"] == 3

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_api_keys_dependencies"])
    async def test_list_api_keys_with_user_filter(self, extension):
        """Test API key listing with user filter"""
        # Generate test keys
        await extension.generate_api_key(name="key1", user_id="user1")
        await extension.generate_api_key(name="key2", user_id="user2")

        # List keys for specific user
        result = await extension.list_api_keys(user_id="user1")

        assert result["success"] is True
        assert len(result["keys"]) == 1
        assert result["keys"][0]["user_id"] == "user1"

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_api_keys_dependencies"])
    async def test_list_api_keys_exclude_revoked(self, extension):
        """Test API key listing excluding revoked keys"""
        # Generate and revoke a key
        generate_result = await extension.generate_api_key(name="revoked_key")
        await extension.revoke_api_key(key_id=generate_result["key_id"])

        # Generate active key
        await extension.generate_api_key(name="active_key")

        # List keys excluding revoked
        result = await extension.list_api_keys(include_revoked=False)

        assert result["success"] is True
        assert len(result["keys"]) == 1
        assert result["keys"][0]["status"] == "active"

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_api_keys_dependencies"])
    async def test_get_usage_statistics_global(self, extension):
        """Test getting global usage statistics"""
        extension.enable_usage_tracking = True
        extension._initialize_usage_tracking()

        result = await extension.get_usage_statistics()

        assert result["success"] is True
        assert "global_statistics" in result
        assert "total_requests" in result["global_statistics"]

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_api_keys_dependencies"])
    async def test_get_usage_statistics_specific_key(self, extension):
        """Test getting usage statistics for specific key"""
        extension.enable_usage_tracking = True
        extension._initialize_usage_tracking()

        # Generate key
        generate_result = await extension.generate_api_key(name="stats_key")
        key_id = generate_result["key_id"]

        result = await extension.get_usage_statistics(key_id=key_id)

        assert result["success"] is True
        assert "key_statistics" in result

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_api_keys_dependencies"])
    async def test_get_usage_statistics_disabled(self, extension):
        """Test getting usage statistics when tracking is disabled"""
        extension.enable_usage_tracking = False

        result = await extension.get_usage_statistics()

        assert result["success"] is False
        assert "not enabled" in result["message"]

    @pytest.mark.dependency(depends=["auth_api_keys_dependencies"])
    def test_generate_raw_key(self, extension):
        """Test raw key generation"""
        key1 = extension._generate_raw_key()
        key2 = extension._generate_raw_key()

        assert isinstance(key1, str)
        assert isinstance(key2, str)
        assert key1 != key2  # Should be unique
        assert len(key1) > 0

    @pytest.mark.dependency(depends=["auth_api_keys_dependencies"])
    def test_hash_key(self, extension):
        """Test key hashing"""
        test_key = "test_api_key_123"
        hash1 = extension._hash_key(test_key)
        hash2 = extension._hash_key(test_key)

        assert isinstance(hash1, str)
        assert hash1 == hash2  # Same input should produce same hash
        assert len(hash1) > 0

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_api_keys_dependencies"])
    async def test_check_user_key_limit(self, extension):
        """Test user key limit checking"""
        extension.max_keys_per_user = 2

        # Should allow first key
        result1 = await extension._check_user_key_limit("user123")
        assert result1 is True

        # Generate keys
        await extension.generate_api_key(name="key1", user_id="user123")
        await extension.generate_api_key(name="key2", user_id="user123")

        # Should not allow third key
        result2 = await extension._check_user_key_limit("user123")
        assert result2 is False

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_api_keys_dependencies"])
    async def test_check_rate_limit_memory_cache(self, extension):
        """Test rate limiting with memory cache"""
        extension.enable_rate_limiting = True
        extension.rate_limit_requests_per_minute = 2
        extension.redis_client = None  # Force memory cache

        test_key_hash = "test_hash"

        # First two requests should be allowed
        result1 = await extension._check_rate_limit(test_key_hash, None)
        result2 = await extension._check_rate_limit(test_key_hash, None)

        assert result1["allowed"] is True
        assert result2["allowed"] is True

        # Third request should be rate limited
        result3 = await extension._check_rate_limit(test_key_hash, None)
        assert result3["allowed"] is False

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_api_keys_dependencies"])
    async def test_update_key_usage(self, extension):
        """Test key usage update"""
        extension.enable_usage_tracking = True
        extension._initialize_usage_tracking()

        # Generate key
        generate_result = await extension.generate_api_key(name="usage_key")
        key_hash = extension._hash_key(generate_result["key"])

        # Update usage
        await extension._update_key_usage(key_hash, "/api/test")

        # Check statistics
        assert extension.usage_statistics["total_requests"] == 1
        assert extension.usage_statistics["requests_by_key"][key_hash] == 1
        assert extension.usage_statistics["requests_by_endpoint"]["/api/test"] == 1

    @pytest.mark.dependency(depends=["auth_api_keys_dependencies"])
    def test_validate_config_all_libraries_available(self):
        """Test configuration validation when all libraries are available"""
        with patch("lib.Logging.logger"):
            mock_libs = {
                "cryptography": MagicMock(),
                "redis": MagicMock(),
            }

            with patch.dict("sys.modules", mock_libs):
                extension = EXT_Auth_APIKeys(
                    enable_rate_limiting=False,  # Disable to avoid Redis checks
                )
                issues = extension.validate_config()

                assert len(issues) == 0

    @pytest.mark.dependency(depends=["auth_api_keys_dependencies"])
    def test_validate_config_missing_cryptography(self):
        """Test configuration validation with missing cryptography"""
        with patch("lib.Logging.logger"):
            with patch.dict("sys.modules", {"cryptography": None}):
                extension = EXT_Auth_APIKeys()
                issues = extension.validate_config()

                assert len(issues) >= 1
                issue_text = " ".join(issues).lower()
                assert "cryptography" in issue_text

    @pytest.mark.dependency(depends=["auth_api_keys_dependencies"])
    def test_validate_config_invalid_hash_algorithm(self):
        """Test configuration validation with invalid hash algorithm"""
        with patch("lib.Logging.logger"):
            extension = EXT_Auth_APIKeys(hash_algorithm="invalid_algorithm")
            issues = extension.validate_config()

            assert len(issues) >= 1
            issue_text = " ".join(issues).lower()
            assert "invalid hash algorithm" in issue_text

    @pytest.mark.dependency(depends=["auth_api_keys_dependencies"])
    def test_get_required_permissions(self, extension):
        """Test getting required permissions"""
        permissions = extension.get_required_permissions()

        assert isinstance(permissions, list)
        assert len(permissions) == 7
        assert "api_keys:create" in permissions
        assert "api_keys:validate" in permissions
        assert "api_keys:revoke" in permissions
        assert "authentication:validate" in permissions

    @pytest.mark.dependency(depends=["auth_api_keys_dependencies"])
    def test_on_start_success(self):
        """Test successful extension start"""
        with patch("lib.Logging.logger"):
            extension = EXT_Auth_APIKeys(enable_rate_limiting=False)
            result = extension.on_start()

            assert result is True

    @pytest.mark.dependency(depends=["auth_api_keys_dependencies"])
    def test_on_start_with_redis_reconnect(self, mock_redis_available):
        """Test extension start with Redis reconnection"""
        with patch("lib.Logging.logger"):
            extension = EXT_Auth_APIKeys(enable_rate_limiting=True)
            extension.redis_client = None  # Simulate disconnected state

            result = extension.on_start()

            assert result is True

    @pytest.mark.dependency(depends=["auth_api_keys_dependencies"])
    def test_on_stop_success(self, extension):
        """Test successful extension stop"""
        # Add some test data
        extension.active_keys["test"] = {"name": "test"}
        extension.rate_limit_cache["test"] = 5
        extension.usage_statistics["total_requests"] = 10

        result = extension.on_stop()

        assert result is True
        assert len(extension.active_keys) == 0
        assert len(extension.rate_limit_cache) == 0
        assert len(extension.usage_statistics) == 0

    @pytest.mark.dependency(depends=["auth_api_keys_dependencies"])
    def test_has_capability(self, extension):
        """Test capability checking"""
        assert extension.has_capability("api_key_generation") is True
        assert extension.has_capability("api_key_validation") is True
        assert extension.has_capability("non_existent_capability") is False

    @pytest.mark.dependency(depends=["auth_api_keys_dependencies"])
    def test_get_active_keys_count(self, extension):
        """Test getting active keys count"""
        # Initially should be 0
        assert extension.get_active_keys_count() == 0

        # Add some test keys
        extension.active_keys["key1"] = {"status": "active"}
        extension.active_keys["key2"] = {"status": "revoked"}
        extension.active_keys["key3"] = {"status": "active"}

        # Should count only active keys
        assert extension.get_active_keys_count() == 2

    @pytest.mark.dependency(depends=["auth_api_keys_dependencies"])
    def test_get_rate_limit_info(self, extension):
        """Test getting rate limit information"""
        extension.enable_rate_limiting = True
        extension.rate_limit_requests_per_minute = 1000
        extension.redis_client = MagicMock()

        info = extension.get_rate_limit_info()

        assert info["enabled"] is True
        assert info["requests_per_minute"] == 1000
        assert info["redis_available"] is True

    @pytest.mark.dependency(depends=["auth_api_keys_dependencies"])
    def test_abilities_discovery(self, extension):
        """Test that all expected abilities are discovered"""
        extension.discover_abilities()

        for expected_ability in self.expected_abilities:
            assert expected_ability in extension.abilities
            assert callable(extension.abilities[expected_ability])

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_api_keys_dependencies"])
    async def test_execute_ability_success(self, extension):
        """Test successful ability execution"""
        result = await extension.execute_ability(
            "generate_api_key",
            {
                "name": "test_key",
                "key_type": "read_only",
            },
        )

        assert "success" in result

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_api_keys_dependencies"])
    async def test_execute_ability_not_found(self, extension):
        """Test executing non-existent ability"""
        result = await extension.execute_ability("non_existent_ability")

        assert "not found" in result

    @pytest.mark.dependency(depends=["auth_api_keys_dependencies"])
    def test_api_key_hooks_registration(self):
        """Test that API key hooks are properly registered"""
        with patch("lib.Logging.logger"):
            with patch(
                "extensions.auth_api_keys.EXT_Auth_APIKeys.AbstractExtension.bll_hook"
            ) as mock_hook:
                extension = EXT_Auth_APIKeys()
                extension._register_api_key_hooks()

                # Should register hooks for key validation and usage tracking
                assert mock_hook.called

    @pytest.mark.dependency(depends=["auth_api_keys_dependencies"])
    def test_lifecycle_methods_integration(self, extension):
        """Test integration of lifecycle methods"""
        # Test startup
        extension.on_startup()

        # Test shutdown
        extension.on_shutdown()

        # These methods should not raise exceptions

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_api_keys_dependencies"])
    async def test_api_key_full_lifecycle(self, extension):
        """Test complete API key lifecycle"""
        # 1. Generate key
        generate_result = await extension.generate_api_key(
            name="lifecycle_test_key",
            user_id="user123",
            permissions=["read", "write"],
        )
        assert generate_result["success"] is True
        api_key = generate_result["key"]
        key_id = generate_result["key_id"]

        # 2. Validate key
        validate_result = await extension.validate_api_key(
            api_key=api_key,
            required_permissions=["read"],
        )
        assert validate_result["success"] is True

        # 3. Rotate key
        rotate_result = await extension.rotate_api_key(key_id=key_id)
        assert rotate_result["success"] is True
        new_key = rotate_result["new_key"]

        # 4. Validate new key works
        validate_new_result = await extension.validate_api_key(api_key=new_key)
        assert validate_new_result["success"] is True

        # 5. Revoke new key
        revoke_result = await extension.revoke_api_key(
            key_id=rotate_result["new_key_id"]
        )
        assert revoke_result["success"] is True

        # 6. Validate revoked key fails
        validate_revoked_result = await extension.validate_api_key(api_key=new_key)
        assert validate_revoked_result["success"] is False

    @pytest.mark.dependency(depends=["auth_api_keys_dependencies"])
    def test_security_key_hashing(self, extension):
        """Test that keys are properly hashed and not stored in plain text"""
        test_key = "ak_test_key_123456789"
        hash1 = extension._hash_key(test_key)
        hash2 = extension._hash_key(test_key)

        # Hash should be consistent
        assert hash1 == hash2

        # Hash should not contain the original key
        assert test_key not in hash1

        # Hash should be hex string
        assert all(c in "0123456789abcdef" for c in hash1)
