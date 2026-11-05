"""Abstract provider template for AI / LLM providers (Item 43).

Reference targets: OpenAI, Anthropic, Google Gemini, Cohere, Mistral,
Azure OpenAI, AWS Bedrock, locally-hosted models served via vLLM /
Ollama.

This module ships the contract only — concrete implementations live in
separate extensions (a follow-up item). Concrete subclasses declare
their typed configuration via the `Settings` inner Pydantic class
(Item 37) and implement each ability marked `@abstractmethod`.

Pre-estimate / post-true-up token quota pattern
-----------------------------------------------
LLM upstreams charge by tokens, but the actual token cost is only
known after the response. The framework's outbound-call dispatch uses
the two helpers declared on this abstract:

* `_pre_estimate_tokens(...)` — conservative upper bound on tokens the
  call MAY consume. The framework pre-decrements the per-tenant /
  per-key quota by this amount and refuses the call with
  `QuotaExhaustedError` when the estimate exceeds remaining quota.
* `_post_true_up_tokens(response)` — actual tokens consumed, read out
  of the upstream response (typically from the `usage` block).
  When `actual < pre` the framework credits the difference back; when
  `actual > pre` the framework debits the overage and emits a
  `QuotaOverrunWarning`.

For streamed responses the true-up runs incrementally — once per
chunk that carries usage metadata.

These helpers are declared here for the framework's quota system to
consume per Item 19; they are not wired into RotationManager in this
change.
"""

from abc import abstractmethod
from typing import Any, AsyncIterator, ClassVar, Dict, List, Optional

from pydantic import BaseModel

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticProvider


# ---------------------------------------------------------------------------
# Canonical tool-use types — provider-neutral request/response shapes.
# ---------------------------------------------------------------------------
class ToolDefinition(BaseModel):
    """Provider-neutral declaration of a tool the model may call.

    Concrete providers translate this into their wire format
    (OpenAI `tools[].function`, Anthropic `tools[]`, etc.).
    """

    name: str
    description: str
    parameters: dict


class ToolCall(BaseModel):
    """A single tool invocation produced by the model in its response."""

    name: str
    arguments: dict


class ToolResult(BaseModel):
    """The caller-side outcome of executing a `ToolCall`, fed back into
    the next `chat()` round so the model can continue.
    """

    name: str
    result: Any


class AbstractAIProvider(AbstractStaticProvider):
    """Abstract AI / LLM provider.

    Concrete implementations declare their settings via the typed
    `Settings` inner Pydantic class (Item 37) and override each ability
    method declared as `@abstractmethod`. The framework discovers the
    abilities for the rotation system through the standard
    `AbstractStaticProvider` surface.
    """

    category: ClassVar[str] = "ai"

    # ------------------------------------------------------------------
    # Core abilities
    # ------------------------------------------------------------------
    @classmethod
    @abstractmethod
    async def complete(
        cls,
        provider_instance: Any,
        prompt: str,
        model: str,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> str:
        """Single-prompt text completion. Returns the model's textual
        continuation of `prompt`. For chat-trained models concrete
        providers MAY internally route this through `chat()`.
        """
        ...

    @classmethod
    @abstractmethod
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
        """Multi-turn chat completion.

        `messages` is the running conversation in the `{"role": ...,
        "content": ...}` shape shared across major LLM APIs. When
        `tools` is non-empty the model MAY emit `ToolCall` entries in
        the response. `output_format` requests structured output (e.g.
        a JSON schema for response_format on OpenAI).

        Returns a dict with at least `content` and optional
        `tool_calls: List[ToolCall]` and `usage` blocks.
        """
        ...

    @classmethod
    @abstractmethod
    async def embed(
        cls,
        provider_instance: Any,
        text: str,
        model: str,
    ) -> List[float]:
        """Embed `text` into the embedding-model `model`'s vector space.
        Returns the dense vector as a list of floats.
        """
        ...

    @classmethod
    @abstractmethod
    async def stream_chat(
        cls,
        provider_instance: Any,
        messages: List[Dict[str, Any]],
        model: str,
        max_tokens: Optional[int] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        """Streaming variant of `chat()`. Each yielded dict is one
        chunk; chunks carrying token-usage metadata trigger an
        incremental quota true-up via `_post_true_up_tokens`.
        """
        ...

    @classmethod
    @abstractmethod
    async def generate_image(
        cls,
        provider_instance: Any,
        prompt: str,
        model: str,
        size: Optional[str] = None,
    ) -> bytes:
        """Generate an image from `prompt`. Returns the raw image bytes
        (typically PNG). `size` is in the provider's documented form
        (e.g. `"1024x1024"` for DALL·E).
        """
        ...

    # ------------------------------------------------------------------
    # Quota flow helpers — consumed by the framework's quota system per
    # Item 19. Not wired into RotationManager in this change.
    # ------------------------------------------------------------------
    @classmethod
    @abstractmethod
    def _pre_estimate_tokens(
        cls,
        prompt: Any,
        max_tokens: Optional[int] = None,
    ) -> int:
        """Conservative upper bound on the tokens this call MAY consume.

        Sum of the input-token count of `prompt` and the per-call
        ceiling implied by `max_tokens` (or the model's default
        ceiling when `max_tokens is None`). Consumed by the framework's
        quota system per Item 19: the framework pre-decrements quota
        by this estimate and refuses the call with
        `QuotaExhaustedError` when it exceeds remaining quota.
        """
        ...

    @classmethod
    @abstractmethod
    def _post_true_up_tokens(cls, response: Any) -> int:
        """Actual tokens consumed by the call, read out of `response`
        (typically the upstream `usage` block).

        Consumed by the framework's quota system per Item 19: the
        framework reconciles against the pre-estimate — crediting
        unused estimate back when `actual < pre`, debiting the overage
        and emitting `QuotaOverrunWarning` when `actual > pre`. For
        streamed responses this runs incrementally per chunk that
        carries usage metadata.
        """
        ...
