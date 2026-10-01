"""
Background service layer for AI Tasks.

Provides two ``AbstractService`` implementations:

- ``TaskExecutionService`` executes a single task on demand, invoking its
  associated AI agent.
- ``TaskMonitorService`` polls for due tasks on an interval and hands each
  one to a ``TaskExecutionService``, sharding work across workers by a
  consistent hash of the task ID (so multiple uvicorn workers don't race to
  execute the same task).

Task persistence goes through ``TaskManager`` (see ``BLL_AI_Tasks.py``)
rather than raw SQLAlchemy queries against a standalone ``Task`` ORM class,
consistent with the rest of the framework's BLL-manager pattern.
"""

import asyncio
import logging
import os
import socket
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Dict, List, Optional

from zephyrex.extensions.ai_tasks.BLL_AI_Tasks import TaskManager
from zephyrex.lib.Environment import env
from zephyrex.logic.AbstractService import AbstractService

# Agent execution timeout, in seconds, for a single task prompt.
TASK_EXECUTION_TIMEOUT_SECONDS = 300
# Length at which a task's response is truncated before being reported back.
RESPONSE_TRUNCATION_LENGTH = 500


class TaskExecutionService(AbstractService):
    """
    Service for executing individual tasks.

    Handles the execution of a specific task, invoking the task's associated
    agent (via an injected SDK-style client) to perform the work.
    """

    def __init__(
        self,
        requester_id: str,
        model_registry: Optional[Any] = None,
        api_client: Optional[Any] = None,
        **kwargs,
    ):
        """
        Initialize the task execution service.

        Args:
            requester_id: ID of the user requesting the service.
            model_registry: ModelRegistry used to resolve the TaskManager's DB context.
            api_client: Optional agent SDK client used to prompt the task's agent.
            **kwargs: Additional AbstractService options (db, interval_seconds, ...).
        """
        super().__init__(requester_id=requester_id, **kwargs)
        self.model_registry = model_registry
        self._api_client = api_client

    async def update(self) -> None:
        """Not used: tasks are executed on demand rather than polled."""

    async def execute_task(self, task_id: str) -> Dict[str, str]:
        """
        Execute a specific task.

        Args:
            task_id: ID of the task to execute.

        Returns:
            Dictionary with a human-readable execution result message.
        """
        manager = TaskManager(
            model_registry=self.model_registry, requester_id=self.requester_id
        )

        task = manager.get(id=task_id)
        if task is None:
            return {"message": f"Task with ID {task_id} not found"}

        if task.status == "completed":
            return {"message": "Task already completed"}

        if not task.ai_agent_id:
            return {"message": "Task has no associated agent"}

        if self._api_client is None:
            return {"message": "No API client configured for task execution"}

        try:
            response = await self._prompt_agent(task)

            manager.update(
                id=task_id,
                status="completed",
                completed_at=datetime.now(timezone.utc),
            )

            truncated = (
                response[:RESPONSE_TRUNCATION_LENGTH] + "..."
                if len(response) > RESPONSE_TRUNCATION_LENGTH
                else response
            )
            return {"message": "Task executed successfully", "response": truncated}

        except asyncio.TimeoutError:
            return {
                "message": f"Task execution timed out after {TASK_EXECUTION_TIMEOUT_SECONDS} seconds"
            }
        except Exception as e:
            logging.error(f"Error executing task {task_id}: {e}")
            return {"message": f"Error executing task: {str(e)}"}

    async def _prompt_agent(self, task: Any) -> str:
        """Invoke the configured agent SDK client with the task's instructions."""
        prompt = (
            f"## Task Details\n"
            f"Name: {task.name}\n"
            f"Instructions: {task.instructions}\n\n"
            "The assistant is executing this scheduled task. Please complete "
            "the task based on the instructions provided."
        )

        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor() as pool:
            return await asyncio.wait_for(
                loop.run_in_executor(
                    pool,
                    lambda: self._api_client.prompt_agent(
                        agent_id=task.ai_agent_id,
                        prompt=prompt,
                    ),
                ),
                timeout=TASK_EXECUTION_TIMEOUT_SECONDS,
            )


class TaskMonitorService(AbstractService):
    """
    Service for monitoring and executing scheduled tasks.

    Runs on ``AbstractService``'s interval loop, polling for tasks whose
    status is ``pending`` and executing the subset assigned to this worker.
    """

    def __init__(
        self,
        requester_id: str,
        model_registry: Optional[Any] = None,
        api_client: Optional[Any] = None,
        interval_seconds: int = 60,
        **kwargs,
    ):
        """
        Initialize the task monitor service.

        Args:
            requester_id: ID of the user requesting the service.
            model_registry: ModelRegistry used to resolve the TaskManager's DB context.
            api_client: Optional agent SDK client, forwarded to each TaskExecutionService.
            interval_seconds: Time between checking for pending tasks.
        """
        super().__init__(
            requester_id=requester_id, interval_seconds=interval_seconds, **kwargs
        )
        self.model_registry = model_registry
        self._api_client = api_client
        self.total_workers = int(env("UVICORN_WORKERS", "1"))
        self.worker_id = self._compute_worker_id()

    def _compute_worker_id(self) -> int:
        """Derive a stable worker index from process identity, for task sharding."""
        hostname = socket.gethostname()
        pid = os.getpid()
        unique_str = f"{hostname}:{pid}:{os.getppid()}"
        hash_obj = sha256(unique_str.encode())
        worker_id = int(hash_obj.hexdigest()[-1], 16) % self.total_workers

        logging.info(
            f"Initialized task monitor worker {worker_id} with PID {pid} of "
            f"{self.total_workers} total expected."
        )
        return worker_id

    def _should_process_task(self, task_id: str) -> bool:
        """Determine if this worker owns the given task, via consistent hashing."""
        hash_obj = sha256(task_id.encode())
        task_worker = int(hash_obj.hexdigest()[-1], 16) % self.total_workers
        return task_worker == self.worker_id

    async def get_pending_tasks(self) -> List[Any]:
        """Get pending tasks assigned to this worker."""
        manager = TaskManager(
            model_registry=self.model_registry, requester_id=self.requester_id
        )
        pending_tasks = manager.list(status="pending")
        return [task for task in pending_tasks if self._should_process_task(task.id)]

    async def update(self) -> None:
        """Check for and execute pending tasks. Runs periodically via the service loop."""
        try:
            pending_tasks = await self.get_pending_tasks()

            if not pending_tasks:
                logging.debug("No pending tasks found for this worker")
                return

            logging.info(f"Found {len(pending_tasks)} pending tasks to execute")

            execution_service = TaskExecutionService(
                requester_id=self.requester_id,
                model_registry=self.model_registry,
                api_client=self._api_client,
            )

            for task in pending_tasks:
                try:
                    result = await execution_service.execute_task(task.id)
                    logging.info(
                        f"Task {task.id} execution result: {result['message']}"
                    )
                except Exception as e:
                    logging.error(f"Error executing task {task.id}: {str(e)}")

        except Exception as e:
            logging.error(f"Error in task monitor update: {str(e)}")
            self._handle_failure(e)
