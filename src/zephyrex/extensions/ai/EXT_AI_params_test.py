# SPDX-License-Identifier: AGPL-3.0-or-later
"""``params``: extra chat request fields, merged after the call's own (never
over messages, model or stream), with an instance's ``extra_body`` under
them; Anthropic translates what it has equivalents for and drops the rest.
``max_tokens`` None sends no limit, except to Anthropic, whose API requires
one: it sends the model's own output maximum."""

from __future__ import annotations

import json
from typing import Any, Dict

import pytest

from zephyrex.extensions.ai.EXT_AI import (
    EXT_AI,
    checked_params,
)
from zephyrex.extensions.ai.PRV_Anthropic import (
    PRV_Anthropic_AI,
    anthropic_params,
    output_maximum,
)
from zephyrex.extensions.ai.PRV_OpenAICompatible import PRV_OpenAICompatible_AI
from zephyrex.extensions.ExternalErrors import (
    InvalidInputExternalError,
    TransientExternalError,
)

HI = [{"role": "user", "content": "Hi"}]
OPENAI_REPLY = json.dumps(
    {
        "model": "m",
        "choices": [
            {"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }
).encode()
ANTHROPIC_REPLY = json.dumps(
    {
        "model": "claude-sonnet-4-5",
        "stop_reason": "end_turn",
        "content": [{"type": "text", "text": "ok"}],
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }
).encode()


class TestTranslation:
    def test_params_must_be_an_object(self):
        assert checked_params(None) is None
        assert checked_params({"top_p": 0.9}) == {"top_p": 0.9}
        for bad in ([1], "x", {1: 2}):
            with pytest.raises(InvalidInputExternalError):
                checked_params(bad)

    def test_anthropic_keeps_its_own_fields_and_translates_the_rest(self):
        fields = anthropic_params(
            {
                "top_p": 0.9,
                "top_k": 40,
                "stop": "END",
                "seed": 7,
                "frequency_penalty": 0.5,
                "response_format": {"type": "json_object"},
                "reasoning_effort": "high",
            }
        )
        assert fields == {
            "top_p": 0.9,
            "top_k": 40,
            "stop_sequences": ["END"],
            "thinking": {"type": "enabled", "budget_tokens": 16384},
        }
        assert anthropic_params({"think": True})["thinking"]["budget_tokens"] == 4096

    def test_known_output_maxima(self):
        assert output_maximum("claude-sonnet-4-5-20250929") == 64000
        assert output_maximum("claude-opus-4-1") == 32000
        assert output_maximum("claude-3-5-haiku-latest") == 8192
        assert output_maximum("some-new-model") is None


class TestRequests:
    @pytest.fixture
    def server(self, local_http_server):
        return local_http_server(
            {
                "/chat/completions": (
                    200,
                    {"Content-Type": "application/json"},
                    OPENAI_REPLY,
                ),
                "/messages": (
                    200,
                    {"Content-Type": "application/json"},
                    ANTHROPIC_REPLY,
                ),
            }
        )

    @staticmethod
    def body(server: Any) -> Dict[str, Any]:
        [request] = server.requests
        body: Dict[str, Any] = json.loads(request.body)
        return body

    async def test_openai_compatible_merges_params_after_its_own(
        self, server, provider_instance
    ):
        instance = provider_instance(
            PRV_OpenAICompatible_AI,
            api_key="k",
            model_name="m",
            settings={
                "base_url": server.base_url,
                "extra_body": json.dumps({"min_p": 0.05, "top_p": 0.5}),
            },
        )
        await PRV_OpenAICompatible_AI.chat(
            instance,
            HI,
            None,
            None,
            0.7,
            None,
            {"top_p": 0.9, "model": "evil", "messages": [], "stream": True, "seed": 3},
        )
        body = self.body(server)
        assert body["model"] == "m" and body["messages"][0]["content"] == "Hi"
        assert "stream" not in body
        assert "max_tokens" not in body  # no limit asked for, none sent
        assert (body["top_p"], body["min_p"], body["seed"]) == (0.9, 0.05, 3)
        assert body["temperature"] == 0.7

    async def test_a_malformed_extra_body_moves_the_rotation_on(
        self, server, provider_instance
    ):
        instance = provider_instance(
            PRV_OpenAICompatible_AI,
            api_key="k",
            model_name="m",
            settings={"base_url": server.base_url, "extra_body": "[1, 2]"},
        )
        with pytest.raises(TransientExternalError):
            await PRV_OpenAICompatible_AI.chat(instance, HI)
        assert server.requests == []

    async def test_anthropic_sends_the_models_maximum_when_uncapped(
        self, server, provider_instance
    ):
        instance = provider_instance(
            PRV_Anthropic_AI, api_key="k", settings={"base_url": server.base_url}
        )
        await PRV_Anthropic_AI.chat(instance, HI, params={"stop": ["A", "B"]})
        body = self.body(server)
        assert body["max_tokens"] == 64000  # claude-sonnet-4-5, the default model
        assert body["stop_sequences"] == ["A", "B"]

    async def test_anthropic_takes_the_instances_maximum_for_an_unknown_model(
        self, server, provider_instance
    ):
        instance = provider_instance(
            PRV_Anthropic_AI,
            api_key="k",
            model_name="claude-next",
            settings={"base_url": server.base_url, "max_output_tokens": "100000"},
        )
        await PRV_Anthropic_AI.chat(instance, HI)
        assert self.body(server)["max_tokens"] == 100000

    async def test_an_unknown_model_without_the_setting_is_not_capped(
        self, server, provider_instance
    ):
        """No smaller limit is sent quietly: it would cut the answer short.
        It is the instance's configuration error, so the rotation moves on."""
        instance = provider_instance(
            PRV_Anthropic_AI,
            api_key="k",
            model_name="claude-next",
            settings={"base_url": server.base_url},
        )
        with pytest.raises(TransientExternalError, match="max_output_tokens"):
            await PRV_Anthropic_AI.chat(instance, HI)
        assert server.requests == []

    @pytest.mark.parametrize(
        "max_tokens, expected",
        [(8000, {"type": "enabled", "budget_tokens": 7999}), (900, None)],
        ids=["clamped", "dropped"],
    )
    async def test_a_thinking_budget_fits_under_max_tokens(
        self, server, provider_instance, max_tokens, expected
    ):
        instance = provider_instance(
            PRV_Anthropic_AI, api_key="k", settings={"base_url": server.base_url}
        )
        await PRV_Anthropic_AI.chat(
            instance, HI, max_tokens=max_tokens, params={"reasoning_effort": "high"}
        )
        assert self.body(server).get("thinking") == expected


def test_params_is_never_a_models_to_fill():
    from zephyrex.extensions.ai_agents.AbilityInvoker import ability_to_tool_schema

    schema = ability_to_tool_schema("chat", EXT_AI.chat)
    assert "params" not in schema["function"]["parameters"]["properties"]
