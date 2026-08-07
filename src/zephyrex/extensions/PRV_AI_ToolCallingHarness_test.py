"""Tests for the AI tool-calling testing harness (Item 43 follow-on).

The harness is itself a contract — it must:
    - Validate chat-response shape correctly (accept good shapes,
      reject bad ones).
    - Translate provider-shaped tool-call entries into the canonical
      `ToolCall` (OpenAI-style ``function.arguments`` JSON string,
      Anthropic-style dict ``input``).
    - Execute the full ``AbstractToolCallingHarness`` test bundle
      against the framework's reference fake without any fixture
      patches (proves the harness is self-contained).

These tests cover the harness machinery; concrete provider extensions
exercise their real upstream by subclassing ``AbstractToolCallingHarness``
and overriding ``make_provider`` / ``make_instance`` / ``model_name``.
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator, ClassVar, Dict, List, Optional

import pytest

from zephyrex.extensions.AbstractExtensionProvider import AbstractProviderInstance
from zephyrex.extensions.PRV_Abstract_AI import (
    AbstractAIProvider,
    ToolCall,
    ToolDefinition,
    ToolResult,
)
from zephyrex.extensions.PRV_AI_ToolCallingHarness import (
    AbstractToolCallingHarness,
    FakeToolCallingProvider,
    validate_chat_response_shape,
    validate_tool_call_payload,
)


# ---------------------------------------------------------------------------
# validate_chat_response_shape — accept / reject contract
# ---------------------------------------------------------------------------


def test_validate_chat_response_accepts_minimal_content():
    validate_chat_response_shape({"content": "hello"})


def test_validate_chat_response_accepts_content_and_tool_calls():
    validate_chat_response_shape(
        {
            "content": "",
            "tool_calls": [ToolCall(name="t", arguments={"a": 1})],
        }
    )


def test_validate_chat_response_accepts_dict_tool_call_entries():
    """Providers may return raw dicts that need translation; the
    validator accepts the unparsed form so providers can defer
    canonicalization to ``validate_tool_call_payload``."""
    validate_chat_response_shape(
        {
            "content": "",
            "tool_calls": [{"name": "t", "arguments": {"a": 1}}],
        }
    )


def test_validate_chat_response_rejects_non_dict():
    with pytest.raises(AssertionError, match="must return a dict"):
        validate_chat_response_shape("a string is not a chat response")


def test_validate_chat_response_rejects_missing_content():
    with pytest.raises(AssertionError, match="must include a 'content' key"):
        validate_chat_response_shape({"tool_calls": []})


def test_validate_chat_response_rejects_non_list_tool_calls():
    with pytest.raises(AssertionError, match="must be a list"):
        validate_chat_response_shape(
            {"content": "", "tool_calls": "not a list"}
        )


def test_validate_chat_response_rejects_invalid_tool_call_entry():
    with pytest.raises(AssertionError, match="must be a ToolCall"):
        validate_chat_response_shape(
            {"content": "", "tool_calls": ["not-a-toolcall"]}
        )


# ---------------------------------------------------------------------------
# validate_tool_call_payload — provider translation
# ---------------------------------------------------------------------------


def test_translate_canonical_toolcall_pass_through():
    tc = ToolCall(name="t", arguments={"a": 1})
    out = validate_tool_call_payload(tc)
    assert out is tc


def test_translate_openai_function_shape_with_string_arguments():
    """OpenAI returns ``{function: {name, arguments: <json str>}}``."""
    payload = {
        "function": {"name": "lookup_user", "arguments": json.dumps({"id": "u1"})}
    }
    canonical = validate_tool_call_payload(payload)
    assert canonical.name == "lookup_user"
    assert canonical.arguments == {"id": "u1"}


def test_translate_anthropic_input_shape_with_dict():
    """Anthropic returns ``{name, input: {...}}``."""
    payload = {"name": "get_weather", "input": {"location": "London"}}
    canonical = validate_tool_call_payload(payload)
    assert canonical.name == "get_weather"
    assert canonical.arguments == {"location": "London"}


def test_translate_simple_dict_with_arguments_dict():
    """The most common framework-internal shape — name + arguments dict."""
    payload = {"name": "t", "arguments": {"a": 1}}
    canonical = validate_tool_call_payload(payload)
    assert canonical.name == "t"
    assert canonical.arguments == {"a": 1}


def test_translate_rejects_non_dict_non_toolcall():
    with pytest.raises(AssertionError, match="must be ToolCall or dict"):
        validate_tool_call_payload("not a dict")


def test_translate_rejects_dict_missing_required_fields():
    with pytest.raises(AssertionError, match="Cannot extract name/arguments"):
        validate_tool_call_payload({"foo": "bar"})


# ---------------------------------------------------------------------------
# FakeToolCallingProvider — reference implementation behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fake_provider_returns_no_tool_calls_when_none_offered():
    resp = await FakeToolCallingProvider.chat(
        None,
        messages=[{"role": "user", "content": "hello"}],
        model="m",
        tools=None,
    )
    assert resp["content"] == "no tool needed"
    assert "tool_calls" not in resp


@pytest.mark.asyncio
async def test_fake_provider_emits_weather_tool_for_weather_prompt():
    tools = [
        ToolDefinition(
            name="get_weather",
            description="x",
            parameters={"type": "object", "properties": {}},
        )
    ]
    resp = await FakeToolCallingProvider.chat(
        None,
        messages=[{"role": "user", "content": "what is the weather in London"}],
        model="m",
        tools=tools,
    )
    assert resp["tool_calls"]
    assert resp["tool_calls"][0].name == "get_weather"


@pytest.mark.asyncio
async def test_fake_provider_acknowledges_tool_role_message():
    """The fake's second-turn behavior: a message with role=tool returns
    an acknowledgement content rather than another tool call."""
    resp = await FakeToolCallingProvider.chat(
        None,
        messages=[
            {"role": "user", "content": "lookup"},
            {"role": "assistant", "tool_calls": [{"name": "lookup_user", "arguments": {"id": "u1"}}]},
            {"role": "tool", "name": "lookup_user", "content": "{'id': 'u1'}"},
        ],
        model="m",
        tools=None,
    )
    assert "acknowledged" in resp["content"]


def test_fake_provider_pre_estimate_handles_string():
    est = FakeToolCallingProvider._pre_estimate_tokens("hello world there", max_tokens=10)
    assert est == 3 + 10  # 3 words + max ceiling


def test_fake_provider_pre_estimate_handles_message_list():
    est = FakeToolCallingProvider._pre_estimate_tokens(
        [{"content": "one two"}, {"content": "three four five"}], max_tokens=20
    )
    assert est == (2 + 3) + 20


def test_fake_provider_post_true_up_extracts_total_tokens():
    actual = FakeToolCallingProvider._post_true_up_tokens(
        {"content": "x", "usage": {"total_tokens": 42}}
    )
    assert actual == 42


def test_fake_provider_post_true_up_returns_zero_when_no_usage():
    """Defensive: a response without usage metadata returns zero so
    the framework's quota math doesn't NaN."""
    assert FakeToolCallingProvider._post_true_up_tokens({"content": "x"}) == 0
    assert FakeToolCallingProvider._post_true_up_tokens(None) == 0


@pytest.mark.asyncio
async def test_fake_provider_complete_returns_text():
    out = await FakeToolCallingProvider.complete(None, "hello", "m")
    assert out.startswith("completed:")


@pytest.mark.asyncio
async def test_fake_provider_embed_returns_vector():
    vec = await FakeToolCallingProvider.embed(None, "hello", "m")
    assert isinstance(vec, list)
    assert all(isinstance(x, float) for x in vec)


@pytest.mark.asyncio
async def test_fake_provider_stream_chat_yields_chunks():
    gen = await FakeToolCallingProvider.stream_chat(None, [], "m")
    chunks = []
    async for chunk in gen:
        chunks.append(chunk)
    assert len(chunks) == 2
    assert chunks[-1].get("usage", {}).get("total_tokens") == 4


# ---------------------------------------------------------------------------
# Self-test: run the AbstractToolCallingHarness against the reference fake.
# ---------------------------------------------------------------------------


class TestHarnessSelfTest(AbstractToolCallingHarness):
    """Inherits and runs the full harness contract against the fake.

    This proves the harness is self-contained: a concrete provider
    that implements the abstract correctly passes every test in the
    bundle without writing additional setup. Real providers (OpenAI,
    Anthropic) will subclass the same harness and override
    ``make_provider`` / ``make_instance`` / ``model_name``.
    """
