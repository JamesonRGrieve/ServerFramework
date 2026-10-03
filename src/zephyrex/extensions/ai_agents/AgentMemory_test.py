"""Tests for short-term agent memory (context management).

Exercises the real AgentMemoryManager against a live registry: upsert semantics
(remember), the {key: content} view (as_dict) used for prompt injection, and
pruning (forget).
"""

import os
import uuid

import pytest

from zephyrex.extensions.ai_agents.BLL_AI_Agents import AgentManager, AgentMemoryManager
from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin


class TestAgentMemory(ExtensionServerMixin):
    @pytest.fixture(scope="module")
    def server(self):
        from fastapi.testclient import TestClient

        from conftest import CORE_COMPANION_EXTENSIONS
        from zephyrex.app import instance
        from zephyrex.pydantic2.sqlalchemy import prepare_test_registry

        prepare_test_registry()
        worker_id = os.environ.get("PYTEST_XDIST_WORKER", "")
        prefix = f"test.agent_memory.{worker_id}" if worker_id else "test.agent_memory"
        wanted = ("ai_agents", "ai", "conversations", "ai_prompts", "ai_memories")
        names = list(wanted) + [c for c in CORE_COMPANION_EXTENSIONS if c not in wanted]
        yield TestClient(instance(db_prefix=prefix, extensions=",".join(names)))

    def _agent(self, admin_a, model_registry):
        with AgentManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as agents:
            return agents.create(name=f"Agent {uuid.uuid4()}", user_id=admin_a.id)

    def test_remember_and_as_dict(self, admin_a, model_registry):
        agent = self._agent(admin_a, model_registry)
        with AgentMemoryManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as mem:
            mem.remember(agent.id, "operator_name", "James")
            mem.remember(agent.id, "goal", "be helpful")
            assert mem.as_dict(agent.id) == {
                "operator_name": "James",
                "goal": "be helpful",
            }

    def test_remember_upserts(self, admin_a, model_registry):
        agent = self._agent(admin_a, model_registry)
        with AgentMemoryManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as mem:
            mem.remember(agent.id, "mood", "curious")
            mem.remember(agent.id, "mood", "focused")  # same key -> update
            d = mem.as_dict(agent.id)
            assert d == {"mood": "focused"}  # not duplicated

    def test_forget(self, admin_a, model_registry):
        agent = self._agent(admin_a, model_registry)
        with AgentMemoryManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as mem:
            mem.remember(agent.id, "a", "1")
            mem.remember(agent.id, "b", "2")
            removed = mem.forget(agent.id, ["a"])
            assert removed == 1
            assert mem.as_dict(agent.id) == {"b": "2"}

    def test_memory_is_agent_scoped(self, admin_a, model_registry):
        a1 = self._agent(admin_a, model_registry)
        a2 = self._agent(admin_a, model_registry)
        with AgentMemoryManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as mem:
            mem.remember(a1.id, "k", "for-a1")
            assert mem.as_dict(a1.id) == {"k": "for-a1"}
            assert mem.as_dict(a2.id) == {}
