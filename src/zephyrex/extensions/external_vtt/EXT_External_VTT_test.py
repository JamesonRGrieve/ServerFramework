# SPDX-License-Identifier: AGPL-3.0-or-later
"""external_vtt: every ability names its world and runs on that instance
alone; reads and writes are held to the document types the app follows and
changes; an agent can never write; and the Foundry provider calls the
world's foundry-mcp server's tools through mcp_client (a stand-in for the
server here: a real one needs a real Foundry), writes only when the
instance allows them, its refusals mapped to the framework's errors."""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

import pytest

from zephyrex.extensions.ai_agents.AbilityInvoker import NEVER_AGENT_INVOCABLE
from zephyrex.extensions.external_vtt.EXT_External_VTT import (
    MAX_DOCUMENTS,
    WRITE_ABILITIES,
    EXT_External_VTT,
)
from zephyrex.extensions.external_vtt.PRV_Foundry import PRV_Foundry, tool_value
from zephyrex.extensions.ExternalErrors import (
    InvalidInputExternalError,
    PermanentExternalError,
    TransientExternalError,
)
from zephyrex.lib.Environment import env


class TestTheWorldIsNamed:
    @pytest.fixture
    def routed(self, monkeypatch) -> List[Dict[str, Any]]:
        seen: List[Dict[str, Any]] = []

        async def rotate_among(cls, names, method, *args, **kwargs):
            seen.append({"names": names, "method": method, "args": args, **kwargs})
            return ["d1"] if method.endswith("documents") else {"_id": "d1"}

        monkeypatch.setattr(EXT_External_VTT, "rotate_among", classmethod(rotate_among))
        return seen

    async def test_a_call_runs_on_the_named_world_alone(self, routed):
        await EXT_External_VTT.vtt_list_documents("world-1", "Combat", as_user="u1")
        [call] = routed
        assert call["instance_ids"] == ["world-1"]
        assert call["as_user"] == "u1"
        assert call["method"] == "list_documents"
        assert call["names"] == ["foundry"]

    async def test_without_a_world_nothing_runs(self, routed):
        with pytest.raises(InvalidInputExternalError, match="world_id"):
            await EXT_External_VTT.vtt_status("")
        assert routed == []

    @pytest.mark.parametrize(
        "call",
        [
            lambda: EXT_External_VTT.vtt_list_documents("w", "Macro"),
            lambda: EXT_External_VTT.vtt_get_document("w", "Macro.abc"),
            lambda: EXT_External_VTT.vtt_list_embedded("w", "Scene.s", "Wall"),
            lambda: EXT_External_VTT.vtt_create_documents("w", "Actor", [{}]),
            lambda: EXT_External_VTT.vtt_update_documents("w", "Actor", [{"_id": "a"}]),
            lambda: EXT_External_VTT.vtt_update_documents(
                "w", "Token", [{"_id": "t", "x": 1}]
            ),
            lambda: EXT_External_VTT.vtt_update_documents(
                "w", "Combat", [{"round": 2}]
            ),
            lambda: EXT_External_VTT.vtt_create_documents("w", "ChatMessage", []),
            lambda: EXT_External_VTT.vtt_create_documents(
                "w", "ChatMessage", [{}] * (MAX_DOCUMENTS + 1)
            ),
        ],
        ids=[
            "unread-type",
            "unread-uuid",
            "unread-embedded",
            "uncreatable",
            "unupdatable",
            "token-without-parent",
            "update-without-id",
            "empty",
            "too-many",
        ],
    )
    async def test_what_the_app_may_not_ask_is_refused_first(self, routed, call):
        with pytest.raises(InvalidInputExternalError):
            await call()
        assert routed == []

    async def test_the_tables_writes_are_allowed(self, routed):
        await EXT_External_VTT.vtt_create_documents(
            "w", "ChatMessage", [{"content": "The door groans."}]
        )
        await EXT_External_VTT.vtt_update_documents(
            "w", "Token", [{"_id": "t1", "x": 100}], parent_uuid="Scene.s1"
        )
        await EXT_External_VTT.vtt_update_documents("w", "Combat", [{"_id": "c1"}])
        assert [c["method"] for c in routed] == [
            "create_documents",
            "update_documents",
            "update_documents",
        ]


def test_an_agent_can_never_approve_a_write():
    """Agents may call the writes (an AI campaign's GM is one), but never
    name who approved one: in assistant mode only the app, after the GM's
    tap, can."""
    from zephyrex.extensions.ai_agents.AbilityInvoker import (
        SERVER_ONLY,
        ability_to_tool_schema,
    )

    assert not WRITE_ABILITIES & NEVER_AGENT_INVOCABLE
    assert "approved_by" in SERVER_ONLY
    for name in WRITE_ABILITIES:
        schema = ability_to_tool_schema(name, getattr(EXT_External_VTT, name))
        assert "approved_by" not in schema["function"]["parameters"]["properties"]


async def test_an_approved_write_runs_as_the_approver_on_the_world(monkeypatch):
    seen: List[Dict[str, Any]] = []

    async def rotate_among(cls, names, method, *args, **kwargs):
        seen.append({"method": method, "args": args, **kwargs})
        return ["d1"]

    monkeypatch.setattr(EXT_External_VTT, "rotate_among", classmethod(rotate_among))
    await EXT_External_VTT.vtt_create_documents(
        "w", "ChatMessage", [{"content": "x"}], approved_by="gm-1"
    )
    await EXT_External_VTT.vtt_update_documents("w", "Combat", [{"_id": "c1"}])
    approved, unapproved = seen
    assert approved["as_user"] == "gm-1" and approved["args"][-1] is True
    assert unapproved["as_user"] is None and unapproved["args"][-1] is False


class TestFoundry:
    @pytest.fixture
    def server(self, monkeypatch) -> List[Tuple[str, str, str, Dict[str, Any]]]:
        """A stand-in foundry-mcp server, answering as mcp_client relays a
        tool's result."""
        from zephyrex.extensions.mcp_client.EXT_MCPClient import EXT_MCPClient

        calls: List[Tuple[str, str, str, Dict[str, Any]]] = []
        answers: Dict[str, Dict[str, Any]] = {
            "foundry_status": {"structured_content": {"title": "Solenne"}},
            "list_documents": {"structured_content": {"result": [{"_id": "c1"}]}},
            "get_document": {
                "structured_content": None,
                "content": [{"type": "text", "text": '{"_id": "a1"}'}],
            },
            "create_documents": {"structured_content": {"result": ["m1"]}},
            "update_documents": {"structured_content": {"result": ["t1"]}},
        }

        async def call_tool(cls, requester_id, server, tool, arguments=None):
            calls.append((requester_id, server, tool, arguments or {}))
            return {"is_error": False, "content": [], **answers[tool]}

        monkeypatch.setattr(EXT_MCPClient, "call_tool", classmethod(call_tool))
        return calls

    def _world(self, provider_instance: Any, **settings: str) -> Any:
        return provider_instance(
            PRV_Foundry, settings={"mcp_server": "foundry-solenne", **settings}
        )

    async def test_an_unconfigured_world_is_passed_over(
        self, provider_instance, server
    ):
        with pytest.raises(TransientExternalError, match="mcp_server"):
            await PRV_Foundry.status(provider_instance(PRV_Foundry, settings={}))
        assert server == []

    async def test_reads_are_the_servers_tools(self, provider_instance, server):
        instance = self._world(provider_instance)
        assert (await PRV_Foundry.status(instance)) == {
            "title": "Solenne",
            "writes": False,
            "mode": "assistant",
        }
        assert await PRV_Foundry.list_documents(instance, "Combat", "", ["round"]) == [
            {"_id": "c1"}
        ]
        assert await PRV_Foundry.get_document(instance, "Actor.a1", False) == {
            "_id": "a1"
        }
        assert [(c[1], c[2], c[3]) for c in server] == [
            ("foundry-solenne", "foundry_status", {}),
            (
                "foundry-solenne",
                "list_documents",
                {"document_type": "Combat", "name_filter": "", "fields": ["round"]},
            ),
            (
                "foundry-solenne",
                "get_document",
                {"uuid": "Actor.a1", "embedded": False},
            ),
        ]
        assert {c[0] for c in server} == {env("ROOT_ID")}

    async def test_writes_need_the_instance_to_allow_them(
        self, provider_instance, server
    ):
        with pytest.raises(PermanentExternalError, match="allow_writes"):
            await PRV_Foundry.create_documents(
                self._world(provider_instance), "ChatMessage", [{"content": "x"}], None
            )
        assert server == []

    async def test_an_approved_write_is_the_servers_tool(
        self, provider_instance, server
    ):
        instance = self._world(provider_instance, allow_writes="true")
        assert await PRV_Foundry.create_documents(
            instance, "ChatMessage", [{"content": "The door groans."}], None, True
        ) == ["m1"]
        assert await PRV_Foundry.update_documents(
            instance, "Token", [{"_id": "t1", "x": 5}], "Scene.s1", True
        ) == ["t1"]
        assert [(c[2], c[3]) for c in server] == [
            (
                "create_documents",
                {
                    "document_type": "ChatMessage",
                    "docs": [{"content": "The door groans."}],
                    "parent_uuid": None,
                },
            ),
            (
                "update_documents",
                {
                    "document_type": "Token",
                    "updates": [{"_id": "t1", "x": 5}],
                    "parent_uuid": "Scene.s1",
                },
            ),
        ]

    async def test_in_assistant_mode_a_write_waits_for_its_gm(
        self, provider_instance, server
    ):
        instance = self._world(provider_instance, allow_writes="true")
        assert (await PRV_Foundry.status(instance))["mode"] == "assistant"
        with pytest.raises(PermanentExternalError, match="assistant mode"):
            await PRV_Foundry.create_documents(
                instance, "ChatMessage", [{"content": "x"}], None
            )
        assert [c[2] for c in server] == ["foundry_status"]

    async def test_in_campaign_mode_the_ai_gm_writes_freely(
        self, provider_instance, server
    ):
        instance = self._world(provider_instance, allow_writes="true", mode="campaign")
        assert await PRV_Foundry.update_documents(
            instance, "Combat", [{"_id": "k1", "turn": 1}], None
        ) == ["t1"]

    async def test_campaign_mode_still_needs_writes_on(self, provider_instance, server):
        instance = self._world(provider_instance, mode="campaign")
        with pytest.raises(PermanentExternalError, match="allow_writes"):
            await PRV_Foundry.update_documents(
                instance, "Combat", [{"_id": "k1"}], None, True
            )
        assert server == []


def _error(text: str) -> Dict[str, Any]:
    return {"is_error": True, "content": [{"type": "text", "text": text}]}


@pytest.mark.parametrize(
    "result, mapped",
    [
        (_error("Needs FOUNDRY_ALLOW_WRITES"), PermanentExternalError),
        (_error("the world did not answer"), TransientExternalError),
    ],
    ids=["server-read-only", "world-silent"],
)
def test_a_tools_error_is_the_frameworks(result, mapped):
    with pytest.raises(mapped):
        tool_value(result)


def test_a_tools_value():
    assert tool_value({"structured_content": {"result": [1]}}) == [1]
    assert tool_value({"structured_content": {"a": 1}}) == {"a": 1}
    assert tool_value(
        {"structured_content": None, "content": [{"type": "text", "text": "[2]"}]}
    ) == [2]
