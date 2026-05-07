from datetime import datetime, timezone
from typing import Any, ClassVar, Dict, List, Optional

from fastapi import HTTPException
from pydantic import Field

from serverframework.lib.Logging import logger
from serverframework.lib.Pydantic import BaseModel
from serverframework.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    DateSearchModel,
    HookContext,
    HookTiming,
    ModelMeta,
    StringSearchModel,
    UpdateMixinModel,
    hook_bll,
)


class AuditLogModel(ApplicationModel, UpdateMixinModel, metaclass=ModelMeta):
    """Model for audit log entries stored in InfluxDB."""

    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), description="Log timestamp"
    )
    user_id: Optional[str] = Field(
        None, description="ID of the user performing the action"
    )
    action: str = Field(..., description="Action being performed")
    resource_type: str = Field(..., description="Type of resource being accessed")
    resource_id: Optional[str] = Field(None, description="ID of the specific resource")
    ip_address: Optional[str] = Field(None, description="IP address of the request")
    user_agent: Optional[str] = Field(None, description="User agent string")
    success: bool = Field(True, description="Whether the action was successful")
    error_message: Optional[str] = Field(
        None, description="Error message if action failed"
    )
    additional_data: Optional[Dict[str, Any]] = Field(
        None, description="Additional context data"
    )
    privacy_impact: bool = Field(
        False, description="Whether this action has privacy implications"
    )
    data_categories: Optional[List[str]] = Field(
        None, description="Categories of data accessed"
    )

    # Database metadata
    table_comment: ClassVar[str] = (
        "Audit log entries for security and compliance tracking"
    )

    class Create(BaseModel):
        user_id: Optional[str] = None
        action: str = Field(..., description="Action being performed")
        resource_type: str = Field(..., description="Type of resource being accessed")
        resource_id: Optional[str] = None
        ip_address: Optional[str] = None
        user_agent: Optional[str] = None
        success: bool = True
        error_message: Optional[str] = None
        additional_data: Optional[Dict[str, Any]] = None
        privacy_impact: bool = False
        data_categories: Optional[List[str]] = None

    class Update(BaseModel):
        success: Optional[bool] = None
        error_message: Optional[str] = None
        additional_data: Optional[Dict[str, Any]] = None
        privacy_impact: Optional[bool] = None
        data_categories: Optional[List[str]] = None

    class Search(ApplicationModel.Search, UpdateMixinModel.Search):
        start_time: Optional[datetime] = None
        end_time: Optional[datetime] = None
        timestamp: Optional[DateSearchModel] = None
        user_id: Optional[StringSearchModel] = None
        action: Optional[StringSearchModel] = None
        resource_type: Optional[StringSearchModel] = None
        resource_id: Optional[StringSearchModel] = None
        success: Optional[bool] = None
        privacy_impact: Optional[bool] = None


class SystemLogModel(ApplicationModel, UpdateMixinModel, metaclass=ModelMeta):
    """Model for general system logs."""

    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), description="Log timestamp"
    )
    level: str = Field(
        ..., description="Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)"
    )
    component: str = Field(..., description="System component generating the log")
    message: str = Field(..., description="Log message")
    user_id: Optional[str] = Field(None, description="Associated user ID if applicable")
    request_id: Optional[str] = Field(None, description="Request ID for tracing")
    additional_data: Optional[Dict[str, Any]] = Field(
        None, description="Additional context data"
    )

    # Database metadata
    table_comment: ClassVar[str] = "General system logs for debugging and monitoring"

    class Create(BaseModel):
        level: str = Field(..., description="Log level")
        component: str = Field(..., description="System component")
        message: str = Field(..., description="Log message")
        user_id: Optional[str] = None
        request_id: Optional[str] = None
        additional_data: Optional[Dict[str, Any]] = None

    class Update(BaseModel):
        level: Optional[str] = None
        component: Optional[str] = None
        message: Optional[str] = None
        additional_data: Optional[Dict[str, Any]] = None

    class Search(ApplicationModel.Search, UpdateMixinModel.Search):
        timestamp: Optional[DateSearchModel] = None
        level: Optional[StringSearchModel] = None
        component: Optional[StringSearchModel] = None
        user_id: Optional[StringSearchModel] = None
        request_id: Optional[StringSearchModel] = None


class AuditLogManager(AbstractBLLManager):
    """Manager for audit log operations with InfluxDB backend."""

    _model = AuditLogModel

    def __init__(
        self,
        requester_id: str,
        target_id: Optional[str] = None,
        target_team_id: Optional[str] = None,
        *,
        model_registry,
        parent: Optional[AbstractBLLManager] = None,
    ):
        super().__init__(
            requester_id=requester_id,
            target_id=target_id,
            target_team_id=target_team_id,
            model_registry=model_registry,
            parent=parent,
        )
        self.database_extension = None
        self.influxdb_available = False
        self._detect_database_extension()

    def _detect_database_extension(self):
        """Detect if the database extension with InfluxDB is available."""
        try:
            from serverframework.extensions.AbstractExtensionProvider import AbstractStaticExtension

            self.database_extension = AbstractStaticExtension.get_extension_by_name(
                "database"
            )

            if self.database_extension and hasattr(self.database_extension, "provider"):
                provider = self.database_extension.provider
                if provider and hasattr(provider, "get_db_type"):
                    db_type = provider.get_db_type()
                    if "InfluxDB" in db_type:
                        self.influxdb_available = True

        except Exception as e:

            logger.debug(f"Error detecting database extension: {e}")

    async def log_audit_event(self, audit_data: AuditLogModel.Create) -> Dict[str, Any]:
        """Log an audit event to InfluxDB."""
        if not self.influxdb_available:
            return {"success": False, "message": "InfluxDB not available"}

        try:
            # Convert to InfluxDB line protocol format
            timestamp = datetime.now(timezone.utc)

            # Build tags (indexed fields)
            tags = {
                "action": audit_data.action,
                "resource_type": audit_data.resource_type,
                "success": str(audit_data.success).lower(),
                "privacy_impact": str(audit_data.privacy_impact).lower(),
            }

            if audit_data.user_id:
                tags["user_id"] = audit_data.user_id
            if audit_data.resource_id:
                tags["resource_id"] = audit_data.resource_id

            # Build fields (non-indexed values)
            fields = {
                "timestamp": timestamp.isoformat(),
            }

            if audit_data.ip_address:
                fields["ip_address"] = audit_data.ip_address
            if audit_data.user_agent:
                fields["user_agent"] = audit_data.user_agent
            if audit_data.error_message:
                fields["error_message"] = audit_data.error_message
            if audit_data.additional_data:
                fields["additional_data"] = str(audit_data.additional_data)
            if audit_data.data_categories:
                fields["data_categories"] = ",".join(audit_data.data_categories)

            # Prepare data for InfluxDB
            influx_data = {
                "measurement": "audit_logs",
                "tags": tags,
                "fields": fields,
                "time": timestamp,
            }

            # Write to InfluxDB via database extension
            result = await self.database_extension.execute_ability(
                "write_data", {"points": [influx_data]}
            )

            return {"success": True, "message": "Audit event logged", "result": result}

        except Exception as e:

            logger.error(f"Failed to log audit event: {e}")
            return {"success": False, "message": f"Failed to log audit event: {str(e)}"}

    async def query_audit_logs(
        self, search_params: AuditLogModel.Search
    ) -> List[Dict[str, Any]]:
        """Query audit logs from InfluxDB."""
        if not self.influxdb_available:
            return []

        try:
            # Build InfluxDB query
            query_parts = ['from(bucket: "audit_logs")', "|> range(start: -30d)"]

            if search_params.start_time:
                query_parts[-1] = (
                    f"|> range(start: {search_params.start_time.isoformat()}Z"
                )
                if search_params.end_time:
                    query_parts[-1] += f", stop: {search_params.end_time.isoformat()}Z)"
                else:
                    query_parts[-1] += ")"

            query_parts.append('|> filter(fn: (r) => r._measurement == "audit_logs")')

            if search_params.user_id:
                query_parts.append(
                    f'|> filter(fn: (r) => r.user_id == "{search_params.user_id}")'
                )
            if search_params.action:
                query_parts.append(
                    f'|> filter(fn: (r) => r.action == "{search_params.action}")'
                )
            if search_params.resource_type:
                query_parts.append(
                    f'|> filter(fn: (r) => r.resource_type == "{search_params.resource_type}")'
                )
            if search_params.success is not None:
                query_parts.append(
                    f'|> filter(fn: (r) => r.success == "{str(search_params.success).lower()}")'
                )
            if search_params.privacy_impact is not None:
                query_parts.append(
                    f'|> filter(fn: (r) => r.privacy_impact == "{str(search_params.privacy_impact).lower()}")'
                )

            flux_query = "\n  ".join(query_parts)

            # Execute query via database extension
            result = await self.database_extension.execute_ability(
                "execute_query", flux_query
            )

            # Parse results (this would need to be adapted based on actual response format)
            return self._parse_influx_results(result)

        except Exception as e:

            logger.error(f"Failed to query audit logs: {e}")
            return []

    def _parse_influx_results(self, result_data: str) -> List[Dict[str, Any]]:
        """Parse InfluxDB query results into a list of dictionaries."""
        try:
            import json

            # Try to parse as JSON first
            if isinstance(result_data, str):
                try:
                    return json.loads(result_data)
                except json.JSONDecodeError:
                    # If not JSON, return raw data wrapped in a list
                    return [{"raw_data": result_data}]

            return result_data if isinstance(result_data, list) else [result_data]

        except Exception as e:

            logger.error(f"Failed to parse InfluxDB results: {e}")
            return []

    async def get_privacy_audit_report(self, days: int = 30) -> Dict[str, Any]:
        """Generate a privacy audit report for the specified number of days."""
        if not self.influxdb_available:
            return {"success": False, "message": "InfluxDB not available"}

        try:
            # Query for privacy-impacting events
            search_params = AuditLogModel.Search(
                start_time=datetime.now(timezone.utc).replace(
                    hour=0, minute=0, second=0, microsecond=0
                ),
                privacy_impact=True,
            )

            privacy_events = await self.query_audit_logs(search_params)

            # Generate summary statistics
            total_events = len(privacy_events)
            unique_users = len(
                set(
                    event.get("user_id")
                    for event in privacy_events
                    if event.get("user_id")
                )
            )

            # Group by data categories
            data_category_counts = {}
            for event in privacy_events:
                categories = (
                    event.get("data_categories", "").split(",")
                    if event.get("data_categories")
                    else []
                )
                for category in categories:
                    if category.strip():
                        data_category_counts[category.strip()] = (
                            data_category_counts.get(category.strip(), 0) + 1
                        )

            return {
                "success": True,
                "report": {
                    "period_days": days,
                    "total_privacy_events": total_events,
                    "unique_users_affected": unique_users,
                    "data_category_breakdown": data_category_counts,
                    "events": privacy_events,
                },
            }

        except Exception as e:

            logger.error(f"Failed to generate privacy audit report: {e}")
            return {"success": False, "message": f"Failed to generate report: {str(e)}"}


class SystemLogManager(AbstractBLLManager):
    """Manager for system log operations."""

    _model = SystemLogModel

    def __init__(
        self,
        requester_id: str,
        target_id: Optional[str] = None,
        target_team_id: Optional[str] = None,
        *,
        model_registry,
        parent: Optional[AbstractBLLManager] = None,
    ):
        super().__init__(
            requester_id=requester_id,
            target_id=target_id,
            target_team_id=target_team_id,
            model_registry=model_registry,
            parent=parent,
        )
        self.database_extension = None
        self.influxdb_available = False
        self._detect_database_extension()

    def _detect_database_extension(self):
        """Detect if the database extension with InfluxDB is available."""
        try:
            from serverframework.extensions.AbstractExtensionProvider import AbstractStaticExtension

            self.database_extension = AbstractStaticExtension.get_extension_by_name(
                "database"
            )

            if self.database_extension and hasattr(self.database_extension, "provider"):
                provider = self.database_extension.provider
                if provider and hasattr(provider, "get_db_type"):
                    db_type = provider.get_db_type()
                    if "InfluxDB" in db_type:
                        self.influxdb_available = True

        except Exception as e:

            logger.debug(f"Error detecting database extension: {e}")

    async def log_system_event(
        self, system_data: SystemLogModel.Create
    ) -> Dict[str, Any]:
        """Log a system event to InfluxDB."""
        if not self.influxdb_available:
            return {"success": False, "message": "InfluxDB not available"}

        try:
            timestamp = datetime.now(timezone.utc)

            # Build tags
            tags = {
                "level": system_data.level,
                "component": system_data.component,
            }

            if system_data.user_id:
                tags["user_id"] = system_data.user_id
            if system_data.request_id:
                tags["request_id"] = system_data.request_id

            # Build fields
            fields = {
                "timestamp": timestamp.isoformat(),
                "message": system_data.message,
            }

            if system_data.additional_data:
                fields["additional_data"] = str(system_data.additional_data)

            # Prepare data for InfluxDB
            influx_data = {
                "measurement": "system_logs",
                "tags": tags,
                "fields": fields,
                "time": timestamp,
            }

            # Write to InfluxDB via database extension
            result = await self.database_extension.execute_ability(
                "write_data", {"points": [influx_data]}
            )

            return {"success": True, "message": "System event logged", "result": result}

        except Exception as e:

            logger.error(f"Failed to log system event: {e}")
            return {
                "success": False,
                "message": f"Failed to log system event: {str(e)}",
            }


class MetaLoggingManager(AbstractBLLManager):
    """Main manager for meta logging operations with InfluxDB backend."""

    _model = AuditLogModel  # Default to AuditLogModel

    def __init__(
        self,
        requester_id: str,
        target_id: Optional[str] = None,
        target_team_id: Optional[str] = None,
        *,
        model_registry,
        parent: Optional[AbstractBLLManager] = None,
    ):
        super().__init__(
            requester_id=requester_id,
            target_id=target_id,
            target_team_id=target_team_id,
            model_registry=model_registry,
            parent=parent,
        )
        self._audit_logs = None
        self._failed_logins = None
        self._system_logs = None

    @property
    def audit_logs(self):
        """Get the audit log manager"""
        if self._audit_logs is None:
            self._audit_logs = AuditLogManager(
                requester_id=self.requester.id,
                target_id=self.target_id,
                target_team_id=self.target_team_id,
                model_registry=self.model_registry,
                parent=self,
            )
        return self._audit_logs

    @property
    def failed_logins(self):
        """Get the failed login manager from logic.BLL_Auth"""
        if self._failed_logins is None:
            from serverframework.extensions.auth_lockout.BLL_Lockout import FailedLoginAttemptManager

            self._failed_logins = FailedLoginAttemptManager(
                requester_id=self.requester.id,
                target_team_id=self.target_team_id,
                model_registry=self.model_registry,
            )
        return self._failed_logins

    @property
    def system_logs(self):
        """Get the system log manager"""
        if self._system_logs is None:
            self._system_logs = SystemLogManager(
                requester_id=self.requester.id,
                target_id=self.target_id,
                target_team_id=self.target_team_id,
                model_registry=self.model_registry,
                parent=self,
            )
        return self._system_logs


# Security and audit hooks for meta logging operations
def meta_logging_security_hook(context: HookContext) -> None:
    """Security validation for meta logging operations."""
    manager = context.manager
    method_name = context.method_name

    # Ensure requester has permission for meta logging operations
    if not hasattr(manager.requester, "id"):
        raise HTTPException(
            status_code=401,
            detail="Authentication required for meta logging operations",
        )

    # Meta logging should typically be system-level operations
    # Add additional authorization checks here if needed
    logger.debug(f"Meta logging operation {method_name} by user {manager.requester.id}")


def meta_logging_audit_hook(context: HookContext) -> None:
    """Audit hook for meta logging operations - tracks who accesses logs."""
    manager = context.manager
    method_name = context.method_name
    requester_id = manager.requester.id

    if context.timing == HookTiming.BEFORE:
        # Log access to logging system
        logger.debug(f"Meta logging access: {method_name} by user {requester_id}")

        # Store audit data
        context.condition_data["audit_start"] = datetime.now(timezone.utc)
        context.condition_data["method"] = method_name
        context.condition_data["requester"] = requester_id

    elif context.timing == HookTiming.AFTER:
        # Log completion and any results
        duration = datetime.now(timezone.utc) - context.condition_data["audit_start"]
        success = context.result is not None

        # For queries, log the number of results
        result_count = 0
        if hasattr(context.result, "__len__"):
            try:
                result_count = len(context.result)
            except TypeError as e:
                # `__len__` exists but is not callable / raises (rare).
                logger.debug(
                    "meta_logging audit: result.__len__() failed: %s", e
                )

        logger.debug(
            f"Meta logging completed: {method_name} by user {requester_id}, "
            f"success={success}, duration={duration.total_seconds():.3f}s, results={result_count}"
        )


def meta_logging_rate_limiting_hook(context: HookContext) -> None:
    """Rate limiting for meta logging queries to prevent abuse."""
    method_name = context.method_name
    manager = context.manager

    # Apply rate limiting to query operations
    if method_name in ["query_audit_logs", "query_failed_logins", "list", "search"]:
        # Simple in-memory rate limiting (could be enhanced with Redis)
        if not hasattr(manager, "_query_rate_limit_tracker"):
            manager._query_rate_limit_tracker = {}

        requester_id = manager.requester.id
        current_time = datetime.now(timezone.utc)

        # Check if user has exceeded rate limit (20 queries per minute)
        if requester_id in manager._query_rate_limit_tracker:
            attempts = manager._query_rate_limit_tracker[requester_id]
            recent_attempts = [t for t in attempts if (current_time - t).seconds < 60]

            if len(recent_attempts) >= 20:
                logger.warning(
                    f"Meta logging query rate limit exceeded by user {requester_id}"
                )
                raise HTTPException(
                    status_code=429,
                    detail="Too many log queries. Please wait before trying again.",
                )

            manager._query_rate_limit_tracker[requester_id] = recent_attempts + [
                current_time
            ]
        else:
            manager._query_rate_limit_tracker[requester_id] = [current_time]


# Privacy audit access hook function
def privacy_audit_access_hook(context: HookContext) -> None:
    """Special security hook for privacy audit report access."""
    manager = context.manager
    requester_id = manager.requester.id

    # Log privacy audit access for compliance
    logger.warning(f"Privacy audit report accessed by user {requester_id}")

    # Could add additional authorization checks here
    # For example, require specific permissions for privacy audit access

    # Store audit trail for this sensitive operation
    context.condition_data["privacy_audit_access"] = {
        "requester": requester_id,
        "timestamp": datetime.now(timezone.utc),
        "ip_address": getattr(context, "ip_address", "unknown"),
    }


# Apply security and audit hooks to all meta logging manager classes
# Class-level hooks apply to ALL methods of these managers

# Apply hooks to AuditLogManager
hook_bll(AuditLogManager, timing=HookTiming.BEFORE, priority=1)(
    meta_logging_security_hook
)
hook_bll(AuditLogManager, timing=HookTiming.BEFORE, priority=5)(meta_logging_audit_hook)
hook_bll(AuditLogManager, timing=HookTiming.AFTER, priority=95)(meta_logging_audit_hook)
hook_bll(AuditLogManager, timing=HookTiming.BEFORE, priority=10)(
    meta_logging_rate_limiting_hook
)

# FailedLoginAttemptManager hooks removed - using the manager from logic.BLL_Auth instead
# Hooks can be applied to the BLL_Auth version if needed

# Apply hooks to SystemLogManager
hook_bll(SystemLogManager, timing=HookTiming.BEFORE, priority=1)(
    meta_logging_security_hook
)
hook_bll(SystemLogManager, timing=HookTiming.BEFORE, priority=5)(
    meta_logging_audit_hook
)
hook_bll(SystemLogManager, timing=HookTiming.AFTER, priority=95)(
    meta_logging_audit_hook
)
hook_bll(SystemLogManager, timing=HookTiming.BEFORE, priority=10)(
    meta_logging_rate_limiting_hook
)

# Apply hooks to MetaLoggingManager
hook_bll(MetaLoggingManager, timing=HookTiming.BEFORE, priority=1)(
    meta_logging_security_hook
)
hook_bll(MetaLoggingManager, timing=HookTiming.BEFORE, priority=5)(
    meta_logging_audit_hook
)
hook_bll(MetaLoggingManager, timing=HookTiming.AFTER, priority=95)(
    meta_logging_audit_hook
)
hook_bll(MetaLoggingManager, timing=HookTiming.BEFORE, priority=10)(
    meta_logging_rate_limiting_hook
)

# Apply specific hook for sensitive audit operations
hook_bll(
    AuditLogManager.get_privacy_audit_report, timing=HookTiming.BEFORE, priority=15
)(privacy_audit_access_hook)
