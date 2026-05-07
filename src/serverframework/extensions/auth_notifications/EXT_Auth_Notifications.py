import asyncio
import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set

from extensions.AbstractExtension import AbstractExtension
from lib.Dependencies import EXT_Dependency, PIP_Dependency
from lib.Environment import env
from lib.Logging import logger


class EXT_Auth_Notifications(AbstractExtension):
    """
    Authentication Notifications extension for AGInfrastructure.

    Provides comprehensive authentication notification capabilities including login
    alerts, security warnings, password changes, account activity monitoring, and
    customizable notification preferences. This extension provides static functionality
    and metadata to organize notification-related components and manage authentication workflows.

    The extension focuses on:
    - Login and logout notifications
    - Security event alerts and warnings
    - Password and account change notifications
    - Failed authentication attempt alerts
    - Device and location-based notifications
    - Customizable notification preferences
    - Multi-channel delivery (email, SMS, push, webhook)

    Component loading (DB, BLL, EP) is handled automatically by the import system
    based on file naming conventions.
    """

    # Extension metadata
    name = "auth_notifications"
    version = "1.0.0"
    description = (
        "Authentication Notifications extension for login alerts, security warnings, "
        "and customizable notification preferences"
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
            reason="Email notification delivery for authentication events",
        ),
    ]

    pip_dependencies = [
        PIP_Dependency(
            name="twilio",
            friendly_name="Twilio SMS Library",
            optional=True,
            reason="SMS notification delivery for authentication events",
            semver=">=8.5.0",
        ),
        PIP_Dependency(
            name="requests",
            friendly_name="HTTP Requests Library",
            optional=False,
            reason="Required for webhook notifications and external service integration",
            semver=">=2.31.0",
        ),
        PIP_Dependency(
            name="pydantic",
            friendly_name="Pydantic Library",
            optional=False,
            reason="Data validation and serialization for notification templates",
            semver=">=2.0.0",
        ),
        PIP_Dependency(
            name="redis",
            friendly_name="Redis Client",
            optional=True,
            reason="Notification queue management and rate limiting",
            semver=">=4.5.0",
        ),
    ]

    sys_dependencies = []

    # Define database tables
    db_tables = []

    # Define what capabilities this extension provides
    capabilities = [
        "authentication_alerts",
        "security_notifications",
        "login_monitoring",
        "notification_preferences",
        "multi_channel_delivery",
        "rate_limiting",
        "template_management",
    ]

    # Notification types
    NOTIFICATION_TYPES = {
        "login_success": "Successful login notification",
        "login_failed": "Failed login attempt notification",
        "logout": "User logout notification",
        "password_changed": "Password change notification",
        "email_changed": "Email address change notification",
        "phone_changed": "Phone number change notification",
        "mfa_enabled": "Multi-factor authentication enabled",
        "mfa_disabled": "Multi-factor authentication disabled",
        "security_alert": "General security alert",
        "account_locked": "Account locked notification",
        "account_unlocked": "Account unlocked notification",
        "new_device": "New device login notification",
        "suspicious_activity": "Suspicious activity detected",
    }

    # Notification channels
    NOTIFICATION_CHANNELS = {
        "email": "Email notification",
        "sms": "SMS notification",
        "push": "Push notification",
        "webhook": "Webhook notification",
        "in_app": "In-application notification",
    }

    # Priority levels
    PRIORITY_LEVELS = {
        "low": "Low priority notification",
        "medium": "Medium priority notification",
        "high": "High priority notification",
        "critical": "Critical security notification",
    }

    def __init__(
        self,
        enable_login_notifications: bool = True,
        enable_security_alerts: bool = True,
        enable_device_notifications: bool = True,
        default_notification_channels: List[str] = None,
        rate_limit_per_hour: int = 10,
        enable_rate_limiting: bool = True,
        notification_retention_days: int = 30,
        batch_processing: bool = True,
        batch_size: int = 100,
        twilio_account_sid: Optional[str] = None,
        twilio_auth_token: Optional[str] = None,
        **kwargs,
    ):
        super().__init__(**kwargs)

        # Configuration settings
        self.enable_login_notifications = enable_login_notifications
        self.enable_security_alerts = enable_security_alerts
        self.enable_device_notifications = enable_device_notifications
        self.default_notification_channels = default_notification_channels or ["email"]
        self.rate_limit_per_hour = rate_limit_per_hour
        self.enable_rate_limiting = enable_rate_limiting
        self.notification_retention_days = notification_retention_days
        self.batch_processing = batch_processing
        self.batch_size = batch_size
        self.twilio_account_sid = twilio_account_sid or env("TWILIO_ACCOUNT_SID")
        self.twilio_auth_token = twilio_auth_token or env("TWILIO_AUTH_TOKEN")

        # Runtime state
        self.notification_queue = []
        self.user_preferences = {}
        self.rate_limit_cache = {}
        self.notification_templates = {}
        self.delivery_stats = {}
        self.redis_client = None

    def on_initialize(self) -> bool:
        """
        Initialize the Authentication Notifications extension.
        """
        logger.debug("Initializing Authentication Notifications Extension...")

        try:
            # Initialize Redis for queue management
            self._initialize_redis()

            # Initialize notification services
            self._initialize_notification_services()

            # Register authentication hooks
            self._register_auth_notification_hooks()

            # Register capabilities
            for capability in self.capabilities:
                self.register_capability(capability)

            # Initialize default templates
            self._initialize_default_templates()

            # Setup notification processing
            if self.batch_processing:
                self._setup_batch_processing()

            logger.debug(
                "Authentication Notifications extension initialized successfully"
            )
            return True

        except Exception as e:
            logger.error(
                f"Failed to initialize Authentication Notifications extension: {str(e)}"
            )
            return False

    def _initialize_redis(self):
        """Initialize Redis connection for queue management."""
        try:
            import redis

            redis_url = env("REDIS_URL", "redis://localhost:6379/5")
            self.redis_client = redis.from_url(redis_url)

            # Test connection
            self.redis_client.ping()
            logger.debug("Redis connection established for notification queue")

        except ImportError:
            logger.warning("Redis not available - using memory queue for notifications")
            self.redis_client = None
        except Exception as e:
            logger.warning(f"Redis connection failed: {e} - using memory queue")
            self.redis_client = None

    def _initialize_notification_services(self):
        """Initialize external notification services."""
        # Initialize Twilio for SMS
        if self.twilio_account_sid and self.twilio_auth_token:
            try:
                from twilio.rest import Client

                self.twilio_client = Client(
                    self.twilio_account_sid, self.twilio_auth_token
                )
                logger.debug("Twilio SMS service initialized")
            except ImportError:
                logger.warning(
                    "Twilio library not available - SMS notifications disabled"
                )
                self.twilio_client = None
        else:
            self.twilio_client = None

        # Initialize other notification services as needed
        self.delivery_stats = {
            "email": {"sent": 0, "failed": 0},
            "sms": {"sent": 0, "failed": 0},
            "push": {"sent": 0, "failed": 0},
            "webhook": {"sent": 0, "failed": 0},
            "in_app": {"sent": 0, "failed": 0},
        }

    def _register_auth_notification_hooks(self):
        """Register hooks for authentication events."""
        try:

            @AbstractExtension.bll_hook("Auth", "UserManager", "login", "after")
            def login_notification_hook(manager, result, create_args=None):
                """Hook to send login notifications."""
                try:
                    if self.enable_login_notifications and result:
                        asyncio.create_task(
                            self._handle_login_notification(result, create_args)
                        )
                        logger.debug("Login notification hook executed")
                except Exception as e:
                    logger.error(f"Error in login notification hook: {e}")

            @AbstractExtension.bll_hook("Auth", "UserManager", "login", "failed")
            def failed_login_notification_hook(manager, result, create_args=None):
                """Hook to send failed login notifications."""
                try:
                    if self.enable_security_alerts:
                        asyncio.create_task(
                            self._handle_failed_login_notification(create_args)
                        )
                        logger.debug("Failed login notification hook executed")
                except Exception as e:
                    logger.error(f"Error in failed login notification hook: {e}")

            @AbstractExtension.bll_hook("Auth", "UserManager", "logout", "after")
            def logout_notification_hook(manager, result, create_args=None):
                """Hook to send logout notifications."""
                try:
                    if self.enable_login_notifications and result:
                        asyncio.create_task(self._handle_logout_notification(result))
                        logger.debug("Logout notification hook executed")
                except Exception as e:
                    logger.error(f"Error in logout notification hook: {e}")

            @AbstractExtension.bll_hook(
                "Auth", "UserManager", "update_password", "after"
            )
            def password_change_notification_hook(manager, result, create_args=None):
                """Hook to send password change notifications."""
                try:
                    if self.enable_security_alerts and result:
                        asyncio.create_task(
                            self._handle_password_change_notification(result)
                        )
                        logger.debug("Password change notification hook executed")
                except Exception as e:
                    logger.error(f"Error in password change notification hook: {e}")

        except Exception as e:
            logger.error(f"Error registering auth notification hooks: {e}")

    def _initialize_default_templates(self):
        """Initialize default notification templates."""
        self.notification_templates = {
            "login_success": {
                "subject": "Login Alert - {user_name}",
                "email_body": """
                Hello {user_name},
                
                A successful login to your account was detected:
                - Time: {login_time}
                - Location: {location}
                - Device: {device}
                - IP Address: {ip_address}
                
                If this wasn't you, please secure your account immediately.
                """,
                "sms_body": "Login alert: Your account was accessed at {login_time} from {location}. If this wasn't you, secure your account.",
            },
            "login_failed": {
                "subject": "Failed Login Attempt - {user_name}",
                "email_body": """
                Hello {user_name},
                
                A failed login attempt was detected on your account:
                - Time: {attempt_time}
                - Location: {location}
                - IP Address: {ip_address}
                - Attempts: {attempt_count}
                
                If this wasn't you, your account may be under attack.
                """,
                "sms_body": "Security alert: Failed login attempt on your account at {attempt_time}. If this wasn't you, secure your account.",
            },
            "password_changed": {
                "subject": "Password Changed - {user_name}",
                "email_body": """
                Hello {user_name},
                
                Your account password was successfully changed:
                - Time: {change_time}
                - IP Address: {ip_address}
                
                If you didn't make this change, please contact support immediately.
                """,
                "sms_body": "Your password was changed at {change_time}. If this wasn't you, contact support immediately.",
            },
        }

    def _setup_batch_processing(self):
        """Setup batch processing for notifications."""
        logger.debug("Batch processing enabled for notifications")

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

    @AbstractExtension.ability("send_authentication_notification")
    async def send_authentication_notification(
        self,
        user_id: str,
        notification_type: str,
        context_data: Dict[str, Any],
        channels: Optional[List[str]] = None,
        priority: str = "medium",
        immediate: bool = False,
    ) -> Dict[str, Any]:
        """
        Send an authentication notification to a user.
        """
        try:
            if notification_type not in self.NOTIFICATION_TYPES:
                return {
                    "success": False,
                    "message": f"Invalid notification type: {notification_type}",
                }

            # Get user preferences
            user_prefs = await self._get_user_notification_preferences(user_id)

            # Determine channels to use
            if channels is None:
                channels = user_prefs.get(
                    "channels", self.default_notification_channels
                )

            # Check if user has disabled this notification type
            if not user_prefs.get("types", {}).get(notification_type, True):
                return {
                    "success": True,
                    "message": "User has disabled this notification type",
                    "skipped": True,
                }

            # Rate limiting check
            if self.enable_rate_limiting and not immediate:
                rate_check = await self._check_rate_limit(user_id, notification_type)
                if not rate_check["allowed"]:
                    return {
                        "success": False,
                        "message": "Rate limit exceeded",
                        "rate_limit": rate_check,
                    }

            # Create notification record
            notification_record = {
                "id": self._generate_notification_id(),
                "user_id": user_id,
                "notification_type": notification_type,
                "context_data": context_data,
                "channels": channels,
                "priority": priority,
                "status": "pending",
                "created_at": datetime.utcnow().isoformat(),
                "immediate": immediate,
            }

            # Queue or send immediately
            if immediate:
                result = await self._deliver_notification(notification_record)
                return {
                    "success": True,
                    "notification_id": notification_record["id"],
                    "delivery_result": result,
                }
            else:
                await self._queue_notification(notification_record)
                return {
                    "success": True,
                    "notification_id": notification_record["id"],
                    "status": "queued",
                }

        except Exception as e:
            logger.error(f"Error sending authentication notification: {e}")
            return {
                "success": False,
                "message": f"Failed to send notification: {str(e)}",
            }

    @AbstractExtension.ability("update_notification_preferences")
    async def update_notification_preferences(
        self,
        user_id: str,
        preferences: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Update user notification preferences.
        """
        try:
            # Validate preferences structure
            validation_result = self._validate_preferences(preferences)
            if not validation_result["valid"]:
                return {"success": False, "message": validation_result["message"]}

            # Update user preferences
            current_prefs = self.user_preferences.get(user_id, {})
            updated_prefs = {**current_prefs, **preferences}
            self.user_preferences[user_id] = updated_prefs

            # Store in persistent storage if available
            if self.redis_client:
                self.redis_client.set(
                    f"notification_prefs:{user_id}",
                    json.dumps(updated_prefs),
                    ex=86400 * 30,  # 30 days
                )

            logger.debug(f"Updated notification preferences for user {user_id}")
            return {
                "success": True,
                "user_id": user_id,
                "preferences": updated_prefs,
            }

        except Exception as e:
            logger.error(f"Error updating notification preferences: {e}")
            return {
                "success": False,
                "message": f"Failed to update preferences: {str(e)}",
            }

    @AbstractExtension.ability("get_notification_history")
    async def get_notification_history(
        self,
        user_id: str,
        notification_type: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """
        Get notification history for a user.
        """
        try:
            # In real implementation, this would query the database
            # For now, return mock data
            notifications = []

            return {
                "success": True,
                "user_id": user_id,
                "notifications": notifications,
                "total": len(notifications),
                "limit": limit,
                "offset": offset,
            }

        except Exception as e:
            logger.error(f"Error getting notification history: {e}")
            return {
                "success": False,
                "message": f"Failed to get notification history: {str(e)}",
            }

    @AbstractExtension.ability("create_notification_template")
    async def create_notification_template(
        self,
        notification_type: str,
        template_data: Dict[str, Any],
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create or update a notification template.
        """
        try:
            # Validate template data
            required_fields = ["subject", "email_body"]
            missing_fields = [
                field for field in required_fields if field not in template_data
            ]

            if missing_fields:
                return {
                    "success": False,
                    "message": f"Missing required fields: {', '.join(missing_fields)}",
                }

            # Store template
            if user_id:
                # User-specific template
                user_templates = self.user_preferences.get(user_id, {}).get(
                    "templates", {}
                )
                user_templates[notification_type] = template_data

                if user_id not in self.user_preferences:
                    self.user_preferences[user_id] = {}
                self.user_preferences[user_id]["templates"] = user_templates
            else:
                # Global template
                self.notification_templates[notification_type] = template_data

            logger.debug(f"Created notification template for type: {notification_type}")
            return {
                "success": True,
                "notification_type": notification_type,
                "template_data": template_data,
                "scope": "user" if user_id else "global",
            }

        except Exception as e:
            logger.error(f"Error creating notification template: {e}")
            return {"success": False, "message": f"Failed to create template: {str(e)}"}

    @AbstractExtension.ability("test_notification")
    async def test_notification(
        self,
        user_id: str,
        notification_type: str,
        test_data: Dict[str, Any],
        channel: str = "email",
    ) -> Dict[str, Any]:
        """
        Send a test notification to verify delivery.
        """
        try:
            if notification_type not in self.NOTIFICATION_TYPES:
                return {
                    "success": False,
                    "message": f"Invalid notification type: {notification_type}",
                }

            if channel not in self.NOTIFICATION_CHANNELS:
                return {"success": False, "message": f"Invalid channel: {channel}"}

            # Create test notification
            test_notification = {
                "id": self._generate_notification_id(),
                "user_id": user_id,
                "notification_type": notification_type,
                "context_data": test_data,
                "channels": [channel],
                "priority": "low",
                "status": "test",
                "created_at": datetime.utcnow().isoformat(),
                "immediate": True,
                "test": True,
            }

            # Send test notification
            result = await self._deliver_notification(test_notification)

            return {
                "success": True,
                "notification_id": test_notification["id"],
                "channel": channel,
                "delivery_result": result,
            }

        except Exception as e:
            logger.error(f"Error sending test notification: {e}")
            return {"success": False, "message": f"Test notification failed: {str(e)}"}

    # Helper methods
    def _generate_notification_id(self) -> str:
        """Generate unique notification ID."""
        import uuid

        return str(uuid.uuid4())

    async def _get_user_notification_preferences(self, user_id: str) -> Dict[str, Any]:
        """Get user notification preferences."""
        # Check memory cache first
        if user_id in self.user_preferences:
            return self.user_preferences[user_id]

        # Check Redis if available
        if self.redis_client:
            cached_prefs = self.redis_client.get(f"notification_prefs:{user_id}")
            if cached_prefs:
                prefs = json.loads(cached_prefs)
                self.user_preferences[user_id] = prefs
                return prefs

        # Return default preferences
        default_prefs = {
            "channels": self.default_notification_channels,
            "types": {
                notification_type: True for notification_type in self.NOTIFICATION_TYPES
            },
        }

        return default_prefs

    def _validate_preferences(self, preferences: Dict[str, Any]) -> Dict[str, Any]:
        """Validate notification preferences structure."""
        try:
            # Check channels
            if "channels" in preferences:
                invalid_channels = [
                    ch
                    for ch in preferences["channels"]
                    if ch not in self.NOTIFICATION_CHANNELS
                ]
                if invalid_channels:
                    return {
                        "valid": False,
                        "message": f"Invalid channels: {', '.join(invalid_channels)}",
                    }

            # Check notification types
            if "types" in preferences:
                invalid_types = [
                    typ
                    for typ in preferences["types"]
                    if typ not in self.NOTIFICATION_TYPES
                ]
                if invalid_types:
                    return {
                        "valid": False,
                        "message": f"Invalid notification types: {', '.join(invalid_types)}",
                    }

            return {"valid": True}

        except Exception as e:
            return {"valid": False, "message": str(e)}

    async def _check_rate_limit(
        self, user_id: str, notification_type: str
    ) -> Dict[str, Any]:
        """Check rate limiting for notifications."""
        try:
            current_hour = datetime.utcnow().replace(minute=0, second=0, microsecond=0)
            cache_key = f"{user_id}:{notification_type}:{current_hour.isoformat()}"

            if self.redis_client:
                # Use Redis for distributed rate limiting
                current_count = self.redis_client.incr(cache_key)
                self.redis_client.expire(cache_key, 3600)  # Expire after 1 hour
            else:
                # Use memory cache
                current_count = self.rate_limit_cache.get(cache_key, 0) + 1
                self.rate_limit_cache[cache_key] = current_count

            allowed = current_count <= self.rate_limit_per_hour

            return {
                "allowed": allowed,
                "current_count": current_count,
                "limit": self.rate_limit_per_hour,
                "reset_time": (current_hour + timedelta(hours=1)).isoformat(),
            }

        except Exception as e:
            logger.error(f"Error checking rate limit: {e}")
            # Allow on error
            return {"allowed": True, "error": str(e)}

    async def _queue_notification(self, notification: Dict[str, Any]):
        """Queue notification for delivery."""
        if self.redis_client:
            self.redis_client.lpush("notification_queue", json.dumps(notification))
        else:
            self.notification_queue.append(notification)

    async def _deliver_notification(
        self, notification: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Deliver notification through specified channels."""
        results = {}

        for channel in notification["channels"]:
            try:
                if channel == "email":
                    result = await self._send_email_notification(notification)
                elif channel == "sms":
                    result = await self._send_sms_notification(notification)
                elif channel == "webhook":
                    result = await self._send_webhook_notification(notification)
                elif channel == "in_app":
                    result = await self._send_in_app_notification(notification)
                else:
                    result = {
                        "success": False,
                        "message": f"Unsupported channel: {channel}",
                    }

                results[channel] = result

                # Update delivery stats
                if result.get("success"):
                    self.delivery_stats[channel]["sent"] += 1
                else:
                    self.delivery_stats[channel]["failed"] += 1

            except Exception as e:
                logger.error(f"Error delivering notification via {channel}: {e}")
                results[channel] = {"success": False, "message": str(e)}
                self.delivery_stats[channel]["failed"] += 1

        return results

    async def _send_email_notification(
        self, notification: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Send email notification."""
        try:
            # Get template
            template = self._get_notification_template(
                notification["notification_type"], notification["user_id"]
            )

            # Render template with context data
            subject = self._render_template(
                template["subject"], notification["context_data"]
            )
            body = self._render_template(
                template["email_body"], notification["context_data"]
            )

            # In real implementation, this would use the email extension
            logger.debug(f"Sending email notification: {subject}")

            return {
                "success": True,
                "channel": "email",
                "subject": subject,
                "message": "Email sent successfully",
            }

        except Exception as e:
            return {"success": False, "channel": "email", "message": str(e)}

    async def _send_sms_notification(
        self, notification: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Send SMS notification."""
        try:
            if not self.twilio_client:
                return {
                    "success": False,
                    "channel": "sms",
                    "message": "SMS service not configured",
                }

            # Get template
            template = self._get_notification_template(
                notification["notification_type"], notification["user_id"]
            )

            # Render SMS body
            message = self._render_template(
                template.get("sms_body", ""), notification["context_data"]
            )

            # In real implementation, this would send via Twilio
            logger.debug(f"Sending SMS notification: {message[:50]}...")

            return {
                "success": True,
                "channel": "sms",
                "message": "SMS sent successfully",
            }

        except Exception as e:
            return {"success": False, "channel": "sms", "message": str(e)}

    async def _send_webhook_notification(
        self, notification: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Send webhook notification."""
        try:
            # In real implementation, this would send HTTP POST to configured webhooks
            logger.debug("Sending webhook notification")

            return {
                "success": True,
                "channel": "webhook",
                "message": "Webhook sent successfully",
            }

        except Exception as e:
            return {"success": False, "channel": "webhook", "message": str(e)}

    async def _send_in_app_notification(
        self, notification: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Send in-app notification."""
        try:
            # In real implementation, this would store notification for in-app display
            logger.debug("Sending in-app notification")

            return {
                "success": True,
                "channel": "in_app",
                "message": "In-app notification created successfully",
            }

        except Exception as e:
            return {"success": False, "channel": "in_app", "message": str(e)}

    def _get_notification_template(
        self, notification_type: str, user_id: str
    ) -> Dict[str, Any]:
        """Get notification template (user-specific or global)."""
        # Check for user-specific template
        user_prefs = self.user_preferences.get(user_id, {})
        user_templates = user_prefs.get("templates", {})

        if notification_type in user_templates:
            return user_templates[notification_type]

        # Fall back to global template
        return self.notification_templates.get(
            notification_type,
            {
                "subject": f"Notification: {notification_type}",
                "email_body": "You have a new notification.",
                "sms_body": "New notification",
            },
        )

    def _render_template(self, template: str, context: Dict[str, Any]) -> str:
        """Render template with context data."""
        try:
            return template.format(**context)
        except KeyError as e:
            logger.warning(f"Missing template variable: {e}")
            return template
        except Exception as e:
            logger.error(f"Error rendering template: {e}")
            return template

    # Hook handlers
    async def _handle_login_notification(self, user_data: Any, create_args: Any):
        """Handle login notification."""
        # Implementation for hook
        pass

    async def _handle_failed_login_notification(self, create_args: Any):
        """Handle failed login notification."""
        # Implementation for hook
        pass

    async def _handle_logout_notification(self, user_data: Any):
        """Handle logout notification."""
        # Implementation for hook
        pass

    async def _handle_password_change_notification(self, user_data: Any):
        """Handle password change notification."""
        # Implementation for hook
        pass

    # Extension lifecycle methods
    def on_start(self) -> bool:
        """Start the Authentication Notifications extension."""
        try:
            # Reconnect to Redis if needed
            if not self.redis_client:
                self._initialize_redis()

            # Reinitialize notification services
            self._initialize_notification_services()

            logger.debug("Authentication Notifications extension started successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to start Authentication Notifications extension: {e}")
            return False

    def on_stop(self) -> bool:
        """Stop the Authentication Notifications extension."""
        try:
            # Clean up resources
            self.notification_queue.clear()
            self.user_preferences.clear()
            self.rate_limit_cache.clear()

            # Close Redis connection
            if self.redis_client:
                self.redis_client.close()

            logger.debug("Authentication Notifications extension stopped successfully")
            return True

        except Exception as e:
            logger.error(f"Error stopping Authentication Notifications extension: {e}")
            return False

    def on_startup(self):
        """Called during application startup."""
        logger.debug("Authentication Notifications extension startup hook called")

    def on_shutdown(self):
        """Called during application shutdown."""
        logger.debug("Authentication Notifications extension shutdown hook called")

    def validate_config(self) -> List[str]:
        """Validate the extension configuration."""
        issues = []

        # Check for required Python packages
        try:
            import requests
        except ImportError:
            issues.append(
                "Requests library not installed - webhook notifications will not work"
            )

        try:
            import pydantic
        except ImportError:
            issues.append(
                "Pydantic library not installed - template validation will not work"
            )

        # Check SMS configuration
        if "sms" in self.default_notification_channels:
            if not self.twilio_account_sid or not self.twilio_auth_token:
                issues.append(
                    "SMS notifications enabled but Twilio credentials not configured"
                )
            else:
                try:
                    import twilio
                except ImportError:
                    issues.append(
                        "Twilio library not available but SMS notifications are enabled"
                    )

        # Check rate limiting
        if self.rate_limit_per_hour <= 0:
            issues.append("Rate limit must be greater than 0")

        return issues

    def get_required_permissions(self) -> List[str]:
        """Return the list of permissions required by this extension."""
        return [
            "notifications:send",
            "notifications:read",
            "notifications:preferences:update",
            "notifications:templates:create",
            "notifications:templates:update",
            "notifications:history:read",
        ]

    def has_capability(self, capability: str) -> bool:
        """Check if this extension has a specific capability."""
        return capability in self.capabilities

    def get_notification_types(self) -> Dict[str, str]:
        """Get available notification types."""
        return self.NOTIFICATION_TYPES.copy()

    def get_notification_channels(self) -> Dict[str, str]:
        """Get available notification channels."""
        return self.NOTIFICATION_CHANNELS.copy()

    def get_priority_levels(self) -> Dict[str, str]:
        """Get available priority levels."""
        return self.PRIORITY_LEVELS.copy()

    def get_delivery_stats(self) -> Dict[str, Any]:
        """Get notification delivery statistics."""
        return {
            "delivery_stats": self.delivery_stats.copy(),
            "queue_size": len(self.notification_queue),
            "user_preferences_count": len(self.user_preferences),
            "template_count": len(self.notification_templates),
            "rate_limiting_enabled": self.enable_rate_limiting,
            "batch_processing_enabled": self.batch_processing,
        }
