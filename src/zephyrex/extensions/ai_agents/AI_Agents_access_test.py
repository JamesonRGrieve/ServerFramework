# SPDX-License-Identifier: AGPL-3.0-or-later
"""Who may do what with agents, their configuration, turns and projects.

Holes these close: a caller could name another user as an agent's or a
project's owner (singly or in a batch) and move a project to another team;
anyone could hang triggers, turns, grants, memories, seats and context
prompts on any agent whose id they knew, since references were checked as
SYSTEM; an agent could be pointed at someone else's rotation, prompt,
conversation or provider instance; a user could write a turn's lifecycle
and a trigger's bookkeeping; a trigger could carry a cron that never fires;
a child activity could hang in another turn; and a turn's activity tree
was answered (empty) for turns the caller could not see."""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

import pytest
from fastapi import HTTPException

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.ai_agents.AgentTurnExecutor import ensure_ability
from zephyrex.extensions.ai_agents.BLL_AI_Agents import (
    ActivityManager,
    AgentAbilityManager,
    AgentContextPromptManager,
    AgentManager,
    ConversationAgentManager,
    InvocationInstanceManager,
    InvocationTriggerManager,
    ProjectManager,
    ProviderInstanceAgentManager,
)
from zephyrex.extensions.ai_agents.EXT_AI_Agents import EXT_AI_Agents
from zephyrex.extensions.ai_agents.SVC_AI_Agents import as_utc
from zephyrex.lib.Environment import env


def auth(user) -> Dict[str, str]:
    return {"Authorization": f"Bearer {user.jwt}"}


def refused(status_codes, call, *args, **kwargs) -> None:
    with pytest.raises(HTTPException) as caught:
        call(*args, **kwargs)
    assert caught.value.status_code in status_codes, caught.value.detail


class TestAgentAccess(ExtensionServerMixin):
    extension_class = EXT_AI_Agents

    def _m(self, manager_class, user, model_registry) -> Any:
        requester = user if isinstance(user, str) else user.id
        return manager_class(requester_id=requester, model_registry=model_registry)

    def _agent(self, user, model_registry, **fields):
        return self._m(AgentManager, user, model_registry).create(
            name=f"Agent {uuid.uuid4()}", **fields
        )

    def test_the_owner_is_the_creator(self, admin_a, admin_b, model_registry):
        agent = self._agent(admin_b, model_registry, user_id=admin_a.id)
        assert agent.user_id == admin_b.id
        batch = self._m(AgentManager, admin_b, model_registry).create(
            entities=[{"name": "one", "user_id": admin_a.id}, {"name": "two"}]
        )
        assert {a.user_id for a in batch} == {admin_b.id}

    def test_root_may_name_the_owner(self, admin_a, model_registry):
        agent = self._agent(env("ROOT_ID"), model_registry, user_id=admin_a.id)
        assert agent.user_id == admin_a.id

    def test_an_agent_thinks_with_a_rotation_its_owner_sees(
        self, admin_a, admin_b, model_registry
    ):
        from zephyrex.logic.BLL_Providers import RotationManager

        theirs = self._m(RotationManager, admin_b, model_registry).create(
            name=f"Rotation {uuid.uuid4()}"
        )
        refused({404}, self._agent, admin_a, model_registry, rotation_id=theirs.id)
        agent = self._agent(admin_a, model_registry)
        refused(
            {404},
            self._m(AgentManager, admin_a, model_registry).update,
            agent.id,
            rotation_id=theirs.id,
        )

    def test_no_one_else_configures_an_agent(self, admin_a, admin_b, model_registry):
        agent = self._agent(admin_a, model_registry)
        for manager_class, fields in (
            (
                InvocationTriggerManager,
                {"invocation_type": "timer", "interval_seconds": 60},
            ),
            (InvocationInstanceManager, {}),
            (
                AgentAbilityManager,
                {"ability_id": ensure_ability(model_registry, "list_agents")},
            ),
        ):
            refused(
                {403, 404},
                self._m(manager_class, admin_b, model_registry).create,
                agent_id=agent.id,
                **fields,
            )
            assert (
                self._m(manager_class, admin_b, model_registry).list(agent_id=agent.id)
                == []
            )

    def test_configuration_is_seen_through_the_agent(
        self, admin_a, admin_b, model_registry
    ):
        agent = self._agent(admin_a, model_registry)
        trigger = self._m(InvocationTriggerManager, admin_a, model_registry).create(
            agent_id=agent.id, invocation_type="timer", interval_seconds=60
        )
        refused(
            {404},
            self._m(InvocationTriggerManager, admin_b, model_registry).get,
            id=trigger.id,
        )
        assert self._m(InvocationTriggerManager, admin_a, model_registry).get(
            id=trigger.id
        )

    def test_a_context_prompt_is_one_the_user_sees(
        self, admin_a, admin_b, model_registry
    ):
        from zephyrex.extensions.ai_prompts.BLL_AI_Prompts import PromptManager

        theirs = self._m(PromptManager, admin_b, model_registry).create(
            name="private", description="", content="secret instructions"
        )
        agent = self._agent(admin_a, model_registry)
        refused(
            {404},
            self._m(AgentContextPromptManager, admin_a, model_registry).create,
            agent_id=agent.id,
            prompt_id=theirs.id,
        )

    def test_an_agent_sits_only_where_its_user_can_see(
        self, admin_a, admin_b, model_registry
    ):
        from zephyrex.extensions.conversations.BLL_Conversations import (
            ConversationManager,
        )

        theirs = self._m(ConversationManager, admin_b, model_registry).create(
            name="Not yours"
        )
        agent = self._agent(admin_a, model_registry)
        refused(
            {404},
            self._m(ConversationAgentManager, admin_a, model_registry).create,
            agent_id=agent.id,
            conversation_id=theirs.id,
        )

    def test_a_provider_instance_link_is_to_one_the_user_sees(
        self, admin_a, admin_b, model_registry
    ):
        from zephyrex.logic.BLL_Providers import (
            ProviderInstanceManager,
            ProviderManager,
        )

        provider = self._m(ProviderManager, env("ROOT_ID"), model_registry).create(
            name=f"provider_{uuid.uuid4().hex[:8]}", friendly_name="Test provider"
        )
        theirs = self._m(ProviderInstanceManager, admin_b, model_registry).create(
            name="Their instance", provider_id=provider.id
        )
        agent = self._agent(admin_a, model_registry)
        refused(
            {404},
            self._m(ProviderInstanceAgentManager, admin_a, model_registry).create,
            agent_id=agent.id,
            provider_instance_id=theirs.id,
        )

    def test_a_turns_lifecycle_is_the_servers(self, admin_a, model_registry):
        agent = self._agent(admin_a, model_registry)
        instances = self._m(InvocationInstanceManager, admin_a, model_registry)
        turn = instances.create(agent_id=agent.id, status="succeeded")
        assert turn.status == "pending"
        updated = instances.update(turn.id, status="succeeded", payload="changed")
        assert (updated.status, updated.payload) == ("pending", "changed")

    def test_a_turn_is_its_triggers_agents(self, admin_a, model_registry):
        first, second = (self._agent(admin_a, model_registry) for _ in range(2))
        trigger = self._m(InvocationTriggerManager, admin_a, model_registry).create(
            agent_id=first.id, invocation_type="timer", interval_seconds=60
        )
        refused(
            {400},
            self._m(InvocationInstanceManager, admin_a, model_registry).create,
            agent_id=second.id,
            invocation_trigger_id=trigger.id,
        )

    def test_trigger_bookkeeping_is_the_monitors(self, admin_a, model_registry):
        agent = self._agent(admin_a, model_registry)
        triggers = self._m(InvocationTriggerManager, admin_a, model_registry)
        trigger = triggers.create(
            agent_id=agent.id,
            invocation_type="timer",
            interval_seconds=60,
            fire_count=9,
        )
        assert trigger.fire_count == 0
        updated = triggers.update(
            trigger.id, fire_count=9, next_fire_at=datetime(2001, 1, 1)
        )
        assert (updated.fire_count, updated.next_fire_at) == (0, None)

    @pytest.mark.parametrize(
        "fields",
        [
            {"invocation_type": "schedule", "cron": "every tuesday"},
            {"invocation_type": "schedule"},
            {"invocation_type": "timer"},
            {"invocation_type": "timer", "due_at": datetime(2030, 1, 1)},
            {"invocation_type": "event"},
        ],
    )
    def test_a_trigger_that_cannot_fire_is_422(self, fields, admin_a, model_registry):
        agent = self._agent(admin_a, model_registry)
        refused(
            {422},
            self._m(InvocationTriggerManager, admin_a, model_registry).create,
            agent_id=agent.id,
            **fields,
        )

    def test_due_at_is_the_first_firing(self, admin_a, model_registry):
        agent = self._agent(admin_a, model_registry)
        triggers = self._m(InvocationTriggerManager, admin_a, model_registry)
        due = datetime.now(timezone.utc) + timedelta(days=2)
        task = triggers.create(
            agent_id=agent.id, invocation_type="timer", one_shot=True, due_at=due
        )
        assert as_utc(task.next_fire_at) == due
        later = due + timedelta(days=1)
        assert as_utc(triggers.update(task.id, due_at=later).next_fire_at) == later
        refused({422}, triggers.update, task.id, one_shot=False)

    def test_a_triggers_team_is_its_agents(
        self, admin_a, team_a, admin_b, team_b, model_registry
    ):
        agent = self._agent(admin_a, model_registry, team_id=team_a.id)
        trigger = self._m(InvocationTriggerManager, admin_a, model_registry).create(
            agent_id=agent.id,
            invocation_type="timer",
            interval_seconds=60,
            team_id=team_b.id,
        )
        assert trigger.team_id == team_a.id

    def _turn(self, user, model_registry):
        agent = self._agent(user, model_registry)
        return self._m(InvocationInstanceManager, user, model_registry).create(
            agent_id=agent.id
        )

    def test_an_activity_is_in_its_parents_turn(self, admin_a, model_registry):
        activities = self._m(ActivityManager, admin_a, model_registry)
        ability_id = ensure_ability(model_registry, "thinking_turn")
        one, other = self._turn(admin_a, model_registry), self._turn(
            admin_a, model_registry
        )
        root = activities.create(
            invocation_instance_id=one.id, ability_id=ability_id, title="t", body="b"
        )
        refused(
            {400},
            activities.create,
            invocation_instance_id=other.id,
            parent_id=root.id,
            ability_id=ability_id,
            title="t",
            body="b",
        )

    def test_activities_are_seen_through_their_turn(
        self, admin_a, admin_b, model_registry
    ):
        turn = self._turn(admin_a, model_registry)
        ability_id = ensure_ability(model_registry, "thinking_turn")
        refused(
            {403, 404},
            self._m(ActivityManager, admin_b, model_registry).create,
            invocation_instance_id=turn.id,
            ability_id=ability_id,
            title="planted",
            body="b",
        )
        self._m(ActivityManager, admin_a, model_registry).create(
            invocation_instance_id=turn.id, ability_id=ability_id, title="t", body="b"
        )
        assert (
            self._m(ActivityManager, admin_b, model_registry).list(
                invocation_instance_id=turn.id
            )
            == []
        )
        refused(
            {404}, self._m(ActivityManager, admin_b, model_registry).hierarchy, turn.id
        )

    def test_a_projects_owner_and_team_stay(
        self, admin_a, admin_b, team_b, model_registry
    ):
        projects = self._m(ProjectManager, admin_b, model_registry)
        project = projects.create(name="Plans", user_id=admin_a.id)
        assert project.user_id == admin_b.id
        moved = projects.update(project.id, team_id=team_b.id, user_id=admin_a.id)
        assert (moved.user_id, moved.team_id) == (admin_b.id, None)

    def test_a_projects_parent_is_one_the_user_sees(
        self, admin_a, admin_b, model_registry
    ):
        theirs = self._m(ProjectManager, admin_b, model_registry).create(name="Theirs")
        refused(
            {404},
            self._m(ProjectManager, admin_a, model_registry).create,
            name="Mine",
            parent_id=theirs.id,
        )

    def test_turn_and_abilities_routes(self, server, admin_a, model_registry):
        agent = self._agent(admin_a, model_registry)
        AgentAbilityManager(
            requester_id=admin_a.id, model_registry=model_registry
        ).create(
            agent_id=agent.id, ability_id=ensure_ability(model_registry, "list_agents")
        )
        listed = server.get(f"/v1/agent/{agent.id}/abilities", headers=auth(admin_a))
        assert listed.status_code == 200, listed.text
        assert [(a["tool"], a["extension"]) for a in listed.json()["abilities"]] == [
            ("list_agents", "ai_agents")
        ]
        turned = server.post(
            f"/v1/agent/{agent.id}/turn", json={"payload": "hi"}, headers=auth(admin_a)
        )
        assert turned.status_code == 200, turned.text
        assert turned.json()["status"] == "failed"  # no rotation to think with
        hierarchy = server.get(
            f"/v1/activity/hierarchy/{turned.json()['id']}", headers=auth(admin_a)
        )
        assert hierarchy.status_code == 200, hierarchy.text
        assert len(hierarchy.json()["activities"]) == 1

    def test_no_one_runs_anothers_agent(self, server, admin_a, admin_b, model_registry):
        agent = self._agent(admin_a, model_registry)
        response = server.post(
            f"/v1/agent/{agent.id}/turn", json={}, headers=auth(admin_b)
        )
        assert response.status_code in (403, 404), response.text
        assert (
            self._m(InvocationInstanceManager, admin_a, model_registry).list(
                agent_id=agent.id
            )
            == []
        )


class TestAgentHooks(ExtensionServerMixin):
    extension_class = EXT_AI_Agents

    def test_a_new_team_gets_its_creators_agent(self, admin_a, model_registry):
        from zephyrex.logic.BLL_Auth import TeamManager

        team = TeamManager(
            requester_id=admin_a.id, model_registry=model_registry
        ).create(name=f"Team {uuid.uuid4()}", description="d")
        [agent] = AgentManager(
            requester_id=admin_a.id, model_registry=model_registry
        ).list(team_id=team.id)
        assert (agent.name, agent.user_id, agent.favourite) == (
            f"{team.name} Agent",
            admin_a.id,
            True,
        )

    def test_a_new_conversation_seats_a_team_agent(
        self, admin_a, team_a, model_registry
    ):
        from zephyrex.extensions.conversations.BLL_Conversations import (
            ConversationManager,
        )
        from zephyrex.logic.BLL_Providers import RotationManager

        rotation = RotationManager(
            requester_id=admin_a.id, model_registry=model_registry
        ).create(name=f"Rotation {uuid.uuid4()}")
        agent = AgentManager(
            requester_id=admin_a.id, model_registry=model_registry
        ).create(name="Team agent", team_id=team_a.id, rotation_id=rotation.id)
        conversation = ConversationManager(
            requester_id=admin_a.id, model_registry=model_registry
        ).create(name="Chat")
        seats = ConversationAgentManager(
            requester_id=admin_a.id, model_registry=model_registry
        ).list(conversation_id=conversation.id)
        assert [(s.agent_id, s.active, s.auto_respond) for s in seats] == [
            (agent.id, True, True)
        ]
