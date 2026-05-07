from typing import Any, Dict, List, Optional, Set

from extensions.AbstractExtension import AbstractExtension
from lib.Dependencies import EXT_Dependency, PIP_Dependency
from lib.Logging import logger


class EXT_Auth_Privacy(AbstractExtension):
    """
    Privacy authentication extension for AGInfrastructure.

    Provides comprehensive privacy management capabilities including data consent,
    privacy settings, data retention, anonymization, and GDPR compliance. This extension
    provides static functionality and metadata to organize privacy-related components
    and manage authentication workflows.

    The extension focuses on:
    - User consent management and tracking
    - Privacy settings and preferences
    - Data retention and deletion policies
    - Data anonymization and pseudonymization
    - GDPR/CCPA compliance features
    - Privacy audit trails and reporting
    - Cookie consent and tracking prevention

    Component loading (DB, BLL, EP) is handled automatically by the import system
    based on file naming conventions.
    """

    # Extension metadata
    name = "auth_privacy"
    version = "1.0.0"
    description = (
        "Privacy authentication extension for data consent, privacy settings, "
        "and compliance management"
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
            name="email",
            friendly_name="Email Extension",
            optional=True,
            reason="Sending privacy notices and consent confirmations",
        ),
    ]

    pip_dependencies = [
        PIP_Dependency(
            name="cryptography",
            friendly_name="Cryptography Library",
            optional=False,
            reason="Required for data anonymization and secure data handling",
            semver=">=41.0.0",
        ),
        PIP_Dependency(
            name="hashlib",
            friendly_name="Hash Library",
            optional=False,
            reason="Required for data pseudonymization and anonymization",
            semver=">=3.9.0",
        ),
        PIP_Dependency(
            name="redis",
            friendly_name="Redis Client",
            optional=True,
            reason="Caching for privacy preferences and consent status",
            semver=">=4.5.0",
        ),
    ]

    sys_dependencies = []

    # Define database tables
    db_tables = []  # Will be loaded via import system

    # Define what capabilities this extension provides
    capabilities = [
        "consent_management",
        "privacy_settings",
        "data_retention",
        "data_anonymization",
        "gdpr_compliance",
        "privacy_audit",
        "cookie_consent",
    ]

    # Consent types
    CONSENT_TYPES = {
        "essential": "Essential functionality consent",
        "analytics": "Analytics and performance consent",
        "marketing": "Marketing and advertising consent",
        "personalization": "Personalization consent",
        "third_party": "Third-party services consent",
        "data_processing": "Data processing consent",
    }

    # Privacy settings
    PRIVACY_SETTINGS = {
        "data_sharing": "Control data sharing with third parties",
        "analytics_tracking": "Control analytics and tracking",
        "marketing_communications": "Control marketing communications",
        "profile_visibility": "Control profile visibility",
        "activity_logging": "Control activity logging",
        "location_tracking": "Control location tracking",
    }

    # Data retention periods
    RETENTION_PERIODS = {
        "immediate": "Delete immediately",
        "7_days": "Delete after 7 days",
        "30_days": "Delete after 30 days",
        "90_days": "Delete after 90 days",
        "1_year": "Delete after 1 year",
        "indefinite": "Keep indefinitely (where legally required)",
    }

    def __init__(
        self,
        enable_gdpr_compliance: bool = True,
        enable_ccpa_compliance: bool = False,
        default_retention_days: int = 365,
        enable_consent_tracking: bool = True,
        enable_data_anonymization: bool = True,
        anonymization_algorithm: str = "sha256",
        enable_audit_logging: bool = True,
        consent_expiry_days: int = 365,
        **kwargs,
    ):
        """
        Initialize the Privacy extension.
        """
        super().__init__(**kwargs)

        self.enable_gdpr_compliance = enable_gdpr_compliance
        self.enable_ccpa_compliance = enable_ccpa_compliance
        self.default_retention_days = default_retention_days
        self.enable_consent_tracking = enable_consent_tracking
        self.enable_data_anonymization = enable_data_anonymization
        self.anonymization_algorithm = anonymization_algorithm
        self.enable_audit_logging = enable_audit_logging
        self.consent_expiry_days = consent_expiry_days

        # Internal state
        self.redis_client = None
        self.audit_logger = None

    def on_initialize(self) -> bool:
        """
        Initialize the Privacy extension.
        """
        logger.debug("Initializing Privacy Extension...")

        try:
            # Initialize Redis if available
            self._initialize_redis()

            # Initialize audit logging
            self._initialize_audit_logging()

            # Register privacy hooks
            self._register_privacy_hooks()

            # Register capabilities
            for capability in self.capabilities:
                self.register_capability(capability)

            logger.debug("Privacy extension initialized successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to initialize Privacy extension: {str(e)}")
            return False

    def _initialize_redis(self):
        """Initialize Redis connection for caching."""
        try:
            import redis

            self.redis_client = redis.Redis(
                host="localhost", port=6379, db=1, decode_responses=True
            )
            self.redis_client.ping()
            logger.debug("Redis connection established for Privacy extension")
        except Exception as e:
            logger.warning(f"Redis not available for Privacy extension: {e}")
            self.redis_client = None

    def _initialize_audit_logging(self):
        """Initialize audit logging for privacy events."""
        if self.enable_audit_logging:
            try:
                import logging

                self.audit_logger = logging.getLogger("privacy_audit")
                logger.debug("Privacy audit logging initialized")
            except Exception as e:
                logger.warning(f"Failed to initialize audit logging: {e}")
                self.audit_logger = None

    def _register_privacy_hooks(self):
        """Register hooks for privacy-related events."""
        try:

            @AbstractExtension.bll_hook("Auth", "UserManager", "create", "after")
            def privacy_user_creation_hook(manager, result, create_args=None):
                """Set default privacy settings for new users."""
                if result and "user_id" in result:
                    # Set default privacy settings asynchronously
                    import asyncio

                    asyncio.create_task(
                        self._set_default_privacy_settings(result["user_id"])
                    )

            @AbstractExtension.bll_hook("Auth", "UserManager", "delete", "before")
            def privacy_user_deletion_hook(manager, user_data, create_args=None):
                """Handle privacy compliance before user deletion."""
                if user_data and "user_id" in user_data:
                    # Log privacy deletion for audit
                    self._log_privacy_event(
                        user_data["user_id"],
                        "user_deletion",
                        {"reason": "account_deletion"},
                    )

        except Exception as e:
            logger.warning(f"Failed to register privacy hooks: {e}")

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

    @AbstractExtension.ability("manage_consent")
    async def manage_consent(
        self,
        user_id: str,
        consent_type: str,
        granted: bool,
        purpose: Optional[str] = None,
        source: str = "user_action",
    ) -> Dict[str, Any]:
        """
        Manage user consent for specific purposes.
        """
        try:
            if consent_type not in self.CONSENT_TYPES:
                return {
                    "success": False,
                    "message": f"Invalid consent type: {consent_type}",
                }

            # Store consent decision
            consent_data = {
                "user_id": user_id,
                "consent_type": consent_type,
                "granted": granted,
                "purpose": purpose or self.CONSENT_TYPES[consent_type],
                "source": source,
                "timestamp": "now()",
                "expires_at": f"now() + interval '{self.consent_expiry_days} days'",
            }

            # Cache consent if Redis available
            if self.redis_client:
                cache_key = f"consent:{user_id}:{consent_type}"
                self.redis_client.setex(
                    cache_key, 3600 * 24 * self.consent_expiry_days, str(granted)
                )

            # Log privacy event
            self._log_privacy_event(
                user_id,
                "consent_updated",
                {"consent_type": consent_type, "granted": granted, "source": source},
            )

            return {
                "success": True,
                "message": f"Consent {consent_type} {'granted' if granted else 'revoked'} successfully",
                "consent": consent_data,
            }

        except Exception as e:
            logger.error(f"Error managing consent: {e}")
            return {"success": False, "message": f"Error managing consent: {str(e)}"}

    @AbstractExtension.ability("update_privacy_settings")
    async def update_privacy_settings(
        self,
        user_id: str,
        settings: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Update user privacy settings.
        """
        try:
            valid_settings = {}
            invalid_settings = []

            for setting, value in settings.items():
                if setting in self.PRIVACY_SETTINGS:
                    valid_settings[setting] = value
                else:
                    invalid_settings.append(setting)

            if not valid_settings and invalid_settings:
                return {
                    "success": False,
                    "message": f"Invalid privacy settings: {invalid_settings}",
                }

            # Cache settings if Redis available
            if self.redis_client:
                for setting, value in valid_settings.items():
                    cache_key = f"privacy:{user_id}:{setting}"
                    self.redis_client.setex(cache_key, 3600 * 24 * 30, str(value))

            # Log privacy event
            self._log_privacy_event(
                user_id, "privacy_settings_updated", {"settings": valid_settings}
            )

            result = {
                "success": True,
                "message": "Privacy settings updated successfully",
                "updated_settings": valid_settings,
            }

            if invalid_settings:
                result["warnings"] = f"Invalid settings ignored: {invalid_settings}"

            return result

        except Exception as e:
            logger.error(f"Error updating privacy settings: {e}")
            return {
                "success": False,
                "message": f"Error updating privacy settings: {str(e)}",
            }

    @AbstractExtension.ability("request_data_deletion")
    async def request_data_deletion(
        self,
        user_id: str,
        data_types: Optional[List[str]] = None,
        reason: str = "user_request",
    ) -> Dict[str, Any]:
        """
        Request deletion of user data (Right to be Forgotten).
        """
        try:
            data_types = data_types or ["all"]

            # Create deletion request
            deletion_request = {
                "user_id": user_id,
                "data_types": data_types,
                "reason": reason,
                "status": "pending",
                "requested_at": "now()",
                "estimated_completion": f"now() + interval '30 days'",
            }

            # Log privacy event
            self._log_privacy_event(
                user_id,
                "data_deletion_requested",
                {"data_types": data_types, "reason": reason},
            )

            return {
                "success": True,
                "message": "Data deletion request submitted successfully",
                "request": deletion_request,
            }

        except Exception as e:
            logger.error(f"Error requesting data deletion: {e}")
            return {
                "success": False,
                "message": f"Error requesting data deletion: {str(e)}",
            }

    @AbstractExtension.ability("anonymize_user_data")
    async def anonymize_user_data(
        self,
        user_id: str,
        fields: Optional[List[str]] = None,
        preserve_analytics: bool = True,
    ) -> Dict[str, Any]:
        """
        Anonymize user data while preserving analytics.
        """
        try:
            if not self.enable_data_anonymization:
                return {
                    "success": False,
                    "message": "Data anonymization is not enabled",
                }

            fields = fields or ["email", "name", "phone", "address"]
            anonymized_data = {}

            for field in fields:
                # Generate anonymous hash
                import hashlib

                hash_obj = hashlib.new(self.anonymization_algorithm)
                hash_obj.update(f"{user_id}:{field}".encode())
                anonymized_data[field] = f"anon_{hash_obj.hexdigest()[:8]}"

            # Log privacy event
            self._log_privacy_event(
                user_id,
                "data_anonymized",
                {"fields": fields, "preserve_analytics": preserve_analytics},
            )

            return {
                "success": True,
                "message": "User data anonymized successfully",
                "anonymized_fields": fields,
                "preserve_analytics": preserve_analytics,
            }

        except Exception as e:
            logger.error(f"Error anonymizing user data: {e}")
            return {
                "success": False,
                "message": f"Error anonymizing user data: {str(e)}",
            }

    @AbstractExtension.ability("get_privacy_report")
    async def get_privacy_report(
        self,
        user_id: str,
        report_type: str = "full",
        include_audit_trail: bool = True,
    ) -> Dict[str, Any]:
        """
        Generate privacy report for user (Data Portability).
        """
        try:
            report = {
                "user_id": user_id,
                "report_type": report_type,
                "generated_at": "now()",
                "consent_status": {},
                "privacy_settings": {},
                "data_retention": {},
            }

            # Get consent status from cache or database
            if self.redis_client:
                for consent_type in self.CONSENT_TYPES:
                    cache_key = f"consent:{user_id}:{consent_type}"
                    consent_status = self.redis_client.get(cache_key)
                    if consent_status:
                        report["consent_status"][consent_type] = (
                            consent_status == "True"
                        )

            # Get privacy settings from cache
            if self.redis_client:
                for setting in self.PRIVACY_SETTINGS:
                    cache_key = f"privacy:{user_id}:{setting}"
                    setting_value = self.redis_client.get(cache_key)
                    if setting_value:
                        report["privacy_settings"][setting] = setting_value

            if include_audit_trail:
                # Add audit trail (implement based on your audit storage)
                report["audit_trail"] = f"Audit trail for user {user_id}"

            return {
                "success": True,
                "message": "Privacy report generated successfully",
                "report": report,
            }

        except Exception as e:
            logger.error(f"Error generating privacy report: {e}")
            return {
                "success": False,
                "message": f"Error generating privacy report: {str(e)}",
            }

    async def _set_default_privacy_settings(self, user_id: str):
        """Set default privacy settings for new users."""
        try:
            default_settings = {
                "data_sharing": "opt_out",
                "analytics_tracking": "essential_only",
                "marketing_communications": "opt_out",
                "profile_visibility": "private",
                "activity_logging": "essential_only",
                "location_tracking": "opt_out",
            }

            await self.update_privacy_settings(user_id, default_settings)
            logger.debug(f"Default privacy settings set for user {user_id}")

        except Exception as e:
            logger.error(f"Error setting default privacy settings: {e}")

    def _log_privacy_event(
        self, user_id: str, event_type: str, details: Dict[str, Any]
    ):
        """Log privacy-related events for audit purposes."""
        if self.audit_logger:
            try:
                audit_entry = {
                    "user_id": user_id,
                    "event_type": event_type,
                    "timestamp": "now()",
                    "details": details,
                }
                self.audit_logger.info(f"Privacy Event: {audit_entry}")
            except Exception as e:
                logger.warning(f"Failed to log privacy event: {e}")

    def on_start(self) -> bool:
        """
        Start the Privacy extension.
        """
        try:
            logger.debug("Privacy extension started successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to start Privacy extension: {e}")
            return False

    def on_stop(self) -> bool:
        """
        Stop the Privacy extension.
        """
        try:
            if self.redis_client:
                self.redis_client.close()

            logger.debug("Privacy extension stopped successfully")
            return True
        except Exception as e:
            logger.error(f"Error stopping Privacy extension: {e}")
            return False

    def on_startup(self):
        """
        Called during application startup.
        """
        logger.debug("Privacy extension startup hook called")

    def on_shutdown(self):
        """
        Called during application shutdown.
        """
        logger.debug("Privacy extension shutdown hook called")

    def validate_config(self) -> List[str]:
        """
        Validate the extension configuration.
        """
        issues = []

        if self.consent_expiry_days <= 0:
            issues.append("Consent expiry days must be positive")

        if self.default_retention_days <= 0:
            issues.append("Default retention days must be positive")

        if self.anonymization_algorithm not in ["sha256", "sha512", "md5"]:
            issues.append(
                f"Unsupported anonymization algorithm: {self.anonymization_algorithm}"
            )

        return issues

    def get_required_permissions(self) -> List[str]:
        """
        Return the list of permissions required by this extension.
        """
        return [
            "privacy:manage_consent",
            "privacy:update_settings",
            "privacy:request_deletion",
            "privacy:anonymize_data",
            "privacy:generate_reports",
            "auth:read_users",
            "auth:update_users",
        ]

    def has_capability(self, capability: str) -> bool:
        """Check if this extension has a specific capability."""
        return capability in self.capabilities

    def get_consent_types(self) -> Dict[str, str]:
        """Return available consent types."""
        return self.CONSENT_TYPES

    def get_privacy_settings(self) -> Dict[str, str]:
        """Return available privacy settings."""
        return self.PRIVACY_SETTINGS

    def get_retention_periods(self) -> Dict[str, str]:
        """Return available retention periods."""
        return self.RETENTION_PERIODS
