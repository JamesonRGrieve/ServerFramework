from __future__ import annotations

import asyncio
import random
import time
import traceback
import uuid
from abc import ABC, abstractmethod
from collections import deque
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, ClassVar, Deque, Dict, List, Literal, Optional, Tuple, TypeVar

from sqlalchemy.orm import Session

from serverframework.lib.Logging import logger

T = TypeVar("T", bound="AbstractService")


# Default drain period (Item 28 / Item 44 alignment): on stop the service has
# this many seconds to finish in-flight work before being forcibly cancelled.
DEFAULT_DRAIN_PERIOD_SECONDS: float = 30.0


# Restart policy literal -- controls how a supervisor handles service exits.
# - "always": restart on any exit
# - "on_failure": restart only on unhandled exception
# - "never": one-shot, do not restart
RestartPolicy = Literal["always", "on_failure", "never"]


class HealthStatus:
    """A simple structured health-status value for AbstractService.health().

    Three states map naturally to the rotation system's HealthStatus from
    Item 27 -- this is intentionally compatible at the value-level so a
    service consumer can use the same OK/DEGRADED/DOWN vocabulary.
    """

    OK = "OK"
    DEGRADED = "DEGRADED"
    DOWN = "DOWN"
    FAILED = "FAILED"

    def __init__(
        self,
        status: str,
        detail: str = "",
        timestamp: Optional[datetime] = None,
    ) -> None:
        self.status = status
        self.detail = detail
        self.timestamp = timestamp or datetime.now(timezone.utc)

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"HealthStatus({self.status}, {self.detail!r})"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "detail": self.detail,
            "timestamp": self.timestamp.isoformat(),
        }


class AbstractService(ABC):
    """
    Abstract base class for all service components.

    Services are background tasks that run periodically, typically used for
    scheduled operations, monitoring, or other ongoing tasks that need to
    execute in the background.

    Features:
    - Configurable run interval
    - Automatic error handling and retry logic
    - Resource cleanup
    - Lifecycle management (start, stop, pause)
    """

    def __init__(
        self,
        requester_id: str,
        db: Optional[Session] = None,
        interval_seconds: int = 60,
        max_failures: int = 3,
        retry_delay_seconds: int = 5,
        service_id: Optional[str] = None,
        **kwargs,
    ):
        """
        Initialize the service.

        Args:
            requester_id: ID of the user requesting the service
            db: Optional database session
            interval_seconds: Time between service update executions in seconds
            max_failures: Maximum number of consecutive failures before service stops
            retry_delay_seconds: Time to wait after a failure before retrying
            service_id: Optional unique identifier for the service instance. Auto-generated if None.
            **kwargs: Additional service-specific parameters
        """
        self.requester_id = requester_id
        self._db = db
        self._close_db_on_exit = db is None
        self.interval_seconds = interval_seconds
        self.max_failures = max_failures
        self.retry_delay_seconds = retry_delay_seconds
        self.failures = 0
        self.running = False
        self.paused = False
        self._last_run_time = 0
        self.service_id = service_id or str(uuid.uuid4())
        self.db_manager = kwargs.get("db_manager")
        # Item 28: drain period and supervisor restart policy.
        self.drain_period_seconds: float = float(
            kwargs.get("drain_period_seconds", DEFAULT_DRAIN_PERIOD_SECONDS)
        )
        self.restart_policy: RestartPolicy = kwargs.get(
            "restart_policy", "on_failure"
        )
        # ``failed`` is the terminal supervisor state per Item 28's acceptance
        # criteria; entered when the service exhausts ``max_failures`` and
        # cleared only by an explicit reset (admin action / tests).
        self.failed: bool = False
        self._failed_reason: Optional[str] = None
        self._configure_service(**kwargs)

    def _configure_service(self, **kwargs) -> None:
        """
        Configure service-specific settings.
        Override this method in subclasses to handle initialization
        of service-specific settings.
        """
        pass

    @property
    def db(self) -> Session:
        """Property that returns an active database session, creating a new one if needed."""
        if self._db is None or not self._db.is_active:
            self._db = self.db_manager.get_session()
            self._close_db_on_exit = True
        return self._db

    def start(self) -> None:
        """Start the service running in the background."""
        if self.running:
            logger.debug(
                f"{self.__class__.__name__} ({self.service_id}) is already running"
            )
            return

        self.running = True
        self.paused = False
        logger.debug(f"Started {self.__class__.__name__} ({self.service_id})")

    def stop(self) -> None:
        """Stop the service from running."""
        self.running = False
        logger.debug(f"Stopped {self.__class__.__name__} ({self.service_id})")

    def pause(self) -> None:
        """Pause the service temporarily."""
        if not self.running:
            logger.warning(
                f"{self.__class__.__name__} ({self.service_id}) is not running, cannot pause"
            )
            return

        self.paused = True
        logger.debug(f"Paused {self.__class__.__name__} ({self.service_id})")

    def resume(self) -> None:
        """Resume a paused service."""
        if not self.running:
            logger.warning(
                f"{self.__class__.__name__} ({self.service_id}) is not running, cannot resume"
            )
            return

        if not self.paused:
            logger.warning(
                f"{self.__class__.__name__} ({self.service_id}) is not paused, cannot resume"
            )
            return

        self.paused = False
        logger.debug(f"Resumed {self.__class__.__name__} ({self.service_id})")

    def _handle_failure(self, error: Exception) -> bool:
        """
        Handle a failure and determine if retrying is appropriate.

        Args:
            error: Exception that occurred

        Returns:
            True if the operation should be retried, False otherwise

        Raises:
            Exception: If max failures reached
        """
        self.failures += 1
        logger.error(
            f"{self.__class__.__name__} ({self.service_id}) error: {str(error)}"
        )
        logger.debug(traceback.format_exc())

        if self.failures > self.max_failures:
            self.running = False
            # Item 28: enter the supervisor ``failed`` terminal state. The
            # supervisor will not auto-restart from this state; an operator
            # must call ``reset_failed`` (mirrored by Item 44's admin endpoint).
            self.failed = True
            self._failed_reason = (
                f"max_failures exceeded ({self.failures}/{self.max_failures}): "
                f"{type(error).__name__}: {error}"
            )
            raise Exception(
                f"{self.__class__.__name__} ({self.service_id}) Error: Too many failures. {error}"
            )

        return True

    def reset_failed(self) -> None:
        """Clear the terminal ``failed`` state set by the supervisor.

        Mirrors Item 44's admin action: an operator that has diagnosed and
        fixed the underlying failure cause calls this to allow the supervisor
        to restart the service.
        """
        self.failed = False
        self._failed_reason = None
        self.failures = 0

    def health(self) -> HealthStatus:
        """Return a structured health status for the service.

        Default mapping:
        - ``failed`` -> ``FAILED``
        - ``running and not paused`` -> ``OK``
        - ``running and paused`` -> ``DEGRADED``
        - otherwise -> ``DOWN``

        Subclasses may override to fold in queue depth, last-message age,
        connection state, etc.
        """
        if self.failed:
            return HealthStatus(
                HealthStatus.FAILED,
                self._failed_reason or "service has entered the failed state",
            )
        if not self.running:
            return HealthStatus(HealthStatus.DOWN, "service is not running")
        if self.paused:
            return HealthStatus(HealthStatus.DEGRADED, "service is paused")
        return HealthStatus(HealthStatus.OK, "running")

    def _reset_failures(self) -> None:
        """Reset the failure counter after a successful execution."""
        self.failures = 0

    async def run_service_loop(self) -> None:
        """
        Main service loop that runs update() at the configured interval.
        This method should be called from an event loop.
        """
        while self.running:
            try:
                current_time = time.time()

                # Skip if paused
                if self.paused:
                    await asyncio.sleep(1)  # Short sleep while paused
                    continue

                # Check if it's time to run again
                if current_time - self._last_run_time < self.interval_seconds:
                    # Sleep for a short time to avoid busy-waiting
                    await asyncio.sleep(0.1)
                    continue

                # Update last run time
                self._last_run_time = current_time

                # Run the update method
                await self.update()

                # Reset failure counter after successful execution
                self._reset_failures()

            except Exception as e:
                retry = self._handle_failure(e)
                if retry:
                    # Wait before retrying
                    await asyncio.sleep(self.retry_delay_seconds)
                else:
                    break  # Stop the service if we shouldn't retry

    @abstractmethod
    async def update(self) -> None:
        """
        Main method to perform the service's work.
        This method should be implemented by all service subclasses.
        """
        pass

    def cleanup(self) -> None:
        """
        Perform any necessary cleanup when the service is being shut down.
        Override this method in subclasses to add specific cleanup operations.
        """
        logger.debug(f"Cleaning up {self.__class__.__name__} ({self.service_id})")
        self.stop()

    def __del__(self):
        """Clean up resources when the service is destroyed"""
        self.cleanup()
        if hasattr(self, "_db") and self._db is not None and self._close_db_on_exit:
            self._db.close()
            self._db = None


class ServiceRegistry:
    """
    Registry for managing service instances.

    This provides a central place to register, retrieve, and manage
    the lifecycle of service instances.
    """

    _services: Dict[str, AbstractService] = {}

    @classmethod
    def register(cls, service_id: str, service: AbstractService) -> None:
        """
        Register a service instance.

        Args:
            service_id: Unique identifier for the service
            service: Service instance to register
        """
        if service_id in cls._services:
            logger.warning(
                f"Service with ID {service_id} already registered. Overwriting."
            )
        cls._services[service_id] = service
        logger.debug(f"Service {service_id} registered ({service.__class__.__name__})")

    @classmethod
    def unregister(cls, service_id: str) -> None:
        """
        Unregister a service instance.

        Args:
            service_id: Unique identifier for the service to unregister
        """
        if service_id in cls._services:
            service = cls._services.pop(service_id)
            service.cleanup()
            logger.debug(
                f"Service {service_id} unregistered ({service.__class__.__name__})"
            )

    @classmethod
    def get(cls, service_id: str) -> Optional[AbstractService]:
        """
        Get a registered service instance.

        Args:
            service_id: Unique identifier for the service

        Returns:
            The service instance or None if not found
        """
        return cls._services.get(service_id)

    @classmethod
    def list(cls) -> List[str]:
        """
        Get a list of all registered service IDs.

        Returns:
            List of registered service IDs
        """
        return list(cls._services.keys())

    @classmethod
    def start_all(cls) -> None:
        """Start all registered services."""
        for service_id, service in cls._services.items():
            try:
                service.start()
                # Logging now happens within service.start()
            except Exception as e:
                logger.error(
                    f"Failed to start service {service_id} ({service.__class__.__name__}): {e}"
                )

    @classmethod
    def stop_all(cls) -> None:
        """Stop all registered services."""
        for service_id, service in cls._services.items():
            try:
                service.stop()
                # Logging now happens within service.stop()
            except Exception as e:
                logger.error(
                    f"Failed to stop service {service_id} ({service.__class__.__name__}): {e}"
                )

    @classmethod
    def cleanup_all(cls) -> None:
        """Clean up all registered services."""
        for service_id in list(cls._services.keys()):
            try:
                cls.unregister(service_id)
                # Logging happens within unregister/cleanup
            except Exception as e:
                logger.error(f"Failed to cleanup/unregister service {service_id}: {e}")
        logger.debug("All services cleaned up")


# ---------------------------------------------------------------------------
# Item 28 - Broadened service interface
# ---------------------------------------------------------------------------


class PerpetualService(AbstractService):
    """Alias for the existing :class:`AbstractService` perpetual-loop semantics.

    Default flavor; provided so callers can be explicit at declaration time:

        class MyAgentLoop(PerpetualService):
            ...
    """

    pass


# ----- Scheduled --------------------------------------------------------------


class ScheduledService(AbstractService):
    """Cron-or-interval scheduled execution.

    Accepts either ``cron_expression`` or the existing ``interval_seconds``.
    Persists ``last_run_at`` via an injectable ``state_store`` callable
    (``state_store(service_id, value=None) -> Optional[datetime]``) so cadence
    survives restarts.

    The cron evaluator is intentionally minimal: if a real cron lib is wired
    via ``cron_evaluator(cron_expression, last_run_at) -> datetime``, that is
    used; otherwise the service falls back to ``interval_seconds`` semantics.
    """

    def __init__(
        self,
        *args: Any,
        cron_expression: Optional[str] = None,
        state_store: Optional[Callable[..., Any]] = None,
        cron_evaluator: Optional[Callable[[str, Optional[datetime]], datetime]] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.cron_expression = cron_expression
        self._state_store = state_store
        self._cron_evaluator = cron_evaluator
        self.last_run_at: Optional[datetime] = None
        if self._state_store is not None:
            try:
                self.last_run_at = self._state_store(self.service_id)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    f"ScheduledService {self.service_id}: state_store load failed: {e}"
                )

    def _persist_last_run(self) -> None:
        if self._state_store is None:
            return
        try:
            self._state_store(self.service_id, value=self.last_run_at)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"ScheduledService {self.service_id}: state_store save failed: {e}"
            )

    def _next_due(self, now: datetime) -> datetime:
        if self.cron_expression and self._cron_evaluator is not None:
            return self._cron_evaluator(self.cron_expression, self.last_run_at)
        # Fallback to interval semantics.
        base = self.last_run_at or now
        return base.fromtimestamp(
            base.timestamp() + self.interval_seconds, tz=timezone.utc
        )

    async def run_service_loop(self) -> None:  # type: ignore[override]
        while self.running:
            try:
                if self.paused:
                    await asyncio.sleep(1)
                    continue
                now = datetime.now(timezone.utc)
                due = self._next_due(now)
                if now < due:
                    await asyncio.sleep(min(1.0, max(0.05, (due - now).total_seconds())))
                    continue
                await self.update()
                self.last_run_at = now
                self._persist_last_run()
                self._reset_failures()
                # Always yield to the event loop so external stop() calls
                # are observable even when ``interval_seconds == 0``.
                await asyncio.sleep(0)
            except Exception as e:  # noqa: BLE001
                retry = self._handle_failure(e)
                if retry:
                    await asyncio.sleep(self.retry_delay_seconds)
                else:
                    break


# ----- QueueConsumer ----------------------------------------------------------


class JobPriority(str, Enum):
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


class QueueSource(ABC):
    """Abstract pull-model queue source."""

    @abstractmethod
    async def pull_one(self) -> Optional[dict]:
        ...

    @abstractmethod
    async def ack(self, item: dict) -> None:
        ...

    @abstractmethod
    async def nack(self, item: dict) -> None:
        ...

    @abstractmethod
    async def move_to_dlq(self, item: dict, reason: str) -> None:
        ...

    def tenant_key(self, item: Any) -> str:
        return "_default"

    def priority(self, item: Any) -> "JobPriority":
        return JobPriority.NORMAL


class InMemoryQueueSource(QueueSource):
    """A FIFO in-memory queue, intended for tests and dev."""

    def __init__(self) -> None:
        self._items: Deque[dict] = deque()
        self.dlq: List[dict] = []
        self.acked: List[dict] = []
        self.nacked: List[dict] = []

    def push(self, item: dict) -> None:
        self._items.append(item)

    async def pull_one(self) -> Optional[dict]:
        if not self._items:
            return None
        return self._items.popleft()

    async def ack(self, item: dict) -> None:
        self.acked.append(item)

    async def nack(self, item: dict) -> None:
        self.nacked.append(item)
        # Re-enqueue at the back for redelivery.
        self._items.append(item)

    async def move_to_dlq(self, item: dict, reason: str) -> None:
        self.dlq.append({"item": item, "reason": reason})


class QueueConsumerService(AbstractService):
    """Pulls from a :class:`QueueSource` with backoff and DLQ handling.

    Override :meth:`process` to do per-item work. Failures are retried up to
    ``max_retries`` times (tracked per-item via ``_attempts``); on exhaustion
    the item is moved to DLQ.
    """

    def __init__(
        self,
        *args: Any,
        queue_source: Optional[QueueSource] = None,
        max_retries: int = 5,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.queue_source = queue_source
        self.max_retries = max_retries
        self._attempts: Dict[str, int] = {}

    async def pull_one(self) -> Optional[dict]:
        if self.queue_source is None:
            return None
        return await self.queue_source.pull_one()

    async def process(self, item: dict) -> None:  # pragma: no cover - override me
        raise NotImplementedError

    def _item_key(self, item: dict) -> str:
        # Prefer an explicit id; fall back to a stable repr.
        return str(item.get("id") or id(item))

    async def update(self) -> None:
        item = await self.pull_one()
        if item is None:
            return
        key = self._item_key(item)
        try:
            await self.process(item)
            if self.queue_source is not None:
                await self.queue_source.ack(item)
            self._attempts.pop(key, None)
        except Exception as e:  # noqa: BLE001
            attempts = self._attempts.get(key, 0) + 1
            self._attempts[key] = attempts
            if attempts > self.max_retries:
                logger.error(
                    f"QueueConsumerService {self.service_id}: item {key} exhausted"
                    f" retries ({attempts}); moving to DLQ. error={e}"
                )
                if self.queue_source is not None:
                    await self.queue_source.move_to_dlq(item, str(e))
                self._attempts.pop(key, None)
            else:
                logger.warning(
                    f"QueueConsumerService {self.service_id}: item {key} failed"
                    f" attempt {attempts}/{self.max_retries}; nacking. error={e}"
                )
                if self.queue_source is not None:
                    await self.queue_source.nack(item)


# ----- FairQueueConsumer ------------------------------------------------------


class FairQueueConsumerService(QueueConsumerService):
    """QueueConsumer with strict-priority lanes and per-tenant fair round-robin.

    On each ``update()``: drains up to ``batch_size`` items from the source and
    partitions them into ``_partitions[lane][tenant]`` deques; then dispatches
    exactly one item using priority lane order (HIGH, NORMAL, LOW) with a
    per-lane round-robin cursor across tenants. Dispatch invokes
    ``self.handler(item)`` (defaults to ``self.process(item)``).
    """

    _drain_counter: ClassVar[Dict[Tuple[str, str], int]] = {}
    _wait_summary: ClassVar[Dict[Tuple[str, str], Dict[str, float]]] = {}

    _LANE_ORDER: ClassVar[Tuple[JobPriority, ...]] = (
        JobPriority.HIGH,
        JobPriority.NORMAL,
        JobPriority.LOW,
    )

    def __init__(
        self,
        *args: Any,
        batch_size: int = 16,
        handler: Optional[Callable[[Any], Any]] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.batch_size = batch_size
        self._handler = handler
        self._partitions: Dict[JobPriority, Dict[str, Deque[Tuple[Any, float]]]] = {
            lane: {} for lane in self._LANE_ORDER
        }
        self._lane_cursors: Dict[JobPriority, int] = {lane: 0 for lane in self._LANE_ORDER}

    async def handler(self, item: Any) -> None:
        if self._handler is not None:
            result = self._handler(item)
            if asyncio.iscoroutine(result):
                await result
            return
        await self.process(item)

    async def _drain_into_partitions(self) -> None:
        if self.queue_source is None:
            return
        for _ in range(self.batch_size):
            item = await self.queue_source.pull_one()
            if item is None:
                break
            lane = self.queue_source.priority(item)
            tenant = self.queue_source.tenant_key(item)
            self._partitions[lane].setdefault(tenant, deque()).append((item, time.time()))

    def _select_next(self) -> Optional[Tuple[JobPriority, str, Any, float]]:
        for lane in self._LANE_ORDER:
            tenants_map = self._partitions[lane]
            tenants = [t for t, q in tenants_map.items() if q]
            if not tenants:
                continue
            tenants.sort()
            cursor = self._lane_cursors[lane] % len(tenants)
            tenant = tenants[cursor]
            self._lane_cursors[lane] = (cursor + 1) % len(tenants)
            item, enqueued_at = tenants_map[tenant].popleft()
            return lane, tenant, item, enqueued_at
        return None

    @classmethod
    def get_drain_counter(cls) -> Dict[Tuple[str, str], int]:
        return dict(cls._drain_counter)

    @classmethod
    def get_wait_summary(cls) -> Dict[Tuple[str, str], Dict[str, float]]:
        return {k: dict(v) for k, v in cls._wait_summary.items()}

    @classmethod
    def reset_metrics(cls) -> None:
        cls._drain_counter = {}
        cls._wait_summary = {}

    def _record_metrics(self, lane: JobPriority, tenant: str, wait: float) -> None:
        key = (tenant, lane.value)
        cls = type(self)
        cls._drain_counter[key] = cls._drain_counter.get(key, 0) + 1
        summary = cls._wait_summary.get(key)
        if summary is None:
            cls._wait_summary[key] = {
                "min": wait,
                "max": wait,
                "count": 1,
                "sum": wait,
            }
        else:
            summary["min"] = min(summary["min"], wait)
            summary["max"] = max(summary["max"], wait)
            summary["count"] = summary["count"] + 1
            summary["sum"] = summary["sum"] + wait

    async def update(self) -> None:  # type: ignore[override]
        await self._drain_into_partitions()
        selected = self._select_next()
        if selected is None:
            return
        lane, tenant, item, enqueued_at = selected
        wait = max(0.0, time.time() - enqueued_at)
        key = self._item_key(item) if isinstance(item, dict) else str(id(item))
        try:
            await self.handler(item)
            if self.queue_source is not None and isinstance(item, dict):
                await self.queue_source.ack(item)
            self._attempts.pop(key, None)
            self._record_metrics(lane, tenant, wait)
        except Exception as e:  # noqa: BLE001
            attempts = self._attempts.get(key, 0) + 1
            self._attempts[key] = attempts
            if attempts > self.max_retries:
                logger.error(
                    f"FairQueueConsumerService {self.service_id}: item {key} exhausted"
                    f" retries ({attempts}); moving to DLQ. error={e}"
                )
                if self.queue_source is not None and isinstance(item, dict):
                    await self.queue_source.move_to_dlq(item, str(e))
                self._attempts.pop(key, None)
            else:
                logger.warning(
                    f"FairQueueConsumerService {self.service_id}: item {key} failed"
                    f" attempt {attempts}/{self.max_retries}; nacking. error={e}"
                )
                if self.queue_source is not None and isinstance(item, dict):
                    await self.queue_source.nack(item)


# ----- Streaming --------------------------------------------------------------


class StreamingService(AbstractService):
    """Long-lived connection-oriented work.

    Lifecycle: ``connect() -> on_message(message) -> disconnect()``. Reconnection
    backoff is exponential with jitter, capped at ``reconnect_max_seconds``.
    """

    reconnect_initial_seconds: float = 1.0
    reconnect_max_seconds: float = 60.0

    async def connect(self) -> Any:  # pragma: no cover - override me
        raise NotImplementedError

    async def on_message(self, message: Any) -> None:  # pragma: no cover - override me
        raise NotImplementedError

    async def disconnect(self) -> None:
        return None

    async def iter_messages(self, connection: Any):  # pragma: no cover - override me
        """Async iterator of messages from the connection. Override per protocol."""
        if False:
            yield None
        return

    async def update(self) -> None:
        # ``run_service_loop`` is overridden; ``update`` exists only to satisfy
        # the abstract contract from AbstractService.
        return None

    async def run_service_loop(self) -> None:  # type: ignore[override]
        attempt = 0
        while self.running:
            if self.paused:
                await asyncio.sleep(1)
                continue
            try:
                connection = await self.connect()
                attempt = 0
                self._reset_failures()
                try:
                    async for message in self.iter_messages(connection):
                        if not self.running:
                            break
                        if self.paused:
                            await asyncio.sleep(0.1)
                            continue
                        try:
                            await self.on_message(message)
                        except Exception as e:  # noqa: BLE001
                            logger.error(
                                f"StreamingService {self.service_id} on_message error: {e}"
                            )
                finally:
                    try:
                        await self.disconnect()
                    except Exception as e:  # noqa: BLE001
                        logger.warning(
                            f"StreamingService {self.service_id} disconnect error: {e}"
                        )
            except Exception as e:  # noqa: BLE001
                if not self._handle_failure(e):
                    break
                attempt += 1
                backoff = min(
                    self.reconnect_max_seconds,
                    self.reconnect_initial_seconds * (2 ** (attempt - 1)),
                )
                # Jitter: +/- 25%.
                backoff = backoff * (0.75 + random.random() * 0.5)
                await asyncio.sleep(backoff)


class ConsumerStreamingService(StreamingService):
    """Long-lived inbound connections: websocket subscribers, SSE listeners, etc."""

    pass


class ProducerStreamingService(StreamingService):
    """Long-lived outbound streams that we write to."""

    pass
