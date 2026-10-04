# SPDX-License-Identifier: AGPL-3.0-or-later
"""The invocation monitor, firing real triggers into real turns.

The turn's model transport is scripted (the one substituted boundary);
triggers, instances, turns and bookkeeping run against the database."""

import asyncio
import os
import types
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.ai_agents.AgentTurnExecutor import AgentTurnExecutor
from zephyrex.extensions.ai_agents.BLL_AI_Agents import (
    AgentManager,
    InvocationInstanceManager,
    InvocationTriggerManager,
)
from zephyrex.extensions.ai_agents.EXT_AI_Agents import EXT_AI_Agents
from zephyrex.extensions.ai_agents.SVC_AI_Agents import (
    DEFAULT_POLL_INTERVAL_SECONDS,
    InvocationMonitorService,
    as_utc,
    due_triggers_first,
)
from zephyrex.lib.Environment import env


class ScriptedChat:
    async def __call__(self, messages, tools):
        return {"message": {"role": "assistant", "content": "done", "tool_calls": None}}


def test_most_urgent_then_soonest_first():
    now = datetime(2026, 10, 3, tzinfo=timezone.utc)

    def trigger(name, priority, minutes):
        return types.SimpleNamespace(
            name=name,
            priority=priority,
            next_fire_at=None if minutes is None else now - timedelta(minutes=minutes),
        )

    ordered = due_triggers_first(
        [trigger("late", 3, 1), trigger("urgent", 1, 0), trigger("early", 3, 9)], now
    )
    assert [t.name for t in ordered] == ["urgent", "early", "late"]


def test_naive_times_are_utc():
    assert as_utc(datetime(2026, 1, 1)).tzinfo is timezone.utc


class TestInvocationMonitorService(ExtensionServerMixin):
    extension_class = EXT_AI_Agents

    @pytest.fixture(scope="module")
    def server(self):
        """A database of its own: the monitor fires every due trigger in
        it, and the other modules' (thousands, in the EP suite) are not
        this module's to fire."""
        from fastapi.testclient import TestClient

        from conftest import CORE_COMPANION_EXTENSIONS
        from zephyrex.app import instance
        from zephyrex.pydantic2.sqlalchemy import prepare_test_registry

        prepare_test_registry()
        wanted = ["ai_agents"] + [d.name for d in EXT_AI_Agents.dependencies.ext]
        names = wanted + [c for c in CORE_COMPANION_EXTENSIONS if c not in wanted]
        worker = os.environ.get("PYTEST_XDIST_WORKER", "")
        prefix = f"test.ai_agents_monitor{'.' + worker if worker else ''}"
        yield TestClient(instance(db_prefix=prefix, extensions=",".join(names)))

    def _monitor(self, model_registry):
        return InvocationMonitorService(
            requester_id=env("ROOT_ID"),
            model_registry=model_registry,
            executor_factory=lambda: AgentTurnExecutor(
                model_registry=model_registry,
                requester_id=env("ROOT_ID"),
                chat_fn=ScriptedChat(),
            ),
        )

    def _agent(self, user, model_registry):
        return AgentManager(requester_id=user.id, model_registry=model_registry).create(
            name=f"Agent {uuid.uuid4()}"
        )

    def _triggers(self, user, model_registry):
        return InvocationTriggerManager(
            requester_id=user.id, model_registry=model_registry
        )

    def _turns(self, agent_id, user, model_registry):
        return InvocationInstanceManager(
            requester_id=user.id, model_registry=model_registry
        ).list(agent_id=agent_id)

    def test_poll_interval_default(self, model_registry):
        assert (
            self._monitor(model_registry).interval_seconds
            == DEFAULT_POLL_INTERVAL_SECONDS
        )

    async def test_a_new_timer_fires_and_is_rescheduled(self, admin_a, model_registry):
        agent = self._agent(admin_a, model_registry)
        trigger = self._triggers(admin_a, model_registry).create(
            agent_id=agent.id, invocation_type="timer", interval_seconds=300
        )
        assert trigger.next_fire_at is None
        await self._monitor(model_registry).update()
        [turn] = self._turns(agent.id, admin_a, model_registry)
        assert (turn.status, turn.user_id) == ("succeeded", admin_a.id)
        refreshed = self._triggers(admin_a, model_registry).get(id=trigger.id)
        assert refreshed.fire_count == 1 and refreshed.last_fired_at
        assert as_utc(refreshed.next_fire_at) > datetime.now(timezone.utc)

    async def test_a_task_waits_for_its_due_time(self, admin_a, model_registry):
        agent = self._agent(admin_a, model_registry)
        due_at = datetime.now(timezone.utc) + timedelta(hours=1)
        task = self._triggers(admin_a, model_registry).create(
            agent_id=agent.id,
            invocation_type="timer",
            one_shot=True,
            due_at=due_at,
            invocation_payload="file the report",
        )
        assert as_utc(task.next_fire_at) == due_at
        await self._monitor(model_registry).update()
        assert self._turns(agent.id, admin_a, model_registry) == []

    async def test_a_due_task_fires_once_with_its_instructions(
        self, admin_a, model_registry
    ):
        agent = self._agent(admin_a, model_registry)
        task = self._triggers(admin_a, model_registry).create(
            agent_id=agent.id,
            invocation_type="timer",
            one_shot=True,
            due_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            invocation_payload="file the report",
        )
        await self._monitor(model_registry).update()
        await self._monitor(model_registry).update()
        [turn] = self._turns(agent.id, admin_a, model_registry)
        assert turn.payload == "file the report"
        refreshed = self._triggers(admin_a, model_registry).get(id=task.id)
        assert (refreshed.enabled, refreshed.fire_count) == (False, 1)

    async def test_a_schedule_waits_for_its_next_tick(self, admin_a, model_registry):
        agent = self._agent(admin_a, model_registry)
        trigger = self._triggers(admin_a, model_registry).create(
            agent_id=agent.id, invocation_type="schedule", cron="0 0 1 1 *"
        )
        await self._monitor(model_registry).update()
        assert self._turns(agent.id, admin_a, model_registry) == []
        seeded = self._triggers(admin_a, model_registry).get(id=trigger.id)
        assert as_utc(seeded.next_fire_at) > datetime.now(timezone.utc)

    async def test_a_trigger_whose_agent_is_gone_stops(self, admin_a, model_registry):
        # It used to fail, and stay due, on every poll for ever.
        agent = self._agent(admin_a, model_registry)
        trigger = self._triggers(admin_a, model_registry).create(
            agent_id=agent.id, invocation_type="timer", interval_seconds=300
        )
        AgentManager(requester_id=admin_a.id, model_registry=model_registry).delete(
            agent.id
        )
        await self._monitor(model_registry).update()
        stopped = InvocationTriggerManager(
            requester_id=env("ROOT_ID"), model_registry=model_registry
        ).get(id=trigger.id)
        assert stopped.enabled is False

    async def test_a_disabled_trigger_does_not_fire(self, admin_a, model_registry):
        agent = self._agent(admin_a, model_registry)
        triggers = self._triggers(admin_a, model_registry)
        trigger = triggers.create(
            agent_id=agent.id, invocation_type="timer", interval_seconds=300
        )
        triggers.update(id=trigger.id, enabled=False)
        await self._monitor(model_registry).update()
        assert self._turns(agent.id, admin_a, model_registry) == []

    async def test_an_event_trigger_is_not_polled(self, admin_a, model_registry):
        agent = self._agent(admin_a, model_registry)
        self._triggers(admin_a, model_registry).create(
            agent_id=agent.id,
            invocation_type="event",
            event_source="conversation_message",
        )
        await self._monitor(model_registry).update()
        assert self._turns(agent.id, admin_a, model_registry) == []

    def test_register_services_returns_the_monitor(self, model_registry):
        services = EXT_AI_Agents.register_services(
            model_registry=model_registry, requester_id=env("ROOT_ID")
        )
        assert any(isinstance(s, InvocationMonitorService) for s in services)

    async def test_boot_starts_the_monitor(self, model_registry):
        from zephyrex.app import _start_background_services
        from zephyrex.logic.AbstractService import ServiceRegistry

        app = types.SimpleNamespace(
            state=types.SimpleNamespace(model_registry=model_registry)
        )
        tasks = _start_background_services(app)
        try:
            assert any(
                isinstance(ServiceRegistry.get(sid), InvocationMonitorService)
                for sid in ServiceRegistry.list()
            )
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                with pytest.raises(asyncio.CancelledError):
                    await task
