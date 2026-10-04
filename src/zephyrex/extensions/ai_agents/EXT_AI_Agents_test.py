# SPDX-License-Identifier: AGPL-3.0-or-later
"""The extension's abilities, acting for the user they name.

Before this they were stand-ins: manage_agents reported zero agents,
configure_agent_providers and manage_agent_abilities said they had done what
they had not, and track_agent_activities returned no activities. Tasks
(the ai_tasks extension's) were never run by anything."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.ai_agents.AgentTurnExecutor import ensure_ability
from zephyrex.extensions.ai_agents.BLL_AI_Agents import (
    AgentAbilityManager,
    AgentManager,
)
from zephyrex.extensions.ai_agents.EXT_AI_Agents import EXT_AI_Agents
from zephyrex.extensions.ExternalErrors import InvalidInputExternalError


class TestAbilities(ExtensionServerMixin):
    extension_class = EXT_AI_Agents

    def _agent(self, user, model_registry):
        return AgentManager(requester_id=user.id, model_registry=model_registry).create(
            name=f"Agent {uuid.uuid4()}"
        )

    def test_every_ability_is_declared(self):
        assert {
            "list_agents",
            "take_turn",
            "schedule_task",
            "list_tasks",
            "cancel_task",
            "grant_ability",
            "turn_activity",
            "thinking_turn",
            "speak",
        } <= EXT_AI_Agents.abilities

    async def test_an_ability_needs_a_requester(self, server):
        with pytest.raises(HTTPException) as refused:
            await EXT_AI_Agents.list_agents("")
        assert refused.value.status_code == 400

    async def test_list_agents_is_the_users(self, admin_a, admin_b, model_registry):
        mine = self._agent(admin_a, model_registry)
        theirs = self._agent(admin_b, model_registry)
        ids = {a["id"] for a in await EXT_AI_Agents.list_agents(admin_a.id)}
        assert mine.id in ids and theirs.id not in ids

    async def test_a_one_shot_task(self, admin_a, model_registry):
        agent = self._agent(admin_a, model_registry)
        due = datetime.now(timezone.utc) + timedelta(days=1)
        task = await EXT_AI_Agents.schedule_task(
            admin_a.id, agent.id, "send the summary", due_at=due.isoformat(), priority=1
        )
        assert (task["invocation_type"], task["one_shot"], task["priority"]) == (
            "timer",
            True,
            1,
        )
        assert task["invocation_payload"] == "send the summary"
        assert datetime.fromisoformat(task["next_fire_at"]).replace(
            tzinfo=timezone.utc
        ) == due.replace(tzinfo=timezone.utc)

    async def test_a_scheduled_task_starts_after_its_due_time(
        self, admin_a, model_registry
    ):
        agent = self._agent(admin_a, model_registry)
        task = await EXT_AI_Agents.schedule_task(
            admin_a.id,
            agent.id,
            "weekly review",
            due_at="2030-01-01T00:00:00+00:00",
            cron="0 9 * * 1",
        )
        assert task["invocation_type"] == "schedule"
        assert task["next_fire_at"].startswith("2030-01-07T09:00")

    @pytest.mark.parametrize(
        "fields",
        [
            {"instructions": " ", "due_at": "2030-01-01T00:00:00"},
            {"instructions": "x"},
            {"instructions": "x", "due_at": "next tuesday"},
        ],
    )
    async def test_a_task_that_cannot_fire_is_refused(
        self, fields, admin_a, model_registry
    ):
        agent = self._agent(admin_a, model_registry)
        with pytest.raises(InvalidInputExternalError):
            await EXT_AI_Agents.schedule_task(admin_a.id, agent.id, **fields)

    async def test_a_bad_cron_is_422(self, admin_a, model_registry):
        agent = self._agent(admin_a, model_registry)
        with pytest.raises(HTTPException) as refused:
            await EXT_AI_Agents.schedule_task(
                admin_a.id, agent.id, "x", cron="not a cron"
            )
        assert refused.value.status_code == 422

    async def test_tasks_most_urgent_first_and_cancelled(self, admin_a, model_registry):
        agent = self._agent(admin_a, model_registry)
        soon = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        later = await EXT_AI_Agents.schedule_task(
            admin_a.id, agent.id, "later", due_at=soon, priority=4
        )
        urgent = await EXT_AI_Agents.schedule_task(
            admin_a.id, agent.id, "urgent", due_at=soon, priority=1
        )
        listed = await EXT_AI_Agents.list_tasks(admin_a.id, agent.id)
        assert [t["id"] for t in listed] == [urgent["id"], later["id"]]
        await EXT_AI_Agents.cancel_task(admin_a.id, urgent["id"])
        listed = await EXT_AI_Agents.list_tasks(admin_a.id, agent.id)
        assert [t["id"] for t in listed] == [later["id"]]

    async def test_no_tasks_on_someone_elses_agent(
        self, admin_a, admin_b, model_registry
    ):
        agent = self._agent(admin_a, model_registry)
        with pytest.raises(HTTPException) as refused:
            await EXT_AI_Agents.schedule_task(
                admin_b.id, agent.id, "x", due_at="2030-01-01T00:00:00"
            )
        assert refused.value.status_code in (403, 404)

    async def test_grant_and_revoke(self, admin_a, model_registry):
        agent = self._agent(admin_a, model_registry)
        ability_id = ensure_ability(model_registry, "list_agents")
        grants = AgentAbilityManager(
            requester_id=admin_a.id, model_registry=model_registry
        )
        await EXT_AI_Agents.grant_ability(admin_a.id, agent.id, ability_id)
        assert [g.name for g in grants.grants(agent.id)] == ["list_agents"]
        await EXT_AI_Agents.grant_ability(
            admin_a.id, agent.id, ability_id, enabled=False
        )
        assert grants.grants(agent.id) == []

    async def test_take_turn_and_its_activity(self, admin_a, model_registry):
        agent = self._agent(admin_a, model_registry)
        turn = await EXT_AI_Agents.take_turn(admin_a.id, agent.id, "hello")
        # No rotation to think with: the turn is recorded, and failed.
        assert (turn["status"], turn["error"]) == (
            "failed",
            "the agent has no rotation configured",
        )
        trees = await EXT_AI_Agents.turn_activity(admin_a.id, turn["id"])
        [root] = trees.values()
        assert root["activity"]["title"] == "Thinking turn"

    async def test_no_one_reads_anothers_turn(self, admin_a, admin_b, model_registry):
        agent = self._agent(admin_a, model_registry)
        turn = await EXT_AI_Agents.take_turn(admin_a.id, agent.id)
        with pytest.raises(HTTPException) as refused:
            await EXT_AI_Agents.turn_activity(admin_b.id, turn["id"])
        assert refused.value.status_code == 404
