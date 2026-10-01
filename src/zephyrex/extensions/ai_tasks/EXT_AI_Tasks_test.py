from unittest.mock import MagicMock, patch

import pytest

from zephyrex.extensions.ai_tasks.EXT_AI_Tasks import EXT_AI_Tasks


class TestAITasksExtension:
    """Test cases for AI Tasks Extension."""

    @pytest.fixture
    def extension(self):
        """Create an EXT_AI_Tasks instance for testing."""
        return EXT_AI_Tasks()

    @pytest.fixture
    def mock_croniter_available(self):
        """Mock croniter library availability."""
        mock_croniter = MagicMock()
        mock_croniter.croniter.is_valid.return_value = True

        with patch.dict("sys.modules", {"croniter": mock_croniter}):
            yield mock_croniter

    def test_extension_metadata(self, extension):
        """Test extension metadata is correctly set."""
        assert extension.name == "ai_tasks"
        assert extension.version == "1.0.0"
        assert "task" in extension.description.lower()

    def test_dependencies(self, extension):
        """Test extension dependencies are properly defined."""
        ext_deps = {dep.name: dep for dep in extension.ext_dependencies}
        assert "core" in ext_deps
        assert not ext_deps["core"].optional
        assert "ai_prompts" in ext_deps
        assert ext_deps["ai_prompts"].optional

        pip_deps = {dep.name: dep for dep in extension.pip_dependencies}
        assert "celery" in pip_deps
        assert ">=5.3.0" in pip_deps["celery"].semver
        assert not pip_deps["celery"].optional
        assert "croniter" in pip_deps
        assert not pip_deps["croniter"].optional
        assert "redis" in pip_deps
        assert pip_deps["redis"].optional
        assert "apscheduler" in pip_deps
        assert pip_deps["apscheduler"].optional
        assert "pydantic" in pip_deps
        assert not pip_deps["pydantic"].optional

        assert isinstance(extension.sys_dependencies, list)

    def test_capabilities(self, extension):
        """Test extension capabilities are properly defined."""
        expected_capabilities = {
            "task_management",
            "task_scheduling",
            "task_automation",
            "workflow_orchestration",
            "task_monitoring",
            "recurring_tasks",
            "task_dependencies",
        }
        assert set(extension.capabilities) == expected_capabilities

    def test_initialization(self, extension):
        """Test extension initialization state."""
        assert hasattr(extension, "running_tasks")
        assert hasattr(extension, "scheduled_tasks")
        assert hasattr(extension, "monitoring_data")
        assert isinstance(extension.running_tasks, dict)
        assert isinstance(extension.scheduled_tasks, dict)
        assert isinstance(extension.monitoring_data, dict)

    def test_initialization_with_custom_settings(self):
        """Test extension initialization with custom settings."""
        extension = EXT_AI_Tasks(
            enable_celery=False,
            max_concurrent_tasks=5,
            task_timeout=1800,
            enable_task_monitoring=False,
            auto_retry_failed_tasks=False,
            max_retries=5,
            retry_delay=120,
            enable_workflows=False,
            task_history_retention_days=60,
        )

        assert extension.enable_celery is False
        assert extension.max_concurrent_tasks == 5
        assert extension.task_timeout == 1800
        assert extension.enable_task_monitoring is False
        assert extension.auto_retry_failed_tasks is False
        assert extension.max_retries == 5
        assert extension.retry_delay == 120
        assert extension.enable_workflows is False
        assert extension.task_history_retention_days == 60

    @patch("zephyrex.extensions.ai_tasks.EXT_AI_Tasks.env")
    def test_initialization_redis_url_from_env(self, mock_env):
        """Redis URL falls back to the environment when not provided explicitly."""
        mock_env.side_effect = lambda key, default=None: {
            "REDIS_URL": "redis://localhost:6379/0",
        }.get(key, default)

        extension = EXT_AI_Tasks()

        assert extension.redis_url == "redis://localhost:6379/0"

    def test_task_constants_structure(self, extension):
        """Test that task constants are properly defined."""
        statuses = extension.get_task_statuses()
        assert isinstance(statuses, dict)
        assert "pending" in statuses
        assert "running" in statuses
        assert "completed" in statuses
        assert "failed" in statuses

        types = extension.get_task_types()
        assert isinstance(types, dict)
        assert "one_time" in types
        assert "recurring" in types
        assert "workflow" in types

        schedule_types = extension.get_schedule_types()
        assert isinstance(schedule_types, dict)
        assert "cron" in schedule_types
        assert "interval" in schedule_types
        assert "date" in schedule_types

    @patch("zephyrex.extensions.ai_tasks.EXT_AI_Tasks.logger")
    def test_on_initialize_success(self, mock_logger, extension):
        """Test successful extension initialization."""
        with patch.object(extension, "_register_task_hooks") as mock_hooks:
            result = extension.on_initialize()

            assert result is True
            mock_hooks.assert_called_once()
            mock_logger.debug.assert_called()

    @patch("zephyrex.extensions.ai_tasks.EXT_AI_Tasks.logger")
    def test_on_initialize_failure(self, mock_logger, extension):
        """Test extension initialization failure handling."""
        with patch.object(
            extension, "_register_task_hooks", side_effect=Exception("Test error")
        ):
            result = extension.on_initialize()

            assert result is False
            mock_logger.error.assert_called()

    def test_initialize_celery_fallback_no_library(self, extension):
        """Celery initialization falls back cleanly when the library is missing."""
        with patch.dict("sys.modules", {"celery": None}):
            extension._initialize_celery()

        assert extension.celery_app is None

    def test_capability_management(self, extension):
        """Test capability management methods."""
        extension.register_capability("test_capability")
        assert "test_capability" in extension.capabilities

        capabilities = extension.get_registered_capabilities()
        assert isinstance(capabilities, set)
        assert "test_capability" in capabilities

        capabilities = extension.get_capabilities()
        assert isinstance(capabilities, set)

        assert extension.has_capability("test_capability") is True
        assert extension.has_capability("nonexistent_capability") is False

    @pytest.mark.asyncio
    async def test_create_task_success(self, extension):
        """Test successful task creation."""
        result = await extension.create_task(
            name="test_task",
            task_type="one_time",
            instructions="Test task instructions",
            metadata={"priority": "high"},
        )

        assert result["success"] is True
        assert "task" in result
        assert result["task"]["name"] == "test_task"
        assert result["task"]["task_type"] == "one_time"
        assert result["task"]["instructions"] == "Test task instructions"

    @pytest.mark.asyncio
    async def test_create_task_with_dependencies(self, extension):
        """Test task creation with dependencies."""
        result = await extension.create_task(
            name="dependent_task",
            task_type="workflow",
            dependencies=["task1", "task2"],
            ai_agent_id="agent123",
        )

        assert result["success"] is True
        assert result["task"]["dependencies"] == ["task1", "task2"]
        assert result["task"]["ai_agent_id"] == "agent123"

    @pytest.mark.asyncio
    async def test_create_task_invalid_data(self, extension):
        """Test task creation with invalid data."""
        with patch.object(extension, "_validate_task_data", return_value=False):
            result = await extension.create_task(name="", task_type="invalid_type")

            assert result["success"] is False
            assert "Invalid task data" in result["message"]

    @pytest.mark.asyncio
    async def test_create_task_monitoring(self, extension):
        """Test that successful creation increments monitoring counters."""
        extension.enable_task_monitoring = True
        extension._initialize_task_monitoring()

        await extension.create_task(name="task_a", task_type="one_time")

        assert extension.monitoring_data["tasks_created"] == 1

    @pytest.mark.asyncio
    async def test_schedule_task_cron_success(self, extension, mock_croniter_available):
        """Test successful task scheduling with cron expression."""
        result = await extension.schedule_task(
            task_id="test-task-id",
            schedule_type="cron",
            schedule_config={"cron_expression": "0 9 * * *"},
        )

        assert result["success"] is True
        assert "scheduled successfully" in result["message"]
        assert "task_test-task-id" in extension.scheduled_tasks

    @pytest.mark.asyncio
    async def test_schedule_task_interval_success(self, extension):
        """Test successful task scheduling with interval."""
        result = await extension.schedule_task(
            task_id="test-task-id",
            schedule_type="interval",
            schedule_config={"interval_seconds": 3600},
        )

        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_schedule_task_date_success(self, extension):
        """Test successful task scheduling with a specific date."""
        result = await extension.schedule_task(
            task_id="test-task-id",
            schedule_type="date",
            schedule_config={"execute_at": "2024-12-31 10:00:00"},
        )

        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_schedule_task_invalid_cron(self, extension):
        """Test task scheduling with invalid cron expression."""
        mock_croniter = MagicMock()
        mock_croniter.croniter.is_valid.return_value = False

        with patch.dict("sys.modules", {"croniter": mock_croniter}):
            result = await extension.schedule_task(
                task_id="test-task-id",
                schedule_type="cron",
                schedule_config={"cron_expression": "invalid cron"},
            )

        assert result["success"] is False
        assert "Invalid cron expression" in result["message"]

    @pytest.mark.asyncio
    async def test_schedule_task_missing_config(self, extension):
        """Test task scheduling with missing configuration."""
        result = await extension.schedule_task(
            task_id="test-task-id",
            schedule_type="cron",
            schedule_config={},
        )

        assert result["success"] is False
        assert "required" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_execute_task_success(self, extension):
        """Test successful task execution."""
        result = await extension.execute_task(
            task_id="test-task-id",
            context={"test": "context"},
        )

        assert result["success"] is True
        assert result["task_id"] == "test-task-id"
        assert "executed successfully" in result["result"]

    @pytest.mark.asyncio
    async def test_execute_task_already_running(self, extension):
        """Test task execution when task is already running."""
        extension.running_tasks["test-task-id"] = {
            "status": "running",
            "start_time": "2024-01-01T10:00:00",
        }

        result = await extension.execute_task(task_id="test-task-id")

        assert result["success"] is False
        assert "already running" in result["message"]

    @pytest.mark.asyncio
    async def test_execute_task_with_monitoring(self, extension):
        """Test task execution with monitoring enabled."""
        extension.enable_task_monitoring = True
        extension._initialize_task_monitoring()

        initial_count = extension.monitoring_data["tasks_executed"]

        result = await extension.execute_task(task_id="test-task-id")

        assert result["success"] is True
        assert extension.monitoring_data["tasks_executed"] == initial_count + 1
        assert extension.monitoring_data["tasks_completed"] == 1

    @pytest.mark.asyncio
    async def test_cancel_task_success(self, extension):
        """Test successful task cancellation."""
        extension.running_tasks["test-task-id"] = {"status": "running"}

        result = await extension.cancel_task(task_id="test-task-id")

        assert result["success"] is True
        assert "cancelled successfully" in result["message"]
        assert extension.running_tasks["test-task-id"]["status"] == "cancelled"

    @pytest.mark.asyncio
    async def test_cancel_scheduled_task(self, extension):
        """Test cancelling a scheduled (not yet running) task."""
        extension.scheduled_tasks["task_test-task-id"] = {
            "task_id": "test-task-id",
            "status": "scheduled",
        }

        result = await extension.cancel_task(task_id="test-task-id")

        assert result["success"] is True
        assert extension.scheduled_tasks["task_test-task-id"]["status"] == "cancelled"

    @pytest.mark.asyncio
    async def test_cancel_task_with_monitoring(self, extension):
        """Test task cancellation with monitoring enabled."""
        extension.enable_task_monitoring = True
        extension._initialize_task_monitoring()

        initial_count = extension.monitoring_data["tasks_cancelled"]

        result = await extension.cancel_task(task_id="test-task-id")

        assert result["success"] is True
        assert extension.monitoring_data["tasks_cancelled"] == initial_count + 1

    @pytest.mark.asyncio
    async def test_get_task_status_running(self, extension):
        """Test getting status of a running task."""
        extension.running_tasks["test-task-id"] = {
            "status": "running",
            "start_time": "2024-01-01T10:00:00",
            "context": {"test": "data"},
        }

        result = await extension.get_task_status(task_id="test-task-id")

        assert result["success"] is True
        assert result["task"]["task_id"] == "test-task-id"
        assert result["task"]["status"] == "running"

    @pytest.mark.asyncio
    async def test_get_task_status_scheduled(self, extension):
        """Test getting status of a scheduled task."""
        await extension.schedule_task(
            task_id="test-task-id",
            schedule_type="date",
            schedule_config={"execute_at": "2024-12-31 10:00:00"},
        )

        result = await extension.get_task_status(task_id="test-task-id")

        assert result["success"] is True
        assert result["task"]["status"] == "scheduled"

    @pytest.mark.asyncio
    async def test_get_task_status_not_found(self, extension):
        """Test getting status of non-existent task."""
        result = await extension.get_task_status(task_id="non-existent-task")

        assert result["success"] is False
        assert "not found" in result["message"]

    @pytest.mark.asyncio
    async def test_list_tasks_success(self, extension):
        """Test successful task listing across running and scheduled tasks."""
        extension.running_tasks["task1"] = {"status": "running"}
        await extension.schedule_task(
            task_id="task2",
            schedule_type="date",
            schedule_config={"execute_at": "2024-12-31 10:00:00"},
        )

        result = await extension.list_tasks(limit=10)

        assert result["success"] is True
        assert len(result["tasks"]) == 2
        assert result["total"] == 2

    @pytest.mark.asyncio
    async def test_list_tasks_with_filter(self, extension):
        """Test task listing with status filter."""
        extension.running_tasks["task1"] = {"status": "running"}
        extension.running_tasks["task2"] = {"status": "cancelled"}

        result = await extension.list_tasks(status_filter="running")

        assert result["success"] is True
        assert len(result["tasks"]) == 1
        assert result["tasks"][0]["status"] == "running"

    @pytest.mark.asyncio
    async def test_list_tasks_respects_limit(self, extension):
        """Test task listing respects the limit parameter."""
        for i in range(5):
            extension.running_tasks[f"task{i}"] = {"status": "running"}

        result = await extension.list_tasks(limit=2)

        assert result["success"] is True
        assert len(result["tasks"]) == 2

    @pytest.mark.asyncio
    async def test_create_workflow_success(self, extension):
        """Test successful workflow creation."""
        tasks = [
            {"name": "task1", "task_type": "one_time"},
            {"name": "task2", "task_type": "recurring"},
        ]

        result = await extension.create_workflow(
            name="test_workflow",
            tasks=tasks,
            workflow_type="sequential",
        )

        assert result["success"] is True
        assert result["workflow"]["name"] == "test_workflow"
        assert result["workflow"]["workflow_type"] == "sequential"
        assert len(result["workflow"]["tasks"]) == 2

    @pytest.mark.asyncio
    async def test_create_workflow_disabled(self, extension):
        """Test workflow creation when workflows are disabled."""
        extension.enable_workflows = False

        result = await extension.create_workflow(
            name="test_workflow",
            tasks=[{"name": "task1", "task_type": "one_time"}],
        )

        assert result["success"] is False
        assert "not enabled" in result["message"]

    @pytest.mark.asyncio
    async def test_create_workflow_invalid_task(self, extension):
        """Test workflow creation with invalid task."""
        tasks = [
            {"name": "task1", "task_type": "one_time"},
            {"invalid": "task"},
        ]

        result = await extension.create_workflow(name="test_workflow", tasks=tasks)

        assert result["success"] is False
        assert "Invalid task in workflow" in result["message"]

    def test_validate_task_data_dict_valid(self, extension):
        """Test task data validation with valid dictionary."""
        task_data = {"name": "test_task", "task_type": "one_time"}
        assert extension._validate_task_data(task_data) is True

    def test_validate_task_data_dict_invalid(self, extension):
        """Test task data validation with invalid dictionary."""
        task_data = {"name": "test_task"}
        assert extension._validate_task_data(task_data) is False

    def test_validate_task_data_object_valid(self, extension):
        """Test task data validation with valid object."""
        task_data = MagicMock()
        task_data.name = "test_task"
        task_data.task_type = "one_time"

        assert extension._validate_task_data(task_data) is True

    def test_validate_task_data_object_invalid(self, extension):
        """Test task data validation with invalid object."""
        task_data = MagicMock(spec=["name"])
        task_data.name = "test_task"

        assert extension._validate_task_data(task_data) is False

    def test_parse_cron_expression_valid(self, extension):
        """Test cron expression parsing with a valid expression."""
        result = extension._parse_cron_expression("0 9 * * *")
        expected = {
            "minute": "0",
            "hour": "9",
            "day": None,
            "month": None,
            "day_of_week": None,
        }
        assert result == expected

    def test_parse_cron_expression_invalid(self, extension):
        """Test cron expression parsing with an invalid expression."""
        result = extension._parse_cron_expression("invalid")
        assert result == {}

    def test_validate_config_all_libraries_available(self, extension):
        """Test configuration validation when all libraries are available."""
        mock_libs = {
            "celery": MagicMock(),
            "croniter": MagicMock(),
            "pydantic": MagicMock(),
        }

        with patch.dict("sys.modules", mock_libs):
            extension.enable_celery = True
            extension.redis_url = "redis://localhost:6379/0"
            issues = extension.validate_config()

            assert issues == []

    def test_validate_config_missing_celery(self, extension):
        """Test configuration validation with missing Celery."""
        with patch.dict("sys.modules", {"celery": None}):
            extension.enable_celery = True
            issues = extension.validate_config()

            assert len(issues) >= 1
            assert any("celery" in issue.lower() for issue in issues)

    def test_validate_config_missing_redis_url(self, extension):
        """Test configuration validation with missing Redis URL."""
        extension.enable_celery = True
        extension.redis_url = ""

        issues = extension.validate_config()

        assert any("redis" in issue.lower() for issue in issues)

    def test_get_required_permissions(self, extension):
        """Test getting required permissions."""
        permissions = extension.get_required_permissions()

        assert isinstance(permissions, list)
        assert len(permissions) == 8
        assert "tasks:create" in permissions
        assert "tasks:execute" in permissions
        assert "tasks:schedule" in permissions
        assert "workflows:create" in permissions

    def test_lifecycle_methods(self, extension):
        """Test extension lifecycle methods."""
        assert extension.on_start() is True

        extension.running_tasks["task1"] = {"status": "running"}
        extension.scheduled_tasks["task_task1"] = {"status": "scheduled"}

        assert extension.on_stop() is True
        assert len(extension.running_tasks) == 0
        assert len(extension.scheduled_tasks) == 0

        extension.on_startup()
        extension.on_shutdown()

    def test_has_capability(self, extension):
        """Test capability checking."""
        assert extension.has_capability("task_management") is True
        assert extension.has_capability("task_scheduling") is True
        assert extension.has_capability("non_existent_capability") is False

    def test_get_monitoring_data_enabled(self, extension):
        """Test getting monitoring data when enabled."""
        extension.enable_task_monitoring = True
        extension._initialize_task_monitoring()

        monitoring = extension.get_monitoring_data()

        assert isinstance(monitoring, dict)
        assert "tasks_created" in monitoring
        assert "tasks_executed" in monitoring

    def test_get_monitoring_data_disabled(self, extension):
        """Test getting monitoring data when disabled."""
        extension.enable_task_monitoring = False

        monitoring = extension.get_monitoring_data()

        assert "message" in monitoring
        assert "not enabled" in monitoring["message"]

    def test_get_running_tasks(self, extension):
        """Test getting running tasks."""
        extension.running_tasks["task1"] = {"status": "running"}
        extension.running_tasks["task2"] = {"status": "cancelled"}

        running = extension.get_running_tasks()

        assert isinstance(running, dict)
        assert len(running) == 2
        assert "task1" in running
        assert "task2" in running

    @pytest.mark.asyncio
    async def test_internal_execute_task_success(self, extension):
        """Test internal task execution method."""
        result = await extension._execute_task("test-task-id", {"test": "context"})

        assert result["success"] is True
        assert result["task_id"] == "test-task-id"
        assert "executed successfully" in result["result"]
        assert result["context"] == {"test": "context"}

    @pytest.mark.asyncio
    async def test_cleanup_old_executions(self, extension):
        """Test cleanup of old task executions does not raise."""
        await extension._cleanup_old_executions()

    def test_task_monitoring_integration(self, extension):
        """Test task monitoring integration."""
        extension.enable_task_monitoring = True
        extension._initialize_task_monitoring()

        extension._track_task_execution("test_result")

        monitoring = extension.get_monitoring_data()
        assert isinstance(monitoring, dict)

    @pytest.mark.asyncio
    async def test_error_handling_create_task(self, extension):
        """Test error handling in the create_task ability."""
        with patch.object(
            extension, "_validate_task_data", side_effect=Exception("Test error")
        ):
            result = await extension.create_task(name="test", task_type="one_time")

            assert result["success"] is False
            assert "Failed to create task" in result["message"]


if __name__ == "__main__":
    pytest.main([__file__])
