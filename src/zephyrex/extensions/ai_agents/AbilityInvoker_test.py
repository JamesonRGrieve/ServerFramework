# SPDX-License-Identifier: AGPL-3.0-or-later
"""The invoker: an agent's granted extension abilities as tools.

Holes these close: a tool's schema offered the model ``requester_id`` (and
provider abilities' ``bonded_instance``), so the model chose whose
permissions a tool ran with; and abilities were resolved by name alone, so
two extensions' abilities of one name (ai's ``speak`` and the agents'
``speak``) were the same tool."""

import uuid
from typing import Optional

import pytest

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.ai_agents.AbilityInvoker import (
    NEVER_AGENT_INVOCABLE,
    AbilityAccessDenied,
    AbilityInvoker,
    ToolInvocationError,
    ability_to_tool_schema,
    coerce_arguments,
)
from zephyrex.extensions.ai_agents.BLL_AI_Agents import (
    AbilityGrant,
    AgentManager,
    tool_names,
)
from zephyrex.extensions.ai_agents.EXT_AI_Agents import EXT_AI_Agents


def grant(name: str, extension: str = "ai_agents") -> AbilityGrant:
    return AbilityGrant(ability_id=str(uuid.uuid4()), name=name, extension=extension)


class TestToolSchema:
    def test_the_requester_is_not_the_models_to_choose(self):
        schema = ability_to_tool_schema("list_agents", EXT_AI_Agents.list_agents)
        assert schema["function"]["parameters"]["properties"] == {}

    def test_parameters_and_required(self):
        schema = ability_to_tool_schema("take_turn", EXT_AI_Agents.take_turn)
        parameters = schema["function"]["parameters"]
        assert parameters["properties"] == {
            "agent_id": {"type": "string"},
            "payload": {"type": "string"},
        }
        assert parameters["required"] == ["agent_id"]

    def test_description_is_the_first_paragraph(self):
        def sample(cls, target: str, maybe: Optional[int] = None) -> None:
            """Do a sample thing.

            Detail left out."""

        function = ability_to_tool_schema("sample", sample)["function"]
        assert function["description"] == "Do a sample thing."
        assert function["parameters"]["required"] == ["target"]
        assert function["parameters"]["properties"]["maybe"] == {"type": "integer"}


class TestToolNames:
    def test_a_unique_name_is_the_tool(self):
        assert set(tool_names([grant("speak"), grant("embed", "ai")])) == {
            "speak",
            "embed",
        }

    def test_shared_names_are_qualified_by_extension(self):
        ours, theirs = grant("speak"), grant("speak", "ai")
        named = tool_names([ours, theirs])
        assert named == {"ai_agents__speak": ours, "ai__speak": theirs}


class TestArguments:
    @pytest.mark.parametrize(
        "given,expected",
        [(None, {}), ("  ", {}), ('{"q": "x"}', {"q": "x"}), ({"a": 1}, {"a": 1})],
    )
    def test_coerced(self, given, expected):
        assert coerce_arguments(given) == expected

    @pytest.mark.parametrize("given", ["{not json}", "[1, 2]"])
    def test_refused(self, given):
        with pytest.raises(ToolInvocationError):
            coerce_arguments(given)


class TestGate:
    invoker = AbilityInvoker(model_registry=None, requester_id="someone")

    def test_ungranted_is_refused(self):
        assert not self.invoker.is_allowed("list_agents", {})

    def test_granted_is_allowed(self):
        assert self.invoker.is_allowed(
            "list_agents", {"list_agents": grant("list_agents")}
        )

    @pytest.mark.parametrize("name", sorted(NEVER_AGENT_INVOCABLE))
    def test_never_invocable_even_granted(self, name):
        assert not self.invoker.is_allowed(name, {name: grant(name)})

    async def test_refused_before_anything_resolves(self):
        with pytest.raises(AbilityAccessDenied):
            await self.invoker.invoke("list_agents", {}, {})


class TestInvoking(ExtensionServerMixin):
    extension_class = EXT_AI_Agents

    def _agent(self, user, model_registry):
        return AgentManager(requester_id=user.id, model_registry=model_registry).create(
            name=f"Agent {uuid.uuid4()}"
        )

    async def test_runs_as_the_turns_requester_whatever_the_model_says(
        self, admin_a, admin_b, model_registry
    ):
        mine = self._agent(admin_a, model_registry)
        theirs = self._agent(admin_b, model_registry)
        tools = {"list_agents": grant("list_agents")}
        invoker = AbilityInvoker(model_registry=model_registry, requester_id=admin_a.id)
        listed = await invoker.invoke(
            "list_agents", {"requester_id": admin_b.id}, tools
        )
        ids = {agent["id"] for agent in listed}
        assert mine.id in ids and theirs.id not in ids

    async def test_resolves_by_extension(self, model_registry):
        invoker = AbilityInvoker(model_registry=model_registry, requester_id="x")
        assert invoker.resolve(grant("list_agents")) is not None
        assert invoker.resolve(grant("list_agents", "ai")) is None
        assert invoker.resolve(grant("no_such_ability")) is None

    def test_tools_are_the_granted_that_run(self, model_registry):
        invoker = AbilityInvoker(model_registry=model_registry, requester_id="x")
        tools = invoker.build_tools(
            {
                "list_agents": grant("list_agents"),
                "no_such_ability": grant("no_such_ability"),
                "take_turn": grant("take_turn"),
            }
        )
        assert [t["function"]["name"] for t in tools] == ["list_agents"]

    async def test_a_wrong_call_is_a_tool_error(self, admin_a, model_registry):
        invoker = AbilityInvoker(model_registry=model_registry, requester_id=admin_a.id)
        tools = {"turn_activity": grant("turn_activity")}
        with pytest.raises(ToolInvocationError, match="called wrongly"):
            await invoker.invoke("turn_activity", {"nope": 1}, tools)
        with pytest.raises(ToolInvocationError, match="refused"):
            await invoker.invoke(
                "turn_activity", {"invocation_instance_id": "missing"}, tools
            )
