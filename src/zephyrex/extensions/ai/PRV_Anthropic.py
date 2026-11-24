# SPDX-License-Identifier: AGPL-3.0-or-later
"""Anthropic's Claude models through the Messages API: chat with tools
and images."""

import json
from typing import (
    Any,
    AsyncIterator,
    ClassVar,
    Dict,
    List,
    Mapping,
    Optional,
    Set,
    Tuple,
)

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.ai.EXT_AI import (
    CHAT,
    EXTRA_BODY_SETTING,
    AbstractAIProvider,
    chat_answer,
    sse_data,
)
from zephyrex.extensions.ExternalErrors import (
    InvalidInputExternalError,
    TransientExternalError,
)
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

API_VERSION = "2023-06-01"
_FINISH = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "tool_use": "tool_calls",
    "max_tokens": "length",
    "refusal": "content_filter",
}

# The Messages API requires max_tokens. A call asking for no limit sends the
# model's own output maximum (Anthropic's models overview), by model-name
# prefix, longest first; a model not listed takes the instance's
# max_output_tokens setting, else the call is refused as misconfigured.
MODEL_OUTPUT_MAXIMUM: Tuple[Tuple[str, int], ...] = (
    ("claude-opus-4-1", 32000),
    ("claude-opus-4", 32000),
    ("claude-sonnet-4-5", 64000),
    ("claude-sonnet-4", 64000),
    ("claude-haiku-4-5", 64000),
    ("claude-3-7-sonnet", 64000),
    ("claude-3-5-haiku", 8192),
    ("claude-3-haiku", 4096),
)
# Fields of the Messages API that a caller's params may set as they are.
NATIVE_FIELDS = frozenset(
    {"top_p", "top_k", "stop_sequences", "thinking", "metadata", "tool_choice"}
)
# Thinking budgets for reasoning_effort (and think=True, which means medium).
THINKING_BUDGET = {"low": 1024, "medium": 4096, "high": 16384}


def output_maximum(model: str) -> Optional[int]:
    for prefix, maximum in MODEL_OUTPUT_MAXIMUM:
        if model.startswith(prefix):
            return maximum
    return None


def anthropic_params(params: Mapping[str, Any]) -> Dict[str, Any]:
    """``params`` as Messages API fields: native ones as they are, ``stop``
    as ``stop_sequences``, ``think``/``reasoning_effort`` as ``thinking``;
    the rest (seed, penalties, logit_bias, response_format, min_p, chat
    template fields, …) have no equivalent and are dropped."""
    fields = {k: v for k, v in params.items() if k in NATIVE_FIELDS}
    stop = params.get("stop")
    if stop is not None and "stop_sequences" not in fields:
        fields["stop_sequences"] = [stop] if isinstance(stop, str) else list(stop)
    effort = params.get("reasoning_effort")
    if "thinking" not in fields and (params.get("think") or effort):
        budget = THINKING_BUDGET.get(str(effort or "medium"), THINKING_BUDGET["medium"])
        fields["thinking"] = {"type": "enabled", "budget_tokens": budget}
    return fields


def anthropic_image(url: str) -> Dict[str, Any]:
    if not url.startswith("data:"):
        return {"type": "image", "source": {"type": "url", "url": url}}
    header, _, data = url.partition(",")
    media_type = header[len("data:") :].split(";")[0]
    if ";base64" not in header or not data:
        raise InvalidInputExternalError("a data: image URL is base64")
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": media_type, "data": data},
    }


def tool_input(arguments: str) -> Any:
    try:
        return json.loads(arguments) if arguments else {}
    except json.JSONDecodeError as exc:
        raise InvalidInputExternalError("a tool call's arguments are JSON") from exc


def anthropic_request(
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]],
) -> Dict[str, Any]:
    """Neutral messages and tools as a Messages API body: system text at
    the top, tool calls as ``tool_use`` blocks, tool results as
    ``tool_result`` blocks in a user turn."""
    system = [m["content"] for m in messages if m["role"] == "system" and m["content"]]
    turns: List[Dict[str, Any]] = []
    for message in messages:
        role = message["role"]
        if role == "system":
            continue
        blocks: List[Dict[str, Any]] = []
        if role == "tool":
            blocks.append(
                {
                    "type": "tool_result",
                    "tool_use_id": message["tool_call_id"],
                    "content": message.get("content") or "",
                }
            )
            role = "user"
        else:
            if message.get("content"):
                blocks.append({"type": "text", "text": message["content"]})
            blocks.extend(anthropic_image(u) for u in message.get("images") or [])
            for call in message.get("tool_calls") or []:
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call["id"],
                        "name": call["name"],
                        "input": tool_input(call["arguments"]),
                    }
                )
        if turns and turns[-1]["role"] == role:
            turns[-1]["content"].extend(blocks)
        else:
            turns.append({"role": role, "content": blocks})
    body: Dict[str, Any] = {"messages": turns}
    if system:
        body["system"] = "\n\n".join(system)
    if tools:
        body["tools"] = [
            {
                "name": tool["function"]["name"],
                "description": tool["function"].get("description", ""),
                "input_schema": tool["function"].get("parameters")
                or {"type": "object", "properties": {}},
            }
            for tool in tools
        ]
    return body


def anthropic_answer(answer: Mapping[str, Any], model: str) -> Dict[str, Any]:
    blocks = answer.get("content") or []
    text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
    calls = [
        {
            "id": str(b.get("id", "")),
            "name": str(b.get("name", "")),
            "arguments": json.dumps(b.get("input") or {}),
        }
        for b in blocks
        if b.get("type") == "tool_use"
    ]
    usage = answer.get("usage") or {}
    reason = answer.get("stop_reason")
    return chat_answer(
        text or None,
        calls,
        _FINISH.get(str(reason), reason),
        str(answer.get("model") or model),
        usage.get("input_tokens"),
        usage.get("output_tokens"),
    )


class PRV_Anthropic_AI(AbstractAIProvider):
    name: ClassVar[str] = "anthropic"
    friendly_name: ClassVar[str] = "Anthropic"
    description: ClassVar[str] = "Anthropic's Claude models"
    _abilities: ClassVar[Set[str]] = {CHAT}
    _env: ClassVar[Dict[str, Any]] = {"ANTHROPIC_API_KEY": ""}
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting(
            "api_key",
            "API key",
            env="ANTHROPIC_API_KEY",
            secret=True,
            field="api_key",
        ),
        InstanceSetting(
            "model", "Model", default="claude-sonnet-4-5", field="model_name"
        ),
        InstanceSetting(
            "base_url", "API address", default="https://api.anthropic.com/v1"
        ),
        InstanceSetting(
            "max_output_tokens",
            "The model's output token maximum, sent when a call asks for no "
            "limit (needed for a model Anthropic's list here doesn't know)",
        ),
        EXTRA_BODY_SETTING,
    )

    @classmethod
    def uncapped_max_tokens(cls, instance: ProviderInstanceModel, model: str) -> int:
        """What a call asking for no limit sends as the required max_tokens:
        the instance's max_output_tokens, else the model's own output
        maximum. A model with neither is a configuration error (transient,
        so the rotation moves on), never a smaller limit sent quietly: that
        would cut the answer short."""
        configured = str(cls.setting(instance, "max_output_tokens") or "").strip()
        if configured.isdigit() and int(configured) > 0:
            return int(configured)
        known = output_maximum(model)
        if known is not None:
            return known
        raise TransientExternalError(
            f"Anthropic model {model} has no known output maximum: set "
            "max_output_tokens on this instance",
            provider=cls.name,
        )

    @classmethod
    async def complete(
        cls,
        instance: ProviderInstanceModel,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        max_tokens: Optional[int],
        temperature: Optional[float],
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        model = cls.model(instance)
        body = cls.messages_body(
            instance, model, messages, tools, max_tokens, temperature, params
        )
        base = str(cls.setting(instance, "base_url")).rstrip("/")
        answer = await cls.http().post(
            f"{base}/messages", json=body, headers=cls.headers(instance)
        )
        return anthropic_answer(answer, model)

    @classmethod
    def headers(cls, instance: ProviderInstanceModel) -> Dict[str, str]:
        return {
            "x-api-key": str(cls.setting(instance, "api_key") or ""),
            "anthropic-version": API_VERSION,
        }

    @classmethod
    async def complete_stream(
        cls,
        instance: ProviderInstanceModel,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        max_tokens: Optional[int],
        temperature: Optional[float],
        params: Optional[Dict[str, Any]] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        """The Messages API with ``stream: true``: text and thinking deltas
        as content and reasoning, tool-use input assembled, then the stop
        reason with the input tokens from ``message_start`` and the output
        tokens from the last ``message_delta``."""
        model = cls.model(instance)
        body = {
            **cls.messages_body(
                instance, model, messages, tools, max_tokens, temperature, params
            ),
            "stream": True,
        }
        base = str(cls.setting(instance, "base_url")).rstrip("/")
        input_tokens: Optional[int] = None
        output_tokens: Optional[int] = None
        reason: Optional[str] = None
        answered_model = model
        tools_by_index: Dict[int, Dict[str, str]] = {}
        async for line in cls.http().stream_lines(
            "POST", f"{base}/messages", json=body, headers=cls.headers(instance)
        ):
            data = sse_data(line)
            if not data:
                continue
            try:
                event = json.loads(data)
            except ValueError:
                continue
            kind = event.get("type")
            if kind == "message_start":
                message = event.get("message") or {}
                answered_model = str(message.get("model") or answered_model)
                input_tokens = (message.get("usage") or {}).get("input_tokens")
            elif kind == "content_block_start":
                block = event.get("content_block") or {}
                if block.get("type") == "tool_use":
                    tools_by_index[int(event.get("index", 0))] = {
                        "id": str(block.get("id", "")),
                        "name": str(block.get("name", "")),
                        "arguments": "",
                    }
            elif kind == "content_block_delta":
                delta = event.get("delta") or {}
                if delta.get("type") == "text_delta" and delta.get("text"):
                    yield {"content": str(delta["text"])}
                elif delta.get("type") == "thinking_delta" and delta.get("thinking"):
                    yield {"reasoning": str(delta["thinking"])}
                elif delta.get("type") == "input_json_delta":
                    call = tools_by_index.get(int(event.get("index", 0)))
                    if call is not None:
                        call["arguments"] += str(delta.get("partial_json") or "")
            elif kind == "message_delta":
                reason = (event.get("delta") or {}).get("stop_reason") or reason
                output_tokens = (event.get("usage") or {}).get(
                    "output_tokens", output_tokens
                )
            elif kind == "error":
                raise TransientExternalError(
                    f"Anthropic stream error: {(event.get('error') or {}).get('message')}",
                    provider=cls.name,
                )
        calls = [
            {**call, "arguments": call["arguments"] or "{}"}
            for _, call in sorted(tools_by_index.items())
        ]
        yield {
            "finish_reason": _FINISH.get(str(reason), reason),
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
            "model": answered_model,
            "tool_calls": calls or None,
        }

    @classmethod
    def messages_body(
        cls,
        instance: ProviderInstanceModel,
        model: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        max_tokens: Optional[int],
        temperature: Optional[float],
        params: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """A Messages API request body: the call's fields, its translated
        params, max_tokens (required: the model's maximum when uncapped) and
        a thinking budget that fits under it."""
        body = {
            "model": model,
            "max_tokens": max_tokens or cls.uncapped_max_tokens(instance, model),
            **anthropic_request(messages, tools),
        }
        if temperature is not None:
            body["temperature"] = temperature
        body.update(anthropic_params(cls.request_params(instance, params)))
        thinking = body.get("thinking")
        if isinstance(thinking, dict) and isinstance(
            thinking.get("budget_tokens"), int
        ):
            # The budget is at least 1024 and below max_tokens, or the API refuses.
            room = int(body["max_tokens"]) - 1
            if room < 1024:
                body.pop("thinking")
            elif thinking["budget_tokens"] > room:
                body["thinking"] = {**thinking, "budget_tokens": room}
        return body
