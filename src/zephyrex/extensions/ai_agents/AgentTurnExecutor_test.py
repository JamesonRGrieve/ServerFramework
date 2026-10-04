# SPDX-License-Identifier: AGPL-3.0-or-later
"""The turn executor, against the app database.

The model is the one substituted boundary: ``ScriptedChat`` implements the
chat transport's contract (``(messages, tools) -> {"message": ...}``, raising
typed errors on failure) and replays a script, so the loop, the gate, the
activity and message writes and the lifecycle run as in production."""

import json
import uuid

import pytest

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.ai_agents.AgentTurnExecutor import (
    ABILITIES_ABILITY,
    SEARCHED_ABILITIES_KEY,
    SELF_ABILITY_SIGNATURES,
    SPEAK_ABILITY,
    AgentTurnExecutor,
    ensure_ability,
)
from zephyrex.extensions.ai_agents.BLL_AI_Agents import (
    ActivityManager,
    ActivityState,
    AgentAbilityManager,
    AgentManager,
    AgentMemoryManager,
    InvocationInstanceManager,
)
from zephyrex.extensions.ai_agents.EXT_AI_Agents import EXT_AI_Agents
from zephyrex.extensions.ExternalErrors import TransientExternalError


def _text(content):
    return {"message": {"role": "assistant", "content": content, "tool_calls": None}}


def _tool(name, arguments, call_id="call_1"):
    return {
        "message": {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": call_id, "name": name, "arguments": arguments}],
        }
    }


class ScriptedChat:
    """A chat transport replaying a script; an exception in it is raised,
    as a provider's failure would be."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def __call__(self, messages, tools):
        index = min(len(self.calls), len(self.responses) - 1)
        self.calls.append({"messages": list(messages), "tools": tools})
        response = self.responses[index]
        if isinstance(response, Exception):
            raise response
        return response


class TestAgentTurnExecutor(ExtensionServerMixin):
    extension_class = EXT_AI_Agents

    def _agent(self, user, model_registry):
        return AgentManager(requester_id=user.id, model_registry=model_registry).create(
            name=f"Agent {uuid.uuid4()}"
        )

    def _instance(self, agent_id, user, model_registry, payload="Take your turn."):
        return InvocationInstanceManager(
            requester_id=user.id, model_registry=model_registry
        ).create(agent_id=agent_id, payload=payload)

    def _grant(self, agent_id, ability_name, user, model_registry):
        AgentAbilityManager(requester_id=user.id, model_registry=model_registry).create(
            agent_id=agent_id, ability_id=ensure_ability(model_registry, ability_name)
        )

    def _activities(self, instance_id, user, model_registry):
        return ActivityManager(
            requester_id=user.id, model_registry=model_registry
        ).list(invocation_instance_id=instance_id)

    async def _turn(self, agent, user, model_registry, script, **grants):
        for name in grants.get("granted", ()):
            self._grant(agent.id, name, user, model_registry)
        instance = self._instance(agent.id, user, model_registry)
        chat = ScriptedChat(script)
        summary = await AgentTurnExecutor(
            model_registry=model_registry, requester_id=user.id, chat_fn=chat
        ).run(instance.id)
        return instance, summary, chat

    def _children(self, instance, user, model_registry):
        return [
            a
            for a in self._activities(instance.id, user, model_registry)
            if a.parent_id is not None
        ]

    async def test_silent_turn_records_root_only(self, admin_a, model_registry):
        agent = self._agent(admin_a, model_registry)
        instance, summary, _ = await self._turn(
            agent, admin_a, model_registry, [_text("nothing needs doing")]
        )
        assert summary["status"] == "succeeded" and summary["spoke"] is False
        acts = self._activities(instance.id, admin_a, model_registry)
        assert [(a.parent_id, a.body) for a in acts] == [(None, "nothing needs doing")]
        refreshed = InvocationInstanceManager(
            requester_id=admin_a.id, model_registry=model_registry
        ).get(id=instance.id)
        assert refreshed.status == "succeeded" and refreshed.completed_at

    async def test_an_extension_ability_runs_as_the_owner(
        self, admin_a, admin_b, model_registry
    ):
        agent = self._agent(admin_a, model_registry)
        theirs = self._agent(admin_b, model_registry)
        args = json.dumps({"requester_id": admin_b.id})
        instance, summary, chat = await self._turn(
            agent,
            admin_a,
            model_registry,
            [_tool("list_agents", args), _text("done")],
            granted=["list_agents"],
        )
        assert summary["tool_calls"] == 1
        [child] = self._children(instance, admin_a, model_registry)
        assert (child.title, child.state) == ("list_agents", ActivityState.SUCCESS)
        listed = {a["id"] for a in json.loads(chat.calls[1]["messages"][-1]["content"])}
        assert agent.id in listed and theirs.id not in listed

    async def test_speak_posts_an_agent_message(self, admin_a, model_registry):
        from zephyrex.extensions.conversations.BLL_Conversations import (
            ConversationManager,
        )

        agent = self._agent(admin_a, model_registry)
        conversations = ConversationManager(
            requester_id=admin_a.id, model_registry=model_registry
        )
        conversation = conversations.create(name=f"Chat {uuid.uuid4()}")
        speak = json.dumps(
            {"message": "Hello there.", "conversation_id": conversation.id}
        )
        instance, summary, _ = await self._turn(
            agent,
            admin_a,
            model_registry,
            [_tool(SPEAK_ABILITY, speak), _text("said it")],
            granted=[SPEAK_ABILITY],
        )
        assert summary["spoke"] is True
        said = [
            m
            for m in conversations.messages.list(conversation_id=conversation.id)
            if m.content == "Hello there."
        ]
        assert len(said) == 1 and said[0].user_id is None
        [child] = self._children(instance, admin_a, model_registry)
        assert (child.title, child.state) == ("speak", ActivityState.SUCCESS)

    async def test_speaking_where_the_owner_cannot_post_is_a_tool_error(
        self, admin_a, admin_b, model_registry
    ):
        from zephyrex.extensions.conversations.BLL_Conversations import (
            ConversationManager,
        )

        elsewhere = ConversationManager(
            requester_id=admin_b.id, model_registry=model_registry
        ).create(name="Not yours")
        agent = self._agent(admin_a, model_registry)
        speak = json.dumps({"message": "hi", "conversation_id": elsewhere.id})
        instance, summary, _ = await self._turn(
            agent,
            admin_a,
            model_registry,
            [_tool(SPEAK_ABILITY, speak), _text("ok")],
            granted=[SPEAK_ABILITY],
        )
        assert summary["status"] == "succeeded" and summary["spoke"] is False
        [child] = self._children(instance, admin_a, model_registry)
        assert child.state == ActivityState.ERROR

    @pytest.mark.parametrize("tool", ["list_agents", "take_turn", ABILITIES_ABILITY])
    async def test_an_ungranted_or_forbidden_tool_is_refused(
        self, tool, admin_a, model_registry
    ):
        agent = self._agent(admin_a, model_registry)
        granted = ["take_turn"] if tool == "take_turn" else []
        instance, summary, chat = await self._turn(
            agent,
            admin_a,
            model_registry,
            [_tool(tool, "{}"), _text("ok")],
            granted=granted,
        )
        assert summary["status"] == "succeeded"
        [child] = self._children(instance, admin_a, model_registry)
        assert (child.title, child.state) == (f"Denied: {tool}", ActivityState.ERROR)
        assert "Refused" in chat.calls[1]["messages"][-1]["content"]

    async def test_an_unnamed_call_is_refused(self, admin_a, model_registry):
        agent = self._agent(admin_a, model_registry)
        instance, summary, chat = await self._turn(
            agent, admin_a, model_registry, [_tool(None, "{}"), _text("ok")]
        )
        assert summary["status"] == "succeeded"
        assert self._children(instance, admin_a, model_registry) == []
        assert "named no ability" in json.dumps(chat.calls[1]["messages"])

    async def test_a_model_failure_fails_the_turn(self, admin_a, model_registry):
        agent = self._agent(admin_a, model_registry)
        instance, summary, _ = await self._turn(
            agent,
            admin_a,
            model_registry,
            [TransientExternalError("model unavailable")],
        )
        assert summary["status"] == "failed"
        refreshed = InvocationInstanceManager(
            requester_id=admin_a.id, model_registry=model_registry
        ).get(id=instance.id)
        assert refreshed.status == "failed" and "model unavailable" in refreshed.error

    async def test_an_agent_without_a_rotation_fails_its_turn(
        self, admin_a, model_registry
    ):
        agent = self._agent(admin_a, model_registry)
        instance = self._instance(agent.id, admin_a, model_registry)
        summary = await AgentTurnExecutor(
            model_registry=model_registry, requester_id=admin_a.id
        ).run(instance.id)
        assert summary == {
            "status": "failed",
            "error": "the agent has no rotation configured",
        }

    async def test_memorize_short_term(self, admin_a, model_registry):
        agent = self._agent(admin_a, model_registry)
        args = json.dumps({"key": "fav_color", "body": "blue", "long": False})
        await self._turn(
            agent,
            admin_a,
            model_registry,
            [_tool("memorize", args), _text("noted")],
            granted=["memorize"],
        )
        memory = AgentMemoryManager(
            requester_id=admin_a.id, model_registry=model_registry
        )
        assert memory.as_dict(agent.id) == {"fav_color": "blue"}

    @pytest.fixture
    def no_embedding_model(self, monkeypatch):
        """Long-term memories are kept and recalled by their words."""
        from zephyrex.extensions.ai.EXT_AI import EXT_AI

        monkeypatch.setattr(EXT_AI, "root", None)

    async def test_memorize_long_term(
        self, admin_a, model_registry, no_embedding_model
    ):
        from zephyrex.extensions.ai_memories.BLL_AI_Memories import MemoryManager

        agent = self._agent(admin_a, model_registry)
        args = json.dumps({"body": "the deadline is Friday", "long": True})
        await self._turn(
            agent,
            admin_a,
            model_registry,
            [_tool("memorize", args), _text("done")],
            granted=["memorize"],
        )
        hits = await MemoryManager(
            requester_id=admin_a.id, model_registry=model_registry
        ).recall(agent.id, "deadline", 5)
        assert [(h.content, h.user_id) for h in hits] == [
            ("the deadline is Friday", admin_a.id)
        ]

    async def test_trim_short_term(self, admin_a, model_registry):
        agent = self._agent(admin_a, model_registry)
        memory = AgentMemoryManager(
            requester_id=admin_a.id, model_registry=model_registry
        )
        memory.remember(agent.id, "a", "1")
        memory.remember(agent.id, "b", "2")
        await self._turn(
            agent,
            admin_a,
            model_registry,
            [_tool("trim", json.dumps({"memories": ["a"]})), _text("trimmed")],
            granted=["trim"],
        )
        assert memory.as_dict(agent.id) == {"b": "2"}

    async def test_recall_long_term(self, admin_a, model_registry, no_embedding_model):
        from zephyrex.extensions.ai_memories.BLL_AI_Memories import MemoryManager

        agent = self._agent(admin_a, model_registry)
        await MemoryManager(
            requester_id=admin_a.id, model_registry=model_registry
        ).keep(agent.id, "James prefers concise answers")
        instance, _, _ = await self._turn(
            agent,
            admin_a,
            model_registry,
            [_tool("recall", json.dumps({"query": "concise"})), _text("ok")],
            granted=["recall"],
        )
        [child] = self._children(instance, admin_a, model_registry)
        assert child.title == "recall" and "concise" in child.body

    async def test_memorize_refused_when_not_granted(self, admin_a, model_registry):
        agent = self._agent(admin_a, model_registry)
        instance, _, _ = await self._turn(
            agent,
            admin_a,
            model_registry,
            [_tool("memorize", json.dumps({"key": "x", "body": "y"})), _text("ok")],
        )
        [child] = self._children(instance, admin_a, model_registry)
        assert child.title == "Denied: memorize"
        memory = AgentMemoryManager(
            requester_id=admin_a.id, model_registry=model_registry
        )
        assert memory.as_dict(agent.id) == {}

    def _discovered(self, instance, user, model_registry):
        [disco] = [
            a
            for a in self._activities(instance.id, user, model_registry)
            if a.title == ABILITIES_ABILITY
        ]
        return disco, {d["name"] for d in json.loads(disco.body.split("\n", 1)[1])}

    async def test_discovery_lists_what_the_agent_may_use(
        self, admin_a, model_registry
    ):
        agent = self._agent(admin_a, model_registry)
        instance, _, _ = await self._turn(
            agent,
            admin_a,
            model_registry,
            [_tool(ABILITIES_ABILITY, "{}"), _text("ok")],
            granted=[ABILITIES_ABILITY, "list_agents", "take_turn"],
        )
        disco, names = self._discovered(instance, admin_a, model_registry)
        assert disco.state == ActivityState.SUCCESS
        assert names == {ABILITIES_ABILITY, "list_agents"}

    async def test_discovery_search_filters(self, admin_a, model_registry):
        agent = self._agent(admin_a, model_registry)
        instance, _, _ = await self._turn(
            agent,
            admin_a,
            model_registry,
            [
                _tool(ABILITIES_ABILITY, json.dumps({"search": "agents the user"})),
                _text("ok"),
            ],
            granted=[ABILITIES_ABILITY, "list_agents"],
        )
        assert self._discovered(instance, admin_a, model_registry)[1] == {"list_agents"}

    async def test_discovered_abilities_reach_later_prompts(
        self, admin_a, model_registry
    ):
        agent = self._agent(admin_a, model_registry)
        memory = AgentMemoryManager(
            requester_id=admin_a.id, model_registry=model_registry
        )
        memory.remember(agent.id, "note", "an ordinary memory")
        instance, _, _ = await self._turn(
            agent,
            admin_a,
            model_registry,
            [_tool(ABILITIES_ABILITY, "{}"), _text("ok")],
            granted=[ABILITIES_ABILITY, "list_agents"],
        )
        assert "list_agents" in memory.as_dict(agent.id)[SEARCHED_ABILITIES_KEY]
        executor = AgentTurnExecutor(
            model_registry=model_registry, requester_id=admin_a.id
        )
        subs = executor._sentience_substitutions(agent, instance, admin_a.id)
        assert "list_agents" in subs["IN_CONTEXT_ABILITIES"]
        assert SEARCHED_ABILITIES_KEY not in subs["SHORT_TERM_MEMORIES"]
        assert "an ordinary memory" in subs["SHORT_TERM_MEMORIES"]

    async def test_every_granted_tool_is_offered(self, admin_a, model_registry):
        agent = self._agent(admin_a, model_registry)
        _, _, chat = await self._turn(
            agent,
            admin_a,
            model_registry,
            [_text("nothing to do")],
            granted=[*SELF_ABILITY_SIGNATURES, "list_agents"],
        )
        offered = {t["function"]["name"] for t in chat.calls[0]["tools"]}
        assert offered == {*SELF_ABILITY_SIGNATURES, "list_agents"}

    async def test_context_prompts_are_the_system_prompt(self, admin_a, model_registry):
        from zephyrex.extensions.ai_agents.BLL_AI_Agents import (
            AgentContextPromptManager,
        )
        from zephyrex.extensions.ai_prompts.BLL_AI_Prompts import PromptManager

        agent = self._agent(admin_a, model_registry)
        prompt = PromptManager(
            requester_id=admin_a.id, model_registry=model_registry
        ).create(
            name="persona", description="", content="You are Ada. It is {CURRENT_TIME}."
        )
        AgentContextPromptManager(
            requester_id=admin_a.id, model_registry=model_registry
        ).create(agent_id=agent.id, prompt_id=prompt.id)
        _, _, chat = await self._turn(agent, admin_a, model_registry, [_text("ok")])
        system = chat.calls[0]["messages"][0]["content"]
        assert system.startswith("You are Ada. It is 20")
