# SPDX-License-Identifier: AGPL-3.0-or-later
"""A user's message wakes the agents in its conversation that react to
messages: those with an enabled ``conversation_message`` trigger, or seated
to auto-respond. Ordinary messages, and agents' own, wake none. These check
the firing (an InvocationInstance); the executor tests cover the turn.

Holes these close: another participant's message made the turn theirs (the
instance was created as the poster, who cannot edit the agent), and
``auto_respond`` configured nothing."""

import time
import uuid

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.ai_agents.BLL_AI_Agents import (
    AgentManager,
    ConversationAgentManager,
    InvocationInstanceManager,
    InvocationTriggerManager,
)
from zephyrex.extensions.ai_agents.EXT_AI_Agents import EXT_AI_Agents
from zephyrex.extensions.conversations.BLL_Conversations import (
    ConversationManager,
    MessageManager,
)
from zephyrex.lib.Environment import env
from zephyrex.testing.factories import add_user_to_team, create_user

WAIT_SECONDS = 10.0
POLL_SECONDS = 0.1
SETTLE_SECONDS = 2.0


class TestConversationMessageTurn(ExtensionServerMixin):
    extension_class = EXT_AI_Agents

    def _seated_agent(self, owner, model_registry, auto_respond=False):
        agent = AgentManager(
            requester_id=owner.id, model_registry=model_registry
        ).create(name=f"Agent {uuid.uuid4()}")
        conversation = ConversationManager(
            requester_id=owner.id, model_registry=model_registry
        ).create(name=f"Chat {uuid.uuid4()}")
        ConversationAgentManager(
            requester_id=owner.id, model_registry=model_registry
        ).create(
            agent_id=agent.id,
            conversation_id=conversation.id,
            active=True,
            auto_respond=auto_respond,
        )
        return agent, conversation

    def _listen(self, agent_id, owner, model_registry):
        return InvocationTriggerManager(
            requester_id=owner.id, model_registry=model_registry
        ).create(
            agent_id=agent_id,
            invocation_type="event",
            event_source="conversation_message",
        )

    def _post(self, conversation_id, user, model_registry, content="Hello agent"):
        return MessageManager(
            requester_id=user.id, model_registry=model_registry
        ).create(conversation_id=conversation_id, content=content)

    def _turns(self, agent_id, owner, model_registry, minimum=0):
        """The agent's turns; the hook runs after the message is made, so
        wait for ``minimum`` of them (or settle when expecting none)."""
        instances = InvocationInstanceManager(
            requester_id=owner.id, model_registry=model_registry
        )
        if not minimum:
            time.sleep(SETTLE_SECONDS)
            return instances.list(agent_id=agent_id)
        deadline = time.time() + WAIT_SECONDS
        while time.time() < deadline:
            found = instances.list(agent_id=agent_id)
            if len(found) >= minimum:
                return found
            time.sleep(POLL_SECONDS)
        return instances.list(agent_id=agent_id)

    def test_a_message_fires_a_listening_agent(self, admin_a, model_registry):
        agent, conversation = self._seated_agent(admin_a, model_registry)
        trigger = self._listen(agent.id, admin_a, model_registry)
        message = self._post(conversation.id, admin_a, model_registry)
        [turn] = self._turns(agent.id, admin_a, model_registry, minimum=1)
        assert (turn.trigger_message_id, turn.invocation_trigger_id) == (
            message.id,
            trigger.id,
        )
        assert turn.payload == "Hello agent"

    def test_another_participants_message_is_the_owners_turn(
        self, server, admin_a, team_a, model_registry
    ):
        # A participant must be a user the owner can see, so the other
        # participant is admin_a's teammate (this once seated user_b, a
        # stranger, when a participant was checked as SYSTEM).
        teammate = create_user(server)
        add_user_to_team(server, teammate.id, team_a.id, env("USER_ROLE_ID"))
        agent, conversation = self._seated_agent(admin_a, model_registry)
        self._listen(agent.id, admin_a, model_registry)
        ConversationManager(
            requester_id=admin_a.id, model_registry=model_registry
        ).add_participant(conversation.id, teammate.id)
        message = self._post(conversation.id, teammate, model_registry, "from b")
        [turn] = self._turns(agent.id, admin_a, model_registry, minimum=1)
        assert turn.trigger_message_id == message.id
        assert turn.user_id == admin_a.id

    def test_auto_respond_fires_without_a_trigger(self, admin_a, model_registry):
        agent, conversation = self._seated_agent(
            admin_a, model_registry, auto_respond=True
        )
        message = self._post(conversation.id, admin_a, model_registry)
        [turn] = self._turns(agent.id, admin_a, model_registry, minimum=1)
        assert turn.trigger_message_id == message.id
        assert turn.invocation_trigger_id is None

    def test_no_trigger_no_turn(self, admin_a, model_registry):
        agent, conversation = self._seated_agent(admin_a, model_registry)
        self._post(conversation.id, admin_a, model_registry)
        assert self._turns(agent.id, admin_a, model_registry) == []

    def test_an_agents_message_does_not_loop(self, admin_a, model_registry):
        # Agents post through create_agent_message: MessageManager.create now
        # always records the poster as the author, so the old version of this
        # test (create with user_id=None) posted a user's message.
        agent, conversation = self._seated_agent(
            admin_a, model_registry, auto_respond=True
        )
        self._listen(agent.id, admin_a, model_registry)
        MessageManager(
            requester_id=admin_a.id, model_registry=model_registry
        ).create_agent_message(conversation_id=conversation.id, content="agent reply")
        assert self._turns(agent.id, admin_a, model_registry) == []

    def test_a_listening_agent_outside_the_conversation_does_not_fire(
        self, admin_a, model_registry
    ):
        _, conversation = self._seated_agent(admin_a, model_registry)
        outsider = AgentManager(
            requester_id=admin_a.id, model_registry=model_registry
        ).create(name=f"Outsider {uuid.uuid4()}")
        self._listen(outsider.id, admin_a, model_registry)
        self._post(conversation.id, admin_a, model_registry)
        assert self._turns(outsider.id, admin_a, model_registry) == []
