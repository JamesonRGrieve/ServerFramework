# SPDX-License-Identifier: AGPL-3.0-or-later
"""The agent's ability grants: its default-deny tool allowlist."""

import uuid

import pytest
from fastapi import HTTPException

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.ai_agents.AgentTurnExecutor import ensure_ability
from zephyrex.extensions.ai_agents.BLL_AI_Agents import (
    AbilityGrant,
    AgentAbilityManager,
    AgentManager,
)
from zephyrex.extensions.ai_agents.EXT_AI_Agents import EXT_AI_Agents


class TestAgentAbility(ExtensionServerMixin):
    extension_class = EXT_AI_Agents

    def _agent(self, user, model_registry):
        return AgentManager(requester_id=user.id, model_registry=model_registry).create(
            name=f"Agent {uuid.uuid4()}"
        )

    def _grants(self, user, model_registry):
        return AgentAbilityManager(requester_id=user.id, model_registry=model_registry)

    def test_no_grants_no_tools(self, admin_a, model_registry):
        agent = self._agent(admin_a, model_registry)
        assert self._grants(admin_a, model_registry).grants(agent.id) == []

    def test_a_grant_names_its_ability_and_extension(self, admin_a, model_registry):
        agent = self._agent(admin_a, model_registry)
        ability_id = ensure_ability(model_registry, "list_agents")
        grants = self._grants(admin_a, model_registry)
        link = grants.create(agent_id=agent.id, ability_id=ability_id)
        assert link.enabled is True
        assert grants.grants(agent.id) == [
            AbilityGrant(
                ability_id=ability_id, name="list_agents", extension="ai_agents"
            )
        ]

    def test_a_disabled_grant_grants_nothing(self, admin_a, model_registry):
        agent = self._agent(admin_a, model_registry)
        grants = self._grants(admin_a, model_registry)
        grants.create(
            agent_id=agent.id,
            ability_id=ensure_ability(model_registry, "list_agents"),
            enabled=False,
        )
        assert grants.grants(agent.id) == []

    @pytest.mark.parametrize("missing", ["agent_id", "ability_id"])
    def test_a_missing_reference_is_404(self, missing, admin_a, model_registry):
        fields = {
            "agent_id": self._agent(admin_a, model_registry).id,
            "ability_id": ensure_ability(model_registry, "list_agents"),
            missing: "does-not-exist",
        }
        with pytest.raises(HTTPException) as refused:
            self._grants(admin_a, model_registry).create(**fields)
        assert refused.value.status_code == 404

    def test_only_who_may_edit_the_agent_grants(self, admin_a, admin_b, model_registry):
        agent = self._agent(admin_a, model_registry)
        with pytest.raises(HTTPException) as refused:
            self._grants(admin_b, model_registry).create(
                agent_id=agent.id,
                ability_id=ensure_ability(model_registry, "list_agents"),
            )
        assert refused.value.status_code in (403, 404)
        assert self._grants(admin_a, model_registry).grants(agent.id) == []
