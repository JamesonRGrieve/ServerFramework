"""Bifrost LLM-gateway provider.

Bifrost (https://github.com/maximhq/bifrost) is a self-hosted, OpenAI-
compatible LLM gateway that fans a single API out to many upstream model
providers with load-balancing, fallbacks and budgeting. Because it exposes
the OpenAI Chat Completions surface, this provider talks to it via the
``openai`` client pointed at the Bifrost base URL; the ``model`` string is
routed by Bifrost (e.g. ``openai/gpt-4o-mini``, ``anthropic/claude-3-5``).
"""

from typing import Any, ClassVar, Dict, List, Optional, Set

try:
    import openai
except ImportError:
    openai = None

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractProviderInstance,
    ability,
)
from zephyrex.extensions.ai.EXT_AI import EXT_AI
from zephyrex.lib.Environment import env
from zephyrex.lib.Logging import logger
from zephyrex.logic.BLL_Providers import ProviderInstanceModel


class BifrostProvider(EXT_AI.AbstractProvider):
    """Static Bifrost gateway provider implementation."""

    name: ClassVar[str] = "Bifrost"
    friendly_name: ClassVar[str] = "Bifrost LLM Gateway"
    platform: ClassVar[str] = "Bifrost"

    _abilities: ClassVar[Set[str]] = {
        "text_generation",
    }

    _env: Dict[str, Any] = {
        "BIFROST_API_KEY": "",
        "BIFROST_API_URI": "http://localhost:8080/v1/",
    }

    @classmethod
    def get_platform_name(cls) -> str:
        return cls.platform

    @classmethod
    def services(cls) -> List[str]:
        return ["llm", "vision"]

    @classmethod
    def validate_config(
        cls, instance: Optional[ProviderInstanceModel] = None
    ) -> List[str]:
        # Bifrost can inject upstream keys itself, so an API key is optional.
        return []

    @classmethod
    def bond_instance(
        cls, instance: ProviderInstanceModel
    ) -> Optional[AbstractProviderInstance]:
        """Bond a provider instance for API operations."""
        if openai is None:
            logger.warning("openai package not available for bonding Bifrost")
            return None

        try:

            class BondedBifrostInstance(AbstractProviderInstance):
                def __init__(self, provider_instance: ProviderInstanceModel):
                    super().__init__(provider_instance)
                    self.api_key = provider_instance.api_key or "bifrost"
                    self.api_uri = (
                        provider_instance.api_uri
                        if getattr(provider_instance, "api_uri", None)
                        else env("BIFROST_API_URI") or "http://localhost:8080/v1/"
                    )
                    if not self.api_uri.endswith("/"):
                        self.api_uri += "/"
                    self.model_name = (
                        provider_instance.model_name or "openai/gpt-4o-mini"
                    )

                    settings = (
                        provider_instance.settings_json
                        if hasattr(provider_instance, "settings_json")
                        and provider_instance.settings_json
                        else {}
                    )
                    self.max_tokens = int(settings.get("max_tokens", 4096))
                    self.temperature = float(settings.get("temperature", 0.1))
                    self.top_p = float(settings.get("top_p", 0.95))
                    # Bound the upstream call so a slow/hung model fails the turn
                    # gracefully rather than blocking indefinitely.
                    self.request_timeout = float(settings.get("request_timeout", 120))

                @property
                def client(self):
                    """Get an OpenAI-compatible client pointed at the Bifrost gateway.

                    ``max_retries=0`` so ``request_timeout`` is a hard per-call
                    bound: the client's default retry-on-timeout would otherwise
                    multiply the effective wait (~3x) before surfacing a failure.
                    """
                    return openai.OpenAI(
                        api_key=self.api_key,
                        base_url=self.api_uri,
                        max_retries=0,
                    )

            return BondedBifrostInstance(instance)

        except Exception as e:
            logger.error(f"Error bonding Bifrost instance: {e}")
            return None

    @classmethod
    def _prepare_messages(cls, prompt: str, images: List[str]) -> List[Dict[str, Any]]:
        if not images:
            return [{"role": "user", "content": prompt}]

        content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
        for image in images:
            if image.startswith("http"):
                content.append({"type": "image_url", "image_url": {"url": image}})
            else:
                import base64

                file_type = image.split(".")[-1]
                with open(image, "rb") as f:
                    image_base64 = base64.b64encode(f.read()).decode()
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/{file_type};base64,{image_base64}"
                        },
                    }
                )
        return [{"role": "user", "content": content}]

    @classmethod
    @ability(name="text_generation")
    def generate_text(
        cls,
        bonded_instance: AbstractProviderInstance,
        prompt: str,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Generate text through the Bifrost gateway."""
        try:
            client = bonded_instance.client
            images = kwargs.get("images", [])
            messages = cls._prepare_messages(prompt, images)

            response = client.chat.completions.create(
                model=bonded_instance.model_name,
                messages=messages,
                temperature=(
                    temperature if temperature is not None else bonded_instance.temperature
                ),
                max_tokens=max_tokens or bonded_instance.max_tokens,
                top_p=kwargs.get("top_p", bonded_instance.top_p),
                n=1,
                stream=False,
                timeout=kwargs.get(
                    "timeout", getattr(bonded_instance, "request_timeout", 120)
                ),
            )
            content = response.choices[0].message.content
            return {
                "success": True,
                "text": content,
                "model": bonded_instance.model_name,
            }
        except Exception as e:
            logger.error(f"Error generating text with Bifrost: {e}")
            return {"success": False, "error": str(e)}

    @classmethod
    def _to_openai_messages(
        cls, messages: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Translate provider-neutral messages to the OpenAI chat wire format.

        Assistant tool-call turns and ``tool`` result turns are expanded into
        the ``tool_calls`` / ``tool_call_id`` shape the OpenAI API requires;
        plain messages pass through unchanged.
        """
        out: List[Dict[str, Any]] = []
        for message in messages:
            role = message.get("role", "user")
            if role == "assistant" and message.get("tool_calls"):
                out.append(
                    {
                        "role": "assistant",
                        "content": message.get("content"),
                        "tool_calls": [
                            {
                                "id": tc["id"],
                                "type": "function",
                                "function": {
                                    "name": tc["name"],
                                    "arguments": tc["arguments"],
                                },
                            }
                            for tc in message["tool_calls"]
                        ],
                    }
                )
            elif role == "tool":
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": message.get("tool_call_id"),
                        "content": message.get("content") or "",
                    }
                )
            else:
                out.append({"role": role, "content": message.get("content") or ""})
        return out

    @classmethod
    def _normalize_chat_response(
        cls, response: Any, model_name: str
    ) -> Dict[str, Any]:
        """Translate an OpenAI chat-completion response to the neutral shape.

        Extracts the assistant message content and, when present, its
        ``tool_calls`` (collapsed to ``{id, name, arguments}``) plus the
        ``finish_reason`` so the caller's tool loop knows whether to continue.
        """
        choice = response.choices[0]
        message = choice.message
        tool_calls = None
        if getattr(message, "tool_calls", None):
            tool_calls = [
                {
                    "id": tc.id,
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                }
                for tc in message.tool_calls
            ]
        # Some upstreams (e.g. Ollama via Bifrost) return the model's
        # deliberation in a non-standard ``reasoning`` field — captured here (from
        # the field or model_extra) so the agent's thought survives even when
        # ``content`` is empty on a tool-call turn.
        reasoning = getattr(message, "reasoning", None)
        if reasoning is None:
            extra = getattr(message, "model_extra", None) or {}
            reasoning = extra.get("reasoning")
        return {
            "success": True,
            "message": {
                "role": "assistant",
                "content": message.content,
                "reasoning": reasoning,
                "tool_calls": tool_calls,
            },
            "finish_reason": choice.finish_reason,
            "model": model_name,
        }

    @classmethod
    def chat(
        cls,
        bonded_instance: AbstractProviderInstance,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Native OpenAI-compatible chat with optional tool-calling via Bifrost.

        Overrides the text-only base fallback: passes the full message list and
        (when supplied) the ``tools`` catalog through to Bifrost's Chat
        Completions surface, and returns the model's ``tool_calls`` when it
        chooses to call one, so the agent-turn loop can execute the tool and
        continue the conversation.
        """
        try:
            client = bonded_instance.client
            create_kwargs: Dict[str, Any] = {
                "model": bonded_instance.model_name,
                "messages": cls._to_openai_messages(messages),
                "temperature": (
                    temperature
                    if temperature is not None
                    else bonded_instance.temperature
                ),
                "max_tokens": max_tokens or bonded_instance.max_tokens,
                "top_p": kwargs.get("top_p", bonded_instance.top_p),
                "n": 1,
                "stream": False,
                "timeout": kwargs.get(
                    "timeout", getattr(bonded_instance, "request_timeout", 120)
                ),
            }
            if tools:
                create_kwargs["tools"] = tools
                tool_choice = kwargs.get("tool_choice")
                if tool_choice is not None:
                    create_kwargs["tool_choice"] = tool_choice
            response = client.chat.completions.create(**create_kwargs)
            return cls._normalize_chat_response(response, bonded_instance.model_name)
        except Exception as e:
            logger.error(f"Error in Bifrost chat: {e}")
            return {"success": False, "error": str(e)}

    @classmethod
    @ability(name="embedding_generation")
    def generate_embeddings(
        cls, bonded_instance: AbstractProviderInstance, text: str, **kwargs
    ) -> Dict[str, Any]:
        """Generate text embeddings through the Bifrost gateway."""
        try:
            client = bonded_instance.client
            model = kwargs.get("model", "text-embedding-3-small")
            response = client.embeddings.create(model=model, input=text)
            embedding = response.data[0].embedding
            return {
                "success": True,
                "embedding": embedding,
                "dimensions": len(embedding),
                "model": model,
            }
        except Exception as e:
            logger.error(f"Error generating embeddings with Bifrost: {e}")
            return {"success": False, "error": str(e)}
