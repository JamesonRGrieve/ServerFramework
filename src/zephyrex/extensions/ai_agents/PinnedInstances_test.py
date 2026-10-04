# SPDX-License-Identifier: AGPL-3.0-or-later
"""An agent pinned to provider instances thinks only on them.

Each turn runs the real executor over real provider instances of the AI
extension's OpenAI-compatible provider, which call a real local HTTP server
(one per test, one path per instance) answering as a chat model would; which
paths were called shows which instances the turn thought on.

Holes these close: ProviderInstanceAgent and ProviderInstanceAgentAbility
rows were stored and never read, so an agent "pinned" to an instance still
thought on its rotation, a disabled or deleted instance was never skipped,
and a restriction (which named no ability) restricted nothing."""

import json
import uuid
from typing import Any, Callable, Dict, List

import pytest
from fastapi import HTTPException

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.ai_agents.AgentTurnExecutor import (
    AgentTurnExecutor,
    ensure_ability,
)
from zephyrex.extensions.ai_agents.BLL_AI_Agents import (
    AgentManager,
    InvocationInstanceManager,
    ProviderInstanceAgentAbilityManager,
    ProviderInstanceAgentManager,
)
from zephyrex.extensions.ai_agents.EXT_AI_Agents import EXT_AI_Agents
from zephyrex.lib.Environment import env
from zephyrex.logic.BLL_Providers import (
    ProviderInstanceManager,
    ProviderInstanceSettingManager,
    ProviderManager,
    RotationManager,
    RotationProviderInstanceManager,
)

PROVIDER = "openai_compatible"
CHAT_PATH = "/v1/chat/completions"


def chat_reply(content: str) -> Callable[[Any], Any]:
    """A route answering every chat request as a model saying ``content``."""

    def answer(_request: Any) -> Any:
        body = {
            "model": "test-model",
            "choices": [
                {
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2},
        }
        return 200, {"Content-Type": "application/json"}, json.dumps(body).encode()

    return answer


class ModelServer:
    """One local server; each instance made on it answers on its own path
    and says its own name."""

    def __init__(self, start: Callable[..., Any], names: List[str]) -> None:
        self.server = start(
            {f"/{name}{CHAT_PATH}": chat_reply(f"from {name}") for name in names}
        )

    def base_url(self, name: str) -> str:
        return f"{self.server.base_url}/{name}/v1"

    def called(self) -> List[str]:
        return [request.path.split("/")[1] for request in self.server.requests]


def model_instance(model_registry: Any, owner: Any, base_url: str) -> Any:
    """An OpenAI-compatible chat instance of ``owner``'s at ``base_url``."""
    root = env("ROOT_ID")
    provider = ProviderManager(requester_id=root, model_registry=model_registry).get(
        name=PROVIDER
    )
    instance = ProviderInstanceManager(
        requester_id=owner.id, model_registry=model_registry
    ).create(
        name=f"model {uuid.uuid4().hex}",
        provider_id=provider.id,
        model_name="test-model",
    )
    ProviderInstanceSettingManager(
        requester_id=root, model_registry=model_registry
    ).create(provider_instance_id=instance.id, key="base_url", value=base_url)
    return instance


class TestPinnedInstances(ExtensionServerMixin):
    extension_class = EXT_AI_Agents

    @pytest.fixture
    def models(self, local_http_server: Any) -> Callable[[List[str]], ModelServer]:
        return lambda names: ModelServer(local_http_server, names)

    def _agent(self, owner: Any, model_registry: Any, **fields: Any) -> Any:
        return AgentManager(
            requester_id=owner.id, model_registry=model_registry
        ).create(name=f"Agent {uuid.uuid4()}", **fields)

    def _pin(self, agent: Any, instance: Any, owner: Any, model_registry: Any) -> None:
        ProviderInstanceAgentManager(
            requester_id=owner.id, model_registry=model_registry
        ).create(agent_id=agent.id, provider_instance_id=instance.id)

    async def _turn(
        self, agent: Any, owner: Any, model_registry: Any
    ) -> Dict[str, Any]:
        instance = InvocationInstanceManager(
            requester_id=owner.id, model_registry=model_registry
        ).create(agent_id=agent.id, payload="Say who you are.")
        summary: Dict[str, Any] = await AgentTurnExecutor(
            model_registry=model_registry, requester_id=owner.id
        ).run(instance.id)
        turn = InvocationInstanceManager(
            requester_id=owner.id, model_registry=model_registry
        ).get(id=instance.id)
        return {**summary, "turn": turn}

    def _rotation_over(self, instance: Any, owner: Any, model_registry: Any) -> Any:
        rotation = RotationManager(
            requester_id=owner.id, model_registry=model_registry
        ).create(name=f"Rotation {uuid.uuid4()}")
        RotationProviderInstanceManager(
            requester_id=owner.id, model_registry=model_registry
        ).create(rotation_id=rotation.id, provider_instance_id=instance.id)
        return rotation

    async def test_without_pins_the_rotation_thinks(
        self, admin_a, model_registry, models
    ):
        server = models(["rotated"])
        rotated = model_instance(model_registry, admin_a, server.base_url("rotated"))
        rotation = self._rotation_over(rotated, admin_a, model_registry)
        agent = self._agent(admin_a, model_registry, rotation_id=rotation.id)
        outcome = await self._turn(agent, admin_a, model_registry)
        assert outcome["status"] == "succeeded", outcome
        assert server.called() == ["rotated"]

    async def test_a_pinned_agent_thinks_on_its_instance_not_its_rotation(
        self, admin_a, model_registry, models
    ):
        server = models(["rotated", "pinned"])
        rotated = model_instance(model_registry, admin_a, server.base_url("rotated"))
        pinned = model_instance(model_registry, admin_a, server.base_url("pinned"))
        rotation = self._rotation_over(rotated, admin_a, model_registry)
        agent = self._agent(admin_a, model_registry, rotation_id=rotation.id)
        self._pin(agent, pinned, admin_a, model_registry)
        outcome = await self._turn(agent, admin_a, model_registry)
        assert outcome["status"] == "succeeded", outcome
        assert server.called() == ["pinned"]

    async def test_a_disabled_pinned_instance_is_skipped(
        self, admin_a, model_registry, models
    ):
        server = models(["off", "on"])
        off = model_instance(model_registry, admin_a, server.base_url("off"))
        on = model_instance(model_registry, admin_a, server.base_url("on"))
        agent = self._agent(admin_a, model_registry)
        self._pin(agent, off, admin_a, model_registry)
        self._pin(agent, on, admin_a, model_registry)
        ProviderInstanceManager(
            requester_id=admin_a.id, model_registry=model_registry
        ).update(off.id, enabled=False)
        outcome = await self._turn(agent, admin_a, model_registry)
        assert outcome["status"] == "succeeded", outcome
        assert server.called() == ["on"]

    async def test_a_deleted_pinned_instance_is_skipped(
        self, admin_a, model_registry, models
    ):
        server = models(["gone", "kept"])
        gone = model_instance(model_registry, admin_a, server.base_url("gone"))
        kept = model_instance(model_registry, admin_a, server.base_url("kept"))
        agent = self._agent(admin_a, model_registry)
        self._pin(agent, gone, admin_a, model_registry)
        self._pin(agent, kept, admin_a, model_registry)
        ProviderInstanceManager(
            requester_id=admin_a.id, model_registry=model_registry
        ).delete(gone.id)
        outcome = await self._turn(agent, admin_a, model_registry)
        assert outcome["status"] == "succeeded", outcome
        assert server.called() == ["kept"]

    async def test_with_no_usable_pinned_instance_the_turn_fails_clearly(
        self, admin_a, model_registry, models
    ):
        server = models(["off", "rotated"])
        off = model_instance(model_registry, admin_a, server.base_url("off"))
        rotated = model_instance(model_registry, admin_a, server.base_url("rotated"))
        rotation = self._rotation_over(rotated, admin_a, model_registry)
        agent = self._agent(admin_a, model_registry, rotation_id=rotation.id)
        self._pin(agent, off, admin_a, model_registry)
        ProviderInstanceManager(
            requester_id=admin_a.id, model_registry=model_registry
        ).update(off.id, enabled=False)
        outcome = await self._turn(agent, admin_a, model_registry)
        assert outcome["status"] == "failed"
        assert outcome["turn"].error.startswith(
            "none of the agent's 1 pinned provider instances can be used for chat"
        )
        # The rotation is not a fallback: pins override it.
        assert server.called() == []

    async def test_a_restriction_allows_only_its_abilities(
        self, admin_a, model_registry, models
    ):
        server = models(["embeds", "chats"])
        embeds = model_instance(model_registry, admin_a, server.base_url("embeds"))
        chats = model_instance(model_registry, admin_a, server.base_url("chats"))
        agent = self._agent(admin_a, model_registry)
        self._pin(agent, embeds, admin_a, model_registry)
        self._pin(agent, chats, admin_a, model_registry)
        restrictions = ProviderInstanceAgentAbilityManager(
            requester_id=admin_a.id, model_registry=model_registry
        )
        restrictions.create(
            agent_id=agent.id,
            provider_instance_id=embeds.id,
            ability_id=ensure_ability(model_registry, "embed", extension="ai"),
        )
        restrictions.create(
            agent_id=agent.id,
            provider_instance_id=chats.id,
            ability_id=ensure_ability(model_registry, "chat", extension="ai"),
        )
        outcome = await self._turn(agent, admin_a, model_registry)
        assert outcome["status"] == "succeeded", outcome
        assert server.called() == ["chats"]

    async def test_a_restriction_turned_off_disallows(
        self, admin_a, model_registry, models
    ):
        server = models(["only"])
        only = model_instance(model_registry, admin_a, server.base_url("only"))
        agent = self._agent(admin_a, model_registry)
        self._pin(agent, only, admin_a, model_registry)
        ProviderInstanceAgentAbilityManager(
            requester_id=admin_a.id, model_registry=model_registry
        ).create(
            agent_id=agent.id,
            provider_instance_id=only.id,
            ability_id=ensure_ability(model_registry, "chat", extension="ai"),
            state=False,
        )
        outcome = await self._turn(agent, admin_a, model_registry)
        assert outcome["status"] == "failed"
        assert server.called() == []

    def test_a_pinned_instance_is_one_the_owner_sees(
        self, admin_a, admin_b, model_registry
    ):
        theirs = model_instance(model_registry, admin_b, "http://127.0.0.1:9/v1")
        agent = self._agent(admin_a, model_registry)
        with pytest.raises(HTTPException) as refused:
            self._pin(agent, theirs, admin_a, model_registry)
        assert refused.value.status_code == 404

    def test_a_restriction_names_a_real_ability(self, admin_a, model_registry):
        instance = model_instance(model_registry, admin_a, "http://127.0.0.1:9/v1")
        agent = self._agent(admin_a, model_registry)
        with pytest.raises(HTTPException) as refused:
            ProviderInstanceAgentAbilityManager(
                requester_id=admin_a.id, model_registry=model_registry
            ).create(
                agent_id=agent.id,
                provider_instance_id=instance.id,
                ability_id=str(uuid.uuid4()),
            )
        assert refused.value.status_code == 404
