"""Abstract provider template for queue / job scheduler (Item 43).

Reference targets: Celery, RQ, AWS SQS + EventBridge Scheduler,
Google Cloud Tasks, Azure Service Bus, dramatiq, arq.

This module ships the contract only — concrete implementations live in
separate extensions (a follow-up item). Concrete subclasses declare
their typed configuration via the `Settings` inner Pydantic class
(Item 37) and implement each ability marked `@abstractmethod`.
"""

from abc import abstractmethod
from datetime import datetime
from typing import Any, ClassVar, Dict, List, Optional

from extensions.AbstractExtensionProvider import AbstractStaticProvider


class AbstractQueueProvider(AbstractStaticProvider):
    """Abstract queue / job scheduler provider.

    Concrete implementations declare their settings via the typed
    `Settings` inner Pydantic class (Item 37) and override each ability
    method declared as `@abstractmethod`. The framework discovers the
    abilities for the rotation system through the standard
    `AbstractStaticProvider` surface.
    """

    category: ClassVar[str] = "queue"

    @classmethod
    @abstractmethod
    async def enqueue(
        cls,
        provider_instance: Any,
        task_name: str,
        payload: Dict[str, Any],
        delay_seconds: Optional[int] = None,
    ) -> str:
        """Enqueue `task_name` with `payload` for asynchronous execution.

        When `delay_seconds` is given the task becomes eligible to run
        after that many seconds; otherwise the task is eligible
        immediately. Returns the broker-assigned `task_id` so callers
        can subsequently `status()` it.
        """
        ...

    @classmethod
    @abstractmethod
    async def schedule(
        cls,
        provider_instance: Any,
        task_name: str,
        payload: Dict[str, Any],
        run_at: datetime,
    ) -> str:
        """Schedule `task_name` to run at the absolute timestamp `run_at`.

        Concrete providers SHOULD reject naive datetimes and treat
        `run_at` as UTC. Returns the broker-assigned `task_id`.
        """
        ...

    @classmethod
    @abstractmethod
    async def cron(
        cls,
        provider_instance: Any,
        task_name: str,
        payload: Dict[str, Any],
        cron_expression: str,
    ) -> str:
        """Register `task_name` to run on the cron schedule
        `cron_expression` (standard 5-field syntax). Returns the
        broker-assigned `schedule_id`.
        """
        ...

    @classmethod
    @abstractmethod
    async def dlq_list(
        cls, provider_instance: Any, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """List up to `limit` entries from the dead-letter queue —
        tasks whose execution exhausted the broker's retry budget.
        Each entry includes at least `task_id`, `task_name`,
        `last_error`, and `failed_at`.
        """
        ...

    @classmethod
    @abstractmethod
    async def status(
        cls, provider_instance: Any, task_id: str
    ) -> Dict[str, Any]:
        """Return the current status of `task_id` — typically a dict
        with at least a `state` field (`"pending"`, `"running"`,
        `"succeeded"`, `"failed"`, or provider-specific). Implementations
        MAY include `result`, `error`, and timing fields.
        """
        ...
