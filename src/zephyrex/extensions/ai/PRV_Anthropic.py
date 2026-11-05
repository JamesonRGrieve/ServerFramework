# SPDX-License-Identifier: AGPL-3.0-or-later
"""Anthropic's Claude models through the Messages API: chat with tools
and images."""

import json
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Set, Tuple

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.ai.EXT_AI import (
    CHAT,
    DEFAULT_MAX_TOKENS,
    AbstractAIProvider,
    chat_answer,
)
from zephyrex.extensions.ExternalErrors import InvalidInputExternalError
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

API_VERSION = "2023-06-01"
_FINISH = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "tool_use": "tool_calls",
    "max_tokens": "length",
    "refusal": "content_filter",
}


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
    )

    @classmethod
    async def complete(
        cls,
        instance: ProviderInstanceModel,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        max_tokens: Optional[int],
        temperature: Optional[float],
    ) -> Dict[str, Any]:
        model = cls.model(instance)
        body = {
            "model": model,
            "max_tokens": max_tokens or DEFAULT_MAX_TOKENS,
            **anthropic_request(messages, tools),
        }
        if temperature is not None:
            body["temperature"] = temperature
        base = str(cls.setting(instance, "base_url")).rstrip("/")
        answer = await cls.http().post(
            f"{base}/messages",
            json=body,
            headers={
                "x-api-key": str(cls.setting(instance, "api_key") or ""),
                "anthropic-version": API_VERSION,
            },
        )
        return anthropic_answer(answer, model)
