# SPDX-License-Identifier: AGPL-3.0-or-later
"""``chat_stream``: a chat turn as it is written, content and reasoning
deltas, then the finish reason and usage (recorded as chat records it),
from the OpenAI-compatible and Anthropic streaming APIs, or whole from a
provider that doesn't stream. The rotation moves on only until the first
delta; a failure after it ends the stream."""

from __future__ import annotations

import json
from typing import Any, Dict, List

import pytest

from zephyrex.extensions.ai.EXT_AI import (
    EXT_AI,
    AbstractAIProvider,
    PrimedStream,
    chat_answer,
)
from zephyrex.extensions.ai.PRV_Anthropic import PRV_Anthropic_AI
from zephyrex.extensions.ai.PRV_OpenAICompatible import PRV_OpenAICompatible_AI
from zephyrex.extensions.ExternalErrors import TransientExternalError
from zephyrex.lib.Environment import env
from zephyrex.logic.BLL_Providers import ProviderInstanceUsageManager

HI = [{"role": "user", "content": "Hi"}]
SSE = {"Content-Type": "text/event-stream"}


def sse(*events: Any) -> bytes:
    return "".join(
        f"data: {e if isinstance(e, str) else json.dumps(e)}\n\n" for e in events
    ).encode()


OPENAI_STREAM = sse(
    {"model": "m", "choices": [{"delta": {"reasoning_content": "Thinking."}}]},
    {"choices": [{"delta": {"content": "Hel"}}]},
    {"choices": [{"delta": {"content": "lo."}}]},
    {
        "choices": [
            {
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "c1",
                            "function": {"name": "weather", "arguments": '{"ci'},
                        }
                    ]
                }
            }
        ]
    },
    {
        "choices": [
            {
                "delta": {
                    "tool_calls": [{"index": 0, "function": {"arguments": 'ty": 1}'}}]
                }
            }
        ]
    },
    {"choices": [{"delta": {}, "finish_reason": "stop"}]},
    {"choices": [], "usage": {"prompt_tokens": 4, "completion_tokens": 6}},
    "[DONE]",
)
ANTHROPIC_STREAM = sse(
    {
        "type": "message_start",
        "message": {"model": "claude-sonnet-4-5", "usage": {"input_tokens": 9}},
    },
    {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking"}},
    {
        "type": "content_block_delta",
        "index": 0,
        "delta": {"type": "thinking_delta", "thinking": "Hmm."},
    },
    {"type": "content_block_start", "index": 1, "content_block": {"type": "text"}},
    {
        "type": "content_block_delta",
        "index": 1,
        "delta": {"type": "text_delta", "text": "Hi there."},
    },
    {
        "type": "message_delta",
        "delta": {"stop_reason": "end_turn"},
        "usage": {"output_tokens": 3},
    },
    {"type": "message_stop"},
)


async def collect(stream: Any) -> List[Dict[str, Any]]:
    return [event async for event in stream]


class TestStreams:
    @pytest.fixture
    def server(self, local_http_server):
        return local_http_server(
            {
                "/ok/chat/completions": (200, SSE, OPENAI_STREAM),
                "/down/chat/completions": (503, {}, b"overloaded"),
                "/messages": (200, SSE, ANTHROPIC_STREAM),
            }
        )

    def openai_instance(self, provider_instance, server, path="ok"):
        return provider_instance(
            PRV_OpenAICompatible_AI,
            api_key="k",
            model_name="m",
            settings={"base_url": f"{server.base_url}/{path}"},
        )

    async def test_openai_compatible_deltas_then_the_end(
        self, server, provider_instance, extension_app
    ):
        instance = self.openai_instance(provider_instance, server)
        events = await collect(
            PRV_OpenAICompatible_AI.stream(instance, HI, requester_id=env("ROOT_ID"))
        )
        assert events[:3] == [
            {"reasoning": "Thinking."},
            {"content": "Hel"},
            {"content": "lo."},
        ]
        final = events[-1]
        assert final["finish_reason"] == "stop"
        assert final["usage"] == {"input_tokens": 4, "output_tokens": 6}
        assert final["tool_calls"] == [
            {"id": "c1", "name": "weather", "arguments": '{"city": 1}'}
        ]
        body = json.loads(server.requests[0].body)
        assert body["stream"] is True
        assert body["stream_options"] == {"include_usage": True}
        rows = ProviderInstanceUsageManager(
            model_registry=extension_app.state.model_registry,
            requester_id=env("ROOT_ID"),
        ).list(provider_instance_id=instance.id)
        assert sorted((r.key, r.value) for r in rows) == [
            ("input_tokens", 4),
            ("output_tokens", 6),
        ]

    async def test_anthropic_thinking_and_text(self, server, provider_instance):
        instance = provider_instance(
            PRV_Anthropic_AI, api_key="k", settings={"base_url": server.base_url}
        )
        events = await collect(PRV_Anthropic_AI.stream(instance, HI))
        assert events[:2] == [{"reasoning": "Hmm."}, {"content": "Hi there."}]
        assert events[-1]["finish_reason"] == "stop"
        assert events[-1]["usage"] == {"input_tokens": 9, "output_tokens": 3}
        assert json.loads(server.requests[0].body)["stream"] is True

    async def test_the_rotation_moves_on_before_the_first_delta(
        self, server, provider_instance, rotation_over, monkeypatch
    ):
        down = self.openai_instance(provider_instance, server, "down")
        up = self.openai_instance(provider_instance, server, "ok")
        monkeypatch.setattr(EXT_AI, "_root_rotation_cache", rotation_over(down, up))
        events = await collect(EXT_AI.chat_stream(HI))
        assert "".join(e.get("content", "") for e in events) == "Hello."
        assert [r.path for r in server.requests][-1] == "/ok/chat/completions"


class Whole(AbstractAIProvider):
    """A provider that answers whole, with no streaming of its own."""

    name = "whole_test"

    @classmethod
    async def complete(
        cls, instance, messages, tools, max_tokens, temperature, params=None
    ):
        return chat_answer("All at once.", None, "stop", "w", 2, 3)


async def test_a_provider_without_streaming_answers_whole():
    events = await collect(Whole.complete_stream(None, HI, None, None, None))
    assert events == [
        {"content": "All at once."},
        {
            "finish_reason": "stop",
            "usage": {"input_tokens": 2, "output_tokens": 3},
            "model": "w",
            "tool_calls": None,
        },
    ]


async def test_a_failure_after_the_first_delta_ends_the_stream():
    async def breaks_midway():
        yield {"content": "Once"}
        raise TransientExternalError("connection lost")

    events = breaks_midway()
    primed = PrimedStream(await events.__anext__(), events)
    seen: List[Dict[str, Any]] = []
    with pytest.raises(TransientExternalError):
        async for event in primed:
            seen.append(event)
    assert seen == [{"content": "Once"}]


async def test_a_stream_that_ends_before_answering_is_a_failure():
    class Silent(AbstractAIProvider):
        name = "silent_test"

        @classmethod
        async def stream(cls, *args: Any, **kwargs: Any):
            return
            yield  # an async generator that yields nothing

    with pytest.raises(TransientExternalError):
        await Silent.open_stream(None, HI)
