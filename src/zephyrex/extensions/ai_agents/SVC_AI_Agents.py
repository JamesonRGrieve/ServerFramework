# SPDX-License-Identifier: AGPL-3.0-or-later
"""The monitor that wakes agents on their schedules, timers and tasks.

``InvocationMonitorService`` polls the enabled ``schedule`` and ``timer``
triggers and fires those that are due, most urgent first: each firing is an
:class:`InvocationInstanceModel` run by the :class:`AgentTurnExecutor`, and
then the trigger's next firing is worked out. A timer with
``interval_seconds=300`` wakes its agent every five minutes; a one-shot task
fires once, at its ``due_at``. ``event`` triggers are fired by what they
listen to (a conversation message), never by the poller.

Triggers are sharded across uvicorn workers by a hash of their id, so two
workers never fire the same one.
"""

import os
import socket
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Any, Callable, Dict, List, Optional

from croniter import croniter
from fastapi import HTTPException

from zephyrex.extensions.ai_agents.AgentTurnExecutor import AgentTurnExecutor
from zephyrex.extensions.ai_agents.BLL_AI_Agents import (
    AgentManager,
    InvocationInstanceManager,
    InvocationTriggerManager,
)
from zephyrex.lib.Environment import env
from zephyrex.lib.Logging import logger
from zephyrex.logic.AbstractService import AbstractService

# How often the monitor looks for due triggers; a trigger's own schedule
# decides how often its agent wakes.
DEFAULT_POLL_INTERVAL_SECONDS = 60
POLLED_TYPES = ("timer", "schedule")


def as_utc(value: datetime) -> datetime:
    """A naive timestamp (SQLite drops the zone) read as UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


class InvocationMonitorService(AbstractService):
    """Fires due triggers, as ``requester_id`` (ROOT, to see every agent's);
    each turn acts as its agent's owner."""

    def __init__(
        self,
        requester_id: str,
        model_registry: Optional[Any] = None,
        interval_seconds: int = DEFAULT_POLL_INTERVAL_SECONDS,
        executor_factory: Optional[Callable[[], AgentTurnExecutor]] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            requester_id=requester_id, interval_seconds=interval_seconds, **kwargs
        )
        self.model_registry = model_registry
        # A caller may run turns through another executor (a different model
        # transport); by default each runs over its agent's rotation.
        self._executor_factory = executor_factory
        self.total_workers = int(env("UVICORN_WORKERS", "1") or "1")
        self.worker_id = self._shard(
            f"{socket.gethostname()}:{os.getpid()}:{os.getppid()}"
        )

    def _shard(self, key: str) -> int:
        return int(sha256(key.encode()).hexdigest()[-1], 16) % self.total_workers

    def _owns(self, trigger_id: str) -> bool:
        return self._shard(trigger_id) == self.worker_id

    def _triggers(self) -> InvocationTriggerManager:
        return InvocationTriggerManager(
            requester_id=self.requester_id, model_registry=self.model_registry
        )

    async def update(self) -> None:
        """Fire every due trigger this worker owns, most urgent first."""
        now = datetime.now(timezone.utc)
        due = [
            trigger
            for trigger in self._triggers().list(enabled=True)
            if trigger.invocation_type in POLLED_TYPES
            and self._owns(trigger.id)
            and self._is_due(trigger, now)
        ]
        for trigger in due_triggers_first(due, now):
            try:
                await self._fire(trigger, now)
            except HTTPException as refused:
                # The agent is gone, or its owner may no longer run it: the
                # trigger can never fire again, so it stops.
                logger.warning(
                    "Disabling invocation trigger %s: %s", trigger.id, refused.detail
                )
                self._triggers().update(id=trigger.id, enabled=False)
            except Exception:  # one trigger's failure must not stop the sweep
                logger.exception("Failed firing invocation trigger %s", trigger.id)
                # Not retried every poll: it next fires when it is next due.
                self._triggers().update(
                    id=trigger.id, **self._after_firing(trigger, now)
                )

    def _is_due(self, trigger: Any, now: datetime) -> bool:
        """A trigger with a next firing is due once it has passed. A timer
        without one fires now; a schedule without one is given its next
        tick, and waits for it."""
        if trigger.next_fire_at is not None:
            return as_utc(trigger.next_fire_at) <= now
        if trigger.invocation_type == "timer":
            return True
        self._triggers().update(
            id=trigger.id, next_fire_at=croniter(trigger.cron, now).get_next(datetime)
        )
        return False

    async def _fire(self, trigger: Any, now: datetime) -> None:
        """A turn for the trigger, owned by its agent's owner; then the
        trigger's bookkeeping and next firing."""
        agent = AgentManager(
            requester_id=self.requester_id, model_registry=self.model_registry
        ).get(id=trigger.agent_id)
        # Made as the owner, whose turn it is: they record its activities.
        instance = InvocationInstanceManager(
            requester_id=agent.user_id or self.requester_id,
            model_registry=self.model_registry,
        ).create(
            agent_id=trigger.agent_id,
            invocation_trigger_id=trigger.id,
            payload=trigger.invocation_payload,
        )
        executor = (
            self._executor_factory()
            if self._executor_factory is not None
            else AgentTurnExecutor(
                model_registry=self.model_registry, requester_id=self.requester_id
            )
        )
        await executor.run(instance.id)
        self._triggers().update(id=trigger.id, **self._after_firing(trigger, now))

    @staticmethod
    def _after_firing(trigger: Any, now: datetime) -> Dict[str, Any]:
        """The bookkeeping, and next firing, of a trigger that fired ``now``."""
        changes: Dict[str, Any] = {
            "last_fired_at": now,
            "fire_count": trigger.fire_count + 1,
        }
        if trigger.invocation_type == "schedule":
            changes["next_fire_at"] = croniter(trigger.cron, now).get_next(datetime)
        elif trigger.one_shot:
            changes["enabled"] = False
        else:
            changes["next_fire_at"] = now + timedelta(seconds=trigger.interval_seconds)
        return changes


def due_triggers_first(triggers: List[Any], now: datetime) -> List[Any]:
    """``triggers`` most urgent first, then soonest due."""
    return sorted(triggers, key=lambda t: (t.priority, as_utc(t.next_fire_at or now)))
