# SPDX-License-Identifier: AGPL-3.0-or-later
"""``as_user``: an AI call made for a user runs only on provider instances
that user may use, their own first, then their teams', then the operator's
system ones, never root ones and never another user's; ``instance_ids``
names exactly which, in order, and one the user may not use is a 404.
Without either, a call rotates over the root rotation as before. Neither is
ever a model's to fill in an agent's tool call."""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, Optional

import pytest
from fastapi import HTTPException

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.ai.EXT_AI import EXT_AI
from zephyrex.extensions.ai.PRV_Anthropic import PRV_Anthropic_AI
from zephyrex.extensions.ai_agents.AbilityInvoker import ability_to_tool_schema
from zephyrex.lib.Environment import env
from zephyrex.logic.BLL_Providers import (
    ProviderInstanceManager,
    ProviderInstanceSettingManager,
    ProviderInstanceUsageManager,
    ProviderManager,
    RotationManager,
    RotationProviderInstanceManager,
)

HI = [{"role": "user", "content": "Hi"}]


def _reply(text: str) -> bytes:
    return json.dumps(
        {
            "model": "claude-sonnet-4-5",
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": text}],
            "usage": {"input_tokens": 5, "output_tokens": 2},
        }
    ).encode()


class TestAsUser(ExtensionServerMixin):
    extension_class = EXT_AI

    @pytest.fixture
    def chain(self, server, admin_a, admin_b, team_a, local_http_server, monkeypatch):
        registry = server.app.state.model_registry
        root = env("ROOT_ID")
        paths = ["own", "team", "system", "root", "other", "off"]
        http = local_http_server(
            {
                f"/{name}/messages": (
                    200,
                    {"Content-Type": "application/json"},
                    _reply(name),
                )
                for name in paths
            }
        )
        provider_id = (
            ProviderManager(model_registry=registry, requester_id=root)
            .get(name=PRV_Anthropic_AI.name)
            .id
        )

        def instance(owner: str, path: str, **fields: Any) -> Any:
            created = ProviderInstanceManager(
                model_registry=registry, requester_id=owner
            ).create(
                name=f"as_user_{path}_{uuid.uuid4().hex[:8]}",
                provider_id=provider_id,
                api_key="k",
                **fields,
            )
            ProviderInstanceSettingManager(
                model_registry=registry, requester_id=owner
            ).create(
                provider_instance_id=created.id,
                key="base_url",
                value=f"{http.base_url}/{path}",
            )
            return created

        made: Dict[str, Any] = {
            "own": instance(admin_a.id, "own", scope="user"),
            "team": instance(admin_a.id, "team", scope="team", team_id=team_a.id),
            "system": instance(root, "system", scope="system"),
            "root": instance(root, "root", scope="root"),
            "other": instance(admin_b.id, "other", scope="user"),
            "off": instance(admin_a.id, "off", scope="user"),
        }
        ProviderInstanceManager(
            model_registry=registry, requester_id=admin_a.id
        ).update(made["off"].id, enabled=False)
        # The root rotation: the root instance, then the system one.
        rotation = RotationManager(model_registry=registry, requester_id=root).create(
            name=f"as_user_rotation_{uuid.uuid4().hex[:8]}", description="d"
        )
        links = RotationProviderInstanceManager(
            model_registry=registry, requester_id=root
        )
        parent: Optional[str] = None
        for key in ("root", "system"):
            parent = links.create(
                rotation_id=rotation.id,
                provider_instance_id=made[key].id,
                parent_id=parent,
            ).id
        monkeypatch.setattr(
            EXT_AI,
            "_root_rotation_cache",
            RotationManager(
                model_registry=registry, requester_id=root, target_id=rotation.id
            ),
        )
        return http, {key: str(value.id) for key, value in made.items()}

    def test_the_chain_is_own_then_team_then_system(self, chain, admin_a):
        _, ids = chain
        ordered = EXT_AI._chain_for(
            EXT_AI.root, [PRV_Anthropic_AI.name], admin_a.id, None
        )
        assert [i for i in ordered if i in ids.values()] == [
            ids["own"],
            ids["team"],
            ids["system"],
        ]

    async def test_a_call_for_a_user_runs_on_their_instance(
        self, chain, admin_a, server
    ):
        http, ids = chain
        answer = await EXT_AI.chat(HI, as_user=admin_a.id, instance_ids=[ids["own"]])
        assert answer["message"]["content"] == "own"
        assert [r.path for r in http.requests] == ["/own/messages"]
        rows = ProviderInstanceUsageManager(
            model_registry=server.app.state.model_registry,
            requester_id=env("ROOT_ID"),
        ).list(provider_instance_id=ids["own"])
        assert rows and {r.user_id for r in rows} == {admin_a.id}

    async def test_instance_ids_are_taken_in_order(self, chain, admin_a):
        http, ids = chain
        answer = await EXT_AI.chat(
            HI, as_user=admin_a.id, instance_ids=[ids["system"], ids["own"]]
        )
        assert answer["message"]["content"] == "system"
        assert [r.path for r in http.requests] == ["/system/messages"]

    @pytest.mark.parametrize("key", ["other", "root", "off"])
    async def test_an_instance_the_user_may_not_use_is_not_found(
        self, chain, admin_a, key
    ):
        http, ids = chain
        with pytest.raises(HTTPException) as refused:
            await EXT_AI.chat(HI, as_user=admin_a.id, instance_ids=[ids[key]])
        assert refused.value.status_code == 404
        assert http.requests == []

    async def test_without_as_user_the_root_rotation_serves(self, chain):
        http, _ = chain
        answer = await EXT_AI.chat(HI)
        assert answer["message"]["content"] == "root"
        assert [r.path for r in http.requests] == ["/root/messages"]

    def test_a_model_never_fills_them_in(self):
        for name, ability in (
            ("chat", EXT_AI.chat),
            ("embed", EXT_AI.embed),
            ("speak", EXT_AI.speak),
        ):
            properties = ability_to_tool_schema(name, ability)["function"][
                "parameters"
            ]["properties"]
            assert "as_user" not in properties
            assert "instance_ids" not in properties
