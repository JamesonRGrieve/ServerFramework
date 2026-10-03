"""Tests for the conversation-message event hook (reactive/conversational mode).

A user message in a conversation fires an agent turn — but only for agents that
participate AND have an enabled ``conversation_message`` trigger. Ordinary
messages (and agent-authored ones) fire nothing. These tests assert the hook's
*firing* decision by checking whether an InvocationInstance was created; the turn
execution itself is covered by the executor tests.
"""

import os
import time
import uuid

import pytest

from zephyrex.extensions.ai_agents.BLL_AI_Agents import (
    AgentManager,
    ConversationAgentManager,
    InvocationInstanceManager,
    InvocationTriggerManager,
)
from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin


class TestConversationMessageTurn(ExtensionServerMixin):
    @pytest.fixture(scope="module")
    def server(self):
        from fastapi.testclient import TestClient

        from conftest import CORE_COMPANION_EXTENSIONS
        from zephyrex.app import instance
        from zephyrex.pydantic2.sqlalchemy import prepare_test_registry

        prepare_test_registry()
        worker_id = os.environ.get("PYTEST_XDIST_WORKER", "")
        prefix = f"test.convo_turn.{worker_id}" if worker_id else "test.convo_turn"
        wanted = (
            "ai_agents",
            "ai",
            "email",
            "conversations",
            "ai_prompts",
            "ai_memories",
        )
        names = list(wanted) + [c for c in CORE_COMPANION_EXTENSIONS if c not in wanted]
        app = instance(db_prefix=prefix, extensions=",".join(names))
        yield TestClient(app)

    def _agent_in_conversation(self, admin_a, model_registry):
        from zephyrex.extensions.conversations.BLL_Conversations import (
            ConversationManager,
        )

        with AgentManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as agents:
            agent = agents.create(name=f"Agent {uuid.uuid4()}", user_id=admin_a.id)
        with ConversationManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as conversations:
            conversation = conversations.create(name=f"Chat {uuid.uuid4()}")
        with ConversationAgentManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as links:
            links.create(
                agent_id=agent.id, conversation_id=conversation.id, active=True
            )
        return agent, conversation

    def _conversation_message_trigger(self, agent_id, admin_a, model_registry):
        with InvocationTriggerManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as triggers:
            return triggers.create(
                agent_id=agent_id,
                invocation_type="event",
                event_source="conversation_message",
            )

    def _post_user_message(self, conversation_id, admin_a, model_registry):
        from zephyrex.extensions.conversations.BLL_Conversations import MessageManager

        with MessageManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as messages:
            return messages.create(
                conversation_id=conversation_id,
                content="Hello agent",
                user_id=admin_a.id,
            )

    def _instances(self, agent_id, admin_a, model_registry):
        with InvocationInstanceManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as instances:
            return instances.list(agent_id=agent_id)

    def _wait_for_instances(
        self, agent_id, admin_a, model_registry, minimum=1, timeout=10.0
    ):
        """Poll for instances — the conversation-message hook runs the turn in a
        fire-and-forget daemon thread, so its effect appears asynchronously."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            found = self._instances(agent_id, admin_a, model_registry)
            if len(found) >= minimum:
                return found
            time.sleep(0.1)
        return self._instances(agent_id, admin_a, model_registry)

    def _settle(self):
        """Give the fire-and-forget hook thread time to run (for negative
        assertions that nothing was created)."""
        time.sleep(2.0)

    # -- tests ------------------------------------------------------------

    def test_message_fires_turn_for_triggered_agent(self, admin_a, model_registry):
        agent, conversation = self._agent_in_conversation(admin_a, model_registry)
        trigger = self._conversation_message_trigger(agent.id, admin_a, model_registry)

        message = self._post_user_message(conversation.id, admin_a, model_registry)

        instances = self._wait_for_instances(agent.id, admin_a, model_registry)
        assert len(instances) == 1
        assert instances[0].trigger_message_id == message.id
        assert instances[0].invocation_trigger_id == trigger.id

    def test_no_trigger_no_turn(self, admin_a, model_registry):
        # Agent participates but has NO conversation_message trigger → no turn.
        agent, conversation = self._agent_in_conversation(admin_a, model_registry)

        self._post_user_message(conversation.id, admin_a, model_registry)
        self._settle()

        assert self._instances(agent.id, admin_a, model_registry) == []

    def test_agent_message_does_not_loop(self, admin_a, model_registry):
        # An agent-authored message (user_id=None) must not fire a turn, or an
        # agent's own reply would re-trigger the hook forever.
        agent, conversation = self._agent_in_conversation(admin_a, model_registry)
        self._conversation_message_trigger(agent.id, admin_a, model_registry)

        from zephyrex.extensions.conversations.BLL_Conversations import MessageManager

        with MessageManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as messages:
            messages.create(
                conversation_id=conversation.id,
                content="agent reply",
                user_id=None,
            )
        self._settle()

        assert self._instances(agent.id, admin_a, model_registry) == []

    def test_trigger_for_non_participant_does_not_fire(self, admin_a, model_registry):
        # A triggered agent that is NOT in the conversation must not fire.
        _agent_in, conversation = self._agent_in_conversation(admin_a, model_registry)
        with AgentManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as agents:
            outsider = agents.create(
                name=f"Outsider {uuid.uuid4()}", user_id=admin_a.id
            )
        self._conversation_message_trigger(outsider.id, admin_a, model_registry)

        self._post_user_message(conversation.id, admin_a, model_registry)
        self._settle()

        assert self._instances(outsider.id, admin_a, model_registry) == []
