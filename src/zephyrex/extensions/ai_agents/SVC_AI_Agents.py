"""Background service that drives autonomous agent turns.

``InvocationMonitorService`` polls the standing :class:`InvocationTriggerModel`
listeners on an interval and fires the ones that are due — creating an
:class:`InvocationInstanceModel` (a turn) for each and running it through the
:class:`AgentTurnExecutor`. This is what makes an agent "wake up every 5
minutes": a ``timer`` trigger with ``interval_seconds=300``.

Only ``schedule`` and ``timer`` triggers are polled here. ``event`` triggers
(email / conversation message / webhook) are driven by hooks and webhook
handlers, not this poller.

Work is sharded across uvicorn workers by a consistent hash of the trigger id
(mirroring ``ai_tasks.SVC_AI_Tasks.TaskMonitorService``) so multiple workers do
not fire the same trigger.
"""

import os
import socket
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Any, Callable, List, Optional

from zephyrex.extensions.ai_agents.AgentTurnExecutor import AgentTurnExecutor
from zephyrex.extensions.ai_agents.BLL_AI_Agents import (
    InvocationInstanceManager,
    InvocationTriggerManager,
)
from zephyrex.lib.Environment import env
from zephyrex.lib.Logging import logger
from zephyrex.logic.AbstractService import AbstractService

# How often the monitor polls for due triggers. This is the poll cadence, not
# an agent's turn cadence — a timer trigger's own ``interval_seconds`` governs
# how often that agent actually fires.
DEFAULT_POLL_INTERVAL_SECONDS = 60


class InvocationMonitorService(AbstractService):
    """Polls due invocation triggers and runs an agent turn for each."""

    def __init__(
        self,
        requester_id: str,
        model_registry: Optional[Any] = None,
        interval_seconds: int = DEFAULT_POLL_INTERVAL_SECONDS,
        executor_factory: Optional[Callable[[], AgentTurnExecutor]] = None,
        **kwargs,
    ) -> None:
        super().__init__(
            requester_id=requester_id, interval_seconds=interval_seconds, **kwargs
        )
        self.model_registry = model_registry
        # Injectable for tests (drive turns with a scripted chat transport);
        # defaults to a rotation-backed executor.
        self._executor_factory = executor_factory
        self.total_workers = int(env("UVICORN_WORKERS", "1") or "1")
        self.worker_id = self._compute_worker_id()

    # -- worker sharding ---------------------------------------------------

    def _compute_worker_id(self) -> int:
        """Derive a stable worker index from process identity, for sharding."""
        unique = f"{socket.gethostname()}:{os.getpid()}:{os.getppid()}"
        return int(sha256(unique.encode()).hexdigest()[-1], 16) % self.total_workers

    def _owns(self, trigger_id: str) -> bool:
        """Whether this worker owns the trigger, via consistent hashing."""
        shard = (
            int(sha256(trigger_id.encode()).hexdigest()[-1], 16) % self.total_workers
        )
        return shard == self.worker_id

    # -- polling -----------------------------------------------------------

    async def update(self) -> None:
        """Poll for due triggers this worker owns and fire each."""
        try:
            due = self._due_triggers(self._now())
        except Exception as exc:
            logger.error(f"Invocation monitor poll failed: {exc}")
            self._handle_failure(exc)
            return

        for trigger in due:
            if not self._owns(trigger.id):
                continue
            try:
                await self._fire(trigger)
            except Exception as exc:  # one bad trigger must not stop the sweep
                logger.error(f"Failed firing invocation trigger {trigger.id}: {exc}")

    def _due_triggers(self, now: datetime) -> List[Any]:
        triggers = InvocationTriggerManager(
            requester_id=self.requester_id, model_registry=self.model_registry
        ).list(enabled=True)
        return [t for t in triggers if self._is_due(t, now)]

    def _is_due(self, trigger: Any, now: datetime) -> bool:
        """Whether a trigger should fire now. Only timer/schedule are polled."""
        kind = getattr(trigger, "invocation_type", None)
        if kind == "timer":
            if not getattr(trigger, "interval_seconds", None):
                return False
            next_fire = getattr(trigger, "next_fire_at", None)
            return next_fire is None or self._as_aware(next_fire) <= now
        if kind == "schedule":
            return self._cron_due(trigger, now)
        return False

    def _cron_due(self, trigger: Any, now: datetime) -> bool:
        """Whether a cron trigger is due. Requires ``croniter``; if unavailable
        the trigger is skipped (logged) rather than fired blindly."""
        cron = getattr(trigger, "cron", None)
        if not cron:
            return False
        next_fire = getattr(trigger, "next_fire_at", None)
        if next_fire is not None:
            return self._as_aware(next_fire) <= now
        # No next_fire_at yet: seed it from the cron so the first fire is aligned
        # to the schedule rather than firing immediately.
        try:
            from croniter import croniter

            InvocationTriggerManager(
                requester_id=self.requester_id, model_registry=self.model_registry
            ).update(
                id=trigger.id,
                next_fire_at=croniter(cron, now).get_next(datetime),
            )
        except ImportError:
            logger.debug(
                f"croniter not installed; skipping schedule trigger {trigger.id}"
            )
        except Exception as exc:
            logger.debug(f"Could not seed cron next_fire_at for {trigger.id}: {exc}")
        return False

    # -- firing ------------------------------------------------------------

    async def _fire(self, trigger: Any) -> None:
        """Create a turn (instance) for the trigger, run it, and update the
        trigger's bookkeeping / next fire time."""
        now = self._now()

        # Scope the instance to the agent's owner so that owner sees their
        # agent's turn history (the monitor itself runs as a system driver).
        from zephyrex.extensions.ai_agents.BLL_AI_Agents import AgentManager

        agent = AgentManager(
            requester_id=self.requester_id, model_registry=self.model_registry
        ).get(id=trigger.agent_id)

        instances = InvocationInstanceManager(
            requester_id=self.requester_id, model_registry=self.model_registry
        )
        instance = instances.create(
            agent_id=trigger.agent_id,
            invocation_trigger_id=trigger.id,
            payload=getattr(trigger, "invocation_payload", None),
            user_id=getattr(agent, "user_id", None),
            team_id=getattr(agent, "team_id", None),
        )

        executor = (
            self._executor_factory()
            if self._executor_factory is not None
            else AgentTurnExecutor(
                model_registry=self.model_registry, requester_id=self.requester_id
            )
        )
        await executor.run(instance.id)

        self._record_fire(trigger, now)

    def _record_fire(self, trigger: Any, now: datetime) -> None:
        """Advance the trigger's bookkeeping after a firing."""
        update: dict = {
            "last_fired_at": now,
            "fire_count": (getattr(trigger, "fire_count", 0) or 0) + 1,
        }
        kind = getattr(trigger, "invocation_type", None)
        if kind == "timer":
            if getattr(trigger, "one_shot", False):
                update["enabled"] = False
            else:
                update["next_fire_at"] = now + timedelta(
                    seconds=trigger.interval_seconds
                )
        elif kind == "schedule":
            next_fire = self._next_cron(trigger, now)
            if next_fire is not None:
                update["next_fire_at"] = next_fire
        InvocationTriggerManager(
            requester_id=self.requester_id, model_registry=self.model_registry
        ).update(id=trigger.id, **update)

    def _next_cron(self, trigger: Any, now: datetime) -> Optional[datetime]:
        cron = getattr(trigger, "cron", None)
        if not cron:
            return None
        try:
            from croniter import croniter

            return croniter(cron, now).get_next(datetime)
        except Exception:
            return None

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _as_aware(value: datetime) -> datetime:
        """Treat naive timestamps (SQLite round-trips) as UTC for comparison."""
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
