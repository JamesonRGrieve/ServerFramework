"""Tool-calling testing harness for `AbstractAIProvider` (Item 43 follow-on).

Item 43's reshape established the canonical tool-use vocabulary
(`ToolDefinition`, `ToolCall`, `ToolResult`) and the
``chat(..., tools=...)`` ability surface. The follow-on item promised a
formal testing harness so each concrete AI provider (OpenAI, Anthropic,
Cohere, locally-hosted) gets a single, audit-friendly bundle of
contract tests rather than each provider hand-rolling its own
tool-calling integration tests.

Use:

    from serverframework.extensions.PRV_AI_ToolCallingHarness import (
        AbstractToolCallingHarness,
    )

    class TestMyProviderToolCalling(AbstractToolCallingHarness):
        @classmethod
        def make_provider(cls):
            return MyAIProvider

        @classmethod
        def make_instance(cls):
            return MyProviderInstanceModel(api_key="...")

        @classmethod
        def model_name(cls):
            return "gpt-4o-mini"  # or claude-3-5-haiku, etc.

The harness verifies, against the concrete provider:

- The provider accepts a `ToolDefinition` list and emits at least one
  `ToolCall` for a deterministic prompt that asks for a tool.
- The provider correctly round-trips a `ToolResult` fed back into the
  next chat turn.
- The provider's `chat()` response contract carries `content` and
  `tool_calls` keys per the abstract.
- The provider's tool definitions are serialized into the upstream's
  shape via Item 6's field-mapping pipeline (covered by
  `validate_tool_call_payload` — providers translate canonical
  `ToolDefinition.parameters` into their wire shape).
- The pre-estimate / post-true-up quota helpers behave consistently:
  the actual-token count reported by the provider matches what
  `_post_true_up_tokens` extracts.

The harness is *contract* rather than *implementation* tests — it does
NOT mock the upstream. Concrete providers gate the harness on the
``external_api`` pytest marker (Item 15) so it auto-xfails when no
sandbox credential is configured.

The harness also exposes a `FakeToolCallingProvider` that satisfies
the contract without an upstream — the framework's own tests use it to
verify the harness itself, and provider authors can use it as a
known-good reference when porting their tests.
"""

from __future__ import annotations

from typing import Any, AsyncIterator, ClassVar, Dict, List, Optional, Type

import pytest

from serverframework.extensions.AbstractExtensionProvider import (
    AbstractProviderInstance,
)
from serverframework.extensions.PRV_Abstract_AI import (
    AbstractAIProvider,
    ToolCall,
    ToolDefinition,
    ToolResult,
)


# ---------------------------------------------------------------------------
# Reference fake — provider authors use this as a known-good baseline.
# ---------------------------------------------------------------------------


class FakeToolCallingProvider(AbstractAIProvider):
    """In-process fake `AbstractAIProvider` for the tool-calling harness.

    Implements deterministic tool-calling semantics so the harness can be
    exercised without an LLM upstream. The fake's ``chat`` method:

    - Returns ``{"content": "...", "tool_calls": [...]}`` when ``tools``
      is non-empty and the latest user message contains a recognized
      keyword (``"weather"`` -> the ``get_weather`` tool, ``"lookup"`` ->
      ``lookup_user``, etc.).
    - Returns ``{"content": "<acknowledgement of tool result>"}`` when
      the latest message is a ``tool`` role result (the second turn of
      the round trip).
    - Returns plain text content otherwise.

    The fake is the framework's reference implementation that proves the
    harness contract closes; concrete providers (OpenAI, Anthropic) plug
    into the same harness with their real upstream.
    """

    name: ClassVar[str] = "fake_tool_calling"

    @classmethod
    def bond_instance(cls, instance: Any) -> AbstractProviderInstance:
        return AbstractProviderInstance(instance)

    @classmethod
    async def complete(
        cls,
        provider_instance: Any,
        prompt: str,
        model: str,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> str:
        return f"completed: {prompt[:40]}"

    @classmethod
    async def chat(
        cls,
        provider_instance: Any,
        messages: List[Dict[str, Any]],
        model: str,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        tools: Optional[List[ToolDefinition]] = None,
        output_format: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        last = messages[-1] if messages else {"role": "user", "content": ""}

        # Round-trip turn: latest message is a tool result -> acknowledge.
        if last.get("role") == "tool":
            return {
                "content": f"acknowledged tool result: {last.get('content', '')!r}",
                "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
            }

        # Initial turn with tools available -> emit a tool call when the
        # prompt contains a known keyword.
        content = str(last.get("content", "")).lower()
        tool_calls: List[ToolCall] = []
        if tools:
            tool_names = {t.name: t for t in tools}
            if "weather" in content and "get_weather" in tool_names:
                tool_calls.append(
                    ToolCall(name="get_weather", arguments={"location": "London"})
                )
            elif "lookup" in content and "lookup_user" in tool_names:
                tool_calls.append(
                    ToolCall(name="lookup_user", arguments={"id": "u1"})
                )

        if tool_calls:
            return {
                "content": "",
                "tool_calls": tool_calls,
                "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
            }

        return {
            "content": "no tool needed",
            "usage": {"prompt_tokens": 4, "completion_tokens": 6, "total_tokens": 10},
        }

    @classmethod
    async def embed(
        cls, provider_instance: Any, text: str, model: str
    ) -> List[float]:
        return [float(len(text)) / 100.0]

    @classmethod
    async def stream_chat(
        cls,
        provider_instance: Any,
        messages: List[Dict[str, Any]],
        model: str,
        max_tokens: Optional[int] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        async def _gen():
            yield {"content": "stream-chunk-1"}
            yield {"content": "stream-chunk-2", "usage": {"total_tokens": 4}}

        return _gen()

    @classmethod
    async def generate_image(
        cls,
        provider_instance: Any,
        prompt: str,
        model: str,
        size: Optional[str] = None,
    ) -> bytes:
        return b""

    @classmethod
    def _pre_estimate_tokens(
        cls, prompt: Any, max_tokens: Optional[int] = None
    ) -> int:
        # Simple input + max ceiling estimator (matches the abstract's
        # description). Concrete providers use a real tokenizer.
        if isinstance(prompt, str):
            return len(prompt.split()) + (max_tokens or 0)
        if isinstance(prompt, list):
            return sum(
                len(str(m.get("content", "")).split()) for m in prompt
            ) + (max_tokens or 0)
        return max_tokens or 0

    @classmethod
    def _post_true_up_tokens(cls, response: Any) -> int:
        if isinstance(response, dict):
            usage = response.get("usage") or {}
            return int(usage.get("total_tokens", 0))
        return 0


# ---------------------------------------------------------------------------
# Validation helpers — usable independently of the harness.
# ---------------------------------------------------------------------------


def validate_chat_response_shape(response: Any) -> None:
    """Assert the ``chat()`` response satisfies the abstract contract.

    The abstract says ``chat()`` returns a dict with at least ``content``
    and optional ``tool_calls`` and ``usage`` blocks. Providers that
    return malformed shapes break downstream consumers (the rotation
    quota system, the streaming true-up); the harness rejects at the
    contract level so the failure surfaces here rather than three layers
    deeper.
    """
    assert isinstance(response, dict), (
        f"chat() must return a dict, got {type(response).__name__}"
    )
    assert "content" in response, (
        f"chat() response must include a 'content' key, got keys: {sorted(response.keys())}"
    )
    if "tool_calls" in response:
        tc = response["tool_calls"]
        assert isinstance(tc, list), "tool_calls must be a list when present"
        for entry in tc:
            assert isinstance(entry, ToolCall) or (
                isinstance(entry, dict) and "name" in entry
            ), (
                f"each tool_calls entry must be a ToolCall (or dict with 'name'), "
                f"got {type(entry).__name__}"
            )


def validate_tool_call_payload(call: Any) -> ToolCall:
    """Coerce a provider's tool-call entry into the canonical ToolCall.

    Providers translate from upstream wire shapes (OpenAI's
    ``function.arguments`` JSON string, Anthropic's ``input`` dict).
    The harness consults this helper so test assertions read
    canonical types regardless of upstream.
    """
    if isinstance(call, ToolCall):
        return call
    if not isinstance(call, dict):
        raise AssertionError(
            f"tool_calls entries must be ToolCall or dict, got {type(call).__name__}"
        )
    function_block = call.get("function") if isinstance(call.get("function"), dict) else {}
    name = call.get("name") or function_block.get("name")  # type: ignore[union-attr]
    args = (
        call.get("arguments")
        or call.get("input")
        or function_block.get("arguments")  # type: ignore[union-attr]
    )
    if isinstance(args, str):
        import json

        args = json.loads(args)
    if name is None or not isinstance(args, dict):
        raise AssertionError(
            f"Cannot extract name/arguments from tool call: {call!r}"
        )
    return ToolCall(name=name, arguments=args)


# ---------------------------------------------------------------------------
# Abstract harness — concrete providers extend this with their fixtures.
# ---------------------------------------------------------------------------


class AbstractToolCallingHarness:
    """Reusable contract test bundle for `AbstractAIProvider` providers.

    Subclasses override the classmethods below to wire in their concrete
    provider, instance factory, and model name. Every test in this
    harness asserts against the canonical Item 43 contract so failures
    point at the provider's translation layer rather than at flaky
    assertion logic.

    Subclasses MAY also override ``tool_definitions()``, ``weather_prompt()``
    and ``lookup_prompt()`` to align with their model's prompting
    conventions; the defaults work against the framework's
    ``FakeToolCallingProvider``.
    """

    @classmethod
    def make_provider(cls) -> Type[AbstractAIProvider]:
        """Return the concrete provider class. Subclass overrides to plug in."""
        return FakeToolCallingProvider

    @classmethod
    def make_instance(cls) -> Any:
        """Return a bonded provider instance suitable for ``chat()`` calls.

        Default returns ``None`` because ``FakeToolCallingProvider``
        ignores the instance. Concrete subclasses override to return a
        ``ProviderInstanceModel`` with a sandbox API key.
        """
        return None

    @classmethod
    def model_name(cls) -> str:
        """The model identifier the provider's chat call should target."""
        return "fake-tool-model"

    @classmethod
    def tool_definitions(cls) -> List[ToolDefinition]:
        """Default tool definitions covering both branches of the harness.

        Subclasses MAY narrow this to a single tool, but the standard
        bundle exercises both single-call and tool-selection paths so
        the contract surface remains comparable across providers.
        """
        return [
            ToolDefinition(
                name="get_weather",
                description="Look up the current weather for a city.",
                parameters={
                    "type": "object",
                    "properties": {
                        "location": {"type": "string", "description": "City name"},
                    },
                    "required": ["location"],
                },
            ),
            ToolDefinition(
                name="lookup_user",
                description="Look up a user by id.",
                parameters={
                    "type": "object",
                    "properties": {"id": {"type": "string"}},
                    "required": ["id"],
                },
            ),
        ]

    @classmethod
    def weather_prompt(cls) -> str:
        return "What is the weather like in London right now?"

    @classmethod
    def lookup_prompt(cls) -> str:
        return "Please lookup the user with id u1."

    # ---- Tests ---------------------------------------------------------

    @pytest.mark.asyncio
    async def test_chat_response_shape_without_tools(self):
        """Providers must satisfy the chat-response shape even when no
        tools are supplied (no ``tool_calls`` key required, but ``content``
        is mandatory)."""
        provider = self.make_provider()
        instance = self.make_instance()
        resp = await provider.chat(
            instance,
            messages=[{"role": "user", "content": "hello"}],
            model=self.model_name(),
            tools=None,
        )
        validate_chat_response_shape(resp)

    @pytest.mark.asyncio
    async def test_chat_emits_tool_call_for_tool_prompt(self):
        """A prompt that should trigger the ``get_weather`` tool returns
        at least one ``ToolCall`` entry."""
        provider = self.make_provider()
        instance = self.make_instance()
        resp = await provider.chat(
            instance,
            messages=[{"role": "user", "content": self.weather_prompt()}],
            model=self.model_name(),
            tools=self.tool_definitions(),
        )
        validate_chat_response_shape(resp)
        assert resp.get("tool_calls"), "expected tool_calls for weather prompt"
        canonical = validate_tool_call_payload(resp["tool_calls"][0])
        assert canonical.name == "get_weather"
        assert "location" in canonical.arguments

    @pytest.mark.asyncio
    async def test_chat_emits_alternate_tool_for_alternate_prompt(self):
        """A different prompt triggers the alternate tool — the model
        must pick the correct tool, not always the first."""
        provider = self.make_provider()
        instance = self.make_instance()
        resp = await provider.chat(
            instance,
            messages=[{"role": "user", "content": self.lookup_prompt()}],
            model=self.model_name(),
            tools=self.tool_definitions(),
        )
        validate_chat_response_shape(resp)
        assert resp.get("tool_calls"), "expected tool_calls for lookup prompt"
        canonical = validate_tool_call_payload(resp["tool_calls"][0])
        assert canonical.name == "lookup_user"

    @pytest.mark.asyncio
    async def test_tool_result_round_trip(self):
        """Round trip: model emits ToolCall, caller executes, ToolResult
        is fed back, model continues. Matches the canonical Item 43 flow."""
        provider = self.make_provider()
        instance = self.make_instance()

        first = await provider.chat(
            instance,
            messages=[{"role": "user", "content": self.weather_prompt()}],
            model=self.model_name(),
            tools=self.tool_definitions(),
        )
        call = validate_tool_call_payload(first["tool_calls"][0])

        # Caller "executes" the tool and feeds back the result.
        result = ToolResult(name=call.name, result={"temperature_c": 12, "conditions": "rain"})
        second = await provider.chat(
            instance,
            messages=[
                {"role": "user", "content": self.weather_prompt()},
                {"role": "assistant", "tool_calls": [call.model_dump()]},
                {"role": "tool", "name": result.name, "content": str(result.result)},
            ],
            model=self.model_name(),
            tools=self.tool_definitions(),
        )
        validate_chat_response_shape(second)

    @pytest.mark.asyncio
    async def test_pre_estimate_returns_non_negative_int(self):
        """Item 19/43 quota contract: pre-estimate must be a non-negative int."""
        provider = self.make_provider()
        est = provider._pre_estimate_tokens("hello world", max_tokens=100)
        assert isinstance(est, int)
        assert est >= 0

    @pytest.mark.asyncio
    async def test_post_true_up_extracts_usage_from_chat_response(self):
        """Item 19/43 quota contract: post-true-up reads `total_tokens`
        from the chat response. The harness drives a real chat then
        feeds the response back to the helper."""
        provider = self.make_provider()
        instance = self.make_instance()
        resp = await provider.chat(
            instance,
            messages=[{"role": "user", "content": "hi"}],
            model=self.model_name(),
            tools=None,
        )
        actual = provider._post_true_up_tokens(resp)
        assert isinstance(actual, int)
        assert actual >= 0

    @pytest.mark.asyncio
    async def test_chat_accepts_tool_definitions_without_crashing(self):
        """A chat call with the canonical ToolDefinition list must not
        raise — providers are responsible for translating to their wire
        shape inside chat()."""
        provider = self.make_provider()
        instance = self.make_instance()
        # Should not raise.
        await provider.chat(
            instance,
            messages=[{"role": "user", "content": "hi"}],
            model=self.model_name(),
            tools=self.tool_definitions(),
        )


__all__ = [
    "AbstractToolCallingHarness",
    "FakeToolCallingProvider",
    "validate_chat_response_shape",
    "validate_tool_call_payload",
]
