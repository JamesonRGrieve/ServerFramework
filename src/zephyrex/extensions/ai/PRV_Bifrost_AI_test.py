"""Tests for the Bifrost AI provider.

Covers the pure, provider-neutral translation logic (message → OpenAI wire
format, OpenAI response → neutral shape, text-only flattening) directly, and
exercises bonding / live chat against a real Bifrost gateway when one is
configured (skipped otherwise, matching the other AI provider tests).
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from zephyrex.extensions.ai.PRV_Bifrost_AI import BifrostProvider
from zephyrex.lib.Environment import env
from zephyrex.logic.BLL_Providers import ProviderInstanceModel


def _openai_response(content, tool_calls=None, finish_reason="stop"):
    """Build a stand-in for an OpenAI chat-completion response object.

    A plain data object (not a mock of provider logic) so the pure
    ``_normalize_chat_response`` translation can be tested without a live model.
    """
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    choice = SimpleNamespace(message=message, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice])


class TestBifrostMetadata:
    def test_platform_metadata(self):
        assert BifrostProvider.name == "Bifrost"
        assert BifrostProvider.friendly_name == "Bifrost LLM Gateway"
        assert BifrostProvider.get_platform_name() == "Bifrost"

    def test_services(self):
        services = BifrostProvider.services()
        assert "llm" in services
        assert "vision" in services

    def test_abilities(self):
        assert "text_generation" in BifrostProvider._abilities

    def test_validate_config_key_optional(self):
        # Bifrost can inject upstream keys itself, so no API key is required.
        assert BifrostProvider.validate_config() == []


class TestBifrostMessagePreparation:
    def test_prepare_messages_text_only(self):
        messages = BifrostProvider._prepare_messages("Hello", [])
        assert messages == [{"role": "user", "content": "Hello"}]

    def test_prepare_messages_with_url_image(self):
        messages = BifrostProvider._prepare_messages(
            "Describe", ["http://example.com/a.jpg"]
        )
        assert messages[0]["role"] == "user"
        content = messages[0]["content"]
        assert content[0] == {"type": "text", "text": "Describe"}
        assert content[1]["type"] == "image_url"
        assert content[1]["image_url"]["url"] == "http://example.com/a.jpg"


class TestBifrostNeutralTranslation:
    def test_to_openai_plain_messages(self):
        neutral = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
        ]
        assert BifrostProvider._to_openai_messages(neutral) == [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
        ]

    def test_to_openai_assistant_tool_calls(self):
        neutral = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "call_1", "name": "search", "arguments": '{"q": "x"}'}
                ],
            }
        ]
        out = BifrostProvider._to_openai_messages(neutral)
        assert out[0]["role"] == "assistant"
        tc = out[0]["tool_calls"][0]
        assert tc["id"] == "call_1"
        assert tc["type"] == "function"
        assert tc["function"] == {"name": "search", "arguments": '{"q": "x"}'}

    def test_to_openai_tool_result(self):
        neutral = [
            {"role": "tool", "tool_call_id": "call_1", "content": "42 results"}
        ]
        out = BifrostProvider._to_openai_messages(neutral)
        assert out[0] == {
            "role": "tool",
            "tool_call_id": "call_1",
            "content": "42 results",
        }

    def test_to_openai_tool_result_empty_content(self):
        # A tool that returns nothing must still yield a string content, not
        # None, or the OpenAI API rejects the message.
        out = BifrostProvider._to_openai_messages(
            [{"role": "tool", "tool_call_id": "c", "content": None}]
        )
        assert out[0]["content"] == ""

    def test_normalize_response_plain_text(self):
        result = BifrostProvider._normalize_chat_response(
            _openai_response("hello there"), "openai/gpt-4o-mini"
        )
        assert result["success"] is True
        assert result["message"]["content"] == "hello there"
        assert result["message"]["tool_calls"] is None
        assert result["finish_reason"] == "stop"
        assert result["model"] == "openai/gpt-4o-mini"

    def test_normalize_response_tool_calls(self):
        tc = SimpleNamespace(
            id="call_9",
            function=SimpleNamespace(name="lookup", arguments='{"id": 3}'),
        )
        response = _openai_response(
            None, tool_calls=[tc], finish_reason="tool_calls"
        )
        result = BifrostProvider._normalize_chat_response(response, "m")
        assert result["message"]["content"] is None
        assert result["message"]["tool_calls"] == [
            {"id": "call_9", "name": "lookup", "arguments": '{"id": 3}'}
        ]
        assert result["finish_reason"] == "tool_calls"

    def test_flatten_messages_to_prompt(self):
        prompt = BifrostProvider._flatten_messages_to_prompt(
            [
                {"role": "system", "content": "be brief"},
                {"role": "user", "content": "hi"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {"id": "c", "name": "now", "arguments": "{}"}
                    ],
                },
            ]
        )
        assert "System: be brief" in prompt
        assert "User: hi" in prompt
        assert "Assistant (tool call): now({})" in prompt


class TestBifrostLive:
    """Live tests against a real Bifrost gateway; skipped when unconfigured."""

    @pytest.fixture
    def bonded(self):
        pytest.importorskip("openai", reason="Bifrost bonding requires the openai SDK")
        api_uri = env("BIFROST_API_URI")
        if not api_uri:
            pytest.skip("BIFROST_API_URI not configured")

        instance = MagicMock(spec=ProviderInstanceModel)
        instance.id = "test_bifrost"
        instance.name = "Test_Bifrost"
        instance.api_key = env("BIFROST_API_KEY") or "bifrost"
        instance.api_uri = api_uri
        instance.model_name = env("BIFROST_MODEL") or "openai/gpt-4o-mini"
        instance.settings_json = {"max_tokens": 256, "temperature": 0.1}

        bonded = BifrostProvider.bond_instance(instance)
        assert bonded is not None
        return bonded

    def test_bond_instance(self):
        pytest.importorskip("openai", reason="Bifrost bonding requires the openai SDK")
        instance = MagicMock(spec=ProviderInstanceModel)
        instance.api_key = "k"
        instance.api_uri = "http://localhost:8080/v1"
        instance.model_name = "openai/gpt-4o-mini"
        instance.settings_json = {}
        bonded = BifrostProvider.bond_instance(instance)
        assert bonded is not None
        assert bonded.api_uri.endswith("/")
        assert bonded.model_name == "openai/gpt-4o-mini"

    def test_chat_plain(self, bonded):
        result = BifrostProvider.chat(
            bonded,
            messages=[{"role": "user", "content": "Reply with the word OK."}],
            max_tokens=16,
        )
        assert result["success"] is True
        assert result["message"]["role"] == "assistant"
        assert isinstance(result["message"]["content"], str)

    def test_chat_with_tools(self, bonded):
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "get_time",
                    "description": "Return the current time.",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]
        result = BifrostProvider.chat(
            bonded,
            messages=[{"role": "user", "content": "What time is it? Use the tool."}],
            tools=tools,
            max_tokens=64,
        )
        assert result["success"] is True
        # The model may or may not call the tool, but the transport must return
        # the neutral shape either way.
        assert "message" in result
        assert result["message"]["role"] == "assistant"
