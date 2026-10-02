# SPDX-License-Identifier: AGPL-3.0-or-later
"""The OpenAI API's wire format, which many model servers share: Chat
Completions (with function tools), embeddings, image generation, audio
transcription and speech. A provider names its address, its auth header,
the abilities it offers, and the error texts that mean a refused key on
the servers that refuse one with a 400."""

import base64
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Tuple

from zephyrex.extensions.ai.EXT_AI import (
    AbstractAIProvider,
    arguments_text,
    chat_answer,
)
from zephyrex.extensions.ExternalErrors import (
    AuthExternalError,
    InvalidInputExternalError,
    PermanentExternalError,
    TransientExternalError,
)
from zephyrex.logic.BLL_Providers import ProviderInstanceModel


def openai_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Neutral messages as Chat Completions messages."""
    found: List[Dict[str, Any]] = []
    for message in messages:
        role = message["role"]
        if role == "tool":
            found.append(
                {
                    "role": "tool",
                    "tool_call_id": message["tool_call_id"],
                    "content": message.get("content") or "",
                }
            )
            continue
        entry: Dict[str, Any] = {"role": role, "content": message.get("content")}
        if message.get("images"):
            entry["content"] = [
                {"type": "text", "text": message.get("content") or ""},
                *(
                    {"type": "image_url", "image_url": {"url": url}}
                    for url in message["images"]
                ),
            ]
        if message.get("tool_calls"):
            entry["tool_calls"] = [
                {
                    "id": call["id"],
                    "type": "function",
                    "function": {"name": call["name"], "arguments": call["arguments"]},
                }
                for call in message["tool_calls"]
            ]
        found.append(entry)
    return found


def openai_answer(answer: Mapping[str, Any], model: str) -> Dict[str, Any]:
    """A Chat Completions response as a neutral chat answer."""
    choices = answer.get("choices") or []
    if not choices:
        raise TransientExternalError("The model answered with no choices")
    choice = choices[0]
    message = choice.get("message") or {}
    calls = [
        {
            "id": str(call.get("id", "")),
            "name": str((call.get("function") or {}).get("name", "")),
            "arguments": arguments_text((call.get("function") or {}).get("arguments")),
        }
        for call in message.get("tool_calls") or []
    ]
    usage = answer.get("usage") or {}
    return chat_answer(
        message.get("content"),
        calls,
        choice.get("finish_reason"),
        str(answer.get("model") or model),
        usage.get("prompt_tokens"),
        usage.get("completion_tokens"),
    )


class OpenAICompatibleProvider(AbstractAIProvider):
    """A server speaking the OpenAI API at ``base(instance)``."""

    max_tokens_field: ClassVar[str] = "max_tokens"
    # Texts in a 400's body that mean the key was refused.
    refused_key_markers: ClassVar[Tuple[str, ...]] = ()
    embedding_model: ClassVar[str] = ""
    image_model: ClassVar[str] = ""
    transcription_model: ClassVar[str] = ""
    speech_model: ClassVar[str] = ""
    default_voice: ClassVar[str] = "alloy"

    @classmethod
    def base(cls, instance: ProviderInstanceModel) -> str:
        url = str(cls.setting(instance, "base_url") or "").rstrip("/")
        if not url:
            raise TransientExternalError(
                f"{cls.friendly_name} address not configured", provider=cls.name
            )
        return url

    @classmethod
    def auth_headers(cls, instance: ProviderInstanceModel) -> Dict[str, str]:
        key = cls.setting(instance, "api_key")
        return {"Authorization": f"Bearer {key}"} if key else {}

    @classmethod
    def required_model(cls, instance: ProviderInstanceModel) -> str:
        model = cls.model(instance)
        if not model:
            raise TransientExternalError(
                f"{cls.friendly_name} model not configured", provider=cls.name
            )
        return model

    @classmethod
    async def call(
        cls, instance: ProviderInstanceModel, path: str, **kwargs: Any
    ) -> Any:
        """POST ``path``; a refused key is an auth failure whatever status
        the server answers it with."""
        headers = {**cls.auth_headers(instance), **kwargs.pop("headers", {})}
        try:
            return await cls.http().post(
                f"{cls.base(instance)}{path}", headers=headers, **kwargs
            )
        except InvalidInputExternalError as exc:
            payload = str(exc.upstream_payload or "")
            if any(marker in payload for marker in cls.refused_key_markers):
                raise AuthExternalError(
                    f"{cls.friendly_name} refused the API key",
                    provider=cls.name,
                    upstream_status=exc.upstream_status,
                ) from exc
            raise

    @classmethod
    async def complete(
        cls,
        instance: ProviderInstanceModel,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        max_tokens: Optional[int],
        temperature: Optional[float],
    ) -> Dict[str, Any]:
        model = cls.required_model(instance)
        body: Dict[str, Any] = {"model": model, "messages": openai_messages(messages)}
        if tools:
            body["tools"] = tools
        if max_tokens is not None:
            body[cls.max_tokens_field] = max_tokens
        if temperature is not None:
            body["temperature"] = temperature
        return openai_answer(
            await cls.call(instance, "/chat/completions", json=body), model
        )

    @classmethod
    async def embed(
        cls, instance: ProviderInstanceModel, texts: List[str]
    ) -> Dict[str, Any]:
        model = str(cls.setting(instance, "embedding_model") or cls.embedding_model)
        answer = await cls.call(
            instance,
            "/embeddings",
            json={"model": model, "input": texts, "encoding_format": "float"},
        )
        rows = sorted(answer.get("data") or [], key=lambda row: row.get("index", 0))
        embeddings = [row["embedding"] for row in rows]
        if len(embeddings) != len(texts):
            raise TransientExternalError(
                f"{cls.friendly_name} returned {len(embeddings)} embeddings "
                f"for {len(texts)} texts",
                provider=cls.name,
            )
        return {
            "embeddings": embeddings,
            "model": str(answer.get("model") or model),
            "dimensions": len(embeddings[0]) if embeddings else 0,
        }

    @classmethod
    async def image(
        cls, instance: ProviderInstanceModel, prompt: str, size: str
    ) -> Dict[str, Any]:
        model = str(cls.setting(instance, "image_model") or cls.image_model)
        answer = await cls.call(
            instance,
            "/images/generations",
            json={"model": model, "prompt": prompt, "size": size, "n": 1},
        )
        images = [
            {"base64": row["b64_json"]} if row.get("b64_json") else {"url": row["url"]}
            for row in answer.get("data") or []
            if row.get("b64_json") or row.get("url")
        ]
        if not images:
            raise PermanentExternalError(
                f"{cls.friendly_name} returned no image", provider=cls.name
            )
        return {"images": images, "model": model}

    @classmethod
    async def transcribe(
        cls,
        instance: ProviderInstanceModel,
        audio: bytes,
        filename: str,
        language: Optional[str],
    ) -> Dict[str, Any]:
        model = str(
            cls.setting(instance, "transcription_model") or cls.transcription_model
        )
        form = {"model": model, "response_format": "json"}
        if language:
            form["language"] = language
        answer = await cls.call(
            instance,
            "/audio/transcriptions",
            data=form,
            files={"file": (filename, audio, "application/octet-stream")},
        )
        return {"text": str(answer.get("text", "")), "model": model}

    @classmethod
    async def speak(
        cls, instance: ProviderInstanceModel, text: str, voice: Optional[str]
    ) -> Dict[str, Any]:
        model = str(cls.setting(instance, "speech_model") or cls.speech_model)
        chosen = voice or cls.default_voice
        response = await cls.call(
            instance,
            "/audio/speech",
            json={
                "model": model,
                "voice": chosen,
                "input": text,
                "response_format": "mp3",
            },
            raw=True,
        )
        return {
            "audio_base64": base64.b64encode(response.content).decode(),
            "content_type": response.headers.get("content-type", "audio/mpeg"),
            "model": model,
            "voice": chosen,
        }
