"""Tests for the agent turn executor.

The LLM is the only substituted boundary: a scripted ``chat_fn`` (a real
function implementing the chat contract) drives the loop deterministically. The
loop, access gate, Activity/Message writes, and instance lifecycle all run for
real against the database.
"""

import json
import os
import uuid

import pytest

from zephyrex.extensions.ai_agents.AgentTurnExecutor import (
    ABILITIES_ABILITY,
    SEARCHED_ABILITIES_KEY,
    SELF_ABILITY_SIGNATURES,
    SPEAK_ABILITY,
    THINKING_TURN_ABILITY,
    AgentTurnExecutor,
)
from zephyrex.extensions.ai_agents.BLL_AI_Agents import (
    ActivityManager,
    ActivityState,
    AgentAbilityManager,
    AgentManager,
    InvocationInstanceManager,
)
from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.lib.Environment import env


def _text(content):
    """A chat result with a plain assistant reply and no tool calls."""
    return {
        "success": True,
        "message": {"role": "assistant", "content": content, "tool_calls": None},
        "finish_reason": "stop",
    }


def _tool(name, arguments, call_id="call_1"):
    """A chat result asking to call one tool."""
    return {
        "success": True,
        "message": {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": call_id, "name": name, "arguments": arguments}],
        },
        "finish_reason": "tool_calls",
    }


class ScriptedChat:
    """A real chat transport that replays a fixed script of responses.

    Not a mock of the executor's logic — it implements the same
    ``(messages, tools) -> chat_result`` contract a provider's chat would, so
    the loop under test runs exactly as in production.
    """

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def __call__(self, messages, tools):
        index = len(self.calls)
        self.calls.append({"messages": list(messages), "tools": tools})
        return self.responses[min(index, len(self.responses) - 1)]


class TestAgentTurnExecutor(ExtensionServerMixin):
    @pytest.fixture(scope="module")
    def server(self):
        from fastapi.testclient import TestClient

        from conftest import CORE_COMPANION_EXTENSIONS
        from zephyrex.app import instance
        from zephyrex.pydantic2.sqlalchemy import prepare_test_registry

        prepare_test_registry()
        worker_id = os.environ.get("PYTEST_XDIST_WORKER", "")
        prefix = (
            f"test.turn_executor.{worker_id}" if worker_id else "test.turn_executor"
        )
        wanted = ("ai_agents", "ai", "email", "conversations", "ai_prompts")
        names = list(wanted) + [c for c in CORE_COMPANION_EXTENSIONS if c not in wanted]
        app = instance(db_prefix=prefix, extensions=",".join(names))
        yield TestClient(app)

    # -- helpers ----------------------------------------------------------

    def _agent(self, admin_a, model_registry):
        with AgentManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as agents:
            return agents.create(name=f"Agent {uuid.uuid4()}", user_id=admin_a.id)

    def _instance(self, agent_id, admin_a, model_registry, payload="Take your turn."):
        with InvocationInstanceManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as instances:
            return instances.create(agent_id=agent_id, payload=payload)

    def _grant(self, agent_id, ability_name, admin_a, model_registry):
        # Ability seeding is not guaranteed in an isolated test registry, so
        # ensure the row exists (get-or-create) rather than depending on it.
        from zephyrex.extensions.ai_agents.AgentTurnExecutor import ensure_ability

        ability_id = ensure_ability(model_registry, ability_name)
        with AgentAbilityManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as links:
            links.create(agent_id=agent_id, ability_id=ability_id)

    def _activities(self, instance_id, admin_a, model_registry):
        with ActivityManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as activities:
            # Keyword filter (the positional Search-model form does not filter).
            return activities.list(invocation_instance_id=instance_id)

    # -- tests ------------------------------------------------------------

    async def test_silent_turn_records_root_only(self, admin_a, model_registry):
        agent = self._agent(admin_a, model_registry)
        instance = self._instance(agent.id, admin_a, model_registry)
        executor = AgentTurnExecutor(
            model_registry=model_registry,
            requester_id=admin_a.id,
            chat_fn=ScriptedChat([_text("nothing needs doing")]),
        )
        summary = await executor.run(instance.id)

        assert summary["status"] == "succeeded"
        assert summary["spoke"] is False
        acts = self._activities(instance.id, admin_a, model_registry)
        assert len(acts) == 1  # just the root thinking-turn activity
        assert acts[0].parent_id is None
        assert acts[0].body == "nothing needs doing"

        with InvocationInstanceManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as instances:
            refreshed = instances.get(id=instance.id)
        assert refreshed.status == "succeeded"
        assert refreshed.completed_at is not None

    async def test_tool_call_executes_and_records_child(self, admin_a, model_registry):
        agent = self._agent(admin_a, model_registry)
        self._grant(agent.id, "email_status", admin_a, model_registry)
        instance = self._instance(agent.id, admin_a, model_registry)
        executor = AgentTurnExecutor(
            model_registry=model_registry,
            requester_id=admin_a.id,
            chat_fn=ScriptedChat([_tool("email_status", "{}"), _text("done")]),
        )
        summary = await executor.run(instance.id)

        assert summary["status"] == "succeeded"
        assert summary["tool_calls"] == 1
        acts = self._activities(instance.id, admin_a, model_registry)
        children = [a for a in acts if a.parent_id is not None]
        assert len(children) == 1
        assert children[0].title == "email_status"
        assert children[0].state == ActivityState.SUCCESS

    async def test_speak_creates_message(self, admin_a, model_registry):
        from zephyrex.extensions.conversations.BLL_Conversations import (
            ConversationManager,
        )

        agent = self._agent(admin_a, model_registry)
        self._grant(agent.id, SPEAK_ABILITY, admin_a, model_registry)
        with ConversationManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as conversations:
            conversation = conversations.create(name=f"Chat {uuid.uuid4()}")

        instance = self._instance(agent.id, admin_a, model_registry)
        speak_args = json.dumps(
            {"message": "Hello there.", "conversation_id": conversation.id}
        )
        executor = AgentTurnExecutor(
            model_registry=model_registry,
            requester_id=admin_a.id,
            chat_fn=ScriptedChat([_tool(SPEAK_ABILITY, speak_args), _text("said it")]),
        )
        summary = await executor.run(instance.id)

        assert summary["spoke"] is True
        with ConversationManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as conversations:
            messages = conversations.messages.list(conversation_id=conversation.id)
        assert any(m.content == "Hello there." for m in messages)

        acts = self._activities(instance.id, admin_a, model_registry)
        speak_acts = [a for a in acts if a.title == "speak"]
        assert len(speak_acts) == 1
        assert speak_acts[0].state == ActivityState.SUCCESS

    async def test_ungranted_tool_is_refused_not_executed(
        self, admin_a, model_registry
    ):
        # The agent is NOT granted email_status, but the (scripted) model calls
        # it anyway — simulating a hallucinated/injected tool name. The gate must
        # refuse it and feed the refusal back, never execute it.
        agent = self._agent(admin_a, model_registry)
        instance = self._instance(agent.id, admin_a, model_registry)
        executor = AgentTurnExecutor(
            model_registry=model_registry,
            requester_id=admin_a.id,
            chat_fn=ScriptedChat([_tool("email_status", "{}"), _text("ok")]),
        )
        summary = await executor.run(instance.id)

        assert summary["status"] == "succeeded"
        acts = self._activities(instance.id, admin_a, model_registry)
        children = [a for a in acts if a.parent_id is not None]
        assert len(children) == 1
        # Recorded as a denial, in error state, and never actually run.
        assert children[0].title == "Denied: email_status"
        assert children[0].state == ActivityState.ERROR

    async def test_denylisted_tool_is_refused(self, admin_a, model_registry):
        # manage_agents is on the global never-grantable denylist; even calling
        # it directly must be refused.
        agent = self._agent(admin_a, model_registry)
        instance = self._instance(agent.id, admin_a, model_registry)
        executor = AgentTurnExecutor(
            model_registry=model_registry,
            requester_id=admin_a.id,
            chat_fn=ScriptedChat([_tool("manage_agents", "{}"), _text("ok")]),
        )
        await executor.run(instance.id)
        acts = self._activities(instance.id, admin_a, model_registry)
        children = [a for a in acts if a.parent_id is not None]
        assert len(children) == 1
        assert children[0].title == "Denied: manage_agents"

    async def test_chat_failure_marks_instance_failed(self, admin_a, model_registry):
        agent = self._agent(admin_a, model_registry)
        instance = self._instance(agent.id, admin_a, model_registry)
        executor = AgentTurnExecutor(
            model_registry=model_registry,
            requester_id=admin_a.id,
            chat_fn=ScriptedChat([{"success": False, "error": "model unavailable"}]),
        )
        summary = await executor.run(instance.id)

        assert summary["status"] == "failed"
        with InvocationInstanceManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as instances:
            refreshed = instances.get(id=instance.id)
        assert refreshed.status == "failed"
        assert refreshed.error

    # -- memory abilities -------------------------------------------------

    async def test_memorize_short_term(self, admin_a, model_registry):
        from zephyrex.extensions.ai_agents.BLL_AI_Agents import AgentMemoryManager

        agent = self._agent(admin_a, model_registry)
        self._grant(agent.id, "memorize", admin_a, model_registry)
        instance = self._instance(agent.id, admin_a, model_registry)
        args = json.dumps({"key": "fav_color", "body": "blue", "long": False})
        executor = AgentTurnExecutor(
            model_registry=model_registry,
            requester_id=admin_a.id,
            chat_fn=ScriptedChat([_tool("memorize", args), _text("noted")]),
        )
        await executor.run(instance.id)

        with AgentMemoryManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as mem:
            assert mem.as_dict(agent.id).get("fav_color") == "blue"

    async def test_memorize_long_term(self, admin_a, model_registry, tmp_path):
        from zephyrex.extensions.ai_agents.MemoryProvider import SQLiteMemoryProvider

        provider = SQLiteMemoryProvider(db_path=str(tmp_path / "ltm.db"))
        agent = self._agent(admin_a, model_registry)
        self._grant(agent.id, "memorize", admin_a, model_registry)
        instance = self._instance(agent.id, admin_a, model_registry)
        args = json.dumps({"body": "the deadline is Friday", "long": True})
        executor = AgentTurnExecutor(
            model_registry=model_registry,
            requester_id=admin_a.id,
            chat_fn=ScriptedChat([_tool("memorize", args), _text("done")]),
            memory_provider=provider,
        )
        await executor.run(instance.id)

        hits = provider.recall(agent.id, "deadline")
        assert len(hits) == 1
        assert "Friday" in hits[0]["content"]

    async def test_trim_short_term(self, admin_a, model_registry):
        from zephyrex.extensions.ai_agents.BLL_AI_Agents import AgentMemoryManager

        agent = self._agent(admin_a, model_registry)
        self._grant(agent.id, "trim", admin_a, model_registry)
        with AgentMemoryManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as mem:
            mem.remember(agent.id, "a", "1")
            mem.remember(agent.id, "b", "2")
        instance = self._instance(agent.id, admin_a, model_registry)
        args = json.dumps({"memories": ["a"]})
        executor = AgentTurnExecutor(
            model_registry=model_registry,
            requester_id=admin_a.id,
            chat_fn=ScriptedChat([_tool("trim", args), _text("trimmed")]),
        )
        await executor.run(instance.id)

        with AgentMemoryManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as mem:
            assert mem.as_dict(agent.id) == {"b": "2"}

    async def test_recall_long_term(self, admin_a, model_registry, tmp_path):
        from zephyrex.extensions.ai_agents.MemoryProvider import SQLiteMemoryProvider

        provider = SQLiteMemoryProvider(db_path=str(tmp_path / "ltm.db"))
        agent = self._agent(admin_a, model_registry)
        provider.store(agent.id, "James prefers concise answers")
        self._grant(agent.id, "recall", admin_a, model_registry)
        instance = self._instance(agent.id, admin_a, model_registry)
        args = json.dumps({"query": "concise"})
        executor = AgentTurnExecutor(
            model_registry=model_registry,
            requester_id=admin_a.id,
            chat_fn=ScriptedChat([_tool("recall", args), _text("ok")]),
            memory_provider=provider,
        )
        await executor.run(instance.id)

        acts = self._activities(instance.id, admin_a, model_registry)
        recall_acts = [a for a in acts if a.title == "recall"]
        assert len(recall_acts) == 1
        assert "concise" in recall_acts[0].body

    async def test_memorize_denied_when_not_granted(self, admin_a, model_registry):
        from zephyrex.extensions.ai_agents.BLL_AI_Agents import AgentMemoryManager

        # Not granted memorize -> the gate refuses it, nothing is written.
        agent = self._agent(admin_a, model_registry)
        instance = self._instance(agent.id, admin_a, model_registry)
        args = json.dumps({"key": "x", "body": "y"})
        executor = AgentTurnExecutor(
            model_registry=model_registry,
            requester_id=admin_a.id,
            chat_fn=ScriptedChat([_tool("memorize", args), _text("ok")]),
        )
        await executor.run(instance.id)

        acts = self._activities(instance.id, admin_a, model_registry)
        assert any(a.title == "Denied: memorize" for a in acts if a.parent_id)
        with AgentMemoryManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as mem:
            assert mem.as_dict(agent.id) == {}

    # -- discovery ability ------------------------------------------------

    async def test_abilities_discovery_lists_granted(self, admin_a, model_registry):
        # With `abilities` and a real extension ability granted, the discovery
        # tool returns both — self abilities and resolvable extension abilities.
        agent = self._agent(admin_a, model_registry)
        self._grant(agent.id, ABILITIES_ABILITY, admin_a, model_registry)
        self._grant(agent.id, "email_status", admin_a, model_registry)
        instance = self._instance(agent.id, admin_a, model_registry)
        executor = AgentTurnExecutor(
            model_registry=model_registry,
            requester_id=admin_a.id,
            chat_fn=ScriptedChat([_tool(ABILITIES_ABILITY, "{}"), _text("ok")]),
        )
        await executor.run(instance.id)

        acts = self._activities(instance.id, admin_a, model_registry)
        disco = [a for a in acts if a.title == ABILITIES_ABILITY]
        assert len(disco) == 1
        assert disco[0].state == ActivityState.SUCCESS
        # The abilities self-ability and the granted extension ability both show.
        names = {d["name"] for d in json.loads(disco[0].body.split("\n", 1)[1])}
        assert ABILITIES_ABILITY in names
        assert "email_status" in names

    async def test_abilities_discovery_search_filters(self, admin_a, model_registry):
        agent = self._agent(admin_a, model_registry)
        self._grant(agent.id, ABILITIES_ABILITY, admin_a, model_registry)
        self._grant(agent.id, "email_status", admin_a, model_registry)
        instance = self._instance(agent.id, admin_a, model_registry)
        args = json.dumps({"search": "email"})
        executor = AgentTurnExecutor(
            model_registry=model_registry,
            requester_id=admin_a.id,
            chat_fn=ScriptedChat([_tool(ABILITIES_ABILITY, args), _text("ok")]),
        )
        await executor.run(instance.id)

        acts = self._activities(instance.id, admin_a, model_registry)
        disco = [a for a in acts if a.title == ABILITIES_ABILITY][0]
        names = {d["name"] for d in json.loads(disco.body.split("\n", 1)[1])}
        assert names == {"email_status"}  # abilities/self filtered out by search

    async def test_abilities_discovery_denied_when_not_granted(
        self, admin_a, model_registry
    ):
        # Discovery is grant-gated like every other ability: an agent without the
        # `abilities` grant cannot enumerate its toolset (hard-limitability).
        agent = self._agent(admin_a, model_registry)
        instance = self._instance(agent.id, admin_a, model_registry)
        executor = AgentTurnExecutor(
            model_registry=model_registry,
            requester_id=admin_a.id,
            chat_fn=ScriptedChat([_tool(ABILITIES_ABILITY, "{}"), _text("ok")]),
        )
        await executor.run(instance.id)

        acts = self._activities(instance.id, admin_a, model_registry)
        children = [a for a in acts if a.parent_id is not None]
        assert len(children) == 1
        assert children[0].title == f"Denied: {ABILITIES_ABILITY}"
        assert children[0].state == ActivityState.ERROR

    async def test_discovered_abilities_persist_to_context(
        self, admin_a, model_registry
    ):
        from zephyrex.extensions.ai_agents.BLL_AI_Agents import AgentMemoryManager

        agent = self._agent(admin_a, model_registry)
        self._grant(agent.id, ABILITIES_ABILITY, admin_a, model_registry)
        self._grant(agent.id, "email_status", admin_a, model_registry)
        with AgentMemoryManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as mem:
            mem.remember(agent.id, "note", "an ordinary memory")
        instance = self._instance(agent.id, admin_a, model_registry)
        executor = AgentTurnExecutor(
            model_registry=model_registry,
            requester_id=admin_a.id,
            chat_fn=ScriptedChat([_tool(ABILITIES_ABILITY, "{}"), _text("ok")]),
        )
        await executor.run(instance.id)

        # The discovered set is persisted under the reserved key ...
        with AgentMemoryManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as mem:
            stored = mem.as_dict(agent.id)
        assert SEARCHED_ABILITIES_KEY in stored
        assert "email_status" in stored[SEARCHED_ABILITIES_KEY]

        # ... and surfaces as {{IN_CONTEXT_ABILITIES}} while staying out of the
        # ordinary short-term memory view.
        subs = executor._sentience_substitutions(agent, instance, admin_a.id)
        assert "email_status" in subs["IN_CONTEXT_ABILITIES"]
        assert SEARCHED_ABILITIES_KEY not in subs["SHORT_TERM_MEMORIES"]
        assert "an ordinary memory" in subs["SHORT_TERM_MEMORIES"]

    async def test_self_abilities_offered_as_native_tools(
        self, admin_a, model_registry
    ):
        # Regression: native mode previously offered only `speak`; every granted
        # self ability must appear in the tool catalog handed to the model.
        agent = self._agent(admin_a, model_registry)
        for name in SELF_ABILITY_SIGNATURES:
            self._grant(agent.id, name, admin_a, model_registry)
        instance = self._instance(agent.id, admin_a, model_registry)
        chat = ScriptedChat([_text("nothing to do")])
        executor = AgentTurnExecutor(
            model_registry=model_registry,
            requester_id=admin_a.id,
            chat_fn=chat,
        )
        await executor.run(instance.id)

        offered = {t["function"]["name"] for t in chat.calls[0]["tools"]}
        assert set(SELF_ABILITY_SIGNATURES) <= offered
