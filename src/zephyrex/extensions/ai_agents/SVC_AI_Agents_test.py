"""Tests for the invocation monitor service.

Drives the real trigger -> instance -> turn cycle against the database: the
monitor polls a timer trigger, fires it (creating an InvocationInstance and
running the executor), and advances the trigger's bookkeeping. The turn's model
transport is scripted (the sole substituted boundary); everything else runs for
real.
"""

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from zephyrex.extensions.ai_agents.AgentTurnExecutor import AgentTurnExecutor
from zephyrex.extensions.ai_agents.BLL_AI_Agents import (
    AgentManager,
    InvocationInstanceManager,
    InvocationTriggerManager,
)
from zephyrex.extensions.ai_agents.SVC_AI_Agents import (
    DEFAULT_POLL_INTERVAL_SECONDS,
    InvocationMonitorService,
)
from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.lib.Environment import env


def _text(content):
    return {
        "success": True,
        "message": {"role": "assistant", "content": content, "tool_calls": None},
        "finish_reason": "stop",
    }


class _ScriptedChat:
    """A real chat transport returning a fixed reply (no tool calls)."""

    def __init__(self, content="done"):
        self.content = content
        self.calls = 0

    async def __call__(self, messages, tools):
        self.calls += 1
        return _text(self.content)


class TestInvocationMonitorService(ExtensionServerMixin):
    @pytest.fixture(scope="module")
    def server(self):
        from fastapi.testclient import TestClient

        from conftest import CORE_COMPANION_EXTENSIONS
        from zephyrex.app import instance
        from zephyrex.pydantic2.sqlalchemy import prepare_test_registry

        prepare_test_registry()
        worker_id = os.environ.get("PYTEST_XDIST_WORKER", "")
        prefix = (
            f"test.invocation_monitor.{worker_id}"
            if worker_id
            else "test.invocation_monitor"
        )
        wanted = ("ai_agents", "ai", "email", "conversations", "ai_prompts")
        names = list(wanted) + [c for c in CORE_COMPANION_EXTENSIONS if c not in wanted]
        app = instance(db_prefix=prefix, extensions=",".join(names))
        yield TestClient(app)

    def _monitor(self, model_registry, chat=None):
        chat = chat or _ScriptedChat()
        return InvocationMonitorService(
            requester_id=env("ROOT_ID"),
            model_registry=model_registry,
            executor_factory=lambda: AgentTurnExecutor(
                model_registry=model_registry,
                requester_id=env("ROOT_ID"),
                chat_fn=chat,
            ),
        )

    def _agent(self, admin_a, model_registry):
        with AgentManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as agents:
            return agents.create(name=f"Agent {uuid.uuid4()}", user_id=admin_a.id)

    def _timer_trigger(self, agent_id, admin_a, model_registry, one_shot=False):
        with InvocationTriggerManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as triggers:
            return triggers.create(
                agent_id=agent_id,
                invocation_type="timer",
                interval_seconds=300,
                one_shot=one_shot,
            )

    def _instances_for(self, agent_id, admin_a, model_registry):
        with InvocationInstanceManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as instances:
            return instances.list(agent_id=agent_id)

    # -- tests ------------------------------------------------------------

    def test_poll_interval_default(self, model_registry):
        monitor = self._monitor(model_registry)
        assert monitor.interval_seconds == DEFAULT_POLL_INTERVAL_SECONDS

    async def test_new_timer_trigger_fires(self, admin_a, model_registry):
        agent = self._agent(admin_a, model_registry)
        trigger = self._timer_trigger(agent.id, admin_a, model_registry)
        assert trigger.next_fire_at is None  # never fired yet

        await self._monitor(model_registry).update()

        instances = self._instances_for(agent.id, admin_a, model_registry)
        assert len(instances) == 1
        assert instances[0].status == "succeeded"

        with InvocationTriggerManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as triggers:
            refreshed = triggers.get(id=trigger.id)
        assert refreshed.fire_count == 1
        assert refreshed.last_fired_at is not None
        assert refreshed.next_fire_at is not None  # scheduled for next interval

    async def test_not_due_trigger_does_not_fire(self, admin_a, model_registry):
        agent = self._agent(admin_a, model_registry)
        trigger = self._timer_trigger(agent.id, admin_a, model_registry)
        # Push next fire well into the future so it is not yet due.
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        with InvocationTriggerManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as triggers:
            triggers.update(id=trigger.id, next_fire_at=future)

        await self._monitor(model_registry).update()

        assert self._instances_for(agent.id, admin_a, model_registry) == []

    async def test_disabled_trigger_does_not_fire(self, admin_a, model_registry):
        agent = self._agent(admin_a, model_registry)
        trigger = self._timer_trigger(agent.id, admin_a, model_registry)
        with InvocationTriggerManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as triggers:
            triggers.update(id=trigger.id, enabled=False)

        await self._monitor(model_registry).update()

        assert self._instances_for(agent.id, admin_a, model_registry) == []

    async def test_one_shot_trigger_disables_after_firing(
        self, admin_a, model_registry
    ):
        agent = self._agent(admin_a, model_registry)
        trigger = self._timer_trigger(agent.id, admin_a, model_registry, one_shot=True)

        await self._monitor(model_registry).update()

        with InvocationTriggerManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as triggers:
            refreshed = triggers.get(id=trigger.id)
        assert refreshed.enabled is False
        assert refreshed.fire_count == 1

    def test_register_services_returns_monitor(self, model_registry):
        from zephyrex.extensions.ai_agents.EXT_AI_Agents import EXT_AI_Agents

        services = EXT_AI_Agents.register_services(
            model_registry=model_registry, requester_id=env("ROOT_ID")
        )
        assert any(isinstance(s, InvocationMonitorService) for s in services)

    async def test_boot_helper_starts_services(self, model_registry):
        # The generic, extension-agnostic boot helper must discover and start the
        # monitor via the register_services convention.
        import asyncio
        import types

        from zephyrex.app import _start_background_services
        from zephyrex.logic.AbstractService import ServiceRegistry

        fake_app = types.SimpleNamespace(
            state=types.SimpleNamespace(model_registry=model_registry)
        )
        tasks = _start_background_services(fake_app)
        try:
            assert len(tasks) >= 1
            assert any(
                isinstance(ServiceRegistry.get(sid), InvocationMonitorService)
                for sid in ServiceRegistry.list()
            )
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    async def test_event_trigger_not_polled(self, admin_a, model_registry):
        # Event triggers are hook/webhook driven, never fired by the poller.
        agent = self._agent(admin_a, model_registry)
        with InvocationTriggerManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as triggers:
            triggers.create(
                agent_id=agent.id,
                invocation_type="event",
                event_source="conversation_message",
            )

        await self._monitor(model_registry).update()

        assert self._instances_for(agent.id, admin_a, model_registry) == []
