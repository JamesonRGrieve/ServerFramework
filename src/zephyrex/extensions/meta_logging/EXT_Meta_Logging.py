from __future__ import annotations

from typing import Any, Dict, List, Set

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension, ability
from zephyrex.lib.Dependencies import EXT_Dependency, PIP_Dependency
from zephyrex.lib.Environment import env
from zephyrex.lib.Logging import logger
from zephyrex.lib.Pydantic import classproperty


class EXT_Meta_Logging(AbstractStaticExtension):
    """
    Meta Logging extension for AGInfrastructure.

    Provides comprehensive audit logging, failed login tracking, and privacy compliance
    logging abilities using InfluxDB for time-series data storage. This extension
    provides static functionality and metadata to organize logging-related components
    and manage audit trail functionality.

    The extension focuses on:
    - Audit logging for user actions and system events
    - Failed login attempt tracking and analysis
    - Privacy compliance reporting
    - System-wide event logging
    - Integration with InfluxDB for time-series data
    - Comprehensive audit hooks for BLL operations

    Component loading (DB, BLL, EP) is handled automatically by the import system
    based on file naming conventions.
    """

    # Extension metadata
    name = "meta_logging"
    version = "1.0.0"
    description = "Meta Logging extension with audit trails, failed login tracking, and privacy compliance logging"

    # Define dependencies
    ext_dependencies = [
        EXT_Dependency(
            name="database",
            friendly_name="Database Extension",
            optional=False,
            reason="Required for InfluxDB logging backend",
        )
    ]

    pip_dependencies = [
        PIP_Dependency(
            name="influxdb-client",
            friendly_name="InfluxDB 2.x Python Client",
            optional=False,
            reason="Required for time-series logging to InfluxDB",
            semver=">=1.36.0",
        ),
    ]

    sys_dependencies: list[EXT_Dependency] = []

    # Define what abilities this extension provides
    abilities: Set[str] = {
        "audit_logging",
        "failed_login_tracking",
        "system_logging",
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        # Create instance copy of abilities to avoid modifying class attribute
        self.abilities = self.__class__.abilities.copy()

        # Track if database extension is available
        self.database_extension = None
        self.influxdb_available = False

        # Logging manager instance
        self.logging_manager = None

    @classmethod
    def get_abilities(cls) -> Set[str]:
        """Return the abilities this extension provides."""
        return cls.abilities.copy()

    @classmethod
    def register_ability(cls, ability: str):
        """Register a new ability."""
        cls.abilities.add(ability)

    @classmethod
    def get_registered_abilities(cls) -> Set[str]:
        """Return currently registered abilities."""
        return cls.abilities.copy()

    def on_initialize(self) -> bool:
        """
        Initialize the Meta Logging extension.
        """
        logger.debug("Initializing Meta Logging Extension...")

        try:
            # Check if database extension is available
            self._detect_database_extension()

            # Initialize logging manager
            self._initialize_logging_manager()

            # Register system hooks for comprehensive audit logging
            self._register_audit_hooks()

            # Register logging-specific abilities
            self.register_ability("audit_logging")
            self.register_ability("failed_login_tracking")
            self.register_ability("system_logging")

            # Add privacy audit ability if InfluxDB is available
            if self.influxdb_available:
                self.register_ability("privacy_audit")
                self.register_ability("compliance_reporting")
                logger.debug("Privacy audit and compliance reporting abilities enabled")
            else:
                logger.warning(
                    "Privacy audit abilities disabled - InfluxDB not available"
                )

            logger.debug("Meta Logging extension initialized successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to initialize Meta Logging extension: {str(e)}")
            return False

    def _detect_database_extension(self):
        """
        Detect if the database extension with InfluxDB is available.
        """
        try:
            # Try to get the database extension instance
            self.database_extension = AbstractStaticExtension.get_extension_by_name(
                "database"
            )

            if self.database_extension:
                # Check if database extension has InfluxDB provider
                if (
                    hasattr(self.database_extension, "provider")
                    and self.database_extension.provider
                ):
                    provider = self.database_extension.provider
                    if hasattr(provider, "get_db_type"):
                        db_type = provider.get_db_type()
                        if "InfluxDB" in db_type:
                            self.influxdb_available = True
                            logger.debug(
                                "InfluxDB detected and configured for meta logging"
                            )
                        else:
                            logger.warning(
                                f"Database extension found but using {db_type}, not InfluxDB"
                            )
                    else:
                        logger.warning(
                            "Database extension found but provider missing get_db_type method"
                        )
                else:
                    logger.warning(
                        "Database extension found but no provider configured"
                    )
            else:
                logger.warning(
                    "Database extension not available - meta logging will be limited"
                )

        except Exception as e:
            logger.debug(f"Error detecting database extension: {e}")

    def _get_model_registry(self):
        """Attempt to retrieve the application's committed model registry."""

        try:
            from zephyrex import app  # type: ignore

            app_obj = getattr(app, "app", None)
            state = getattr(app_obj, "state", None)
            registry = getattr(state, "model_registry", None)
            if registry is not None:
                return registry
        except Exception as exc:
            logger.debug(f"Error retrieving model registry for meta logging: {exc}")

        return None

    def _initialize_logging_manager(self):
        """Initialize the logging manager."""
        try:
            if self.influxdb_available:
                from zephyrex.extensions.meta_logging.BLL_Meta_Logging import MetaLoggingManager

                model_registry = self._get_model_registry()
                if not model_registry:
                    logger.warning(
                        "Model registry not available - meta logging manager not initialized"
                    )
                    return

                # Use ROOT_ID for system-level logging
                self.logging_manager = MetaLoggingManager(
                    requester_id=env("ROOT_ID"),
                    target_team_id=None,
                    model_registry=model_registry,
                )
                logger.debug("Meta logging manager initialized successfully")
            else:
                logger.warning(
                    "Meta logging manager not initialized - InfluxDB not available"
                )
        except Exception as e:
            logger.error(f"Failed to initialize logging manager: {e}")

    @ability("log_audit_event")
    async def log_audit_event(
        self,
        user_id: str | None = None,
        action: str = "",
        resource_type: str = "",
        resource_id: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        success: bool = True,
        error_message: str | None = None,
        additional_data: Dict[str, Any] | None = None,
        privacy_impact: bool = False,
        data_categories: List[str] | None = None,
    ) -> Dict[str, Any]:
        """
        Log an audit event to InfluxDB.
        """
        if not self.logging_manager:
            return {"success": False, "message": "Logging manager not available"}

        try:
            from zephyrex.extensions.meta_logging.BLL_Meta_Logging import AuditLogModel

            audit_data = AuditLogModel.Create(
                user_id=user_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                ip_address=ip_address,
                user_agent=user_agent,
                success=success,
                error_message=error_message,
                additional_data=additional_data,
                privacy_impact=privacy_impact,
                data_categories=data_categories or [],
            )

            result = await self.logging_manager.log_audit_event(audit_data)

            if result.get("success"):
                logger.debug(f"Audit event logged: {action} on {resource_type}")
            else:
                logger.warning(f"Failed to log audit event: {result.get('message')}")

            return result  # type: ignore[no-any-return]

        except Exception as e:
            logger.error(f"Error logging audit event: {str(e)}")
            return {"success": False, "message": f"Error logging audit event: {str(e)}"}

    @ability("log_failed_login")
    async def log_failed_login(
        self,
        username: str = "",
        ip_address: str = "",
        user_agent: str | None = None,
        failure_reason: str = "",
        attempt_count: int = 1,
        is_blocked: bool = False,
        country: str | None = None,
        risk_score: float = 0.0,
    ) -> Dict[str, Any]:
        """
        Log a failed login attempt to InfluxDB.
        """
        if not self.logging_manager:
            return {"success": False, "message": "Logging manager not available"}

        try:
            from zephyrex.extensions.meta_logging.BLL_Meta_Logging import FailedLoginAttemptModel

            login_data = FailedLoginAttemptModel.Create(
                username=username,
                ip_address=ip_address,
                user_agent=user_agent,
                failure_reason=failure_reason,
                attempt_count=attempt_count,
                is_blocked=is_blocked,
                country=country,
                risk_score=risk_score,
            )

            result = await self.logging_manager.log_failed_login(login_data)

            if result.get("success"):
                logger.debug(f"Failed login logged: {username} from {ip_address}")
            else:
                logger.warning(f"Failed to log failed login: {result.get('message')}")

            return result  # type: ignore[no-any-return]

        except Exception as e:
            logger.error(f"Error logging failed login: {str(e)}")
            return {
                "success": False,
                "message": f"Error logging failed login: {str(e)}",
            }

    @ability("log_system_event")
    async def log_system_event(
        self,
        level: str = "INFO",
        component: str = "",
        message: str = "",
        user_id: str | None = None,
        request_id: str | None = None,
        additional_data: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """
        Log a system event to InfluxDB.
        """
        if not self.logging_manager:
            return {"success": False, "message": "Logging manager not available"}

        try:
            from zephyrex.extensions.meta_logging.BLL_Meta_Logging import SystemLogModel

            system_data = SystemLogModel.Create(
                level=level,
                component=component,
                message=message,
                user_id=user_id,
                request_id=request_id,
                additional_data=additional_data,
            )

            result = await self.logging_manager.log_system_event(system_data)

            if result.get("success"):
                logger.debug(f"System event logged: {level} from {component}")
            else:
                logger.warning(f"Failed to log system event: {result.get('message')}")

            return result  # type: ignore[no-any-return]

        except Exception as e:
            logger.error(f"Error logging system event: {str(e)}")
            return {
                "success": False,
                "message": f"Error logging system event: {str(e)}",
            }

    @ability("query_audit_logs")
    async def query_audit_logs(
        self,
        start_time: str | None = None,
        end_time: str | None = None,
        user_id: str | None = None,
        action: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        success: bool | None = None,
        privacy_impact: bool | None = None,
    ) -> List[Dict[str, Any]]:
        """
        Query audit logs from InfluxDB.
        """
        if not self.logging_manager:
            return []

        try:
            from datetime import datetime

            from zephyrex.extensions.meta_logging.BLL_Meta_Logging import AuditLogModel

            # Parse datetime strings if provided
            start_dt = (
                datetime.fromisoformat(start_time.replace("Z", "+00:00"))
                if start_time
                else None
            )
            end_dt = (
                datetime.fromisoformat(end_time.replace("Z", "+00:00"))
                if end_time
                else None
            )

            search_params = AuditLogModel.Search(
                start_time=start_dt,
                end_time=end_dt,
                user_id=user_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                success=success,
                privacy_impact=privacy_impact,
            )

            results = await self.logging_manager.query_audit_logs(search_params)
            logger.debug(f"Audit logs query returned {len(results)} results")

            return results  # type: ignore[no-any-return]

        except Exception as e:
            logger.error(f"Error querying audit logs: {str(e)}")
            return []

    @ability("query_failed_logins")
    async def query_failed_logins(
        self,
        start_time: str | None = None,
        end_time: str | None = None,
        username: str | None = None,
        ip_address: str | None = None,
        failure_reason: str | None = None,
        is_blocked: bool | None = None,
        min_risk_score: float | None = None,
    ) -> List[Dict[str, Any]]:
        """
        Query failed login attempts from InfluxDB.
        """
        if not self.logging_manager:
            return []

        try:
            from datetime import datetime

            from zephyrex.extensions.meta_logging.BLL_Meta_Logging import FailedLoginAttemptModel

            # Parse datetime strings if provided
            start_dt = (
                datetime.fromisoformat(start_time.replace("Z", "+00:00"))
                if start_time
                else None
            )
            end_dt = (
                datetime.fromisoformat(end_time.replace("Z", "+00:00"))
                if end_time
                else None
            )

            search_params = FailedLoginAttemptModel.Search(
                start_time=start_dt,
                end_time=end_dt,
                username=username,
                ip_address=ip_address,
                failure_reason=failure_reason,
                is_blocked=is_blocked,
                min_risk_score=min_risk_score,
            )

            results = await self.logging_manager.query_failed_logins(search_params)
            logger.debug(f"Failed logins query returned {len(results)} results")

            return results  # type: ignore[no-any-return]

        except Exception as e:
            logger.error(f"Error querying failed logins: {str(e)}")
            return []

    @ability("generate_privacy_report")
    async def generate_privacy_report(self, days: int = 30) -> Dict[str, Any]:
        """
        Generate a privacy audit report for compliance.
        """
        if not self.logging_manager:
            return {"success": False, "message": "Logging manager not available"}

        try:
            result = await self.logging_manager.get_privacy_audit_report(days)

            if result.get("success"):
                logger.debug(f"Privacy audit report generated for {days} days")
            else:
                logger.warning(
                    f"Failed to generate privacy report: {result.get('message')}"
                )

            return result  # type: ignore[no-any-return]

        except Exception as e:
            logger.error(f"Error generating privacy report: {str(e)}")
            return {
                "success": False,
                "message": f"Error generating privacy report: {str(e)}",
            }

    @classmethod
    def get_required_permissions(cls) -> List[str]:
        """
        Return the list of permissions required by this extension.
        """
        return ["logging:read", "logging:write", "logging:manage"]

    def on_start(self) -> bool:
        """
        Start the Meta Logging extension.
        """
        try:
            # Re-detect database extension in case it was loaded after this extension
            self._detect_database_extension()

            # Re-initialize logging manager if needed
            if not self.logging_manager and self.influxdb_available:
                self._initialize_logging_manager()

            # Ensure hooks are registered
            self._register_audit_hooks()

            logger.debug("Meta Logging extension started successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to start Meta Logging extension: {e}")
            return False

    def on_stop(self) -> bool:
        """
        Stop the Meta Logging extension.
        """
        try:
            # Clean up any resources
            if self.logging_manager:
                self.logging_manager = None

            logger.debug("Meta Logging extension stopped successfully")
            return True

        except Exception as e:
            logger.error(f"Error stopping Meta Logging extension: {e}")
            return False

    def on_startup(self):
        """
        Called during application startup.
        """
        logger.debug("Meta Logging extension startup hook called")

    def on_shutdown(self):
        """
        Called during application shutdown.
        """
        logger.debug("Meta Logging extension shutdown hook called")

    def validate_config(self) -> List[str]:
        """
        Validate the extension configuration.
        """
        issues = []

        # Check for required dependencies
        try:
            import influxdb_client
        except ImportError:
            issues.append(
                "InfluxDB client library not installed - time-series logging will not work"
            )

        # Check if database extension is available
        if not self.database_extension:
            issues.append(
                "Database extension not available - meta logging functionality limited"
            )

        # Check if InfluxDB is configured
        if not self.influxdb_available:
            issues.append("InfluxDB not configured - audit logs will not be stored")

        return issues

    def has_ability(self, ability: str) -> bool:
        """Check if this extension has a specific ability."""
        return ability in self.abilities

    def get_audit_summary(self, hours: int = 24) -> Dict[str, Any]:
        """
        Get a summary of audit events for the specified time period.
        This is a synchronous method for quick status checks.
        """
        try:
            if not self.influxdb_available:
                return {"error": "InfluxDB not available"}

            # This would typically query recent events
            # For now, return a placeholder
            return {
                "hours": hours,
                "audit_events": 0,
                "failed_logins": 0,
                "privacy_events": 0,
                "status": "InfluxDB available",
            }

        except Exception as e:
            logger.error(f"Error getting audit summary: {e}")
            return {"error": str(e)}

    @classmethod
    def get_providers(cls) -> Set[str]:
        """Return available logging providers."""
        return {"file", "console", "database", "remote"}

    @classmethod
    def get_provider_class(cls, provider_name: str):
        """Get the provider class for a specific provider."""
        provider_map = {
            "file": "zephyrex.extensions.meta_logging.providers.FileLogProvider",
            "console": "zephyrex.extensions.meta_logging.providers.ConsoleLogProvider",
            "database": "zephyrex.extensions.meta_logging.providers.DatabaseLogProvider",
            "remote": "zephyrex.extensions.meta_logging.providers.RemoteLogProvider",
        }

        if provider_name.lower() in provider_map:
            module_path, class_name = provider_map[provider_name.lower()].rsplit(".", 1)
            module = __import__(module_path, fromlist=[class_name])
            return getattr(module, class_name)
        else:
            raise ValueError(f"Provider {provider_name} not implemented yet")

    @classproperty
    def env(cls) -> Dict[str, Any]:
        """Get environment variables for this extension."""
        return {
            "INFLUXDB_HOST": "",
            "INFLUXDB_PORT": "8086",
            "INFLUXDB_DATABASE": "",
            "INFLUXDB_USERNAME": "",
            "INFLUXDB_PASSWORD": "",
            "INFLUXDB_VERSION": "1",
            "INFLUXDB_ORG": "",
            "INFLUXDB_TOKEN": "",
            "INFLUXDB_BUCKET": "",
        }

    @classproperty  # type: ignore[no-redef]
    def pip_dependencies(cls):
        """Get PIP dependencies for backward compatibility."""
        return cls.dependencies.pip

    @classproperty  # type: ignore[no-redef]
    def ext_dependencies(cls):
        """Get extension dependencies for backward compatibility."""
        return cls.dependencies.ext

    def _register_audit_hooks(self):
        """
        Register hooks for comprehensive audit logging throughout the system.
        """
        try:
            logger.debug("Registering audit hooks for Meta Logging extension")
            # Hook registration would go here - placeholder for now
            # In a real implementation, this would register hooks with the AbstractStaticExtension system
        except Exception as e:
            logger.error(f"Error registering audit hooks: {e}")
