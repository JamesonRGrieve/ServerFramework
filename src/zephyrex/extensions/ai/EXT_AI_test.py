# SPDX-License-Identifier: AGPL-3.0-or-later
"""AI: neutral messages, tools, texts, audio and sizes checked before any
call; the neutral shape translated to the OpenAI and Anthropic wire
formats and their documented answers read back; each ability rotated only
over providers that offer it; and the tokens a chat uses recorded."""

import base64
import json

import pytest
from fastapi import HTTPException

from zephyrex.extensions.ai.EXT_AI import (
    EXT_AI,
    MAX_AUDIO_BYTES,
    audio_bytes,
    checked_messages,
    checked_texts,
    checked_tools,
    flatten_messages,
    image_size,
    speech_text,
)
from zephyrex.extensions.ai.OpenAICompatible import openai_answer, openai_messages
from zephyrex.extensions.ai.PRV_Anthropic import (
    PRV_Anthropic_AI,
    anthropic_answer,
    anthropic_request,
)
from zephyrex.extensions.ai.PRV_ElevenLabs import PRV_ElevenLabs_AI
from zephyrex.extensions.ExternalErrors import InvalidInputExternalError
from zephyrex.logic.BLL_Providers import ProviderInstanceUsageManager
from zephyrex.lib.Environment import env

TOOL = {
    "type": "function",
    "function": {
        "name": "weather",
        "description": "The weather in a city",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    },
}
CONVERSATION = [
    {"role": "system", "content": "Be brief."},
    {"role": "user", "content": "Weather in Oslo?", "images": ["https://x.test/a.png"]},
    {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": "call_1", "name": "weather", "arguments": '{"city": "Oslo"}'}
        ],
    },
    {"role": "tool", "tool_call_id": "call_1", "content": "4 °C, rain"},
]


class TestChecks:
    def test_messages(self):
        checked = checked_messages(CONVERSATION)
        assert [m["role"] for m in checked] == ["system", "user", "assistant", "tool"]
        assert checked[2]["tool_calls"][0]["name"] == "weather"

    @pytest.mark.parametrize(
        "messages",
        [
            [],
            "hello",
            [{"role": "robot", "content": "x"}],
            [{"role": "user", "content": 3}],
            [{"role": "assistant", "content": "x", "images": ["https://x.test/a.png"]}],
            [{"role": "user", "content": "x", "images": ["file:///etc/passwd"]}],
            [{"role": "tool", "content": "x"}],
            [
                {
                    "role": "assistant",
                    "tool_calls": [{"id": "1", "name": "f", "arguments": {}}],
                }
            ],
        ],
    )
    def test_malformed_messages(self, messages):
        with pytest.raises(InvalidInputExternalError):
            checked_messages(messages)

    def test_tools(self):
        assert checked_tools(None) is None and checked_tools([]) is None
        assert checked_tools([TOOL]) == [TOOL]
        for bad in ({"type": "function"}, [{"type": "code"}], ["weather"]):
            with pytest.raises(InvalidInputExternalError):
                checked_tools(bad)

    def test_texts_audio_speech_and_sizes(self):
        assert checked_texts("one") == ["one"]
        with pytest.raises(InvalidInputExternalError):
            checked_texts(["", "x"])
        assert audio_bytes(base64.b64encode(b"ID3").decode()) == b"ID3"
        with pytest.raises(InvalidInputExternalError):
            audio_bytes("not base64!")
        assert MAX_AUDIO_BYTES == 25 * 1024 * 1024
        with pytest.raises(InvalidInputExternalError):
            speech_text("x" * 5000)
        assert image_size("1024x1024") == "1024x1024"
        with pytest.raises(InvalidInputExternalError):
            image_size("640x480")

    def test_flatten(self):
        flat = flatten_messages(checked_messages(CONVERSATION))
        assert 'Assistant (tool call): weather({"city": "Oslo"})' in flat
        assert flat.startswith("System: Be brief.")


class TestOpenAIFormat:
    def test_messages(self):
        wire = openai_messages(checked_messages(CONVERSATION))
        assert wire[1]["content"][1] == {
            "type": "image_url",
            "image_url": {"url": "https://x.test/a.png"},
        }
        assert wire[2]["tool_calls"] == [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "weather", "arguments": '{"city": "Oslo"}'},
            }
        ]
        assert wire[3] == {
            "role": "tool",
            "tool_call_id": "call_1",
            "content": "4 °C, rain",
        }

    def test_answer_with_tool_calls(self):
        answer = openai_answer(
            {
                "model": "gpt-5-mini-2025-08-07",
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_9",
                                    "type": "function",
                                    "function": {
                                        "name": "weather",
                                        "arguments": '{"city":"Bergen"}',
                                    },
                                }
                            ],
                        },
                    }
                ],
                "usage": {"prompt_tokens": 50, "completion_tokens": 12},
            },
            "gpt-5-mini",
        )
        assert answer["message"]["tool_calls"] == [
            {"id": "call_9", "name": "weather", "arguments": '{"city":"Bergen"}'}
        ]
        assert answer["finish_reason"] == "tool_calls"
        assert answer["usage"] == {"input_tokens": 50, "output_tokens": 12}
        assert answer["model"] == "gpt-5-mini-2025-08-07"


class TestAnthropicFormat:
    def test_request(self):
        body = anthropic_request(checked_messages(CONVERSATION), [TOOL])
        assert body["system"] == "Be brief."
        user, assistant, result = body["messages"]
        assert user["content"][1] == {
            "type": "image",
            "source": {"type": "url", "url": "https://x.test/a.png"},
        }
        assert assistant["content"] == [
            {
                "type": "tool_use",
                "id": "call_1",
                "name": "weather",
                "input": {"city": "Oslo"},
            }
        ]
        assert result == {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "call_1",
                    "content": "4 °C, rain",
                }
            ],
        }
        assert body["tools"][0]["input_schema"]["required"] == ["city"]

    def test_consecutive_tool_results_share_a_turn(self):
        body = anthropic_request(
            checked_messages(
                [
                    {"role": "user", "content": "go"},
                    {
                        "role": "assistant",
                        "tool_calls": [
                            {"id": "a", "name": "f", "arguments": "{}"},
                            {"id": "b", "name": "f", "arguments": "{}"},
                        ],
                    },
                    {"role": "tool", "tool_call_id": "a", "content": "1"},
                    {"role": "tool", "tool_call_id": "b", "content": "2"},
                ]
            ),
            None,
        )
        assert [t["role"] for t in body["messages"]] == ["user", "assistant", "user"]
        assert len(body["messages"][2]["content"]) == 2

    def test_a_base64_image(self):
        body = anthropic_request(
            checked_messages(
                [
                    {
                        "role": "user",
                        "content": "x",
                        "images": ["data:image/png;base64,iVBO"],
                    }
                ]
            ),
            None,
        )
        assert body["messages"][0]["content"][1]["source"] == {
            "type": "base64",
            "media_type": "image/png",
            "data": "iVBO",
        }

    def test_answer(self):
        answer = anthropic_answer(
            {
                "model": "claude-sonnet-4-5-20250929",
                "stop_reason": "tool_use",
                "content": [
                    {"type": "text", "text": "Checking."},
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "weather",
                        "input": {"city": "Oslo"},
                    },
                ],
                "usage": {"input_tokens": 30, "output_tokens": 9},
            },
            "claude-sonnet-4-5",
        )
        assert answer["message"]["content"] == "Checking."
        assert answer["message"]["tool_calls"] == [
            {
                "id": "toolu_1",
                "name": "weather",
                "arguments": json.dumps({"city": "Oslo"}),
            }
        ]
        assert answer["finish_reason"] == "tool_calls"


ANTHROPIC_REPLY = json.dumps(
    {
        "model": "claude-sonnet-4-5",
        "stop_reason": "end_turn",
        "content": [{"type": "text", "text": "Hello."}],
        "usage": {"input_tokens": 7, "output_tokens": 3},
    }
).encode()


class TestRotation:
    @pytest.fixture
    def models(self, local_http_server, provider_instance, rotation_over, monkeypatch):
        server = local_http_server(
            {
                "/messages": (
                    200,
                    {"Content-Type": "application/json"},
                    ANTHROPIC_REPLY,
                ),
                "/text-to-speech/voice1?output_format=mp3_44100_128": (
                    200,
                    {"Content-Type": "audio/mpeg"},
                    b"ID3fake-mp3",
                ),
            }
        )
        claude = provider_instance(
            PRV_Anthropic_AI, api_key="k", settings={"base_url": server.base_url}
        )
        voice = provider_instance(
            PRV_ElevenLabs_AI,
            api_key="k",
            settings={"base_url": server.base_url, "voice": "voice1"},
        )
        monkeypatch.setattr(
            EXT_AI, "_root_rotation_cache", rotation_over(voice, claude)
        )
        return server, claude, voice

    async def test_chat_skips_a_speech_only_provider(self, models):
        server, _, _ = models
        answer = await EXT_AI.chat([{"role": "user", "content": "Hi"}])
        assert answer["message"]["content"] == "Hello."
        assert [r.path for r in server.requests] == ["/messages"]

    async def test_speech_skips_a_chat_only_provider(self, models):
        server, _, _ = models
        spoken = await EXT_AI.speak("Hello there")
        assert base64.b64decode(spoken["audio_base64"]) == b"ID3fake-mp3"
        assert spoken["content_type"] == "audio/mpeg"
        assert len(server.requests) == 1

    async def test_no_provider_offers_it(self, models):
        with pytest.raises(HTTPException) as raised:
            await EXT_AI.embed(["text"])
        assert raised.value.status_code == 503

    async def test_generate_text(self, models):
        assert (await EXT_AI.generate_text("Hi"))["text"] == "Hello."

    async def test_tokens_are_recorded(self, models, extension_app):
        _, claude, _ = models
        await PRV_Anthropic_AI.chat(claude, [{"role": "user", "content": "Hi"}])
        rows = ProviderInstanceUsageManager(
            model_registry=extension_app.state.model_registry,
            requester_id=env("ROOT_ID"),
        ).list(provider_instance_id=claude.id)
        assert sorted((r.key, r.value) for r in rows) == [
            ("input_tokens", 7),
            ("output_tokens", 3),
        ]
        usage = {u["id"]: u for u in await EXT_AI.ai_usage()}
        assert usage[str(claude.id)]["output_tokens"] == 3

    async def test_input_is_checked_before_any_call(self, models):
        server, _, _ = models
        with pytest.raises(InvalidInputExternalError):
            await EXT_AI.chat([{"role": "robot", "content": "x"}])
        with pytest.raises(InvalidInputExternalError):
            await EXT_AI.transcribe("not base64!")
        assert server.requests == []
