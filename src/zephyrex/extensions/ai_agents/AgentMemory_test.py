# SPDX-License-Identifier: AGPL-3.0-or-later
"""Short-term agent memory: keyed, upserted, pruned, the agent's own."""

import uuid

import pytest
from fastapi import HTTPException

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.ai_agents.BLL_AI_Agents import AgentManager, AgentMemoryManager
from zephyrex.extensions.ai_agents.EXT_AI_Agents import EXT_AI_Agents


class TestAgentMemory(ExtensionServerMixin):
    extension_class = EXT_AI_Agents

    def _agent(self, user, model_registry):
        return AgentManager(requester_id=user.id, model_registry=model_registry).create(
            name=f"Agent {uuid.uuid4()}"
        )

    def _memory(self, user, model_registry):
        return AgentMemoryManager(requester_id=user.id, model_registry=model_registry)

    def test_remember_and_as_dict(self, admin_a, model_registry):
        agent = self._agent(admin_a, model_registry)
        memory = self._memory(admin_a, model_registry)
        memory.remember(agent.id, "operator_name", "James")
        memory.remember(agent.id, "goal", "be helpful")
        assert memory.as_dict(agent.id) == {
            "operator_name": "James",
            "goal": "be helpful",
        }

    def test_remember_replaces(self, admin_a, model_registry):
        agent = self._agent(admin_a, model_registry)
        memory = self._memory(admin_a, model_registry)
        memory.remember(agent.id, "mood", "curious")
        memory.remember(agent.id, "mood", "focused")
        assert memory.as_dict(agent.id) == {"mood": "focused"}

    def test_a_key_is_the_agents_once(self, admin_a, model_registry):
        agent = self._agent(admin_a, model_registry)
        memory = self._memory(admin_a, model_registry)
        memory.create(agent_id=agent.id, key="k", content="one")
        with pytest.raises(HTTPException) as refused:
            memory.create(agent_id=agent.id, key="k", content="two")
        assert refused.value.status_code == 409

    def test_forget(self, admin_a, model_registry):
        agent = self._agent(admin_a, model_registry)
        memory = self._memory(admin_a, model_registry)
        memory.remember(agent.id, "a", "1")
        memory.remember(agent.id, "b", "2")
        assert memory.forget(agent.id, ["a"]) == 1
        assert memory.as_dict(agent.id) == {"b": "2"}

    def test_memory_is_per_agent(self, admin_a, model_registry):
        first, second = (self._agent(admin_a, model_registry) for _ in range(2))
        memory = self._memory(admin_a, model_registry)
        memory.remember(first.id, "k", "for the first")
        assert memory.as_dict(second.id) == {}

    def test_another_user_neither_reads_nor_writes_it(
        self, admin_a, admin_b, model_registry
    ):
        agent = self._agent(admin_a, model_registry)
        self._memory(admin_a, model_registry).remember(agent.id, "secret", "s")
        stranger = self._memory(admin_b, model_registry)
        assert stranger.as_dict(agent.id) == {}
        with pytest.raises(HTTPException) as refused:
            stranger.create(agent_id=agent.id, key="planted", content="x")
        assert refused.value.status_code in (403, 404)
