# SPDX-License-Identifier: AGPL-3.0-or-later
"""Audit and system log records, and the guards on the managers that read them.

``AuditLogModel`` and ``SystemLogModel`` are ordinary database models (the
``audit_logs`` and ``system_logs`` tables); their managers expose the standard
CRUD surface. Every call on either manager requires an authenticated requester
and is logged with its duration, and list/search calls are rate limited per
requester. The hooks are bound to these two manager classes only, so they act
solely in apps that loaded meta_logging.

Model fields are spelled ``Optional[...]``: the SQLAlchemy column builder
unwraps ``typing.Union`` only, so ``X | None`` would turn a JSON column into a
string one.
"""

import time
from collections.abc import Sized
from datetime import datetime, timezone
from typing import Any, ClassVar, Dict, List, Optional, Self

from fastapi import HTTPException
from pydantic import Field, model_validator

from zephyrex.lib.InboundSecurity import _get_counter
from zephyrex.lib.Logging import logger
from zephyrex.logic.AbstractLogicManager import (
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
from zephyrex.pydantic2.registry import BaseModel


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class _LogEntryCreate(BaseModel):
    """Create body shared by both log tables.

    ``timestamp`` (when the event occurred) defaults to now and is always
    written: the column is NOT NULL with no database default, and the manager
    persists only the fields a Create body marks as set."""

    timestamp: datetime = Field(
        default_factory=_utc_now, description="When the event occurred"
    )

    @model_validator(mode="after")
    def _always_persist_timestamp(self) -> Self:
        self.model_fields_set.add("timestamp")
        return self


class AuditLogModel(ApplicationModel, UpdateMixinModel, metaclass=ModelMeta):
    """One audited action: who did what to which resource, and whether it
    succeeded."""

    timestamp: datetime = Field(
        default_factory=_utc_now, description="When the event occurred"
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

    table_comment: ClassVar[str] = (
        "Audit log entries for security and compliance tracking"
    )

    class Create(_LogEntryCreate):
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
        timestamp: Optional[DateSearchModel] = None
        user_id: Optional[StringSearchModel] = None
        action: Optional[StringSearchModel] = None
        resource_type: Optional[StringSearchModel] = None
        resource_id: Optional[StringSearchModel] = None
        success: Optional[bool] = None
        privacy_impact: Optional[bool] = None


class SystemLogModel(ApplicationModel, UpdateMixinModel, metaclass=ModelMeta):
    """One system event from a named component."""

    timestamp: datetime = Field(
        default_factory=_utc_now, description="When the event occurred"
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

    table_comment: ClassVar[str] = "General system logs for debugging and monitoring"

    class Create(_LogEntryCreate):
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


class AuditLogManager(AbstractBLLManager[AuditLogModel]):
    _model = AuditLogModel

    def log_audit_event(self, event: AuditLogModel.Create) -> AuditLogModel:
        """Persist one audit event (the entry point for audit producers such
        as billing's cost-audit emitter)."""
        logged: AuditLogModel = self.create(**event.model_dump())
        return logged


class SystemLogManager(AbstractBLLManager[SystemLogModel]):
    _model = SystemLogModel


META_LOG_QUERY_LIMIT = 20
META_LOG_QUERY_WINDOW_SECONDS = 60
RATE_LIMITED_QUERY_METHODS = frozenset({"list", "search"})
_ACCESS_STARTED_AT = "meta_logging_access_started_at"


def require_requester_hook(context: HookContext) -> None:
    """Reject any log operation from a manager built without a requester."""
    if context.manager.optional_requester is None:
        raise HTTPException(
            status_code=401, detail="Authentication required for log access"
        )


def access_log_hook(context: HookContext) -> None:
    """Record who touched the logs, how long it took and how much came back."""
    requester_id = context.manager.requester.id
    if context.timing == HookTiming.BEFORE:
        context.condition_data[_ACCESS_STARTED_AT] = time.monotonic()
        logger.debug(
            "Meta logging access: %s by user %s", context.method_name, requester_id
        )
        return
    duration = time.monotonic() - context.condition_data[_ACCESS_STARTED_AT]
    result_count = len(context.result) if isinstance(context.result, Sized) else 0
    logger.debug(
        "Meta logging completed: %s by user %s, success=%s, duration=%.3fs, "
        "results=%d",
        context.method_name,
        requester_id,
        context.result is not None,
        duration,
        result_count,
    )


def query_rate_limit_hook(context: HookContext) -> None:
    """Limit each requester to META_LOG_QUERY_LIMIT log queries per window,
    counted through the framework's shared rate-limit counter (process-wide,
    or distributed when one is wired) so the tally outlives the per-request
    manager."""
    if context.method_name not in RATE_LIMITED_QUERY_METHODS:
        return
    requester = context.manager.optional_requester
    if requester is None:
        return
    attempts = _get_counter().incr(
        f"meta_logging_query:{requester.id}", META_LOG_QUERY_WINDOW_SECONDS
    )
    if attempts > META_LOG_QUERY_LIMIT:
        logger.warning(
            "Meta logging query rate limit exceeded by user %s", requester.id
        )
        raise HTTPException(
            status_code=429,
            detail="Too many log queries. Please wait before trying again.",
        )


def _bind_log_manager_hooks(manager_class: type[AbstractBLLManager[Any]]) -> None:
    hook_bll(manager_class, timing=HookTiming.BEFORE, priority=1)(
        require_requester_hook
    )
    hook_bll(manager_class, timing=HookTiming.BEFORE, priority=5)(access_log_hook)
    hook_bll(manager_class, timing=HookTiming.BEFORE, priority=10)(
        query_rate_limit_hook
    )
    hook_bll(manager_class, timing=HookTiming.AFTER, priority=95)(access_log_hook)


# Bound once, at import, to meta_logging's own manager classes: the hooks fire
# only where those managers run, i.e. in apps that loaded this extension.
_bind_log_manager_hooks(AuditLogManager)
_bind_log_manager_hooks(SystemLogManager)
