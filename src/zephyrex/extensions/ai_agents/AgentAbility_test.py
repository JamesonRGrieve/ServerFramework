"""Tests for the AgentAbility allowlist (the agent's default-deny tool grant).

Exercises the real model/manager against a live registry with seeded Ability
rows (no mocks): granting an ability, the enabled/disabled distinction, and the
``enabled_ability_names`` helper that feeds ``AbilityInvoker``'s access gate.
"""

import os
import uuid

import pytest

from zephyrex.extensions.ai_agents.BLL_AI_Agents import AgentAbilityManager, AgentManager
from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.lib.Environment import env


class TestAgentAbility(ExtensionServerMixin):
    """The agent tool allowlist, tested end-to-end against seeded abilities."""

    @pytest.fixture(scope="module")
    def server(self):
        from fastapi.testclient import TestClient

        from conftest import CORE_COMPANION_EXTENSIONS
        from zephyrex.app import instance
        from zephyrex.pydantic2.sqlalchemy import prepare_test_registry

        prepare_test_registry()
        worker_id = os.environ.get("PYTEST_XDIST_WORKER", "")
        prefix = f"test.agent_ability.{worker_id}" if worker_id else "test.agent_ability"
        wanted = ("ai_agents", "ai", "email", "conversations", "ai_prompts")
        names = list(wanted) + [
            c for c in CORE_COMPANION_EXTENSIONS if c not in wanted
        ]
        app = instance(db_prefix=prefix, extensions=",".join(names))
        yield TestClient(app)

    def _an_ability(self, model_registry, name="email_status"):
        """Fetch a real seeded Ability row by name (ROOT-scoped)."""
        from zephyrex.logic.BLL_Extensions import AbilityManager

        with AbilityManager(
            requester_id=env("ROOT_ID"), model_registry=model_registry
        ) as abilities:
            matches = abilities.list(name=name)
        assert matches, f"expected a seeded ability named {name!r}"
        return matches[0]

    def test_default_deny_no_links(self, admin_a, model_registry):
        with AgentManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as agents:
            agent = agents.create(name=f"Agent {uuid.uuid4()}")
        with AgentAbilityManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as links:
            # No grants → empty allowlist. This is the default-deny floor.
            assert links.enabled_ability_names(agent.id) == set()

    def test_grant_enables_ability_name(self, admin_a, model_registry):
        ability = self._an_ability(model_registry)
        with AgentManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as agents:
            agent = agents.create(name=f"Agent {uuid.uuid4()}")
        with AgentAbilityManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as links:
            link = links.create(agent_id=agent.id, ability_id=ability.id)
            assert link.enabled is True
            assert links.enabled_ability_names(agent.id) == {ability.name}

    def test_disabled_grant_excluded(self, admin_a, model_registry):
        ability = self._an_ability(model_registry)
        with AgentManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as agents:
            agent = agents.create(name=f"Agent {uuid.uuid4()}")
        with AgentAbilityManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as links:
            links.create(agent_id=agent.id, ability_id=ability.id, enabled=False)
            # A disabled grant confers no access.
            assert links.enabled_ability_names(agent.id) == set()

    def test_grant_unknown_ability_rejected(self, admin_a, model_registry):
        from fastapi import HTTPException

        with AgentManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as agents:
            agent = agents.create(name=f"Agent {uuid.uuid4()}")
        with AgentAbilityManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as links:
            with pytest.raises(HTTPException) as exc:
                links.create(agent_id=agent.id, ability_id="does-not-exist")
            assert exc.value.status_code == 404

    def test_grant_unknown_agent_rejected(self, admin_a, model_registry):
        from fastapi import HTTPException

        ability = self._an_ability(model_registry)
        with AgentAbilityManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as links:
            with pytest.raises(HTTPException) as exc:
                links.create(agent_id="does-not-exist", ability_id=ability.id)
            assert exc.value.status_code == 404
