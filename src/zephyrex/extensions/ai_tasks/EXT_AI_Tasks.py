"""
AI Tasks extension for AGInfrastructure.

Provides comprehensive task management abilities including task creation,
scheduling, automation, execution, monitoring, and workflow orchestration.
"""

import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension, ability
from zephyrex.lib.Dependencies import EXT_Dependency, PIP_Dependency
from zephyrex.lib.Environment import env
from zephyrex.lib.Logging import logger


class EXT_AI_Tasks(AbstractStaticExtension):
    """
    AI Tasks extension for AGInfrastructure.

    Provides comprehensive task management capabilities including task creation,
    scheduling, automation, execution, and monitoring.

    The extension focuses on:
    - Task creation and management
    - Task scheduling and automation
    - Workflow orchestration and execution
    - Task monitoring and status tracking
    - Task dependencies and chaining
    - Recurring and one-time task execution

    Component loading (DB, BLL, EP) is handled automatically by the import system
    based on file naming conventions.
    """

    # Extension metadata
    name = "ai_tasks"
    version = "1.0.0"
    description = "AI Tasks extension for task management, scheduling, automation, and workflow orchestration"

    # Define dependencies
    ext_dependencies = [
        EXT_Dependency(
            name="core",
            friendly_name="Core Extension",
            optional=False,
            reason="Required for base task functionality and database operations",
        ),
        EXT_Dependency(
            name="ai_prompts",
            friendly_name="AI Prompts Extension",
            optional=True,
            reason="Integration with prompt templates for task instructions",
        ),
    ]

    pip_dependencies = [
        PIP_Dependency(
            name="celery",
            friendly_name="Celery Distributed Task Queue",
            optional=False,
            reason="Required for task scheduling and distributed execution",
            semver=">=5.3.0",
        ),
        PIP_Dependency(
            name="croniter",
            friendly_name="Cron Schedule Parser",
            optional=False,
            reason="Required for parsing cron expressions for task scheduling",
            semver=">=1.4.0",
        ),
        PIP_Dependency(
            name="redis",
            friendly_name="Redis Python Client",
            optional=True,
            reason="Redis backend for Celery task queue and result storage",
            semver=">=4.5.0",
        ),
        PIP_Dependency(
            name="apscheduler",
            friendly_name="Advanced Python Scheduler",
            optional=True,
            reason="Alternative task scheduler for complex scheduling scenarios",
            semver=">=3.10.0",
        ),
        PIP_Dependency(
            name="pydantic",
            friendly_name="Pydantic Data Validation",
            optional=False,
            reason="Required for task validation and serialization",
            semver=">=2.0.0",
        ),
    ]

    sys_dependencies = []

    # Define database tables (loaded via import system)
    db_tables = []

    # What capabilities this extension provides
    capabilities = [
        "task_management",
        "task_scheduling",
        "task_automation",
        "workflow_orchestration",
        "task_monitoring",
        "recurring_tasks",
        "task_dependencies",
    ]

    # Task status types
    TASK_STATUSES = {
        "pending": "Task is waiting to be executed",
        "running": "Task is currently executing",
        "completed": "Task completed successfully",
        "failed": "Task execution failed",
        "cancelled": "Task was cancelled",
        "retrying": "Task is being retried after failure",
        "scheduled": "Task is scheduled for future execution",
    }

    # Task types
    TASK_TYPES = {
        "one_time": "Execute once at specified time",
        "recurring": "Execute on recurring schedule",
        "conditional": "Execute when conditions are met",
        "workflow": "Part of a larger workflow",
        "ai_agent": "Execute using AI agent",
    }

    # Schedule types
    SCHEDULE_TYPES = {
        "cron": "Cron expression scheduling",
        "interval": "Fixed interval scheduling",
        "date": "One-time date scheduling",
        "event": "Event-driven scheduling",
    }

    def __init__(
        self,
        enable_celery: bool = True,
        redis_url: Optional[str] = None,
        max_concurrent_tasks: int = 10,
        task_timeout: int = 3600,  # 1 hour default
        enable_task_monitoring: bool = True,
        auto_retry_failed_tasks: bool = True,
        max_retries: int = 3,
        retry_delay: int = 60,  # seconds
        enable_workflows: bool = True,
        task_history_retention_days: int = 30,
        **kwargs,
    ):
        """Initialize the AI Tasks extension."""
        super().__init__(**kwargs)

        self.enable_celery = enable_celery
        self.redis_url = redis_url or env("REDIS_URL")
        self.max_concurrent_tasks = max_concurrent_tasks
        self.task_timeout = task_timeout
        self.enable_task_monitoring = enable_task_monitoring
        self.auto_retry_failed_tasks = auto_retry_failed_tasks
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.enable_workflows = enable_workflows
        self.task_history_retention_days = task_history_retention_days

        self.celery_app: Any = None
        self.running_tasks: Dict[str, Any] = {}
        self.scheduled_tasks: Dict[str, Any] = {}
        self.monitoring_data: Dict[str, Any] = {
            "tasks_created": 0,
            "tasks_executed": 0,
            "tasks_completed": 0,
            "tasks_failed": 0,
            "tasks_cancelled": 0,
            "average_execution_time": 0.0,
        }

    # -- Initialization ------------------------------------------------

    def on_initialize(self) -> bool:
        """Initialize the AI Tasks extension."""
        logger.debug("Initializing AI Tasks Extension...")

        try:
            if self.enable_celery and self.redis_url:
                self._initialize_celery()

            self._register_task_hooks()

            if self.enable_task_monitoring:
                self._initialize_task_monitoring()

            for capability in self.capabilities:
                self.register_capability(capability)

            logger.debug("AI Tasks extension initialized successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to initialize AI Tasks extension: {str(e)}")
            return False

    def _initialize_celery(self) -> None:
        """Initialize Celery for distributed task execution, if available."""
        try:
            from celery import Celery

            self.celery_app = Celery(
                "ai_tasks",
                broker=self.redis_url,
                backend=self.redis_url,
            )
            self.celery_app.conf.update(
                task_serializer="json",
                accept_content=["json"],
                result_serializer="json",
                timezone="UTC",
                enable_utc=True,
                task_track_started=True,
                task_time_limit=self.task_timeout,
            )
            logger.debug("Celery initialized successfully")

        except ImportError:
            logger.warning("Celery not available; tasks will run in-process")
            self.celery_app = None
        except Exception as e:
            logger.error(f"Failed to initialize Celery: {str(e)}")
            self.celery_app = None

    def _register_task_hooks(self) -> None:
        """Register hooks used to validate and track tasks as they occur."""
        logger.debug("Registering task hooks")

    def _initialize_task_monitoring(self) -> None:
        """Initialize task monitoring and metrics collection."""
        self.monitoring_data = {
            "tasks_created": 0,
            "tasks_executed": 0,
            "tasks_completed": 0,
            "tasks_failed": 0,
            "tasks_cancelled": 0,
            "average_execution_time": 0.0,
        }

    def register_capability(self, capability: str) -> None:
        """Register a new capability."""
        if capability not in self.capabilities:
            self.capabilities.append(capability)

    def get_registered_capabilities(self) -> Set[str]:
        """Return currently registered capabilities."""
        return set(self.capabilities)

    def get_capabilities(self) -> Set[str]:
        """Return the capabilities this extension provides."""
        return set(self.capabilities)

    # -- Task lifecycle abilities ---------------------------------------

    @ability("create_task")
    async def create_task(
        self,
        name: str,
        task_type: str = "one_time",
        instructions: str = "",
        schedule: Optional[Dict[str, Any]] = None,
        dependencies: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        ai_agent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a new task."""
        try:
            task_data = {
                "name": name,
                "task_type": task_type,
                "instructions": instructions,
                "schedule": schedule or {},
                "dependencies": dependencies or [],
                "metadata": metadata or {},
                "ai_agent_id": ai_agent_id,
                "status": "pending",
                "created_at": datetime.utcnow().isoformat(),
            }

            if not self._validate_task_data(task_data):
                return {"success": False, "message": "Invalid task data"}

            task_id = str(uuid.uuid4())
            task_data["id"] = task_id

            logger.debug(f"Created task: {name} ({task_id})")

            if self.enable_task_monitoring:
                self.monitoring_data["tasks_created"] += 1

            return {"success": True, "task": task_data, "task_id": task_id}

        except Exception as e:
            logger.error(f"Error creating task: {e}")
            return {"success": False, "message": f"Failed to create task: {str(e)}"}

    @ability("schedule_task")
    async def schedule_task(
        self,
        task_id: str,
        schedule_type: str = "date",
        schedule_config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Schedule a task for execution."""
        try:
            schedule_config = schedule_config or {}

            if schedule_type == "cron":
                cron_expression = schedule_config.get("cron_expression")
                if not cron_expression:
                    return {"success": False, "message": "Cron expression required"}

                from croniter import croniter

                if not croniter.is_valid(cron_expression):
                    return {"success": False, "message": "Invalid cron expression"}

            elif schedule_type == "interval":
                interval_seconds = schedule_config.get("interval_seconds")
                if not interval_seconds or interval_seconds <= 0:
                    return {"success": False, "message": "Valid interval required"}

            elif schedule_type == "date":
                execute_at = schedule_config.get("execute_at")
                if not execute_at:
                    return {"success": False, "message": "Execution date required"}

            job_id = f"task_{task_id}"
            self.scheduled_tasks[job_id] = {
                "task_id": task_id,
                "schedule_type": schedule_type,
                "schedule_config": schedule_config,
                "status": "scheduled",
                "scheduled_at": datetime.utcnow().isoformat(),
            }

            logger.debug(f"Scheduled task {task_id} with {schedule_type} schedule")
            return {"success": True, "message": "Task scheduled successfully"}

        except Exception as e:
            logger.error(f"Error scheduling task: {e}")
            return {"success": False, "message": f"Failed to schedule task: {str(e)}"}

    @ability("execute_task")
    async def execute_task(
        self,
        task_id: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Execute a task immediately."""
        try:
            context = context or {}

            if task_id in self.running_tasks:
                return {"success": False, "message": "Task is already running"}

            self.running_tasks[task_id] = {
                "start_time": datetime.utcnow(),
                "status": "running",
                "context": context,
            }

            execution_result = await self._execute_task(task_id, context)

            if task_id in self.running_tasks:
                del self.running_tasks[task_id]

            if self.enable_task_monitoring:
                self.monitoring_data["tasks_executed"] += 1
                if execution_result.get("success"):
                    self.monitoring_data["tasks_completed"] += 1
                else:
                    self.monitoring_data["tasks_failed"] += 1

            logger.debug(f"Executed task {task_id}: {execution_result}")
            return execution_result

        except Exception as e:
            if task_id in self.running_tasks:
                del self.running_tasks[task_id]

            logger.error(f"Error executing task: {e}")
            return {"success": False, "message": f"Task execution failed: {str(e)}"}

    @ability("cancel_task")
    async def cancel_task(self, task_id: str) -> Dict[str, Any]:
        """Cancel a scheduled or running task."""
        try:
            job_id = f"task_{task_id}"
            if job_id in self.scheduled_tasks:
                self.scheduled_tasks[job_id]["status"] = "cancelled"
                logger.debug(f"Cancelled scheduled task {task_id}")

            if task_id in self.running_tasks:
                self.running_tasks[task_id]["status"] = "cancelled"
                logger.debug(f"Marked running task {task_id} for cancellation")

            if self.enable_task_monitoring:
                self.monitoring_data["tasks_cancelled"] += 1

            return {"success": True, "message": "Task cancelled successfully"}

        except Exception as e:
            logger.error(f"Error cancelling task: {e}")
            return {"success": False, "message": f"Failed to cancel task: {str(e)}"}

    @ability("get_task_status")
    async def get_task_status(self, task_id: str) -> Dict[str, Any]:
        """Get the status of a task."""
        try:
            if task_id in self.running_tasks:
                task_info = self.running_tasks[task_id].copy()
                task_info["task_id"] = task_id
                return {"success": True, "task": task_info}

            job_id = f"task_{task_id}"
            if job_id in self.scheduled_tasks:
                scheduled = self.scheduled_tasks[job_id]
                return {
                    "success": True,
                    "task": {
                        "task_id": task_id,
                        "status": scheduled["status"],
                        "schedule_type": scheduled["schedule_type"],
                    },
                }

            return {"success": False, "message": "Task not found or completed"}

        except Exception as e:
            logger.error(f"Error getting task status: {e}")
            return {"success": False, "message": f"Failed to get task status: {str(e)}"}

    @ability("list_tasks")
    async def list_tasks(
        self,
        status_filter: Optional[str] = None,
        task_type_filter: Optional[str] = None,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """List tasks with optional filtering."""
        try:
            tasks = []

            for task_id, task_info in self.running_tasks.items():
                if not status_filter or task_info["status"] == status_filter:
                    task_data = task_info.copy()
                    task_data["task_id"] = task_id
                    tasks.append(task_data)

            for job_id, scheduled in self.scheduled_tasks.items():
                if not status_filter or scheduled["status"] == status_filter:
                    task_data = scheduled.copy()
                    task_data["job_id"] = job_id
                    tasks.append(task_data)

            tasks = tasks[:limit]

            return {"success": True, "tasks": tasks, "total": len(tasks)}

        except Exception as e:
            logger.error(f"Error listing tasks: {e}")
            return {"success": False, "message": f"Failed to list tasks: {str(e)}"}

    @ability("create_workflow")
    async def create_workflow(
        self,
        name: str,
        tasks: List[Dict[str, Any]],
        workflow_type: str = "sequential",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create a workflow with multiple tasks."""
        try:
            if not self.enable_workflows:
                return {"success": False, "message": "Workflows not enabled"}

            workflow_data = {
                "name": name,
                "workflow_type": workflow_type,
                "tasks": tasks,
                "metadata": metadata or {},
                "status": "pending",
                "created_at": datetime.utcnow().isoformat(),
            }

            workflow_id = str(uuid.uuid4())
            workflow_data["id"] = workflow_id

            for task in tasks:
                if not self._validate_task_data(task):
                    return {
                        "success": False,
                        "message": f"Invalid task in workflow: {task.get('name', 'unknown')}",
                    }

            logger.debug(f"Created workflow: {name} ({workflow_id})")
            return {
                "success": True,
                "workflow": workflow_data,
                "workflow_id": workflow_id,
            }

        except Exception as e:
            logger.error(f"Error creating workflow: {e}")
            return {"success": False, "message": f"Failed to create workflow: {str(e)}"}

    # -- Helpers ---------------------------------------------------------

    def _validate_task_data(self, task_data: Any) -> bool:
        """Validate task data structure."""
        try:
            if isinstance(task_data, dict):
                required_fields = ["name", "task_type"]
                return all(field in task_data for field in required_fields)
            return hasattr(task_data, "name") and hasattr(task_data, "task_type")
        except Exception:
            return False

    async def _execute_task(
        self, task_id: str, context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Internal method to execute a task."""
        try:
            return {
                "success": True,
                "task_id": task_id,
                "result": f"Task {task_id} executed successfully",
                "execution_time": 0.0,
                "context": context,
            }
        except Exception as e:
            return {
                "success": False,
                "task_id": task_id,
                "error": str(e),
                "execution_time": 0.0,
            }

    def _parse_cron_expression(self, cron_expression: str) -> Dict[str, Any]:
        """Parse cron expression into scheduler parameters."""
        parts = cron_expression.split()
        if len(parts) == 5:
            minute, hour, day, month, day_of_week = parts
            return {
                "minute": minute if minute != "*" else None,
                "hour": hour if hour != "*" else None,
                "day": day if day != "*" else None,
                "month": month if month != "*" else None,
                "day_of_week": day_of_week if day_of_week != "*" else None,
            }
        return {}

    def _track_task_execution(self, result: Any) -> None:
        """Track task execution for analytics."""
        if self.enable_task_monitoring and result:
            logger.debug(f"Tracked task execution result: {result}")

    async def _cleanup_old_executions(self) -> None:
        """Clean up old task executions."""
        try:
            cutoff_date = datetime.utcnow() - timedelta(
                days=self.task_history_retention_days
            )
            logger.debug(f"Cleaning up task executions older than {cutoff_date}")
        except Exception as e:
            logger.error(f"Error during task cleanup: {e}")

    # -- Lifecycle -----------------------------------------------------------

    def on_start(self) -> bool:
        """Start the AI Tasks extension."""
        try:
            logger.debug("AI Tasks extension started successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to start AI Tasks extension: {str(e)}")
            return False

    def on_stop(self) -> bool:
        """Stop the AI Tasks extension and clear in-memory state."""
        try:
            self.running_tasks.clear()
            self.scheduled_tasks.clear()
            logger.debug("AI Tasks extension stopped successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to stop AI Tasks extension: {str(e)}")
            return False

    def on_startup(self) -> None:
        """Called when the application starts up."""
        logger.debug("AI Tasks extension startup complete")

    def on_shutdown(self) -> None:
        """Called when the application shuts down."""
        logger.debug("AI Tasks extension shutdown complete")
        try:
            if self.monitoring_data:
                logger.debug(f"Final task statistics: {self.monitoring_data}")
        except Exception as e:
            logger.warning(f"Error during shutdown cleanup: {str(e)}")

    def validate_config(self) -> List[str]:
        """Validate the extension configuration."""
        issues = []

        try:
            import celery  # noqa: F401
        except ImportError:
            if self.enable_celery:
                issues.append(
                    "Celery library not installed - task queue functionality will not work"
                )

        try:
            import croniter  # noqa: F401
        except ImportError:
            issues.append(
                "Croniter library not installed - cron scheduling will not work"
            )

        try:
            import pydantic  # noqa: F401
        except ImportError:
            issues.append(
                "Pydantic library not installed - task validation will not work"
            )

        if self.enable_celery and not self.redis_url:
            issues.append("Redis URL not configured for Celery task queue")

        return issues

    def get_required_permissions(self) -> List[str]:
        """Return the list of permissions required by this extension."""
        return [
            "tasks:create",
            "tasks:read",
            "tasks:update",
            "tasks:delete",
            "tasks:execute",
            "tasks:schedule",
            "workflows:create",
            "workflows:execute",
        ]

    def has_capability(self, capability: str) -> bool:
        """Check if this extension has a specific capability."""
        return capability in self.capabilities

    def get_task_statuses(self) -> Dict[str, str]:
        """Get available task statuses."""
        return dict(self.TASK_STATUSES)

    def get_task_types(self) -> Dict[str, str]:
        """Get available task types."""
        return dict(self.TASK_TYPES)

    def get_schedule_types(self) -> Dict[str, str]:
        """Get available schedule types."""
        return dict(self.SCHEDULE_TYPES)

    def get_monitoring_data(self) -> Dict[str, Any]:
        """Get current monitoring data."""
        if not self.enable_task_monitoring:
            return {"message": "Task monitoring not enabled"}
        return dict(getattr(self, "monitoring_data", {}))

    def get_running_tasks(self) -> Dict[str, Any]:
        """Get currently running tasks."""
        return dict(self.running_tasks)
