import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set

from extensions.AbstractExtension import AbstractExtension
from extensions.auth_api_keys.DB_Auth_APIKeys import APIKey
from lib.Dependencies import EXT_Dependency, PIP_Dependency
from lib.Environment import env
from lib.Logging import logger


class EXT_Auth_APIKeys(AbstractExtension):
    """
    API Keys authentication extension for AGInfrastructure.

    Provides comprehensive API key management capabilities including key generation,
    validation, rotation, and access control. This extension provides static
    functionality and metadata to organize API key-related components and manage
    authentication workflows.

    The extension focuses on:
    - API key generation and management
    - Key hashing and secure storage
    - Key validation and authentication
    - Access control and permissions
    - Key rotation and expiration
    - Rate limiting and usage tracking
    - Scoped access and role-based permissions

    Component loading (DB, BLL, EP) is handled automatically by the import system
    based on file naming conventions.
    """

    # Extension metadata
    name = "auth_api_keys"
    version = "1.0.0"
    description = "API Keys authentication extension for programmatic access management"

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
    ]

    pip_dependencies = [
        PIP_Dependency(
            name="cryptography",
            friendly_name="Cryptography Library",
            optional=False,
            reason="Required for secure API key generation and hashing",
            semver=">=41.0.0",
        ),
        PIP_Dependency(
            name="bcrypt",
            friendly_name="bcrypt Password Hashing",
            optional=True,
            reason="Alternative secure hashing for API keys",
            semver=">=4.0.0",
        ),
        PIP_Dependency(
            name="redis",
            friendly_name="Redis Client",
            optional=True,
            reason="Rate limiting and session management for API keys",
            semver=">=4.5.0",
        ),
    ]

    sys_dependencies = []

    # Define database tables
    db_tables = [APIKey]

    # Define what capabilities this extension provides
    capabilities = [
        "api_key_generation",
        "api_key_validation",
        "api_key_management",
        "access_control",
        "rate_limiting",
        "key_rotation",
        "usage_tracking",
    ]

    # API key types
    API_KEY_TYPES = {
        "read_only": "Read-only access to resources",
        "read_write": "Read and write access to resources",
        "admin": "Administrative access with full permissions",
        "service": "Service-to-service authentication",
        "webhook": "Webhook endpoint authentication",
        "public": "Public API access with limited permissions",
    }

    # Key statuses
    KEY_STATUSES = {
        "active": "Key is active and can be used",
        "expired": "Key has expired and cannot be used",
        "revoked": "Key has been manually revoked",
        "suspended": "Key is temporarily suspended",
        "pending": "Key is pending activation",
    }

    def __init__(
        self,
        key_length: int = 32,
        key_prefix: str = "ak_",
        default_expiry_days: int = 365,
        enable_rate_limiting: bool = True,
        rate_limit_requests_per_minute: int = 1000,
        enable_usage_tracking: bool = True,
        max_keys_per_user: int = 10,
        enable_key_rotation: bool = True,
        rotation_warning_days: int = 30,
        hash_algorithm: str = "sha256",
        **kwargs,
    ):
        super().__init__(**kwargs)

        # Store configuration
        self.key_length = key_length
        self.key_prefix = key_prefix
        self.default_expiry_days = default_expiry_days
        self.enable_rate_limiting = enable_rate_limiting
        self.rate_limit_requests_per_minute = rate_limit_requests_per_minute
        self.enable_usage_tracking = enable_usage_tracking
        self.max_keys_per_user = max_keys_per_user
        self.enable_key_rotation = enable_key_rotation
        self.rotation_warning_days = rotation_warning_days
        self.hash_algorithm = hash_algorithm

        # Provider instance reference
        self.provider = None
        self.redis_client = None
        self.usage_tracker = None
        self.rate_limiter = None

    def on_initialize(self) -> bool:
        """
        Initialize the API Keys extension.
        """
        logger.debug("Initializing API Keys Extension...")

        try:
            # Create provider instance
            self._create_provider()

            # Initialize Redis if rate limiting or usage tracking is enabled
            if self.enable_rate_limiting or self.enable_usage_tracking:
                self._initialize_redis()

            # Register API key hooks
            self._register_api_key_hooks()

            # Initialize usage tracking if enabled
            if self.enable_usage_tracking:
                self._initialize_usage_tracking()

            # Schedule key rotation checks if enabled
            if self.enable_key_rotation:
                self._schedule_key_rotation_checks()

            # Register capabilities
            for capability in self.capabilities:
                self.register_capability(capability)

            logger.debug("API Keys extension initialized successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to initialize API Keys extension: {str(e)}")
            return False

    def _create_provider(self):
        """
        Create and configure the API keys provider instance.
        """
        try:
            self.provider = {
                "type": "auth_api_keys",
                "redis_client": None,
                "usage_tracker": None,
                "rate_limiter": {},
                "active_keys": {},
                "active_keys_count": 0,
            }

            logger.debug("API Keys provider created successfully")

        except Exception as e:
            logger.error(f"Failed to create API Keys provider: {str(e)}")
            self.provider = None

    def _initialize_redis(self):
        """Initialize Redis connection for rate limiting."""
        try:
            import redis

            redis_url = env("REDIS_URL")
            if redis_url:
                self.redis_client = redis.from_url(redis_url)

                if self.provider:
                    self.provider["redis_client"] = self.redis_client

                logger.debug("Redis client initialized for API key rate limiting")
            else:
                logger.warning(
                    "REDIS_URL not configured, using in-memory rate limiting"
                )
                # Initialize in-memory structures
                self.rate_limiter = {}

                if self.provider:
                    self.provider["rate_limiter"] = self.rate_limiter

        except ImportError:
            logger.warning("Redis library not available, using in-memory rate limiting")
            self.rate_limiter = {}

            if self.provider:
                self.provider["rate_limiter"] = self.rate_limiter
        except Exception as e:
            logger.error(f"Failed to initialize Redis: {str(e)}")
            self.rate_limiter = {}

            if self.provider:
                self.provider["rate_limiter"] = self.rate_limiter

    def _register_api_key_hooks(self):
        """Register hooks for API key-related operations."""
        try:

            @AbstractExtension.bll_hook("Auth", "APIKeyManager", "create", "before")
            def validate_api_key_hook(manager, key_data, create_args=None):
                """Hook to validate API keys before creation."""
                try:
                    if hasattr(key_data, "user_id") and key_data.user_id:
                        self._validate_user_key_limits(key_data.user_id)
                        logger.debug("API key validation hook executed successfully")
                except Exception as e:
                    logger.error(f"Error in API key validation hook: {e}")

            @AbstractExtension.bll_hook(
                "Auth", "APIKeyManager", "authenticate", "after"
            )
            def track_api_key_usage_hook(manager, result, create_args=None):
                """Hook to track API key usage."""
                try:
                    if self.enable_usage_tracking and result:
                        self._track_key_usage(result)
                        logger.debug("API key usage tracking hook executed")
                except Exception as e:
                    logger.error(f"Error in API key usage tracking hook: {e}")

        except Exception as e:
            logger.error(f"Error registering API key hooks: {e}")

    def _initialize_usage_tracking(self):
        """Initialize usage tracking system."""
        self.usage_tracker = {
            "total_requests": 0,
            "requests_by_key": {},
            "requests_by_endpoint": {},
            "requests_by_hour": {},
        }

        if self.provider:
            self.provider["usage_tracker"] = self.usage_tracker

    def _schedule_key_rotation_checks(self):
        """Schedule periodic key rotation checks."""
        # In a real implementation, this would schedule a background task
        logger.debug("Key rotation checks scheduling enabled")

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

    @AbstractExtension.ability("generate_api_key")
    async def generate_api_key(
        self,
        name: str,
        user_id: Optional[str] = None,
        team_id: Optional[str] = None,
        role_id: Optional[str] = None,
        key_type: str = "read_write",
        expires_at: Optional[datetime] = None,
        permissions: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Generate a new API key.
        """
        try:
            # Check user key limits
            if user_id and not await self._check_user_key_limit(user_id):
                return {"success": False, "message": "User API key limit exceeded"}

            # Generate raw key
            raw_key = self._generate_raw_key()

            # Create full key with prefix
            full_key = f"{self.key_prefix}{raw_key}"

            # Hash the key for storage
            key_hash = self._hash_key(full_key)

            # Set expiration
            if expires_at is None:
                expires_at = datetime.utcnow() + timedelta(
                    days=self.default_expiry_days
                )

            # Create key data
            key_data = {
                "name": name,
                "key_hash": key_hash,
                "user_id": user_id,
                "team_id": team_id,
                "role_id": role_id,
                "key_type": key_type,
                "permissions": permissions or [],
                "metadata": metadata or {},
                "status": "active",
                "expires_at": expires_at.isoformat(),
                "created_at": datetime.utcnow().isoformat(),
                "last_used_at": None,
                "usage_count": 0,
            }

            # Generate key ID
            import uuid

            key_id = str(uuid.uuid4())
            key_data["id"] = key_id

            # Cache key for validation
            self.provider["active_keys_count"] += 1
            self.provider["active_keys"][key_hash] = key_data

            logger.debug(f"Generated API key: {name} ({key_id})")
            return {
                "success": True,
                "key": full_key,  # Return the actual key only once
                "key_data": {k: v for k, v in key_data.items() if k != "key_hash"},
                "key_id": key_id,
            }

        except Exception as e:
            logger.error(f"Error generating API key: {e}")
            return {
                "success": False,
                "message": f"Failed to generate API key: {str(e)}",
            }

    @AbstractExtension.ability("validate_api_key")
    async def validate_api_key(
        self,
        api_key: str,
        required_permissions: Optional[List[str]] = None,
        endpoint: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Validate an API key and check permissions.
        """
        try:
            # Hash the provided key
            key_hash = self._hash_key(api_key)

            # Check if key exists and is active
            key_data = self.provider["active_keys"].get(key_hash)
            if not key_data:
                return {"success": False, "message": "Invalid API key"}

            # Check key status
            if key_data.get("status") != "active":
                return {
                    "success": False,
                    "message": f"API key is {key_data.get('status')}",
                }

            # Check expiration
            expires_at = datetime.fromisoformat(key_data.get("expires_at", ""))
            if datetime.utcnow() > expires_at:
                key_data["status"] = "expired"
                return {"success": False, "message": "API key has expired"}

            # Check rate limiting
            if self.enable_rate_limiting:
                rate_limit_check = await self._check_rate_limit(key_hash, endpoint)
                if not rate_limit_check["allowed"]:
                    return {
                        "success": False,
                        "message": "Rate limit exceeded",
                        "rate_limit": rate_limit_check,
                    }

            # Check permissions
            if required_permissions:
                key_permissions = key_data.get("permissions", [])
                if not all(perm in key_permissions for perm in required_permissions):
                    return {"success": False, "message": "Insufficient permissions"}

            # Update usage statistics
            await self._update_key_usage(key_hash, endpoint)

            return {
                "success": True,
                "key_data": {k: v for k, v in key_data.items() if k != "key_hash"},
                "message": "API key is valid",
            }

        except Exception as e:
            logger.error(f"Error validating API key: {e}")
            return {"success": False, "message": f"API key validation failed: {str(e)}"}

    @AbstractExtension.ability("revoke_api_key")
    async def revoke_api_key(
        self,
        key_id: Optional[str] = None,
        key_hash: Optional[str] = None,
        reason: str = "Manual revocation",
    ) -> Dict[str, Any]:
        """
        Revoke an API key.
        """
        try:
            target_key = None
            target_hash = key_hash

            # Find the key
            if key_id:
                for hash_val, key_data in self.provider["active_keys"].items():
                    if key_data.get("id") == key_id:
                        target_key = key_data
                        target_hash = hash_val
                        break
            elif key_hash:
                target_key = self.provider["active_keys"].get(key_hash)

            if not target_key:
                return {"success": False, "message": "API key not found"}

            # Update key status
            target_key["status"] = "revoked"
            target_key["revoked_at"] = datetime.utcnow().isoformat()
            target_key["revocation_reason"] = reason

            logger.debug(f"Revoked API key: {target_key.get('name')} - {reason}")
            return {"success": True, "message": "API key revoked successfully"}

        except Exception as e:
            logger.error(f"Error revoking API key: {e}")
            return {"success": False, "message": f"Failed to revoke API key: {str(e)}"}

    @AbstractExtension.ability("rotate_api_key")
    async def rotate_api_key(
        self,
        key_id: str,
        new_name: Optional[str] = None,
        extend_expiry: bool = True,
    ) -> Dict[str, Any]:
        """
        Rotate an API key (generate new key, keep same permissions).
        """
        try:
            # Find the existing key
            old_key_data = None
            old_hash = None
            for hash_val, key_data in self.provider["active_keys"].items():
                if key_data.get("id") == key_id:
                    old_key_data = key_data
                    old_hash = hash_val
                    break

            if not old_key_data:
                return {"success": False, "message": "API key not found"}

            # Generate new key
            raw_key = self._generate_raw_key()
            full_key = f"{self.key_prefix}{raw_key}"
            new_hash = self._hash_key(full_key)

            # Create new key data
            new_key_data = old_key_data.copy()
            new_key_data["key_hash"] = new_hash
            new_key_data["name"] = new_name or f"{old_key_data['name']} (Rotated)"
            new_key_data["created_at"] = datetime.utcnow().isoformat()
            new_key_data["rotated_from"] = key_id

            # Extend expiry if requested
            if extend_expiry:
                new_expiry = datetime.utcnow() + timedelta(
                    days=self.default_expiry_days
                )
                new_key_data["expires_at"] = new_expiry.isoformat()

            # Generate new ID
            import uuid

            new_key_id = str(uuid.uuid4())
            new_key_data["id"] = new_key_id

            # Add new key and mark old as rotated
            self.provider["active_keys"][new_hash] = new_key_data
            self.provider["active_keys"][old_hash]["status"] = "rotated"
            self.provider["active_keys"][old_hash][
                "rotated_at"
            ] = datetime.utcnow().isoformat()
            self.provider["active_keys"][old_hash]["rotated_to"] = new_key_id

            logger.debug(f"Rotated API key: {old_key_data['name']}")
            return {
                "success": True,
                "new_key": full_key,
                "new_key_id": new_key_id,
                "old_key_id": key_id,
            }

        except Exception as e:
            logger.error(f"Error rotating API key: {e}")
            return {"success": False, "message": f"Failed to rotate API key: {str(e)}"}

    @AbstractExtension.ability("list_api_keys")
    async def list_api_keys(
        self,
        user_id: Optional[str] = None,
        team_id: Optional[str] = None,
        status_filter: Optional[str] = None,
        include_revoked: bool = False,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """
        List API keys with optional filtering.
        """
        try:
            keys = []

            for key_data in self.provider["active_keys"].values():
                # Apply filters
                if user_id and key_data.get("user_id") != user_id:
                    continue
                if team_id and key_data.get("team_id") != team_id:
                    continue
                if status_filter and key_data.get("status") != status_filter:
                    continue
                if not include_revoked and key_data.get("status") in [
                    "revoked",
                    "rotated",
                ]:
                    continue

                # Remove sensitive data
                safe_key_data = {k: v for k, v in key_data.items() if k != "key_hash"}
                keys.append(safe_key_data)

            # Sort by creation date (newest first)
            keys.sort(key=lambda x: x.get("created_at", ""), reverse=True)

            # Apply limit
            keys = keys[:limit]

            return {"success": True, "keys": keys, "total": len(keys)}

        except Exception as e:
            logger.error(f"Error listing API keys: {e}")
            return {"success": False, "message": f"Failed to list API keys: {str(e)}"}

    @AbstractExtension.ability("get_usage_statistics")
    async def get_usage_statistics(
        self,
        key_id: Optional[str] = None,
        time_range_hours: int = 24,
    ) -> Dict[str, Any]:
        """
        Get usage statistics for API keys.
        """
        try:
            if not self.enable_usage_tracking:
                return {"success": False, "message": "Usage tracking not enabled"}

            stats = self.usage_tracker.copy()

            if key_id:
                # Get stats for specific key
                key_stats = {}
                for hash_val, key_data in self.provider["active_keys"].items():
                    if key_data.get("id") == key_id:
                        key_stats = {
                            "usage_count": key_data.get("usage_count", 0),
                            "last_used_at": key_data.get("last_used_at"),
                            "requests": stats["requests_by_key"].get(hash_val, 0),
                        }
                        break

                return {"success": True, "key_statistics": key_stats}
            else:
                # Return global stats
                return {"success": True, "global_statistics": stats}

        except Exception as e:
            logger.error(f"Error getting usage statistics: {e}")
            return {
                "success": False,
                "message": f"Failed to get usage statistics: {str(e)}",
            }

    # Helper methods
    def _generate_raw_key(self) -> str:
        """Generate a raw API key."""
        return secrets.token_urlsafe(self.key_length)

    def _hash_key(self, key: str) -> str:
        """Hash an API key for secure storage."""
        return hashlib.new(self.hash_algorithm, key.encode()).hexdigest()

    async def _check_user_key_limit(self, user_id: str) -> bool:
        """Check if user has reached their API key limit."""
        user_key_count = sum(
            1
            for key_data in self.provider["active_keys"].values()
            if key_data.get("user_id") == user_id and key_data.get("status") == "active"
        )
        return user_key_count < self.max_keys_per_user

    def _validate_user_key_limits(self, user_id: str):
        """Validate user key limits (used in hooks)."""
        # Implementation for hook validation
        pass

    def _check_rate_limit(
        self, key_hash: str, endpoint: Optional[str]
    ) -> Dict[str, Any]:
        """Check rate limit for API key."""
        try:
            from datetime import datetime

            current_time = datetime.utcnow()
            current_minute = current_time.replace(second=0, microsecond=0)

            if self.redis_client:
                # Use Redis for distributed rate limiting
                cache_key = f"rate_limit:{key_hash}:{current_minute.isoformat()}"
                current_count = self.redis_client.incr(cache_key)
                self.redis_client.expire(cache_key, 60)  # Expire after 1 minute
            else:
                # Use memory cache
                rate_limiter = (
                    self.provider.get("rate_limiter", {}) if self.provider else {}
                )
                cache_key = f"{key_hash}:{current_minute.isoformat()}"
                current_count = rate_limiter.get(cache_key, 0) + 1
                rate_limiter[cache_key] = current_count

            allowed = current_count <= self.rate_limit_requests_per_minute
            remaining = max(0, self.rate_limit_requests_per_minute - current_count)

            return {
                "allowed": allowed,
                "current_count": current_count,
                "limit": self.rate_limit_requests_per_minute,
                "remaining": remaining,
                "reset_at": (current_minute.timestamp() + 60),
            }

        except Exception as e:
            logger.error(f"Error checking rate limit: {str(e)}")
            return {
                "allowed": True,
                "current_count": 0,
                "limit": self.rate_limit_requests_per_minute,
            }

    def _update_key_usage(self, key_hash: str, endpoint: Optional[str]):
        """Update API key usage statistics."""
        try:
            usage_tracker = (
                self.provider.get("usage_tracker", {}) if self.provider else {}
            )
            active_keys = self.provider.get("active_keys", {}) if self.provider else {}

            # Update global stats
            usage_tracker["total_requests"] = usage_tracker.get("total_requests", 0) + 1

            # Update per-key stats
            if key_hash in usage_tracker.get("requests_by_key", {}):
                usage_tracker["requests_by_key"][key_hash] += 1
            else:
                usage_tracker.setdefault("requests_by_key", {})[key_hash] = 1

            # Update per-endpoint stats
            if endpoint:
                if endpoint in usage_tracker.get("requests_by_endpoint", {}):
                    usage_tracker["requests_by_endpoint"][endpoint] += 1
                else:
                    usage_tracker.setdefault("requests_by_endpoint", {})[endpoint] = 1

            # Update key data
            if key_hash in active_keys:
                active_keys[key_hash]["usage_count"] = (
                    active_keys[key_hash].get("usage_count", 0) + 1
                )
                from datetime import datetime

                active_keys[key_hash]["last_used_at"] = datetime.utcnow().isoformat()

            # Store in Redis if available (synchronous calls)
            if self.redis_client:
                try:
                    self.redis_client.hincrby("api_key_usage", key_hash, 1)
                    if endpoint:
                        self.redis_client.hincrby("endpoint_usage", endpoint, 1)
                except Exception as redis_error:
                    logger.warning(f"Redis update failed: {str(redis_error)}")

        except Exception as e:
            logger.error(f"Error updating key usage: {str(e)}")

    def _track_key_usage(self, result: Any):
        """Track key usage for analytics (used in hooks)."""
        # Implementation for hook tracking
        pass

    # Extension lifecycle methods
    def on_start(self) -> bool:
        """
        Lifecycle method called when the extension is started.
        """
        try:
            logger.debug("Starting API Keys extension...")

            # Verify provider is ready
            if not self.provider:
                logger.warning("API Keys provider not initialized")
                return False

            # Test Redis connection if enabled
            if self.redis_client:
                try:
                    self.redis_client.ping()
                    logger.debug("Redis connection verified")
                except Exception as e:
                    logger.warning(f"Redis connection test failed: {str(e)}")

            logger.debug("API Keys extension started successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to start API Keys extension: {str(e)}")
            return False

    def on_stop(self) -> bool:
        """
        Lifecycle method called when the extension is stopped.
        """
        try:
            logger.debug("Stopping API Keys extension...")

            # Close Redis connections
            if self.redis_client:
                try:
                    self.redis_client.close()
                    logger.debug("Closed Redis client")
                except Exception as e:
                    logger.warning(f"Error closing Redis client: {str(e)}")

            logger.debug("API Keys extension stopped successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to stop API Keys extension: {str(e)}")
            return False

    def on_startup(self):
        """Called when the application starts up."""
        logger.debug("API Keys extension startup complete")

        # Log configuration
        logger.debug(f"Rate limiting enabled: {self.enable_rate_limiting}")
        logger.debug(f"Usage tracking enabled: {self.enable_usage_tracking}")

    def on_shutdown(self):
        """Called when the application shuts down."""
        logger.debug("API Keys extension shutdown complete")

        # Perform final cleanup
        try:
            if self.provider:
                active_count = self.provider.get("active_keys_count", 0)
                logger.debug(f"Active API keys at shutdown: {active_count}")

        except Exception as e:
            logger.warning(f"Error during shutdown cleanup: {str(e)}")

    def validate_config(self) -> List[str]:
        """Validate the extension configuration."""
        issues = []

        # Check for required Python packages
        try:
            import cryptography
        except ImportError:
            issues.append(
                "Cryptography library not installed - secure key generation will not work"
            )

        # Check hash algorithm
        try:
            hashlib.new(self.hash_algorithm)
        except ValueError:
            issues.append(f"Invalid hash algorithm: {self.hash_algorithm}")

        # Check Redis connection if rate limiting enabled
        if self.enable_rate_limiting and not self.redis_client:
            try:
                import redis

                redis_url = env("REDIS_URL")
                if not redis_url:
                    issues.append(
                        "Redis URL not configured but rate limiting is enabled"
                    )
            except ImportError:
                issues.append("Redis not available but rate limiting is enabled")

        return issues

    def get_required_permissions(self) -> List[str]:
        """Return the list of permissions required by this extension."""
        return [
            "api_keys:create",
            "api_keys:read",
            "api_keys:update",
            "api_keys:delete",
            "api_keys:revoke",
            "api_keys:rotate",
            "authentication:validate",
        ]

    def has_capability(self, capability: str) -> bool:
        """Check if this extension has a specific capability."""
        return capability in self.capabilities

    def get_api_key_types(self) -> Dict[str, str]:
        """Get available API key types."""
        return self.API_KEY_TYPES.copy()

    def get_key_statuses(self) -> Dict[str, str]:
        """Get available key statuses."""
        return self.KEY_STATUSES.copy()

    def get_active_keys_count(self) -> int:
        """Get the count of currently active API keys."""
        if not self.provider:
            return 0

        active_keys = self.provider.get("active_keys", {})
        return sum(
            1 for key_data in active_keys.values() if key_data.get("status") == "active"
        )

    def get_rate_limit_info(self) -> Dict[str, Any]:
        """Get rate limiting configuration."""
        return {
            "enabled": self.enable_rate_limiting,
            "requests_per_minute": self.rate_limit_requests_per_minute,
            "redis_available": self.redis_client is not None,
        }
