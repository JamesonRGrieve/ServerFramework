# SPDX-License-Identifier: AGPL-3.0-or-later
"""AI models: chat (with tool calls), text, embeddings, images,
transcription and speech, across OpenAI, Anthropic, Google, Azure OpenAI,
DeepSeek, xAI, Hugging Face, ElevenLabs and any OpenAI-compatible server
(Ollama, vLLM, LM Studio, Bifrost, AGInYourPC).

Each provider instance is one account or server: its API key, address
and model. An ability rotates over the instances of the providers that
offer it, so speech never lands on a chat-only model.

Messages are provider-neutral, and each provider translates them::

    {"role": "system" | "user" | "assistant" | "tool",
     "content": str | None,
     "images": [url or data: URL],                     # user, optional
     "tool_calls": [{"id", "name", "arguments": str}],  # assistant
     "tool_call_id": str}                               # tool

Tools take the OpenAI function-tool shape: ``{"type": "function",
"function": {"name", "description", "parameters": <JSON Schema>}}``.

A chat answers ``{"message": {"role": "assistant", "content",
"tool_calls"}, "finish_reason": "stop" | "tool_calls" | "length" | ...,
"model", "usage": {"input_tokens", "output_tokens"}}``. The tokens each
call uses are recorded against its instance (ProviderInstanceUsage), and
against the user it ran for when one is named.
"""

import base64
import binascii
import json
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Any, ClassVar

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractProviderInstance,
    AbstractStaticExtension,
    AbstractStaticProvider,
    InstanceSetting,
    ability,
)
from zephyrex.extensions.ExternalErrors import (
    InvalidInputExternalError,
    PermanentExternalError,
    TransientExternalError,
)
from zephyrex.lib.Dependencies import Dependencies
from zephyrex.lib.Environment import env
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

AI_REQUEST_TIMEOUT_SECONDS = 120.0
DEFAULT_MAX_TOKENS = 4096
MAX_MESSAGES = 1000
MAX_EMBEDDING_INPUTS = 2048
MAX_AUDIO_BYTES = 25 * 1024 * 1024
MAX_SPEECH_CHARACTERS = 4096
ROLES = ("system", "user", "assistant", "tool")
CHAT, EMBEDDINGS, IMAGES = "chat", "embeddings", "image_generation"
TRANSCRIPTION, SPEECH = "transcription", "text_to_speech"
# Transcription with each segment's start and end (captions, read-along).
TIMED_TRANSCRIPTION = "timed_transcription"
IMAGE_SIZES = ("1024x1024", "1024x1536", "1536x1024", "auto")
# Request fields a caller's ``params`` never replace: what is asked, of which
# model, and how it is answered are the provider's to set.
PROTECTED_REQUEST_FIELDS = frozenset({"messages", "model", "stream"})
# An instance's own request fields (an endpoint's extra_body), sent with every
# chat it serves, under the caller's ``params``.
EXTRA_BODY_SETTING = InstanceSetting(
    "extra_body",
    "Extra fields sent with every chat request, as a JSON object",
    multiline=True,
)


def checked_params(params: Any) -> dict[str, Any] | None:
    """``params``: extra request fields, a JSON object (or None)."""
    if params is None:
        return None
    if not isinstance(params, Mapping) or not all(isinstance(k, str) for k in params):
        raise InvalidInputExternalError("params is an object of request fields")
    return dict(params)


def _image_reference(url: Any) -> str:
    if not isinstance(url, str) or not url.startswith(
        ("https://", "http://", "data:image/")
    ):
        raise InvalidInputExternalError(
            "an image is an http(s) URL or a data:image/ URL"
        )
    return url


def checked_messages(messages: Any) -> list[dict[str, Any]]:
    """``messages`` in the neutral shape, or an invalid-input error."""
    if not isinstance(messages, list) or not messages:
        raise InvalidInputExternalError("messages is a non-empty list")
    if len(messages) > MAX_MESSAGES:
        raise InvalidInputExternalError(f"at most {MAX_MESSAGES} messages")
    checked = []
    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in ROLES:
            raise InvalidInputExternalError(
                f"each message is an object whose role is one of {', '.join(ROLES)}"
            )
        content = message.get("content")
        if content is not None and not isinstance(content, str):
            raise InvalidInputExternalError("a message's content is text")
        role = message["role"]
        entry: dict[str, Any] = {"role": role, "content": content}
        if message.get("images"):
            if role != "user":
                raise InvalidInputExternalError("only a user message has images")
            entry["images"] = [_image_reference(u) for u in message["images"]]
        if role == "assistant" and message.get("tool_calls"):
            entry["tool_calls"] = [_tool_call(c) for c in message["tool_calls"]]
        if role == "tool":
            if not isinstance(message.get("tool_call_id"), str):
                raise InvalidInputExternalError("a tool message has a tool_call_id")
            entry["tool_call_id"] = message["tool_call_id"]
        checked.append(entry)
    return checked


def _tool_call(call: Any) -> dict[str, str]:
    if not isinstance(call, dict) or not all(
        isinstance(call.get(k), str) for k in ("id", "name", "arguments")
    ):
        raise InvalidInputExternalError(
            "a tool call is {id, name, arguments} with arguments as JSON text"
        )
    return {"id": call["id"], "name": call["name"], "arguments": call["arguments"]}


def checked_tools(tools: Any) -> list[dict[str, Any]] | None:
    """``tools`` in the OpenAI function-tool shape, or None for no tools."""
    if not tools:
        return None
    if not isinstance(tools, list):
        raise InvalidInputExternalError("tools is a list")
    for tool in tools:
        function = tool.get("function") if isinstance(tool, dict) else None
        if (
            not isinstance(tool, dict)
            or tool.get("type") != "function"
            or not isinstance(function, dict)
            or not isinstance(function.get("name"), str)
        ):
            raise InvalidInputExternalError(
                'a tool is {"type": "function", "function": {"name", '
                '"description", "parameters"}}'
            )
    return list(tools)


def checked_texts(texts: Any) -> list[str]:
    if isinstance(texts, str):
        texts = [texts]
    if (
        not isinstance(texts, list)
        or not texts
        or not all(isinstance(t, str) and t for t in texts)
    ):
        raise InvalidInputExternalError("texts is a non-empty list of text")
    if len(texts) > MAX_EMBEDDING_INPUTS:
        raise InvalidInputExternalError(f"at most {MAX_EMBEDDING_INPUTS} texts")
    return texts


def audio_bytes(audio_base64: str) -> bytes:
    try:
        audio = base64.b64decode(audio_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise InvalidInputExternalError("audio_base64 is not base64") from exc
    if not audio:
        raise InvalidInputExternalError("the audio is empty")
    if len(audio) > MAX_AUDIO_BYTES:
        raise InvalidInputExternalError(
            f"audio is at most {MAX_AUDIO_BYTES // (1024 * 1024)} MiB"
        )
    return audio


def speech_text(text: str) -> str:
    if not text or not text.strip() or len(text) > MAX_SPEECH_CHARACTERS:
        raise InvalidInputExternalError(
            f"speech text is 1-{MAX_SPEECH_CHARACTERS} characters"
        )
    return text


def image_size(size: str) -> str:
    if size not in IMAGE_SIZES:
        raise InvalidInputExternalError(
            f"size is one of {', '.join(IMAGE_SIZES)}, not {size!r}"
        )
    return size


def flatten_messages(messages: list[dict[str, Any]]) -> str:
    """A conversation as one labelled prompt, tool calls included."""
    lines: list[str] = []
    for message in messages:
        role = str(message["role"]).capitalize()
        if message.get("content"):
            lines.append(f"{role}: {message['content']}")
        for call in message.get("tool_calls") or []:
            lines.append(f"{role} (tool call): {call['name']}({call['arguments']})")
    return "\n".join(lines)


def chat_answer(
    content: str | None,
    tool_calls: list[dict[str, str]] | None,
    finish_reason: str | None,
    model: str,
    input_tokens: int | None,
    output_tokens: int | None,
) -> dict[str, Any]:
    return {
        "message": {
            "role": "assistant",
            "content": content,
            "tool_calls": tool_calls or None,
        },
        "finish_reason": finish_reason,
        "model": model,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }


@dataclass
class PrimedStream:
    """A chat stream whose first event has arrived: what the rotation hands
    back once an instance has started answering, so a failure before the
    first delta moves on to the next instance and one after it ends the
    stream. Iterating it yields that first event, then the rest."""

    first: dict[str, Any]
    rest: AsyncIterator[dict[str, Any]]

    async def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        yield self.first
        async for event in self.rest:
            yield event


def sse_data(line: str) -> str | None:
    """A server-sent event line's data, or None for any other line."""
    if not line.startswith("data:"):
        return None
    return line[len("data:") :].strip()


def arguments_text(arguments: Any) -> str:
    """A tool call's arguments as JSON text, whichever form they came in."""
    return arguments if isinstance(arguments, str) else json.dumps(arguments or {})


class AbstractAIProvider(AbstractStaticProvider):
    """An AI model API; each instance is one account or server. A provider
    declares the abilities it offers (``chat``, ``embeddings``,
    ``image_generation``, ``transcription``, ``text_to_speech``) and
    implements those methods."""

    name: ClassVar[str] = ""
    friendly_name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    _abilities: ClassVar[set[str]] = {CHAT}
    _env: ClassVar[dict[str, Any]] = {}
    http_timeout_seconds: ClassVar[float] = AI_REQUEST_TIMEOUT_SECONDS

    @classmethod
    def bond_instance(cls, instance: ProviderInstanceModel) -> AbstractProviderInstance:
        return AbstractProviderInstance(instance)

    @classmethod
    def unsupported(cls, what: str) -> PermanentExternalError:
        return PermanentExternalError(
            f"{cls.friendly_name} does not offer {what}", provider=cls.name
        )

    @classmethod
    async def complete(
        cls,
        instance: ProviderInstanceModel,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        max_tokens: int | None,
        temperature: float | None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """One chat turn in this provider's API: a :func:`chat_answer`.
        ``max_tokens`` None asks for no limit of the caller's; ``params``
        are extra request fields (:meth:`request_params`), translated where
        the API names them differently and dropped where it has none."""
        raise cls.unsupported("chat")

    @classmethod
    async def complete_stream(
        cls,
        instance: ProviderInstanceModel,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        max_tokens: int | None,
        temperature: float | None,
        params: dict[str, Any] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """One chat turn as it is written: ``{"content": …}`` and
        ``{"reasoning": …}`` deltas, then one final ``{"finish_reason",
        "usage", "model", "tool_calls"}``. A provider whose API doesn't
        stream (or whose streaming isn't wired) answers whole: one content
        delta, then the final event."""
        answer = await cls.complete(
            instance, messages, tools, max_tokens, temperature, params=params
        )
        message = answer.get("message") or {}
        if message.get("content"):
            yield {"content": message["content"]}
        yield {
            "finish_reason": answer.get("finish_reason"),
            "usage": answer.get("usage") or {},
            "model": answer.get("model"),
            "tool_calls": message.get("tool_calls"),
        }

    @classmethod
    async def stream(
        cls,
        instance: ProviderInstanceModel,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        requester_id: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """:meth:`complete_stream` with its input checked first and its
        tokens recorded when the final event arrives, as :meth:`chat`
        records them."""
        async for event in cls.complete_stream(
            instance,
            checked_messages(messages),
            checked_tools(tools),
            max_tokens,
            temperature,
            params=checked_params(params),
        ):
            if "finish_reason" in event:
                record_usage(instance, event.get("usage") or {}, requester_id)
            yield event

    @classmethod
    async def open_stream(
        cls, instance: ProviderInstanceModel, *args: Any, **kwargs: Any
    ) -> PrimedStream:
        """:meth:`stream` started: it returns once the first event has
        arrived, so the rotation can still move on when an instance fails
        before answering (a refused key, a stall, a dead server)."""
        events = cls.stream(instance, *args, **kwargs)
        try:
            first = await events.__anext__()
        except StopAsyncIteration:
            raise TransientExternalError(
                f"{cls.friendly_name} ended the stream before answering",
                provider=cls.name,
            )
        return PrimedStream(first, events)

    @classmethod
    def request_params(
        cls, instance: ProviderInstanceModel, params: dict[str, Any] | None
    ) -> dict[str, Any]:
        """The extra request fields for a call on ``instance``: its
        ``extra_body`` setting (when the provider declares it), then the
        caller's ``params`` over it, less :data:`PROTECTED_REQUEST_FIELDS`.
        A malformed ``extra_body`` is the instance's fault, so the rotation
        moves on (transient)."""
        merged: dict[str, Any] = {}
        if any(s.key == EXTRA_BODY_SETTING.key for s in cls.instance_settings):
            raw = cls.setting(instance, EXTRA_BODY_SETTING.key)
            if raw and raw.strip():
                try:
                    extra = json.loads(raw)
                except ValueError:
                    extra = None
                if not isinstance(extra, dict):
                    raise TransientExternalError(
                        f"{cls.friendly_name} extra_body is not a JSON object",
                        provider=cls.name,
                    )
                merged.update(extra)
        merged.update(params or {})
        return {k: v for k, v in merged.items() if k not in PROTECTED_REQUEST_FIELDS}

    @classmethod
    async def chat(
        cls,
        instance: ProviderInstanceModel,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        requester_id: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """One chat turn, its tokens recorded against ``instance`` and the
        user named by ``requester_id``."""
        answer = await cls.complete(
            instance,
            checked_messages(messages),
            checked_tools(tools),
            max_tokens,
            temperature,
            params=checked_params(params),
        )
        record_usage(instance, answer.get("usage") or {}, requester_id)
        return answer

    @classmethod
    async def embed(
        cls, instance: ProviderInstanceModel, texts: list[str]
    ) -> dict[str, Any]:
        """``{embeddings: [[float]], model, dimensions}``, one per text."""
        raise cls.unsupported("embeddings")

    @classmethod
    async def image(
        cls, instance: ProviderInstanceModel, prompt: str, size: str
    ) -> dict[str, Any]:
        """``{images: [{base64 | url}], model}``."""
        raise cls.unsupported("image generation")

    @classmethod
    async def transcribe(
        cls,
        instance: ProviderInstanceModel,
        audio: bytes,
        filename: str,
        language: str | None,
    ) -> dict[str, Any]:
        """``{text, model}``."""
        raise cls.unsupported("transcription")

    @classmethod
    async def transcribe_timed(
        cls,
        instance: ProviderInstanceModel,
        audio: bytes,
        filename: str,
        language: str | None,
    ) -> dict[str, Any]:
        """``{text, model, segments: [{start, end, text}]}``, times in seconds."""
        raise cls.unsupported("timed transcription")

    @classmethod
    async def speak(
        cls, instance: ProviderInstanceModel, text: str, voice: str | None
    ) -> dict[str, Any]:
        """``{audio_base64, content_type, model, voice}``."""
        raise cls.unsupported("speech")

    @classmethod
    def model(cls, instance: ProviderInstanceModel) -> str:
        return str(cls.setting(instance, "model") or "")

    @classmethod
    def services(cls) -> list[str]:
        return ["ai"]


def record_usage(
    instance: ProviderInstanceModel,
    usage: Mapping[str, Any],
    requester_id: str | None,
) -> None:
    """Record a call's input and output tokens against ``instance`` (and
    the user it ran for), when an app is running to record them in."""
    from zephyrex.logic.BLL_Providers import ProviderInstanceUsageManager
    from zephyrex.pydantic2.registry import ModelRegistry

    registry = ModelRegistry.attached()
    if registry is None:
        return
    usages = ProviderInstanceUsageManager(
        model_registry=registry, requester_id=env("ROOT_ID")
    )
    for key in ("input_tokens", "output_tokens"):
        if isinstance(usage.get(key), int) and usage[key] > 0:
            usages.create(
                provider_instance_id=instance.id,
                key=key,
                value=usage[key],
                user_id=requester_id,
            )


class EXT_AI(AbstractStaticExtension):
    name: ClassVar[str] = "ai"
    version: ClassVar[str] = "3.0.0"
    description: ClassVar[str] = (
        "AI chat, embeddings, images, transcription and speech across "
        "hosted and self-hosted models"
    )

    _env: ClassVar[dict[str, Any]] = {}
    dependencies: ClassVar[Dependencies] = Dependencies([])
    _abilities: ClassVar[set[str]] = {
        "list_ai_models",
        "chat",
        "chat_stream",
        "generate_text",
        "embed",
        "generate_image",
        "transcribe",
        "transcribe_timed",
        "speak",
        "ai_usage",
    }

    @classmethod
    @ability("list_ai_models")
    async def list_ai_models(cls) -> list[dict[str, Any]]:
        """The configured model instances: id, name and provider."""
        return cls.instances_of()

    @classmethod
    @ability("chat")
    async def chat(
        cls,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        requester_id: str | None = None,
        as_user: str | None = None,
        instance_ids: list[str] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """One chat turn over the neutral message shape; with ``tools`` the
        model may answer with tool calls for the caller to run. ``params``
        are extra request fields (top_p, stop, seed, response_format, …),
        merged after the call's own; ``max_tokens`` None sends no limit.

        ``as_user`` runs it only on instances that user may use, their own
        first (:meth:`rotate_among`), and records its usage to them;
        ``instance_ids`` names those instances, in order. Without either it
        rotates over the root rotation, as the server's own call."""
        result: dict[str, Any] = await cls.rotate_capable(
            CHAT,
            "chat",
            checked_messages(messages),
            checked_tools(tools),
            max_tokens,
            temperature,
            requester_id or as_user,
            checked_params(params),
            as_user=as_user,
            instance_ids=instance_ids,
        )
        return result

    @classmethod
    @ability("chat_stream")
    async def chat_stream(
        cls,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        requester_id: str | None = None,
        as_user: str | None = None,
        instance_ids: list[str] | None = None,
        params: dict[str, Any] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """:meth:`chat` as it is written: ``{"content": …}`` and
        ``{"reasoning": …}`` deltas as the model produces them, then one
        final ``{"finish_reason", "usage", "model", "tool_calls"}``. The
        rotation moves on to the next instance only until the first delta
        arrives; after that, a failure ends the stream (it raises there).
        Usage is recorded when the final event arrives."""
        primed: PrimedStream = await cls.rotate_capable(
            CHAT,
            "open_stream",
            checked_messages(messages),
            checked_tools(tools),
            max_tokens,
            temperature,
            requester_id or as_user,
            checked_params(params),
            as_user=as_user,
            instance_ids=instance_ids,
        )
        async for event in primed:
            yield event

    @classmethod
    @ability("generate_text")
    async def generate_text(
        cls,
        prompt: str,
        max_tokens: int | None = None,
        temperature: float | None = None,
        requester_id: str | None = None,
        as_user: str | None = None,
        instance_ids: list[str] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """A reply to one prompt: ``{text, model, usage}`` (``as_user``,
        ``instance_ids`` and ``params`` as for :meth:`chat`)."""
        answer = await cls.chat(
            [{"role": "user", "content": prompt}],
            None,
            max_tokens,
            temperature,
            requester_id,
            as_user=as_user,
            instance_ids=instance_ids,
            params=params,
        )
        return {
            "text": answer["message"]["content"] or "",
            "model": answer["model"],
            "usage": answer["usage"],
        }

    @classmethod
    @ability("embed")
    async def embed(
        cls,
        texts: list[str],
        as_user: str | None = None,
        instance_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """An embedding vector per text (``as_user`` and ``instance_ids`` as
        for :meth:`chat`)."""
        result: dict[str, Any] = await cls.rotate_capable(
            EMBEDDINGS,
            "embed",
            checked_texts(texts),
            as_user=as_user,
            instance_ids=instance_ids,
        )
        return result

    @classmethod
    @ability("generate_image")
    async def generate_image(
        cls,
        prompt: str,
        size: str = "1024x1024",
        as_user: str | None = None,
        instance_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """An image for ``prompt`` (``as_user`` and ``instance_ids`` as for
        :meth:`chat`)."""
        if not prompt or not prompt.strip():
            raise InvalidInputExternalError("an image needs a prompt")
        result: dict[str, Any] = await cls.rotate_capable(
            IMAGES,
            "image",
            prompt,
            image_size(size),
            as_user=as_user,
            instance_ids=instance_ids,
        )
        return result

    @classmethod
    @ability("transcribe")
    async def transcribe(
        cls,
        audio_base64: str,
        filename: str = "audio.mp3",
        language: str | None = None,
    ) -> dict[str, Any]:
        """The text spoken in the audio (mp3, wav, m4a, ogg, webm, flac)."""
        result: dict[str, Any] = await cls.rotate_capable(
            TRANSCRIPTION, "transcribe", audio_bytes(audio_base64), filename, language
        )
        return result

    @classmethod
    @ability("transcribe_timed")
    async def transcribe_timed(
        cls,
        audio_base64: str,
        filename: str = "audio.mp3",
        language: str | None = None,
    ) -> dict[str, Any]:
        """The text spoken in the audio, in segments with their start and end
        (seconds): what captions and transcripts are made from."""
        result: dict[str, Any] = await cls.rotate_capable(
            TIMED_TRANSCRIPTION,
            "transcribe_timed",
            audio_bytes(audio_base64),
            filename,
            language,
        )
        return result

    @classmethod
    @ability("speak")
    async def speak(
        cls,
        text: str,
        voice: str | None = None,
        as_user: str | None = None,
        instance_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """``text`` as speech: base64 audio and its content type (``as_user``
        and ``instance_ids`` as for :meth:`chat`)."""
        result: dict[str, Any] = await cls.rotate_capable(
            SPEECH,
            "speak",
            speech_text(text),
            voice,
            as_user=as_user,
            instance_ids=instance_ids,
        )
        return result

    @classmethod
    @ability("ai_usage")
    async def ai_usage(cls) -> list[dict[str, Any]]:
        """Tokens used per model instance: input and output totals."""
        from zephyrex.logic.BLL_Providers import ProviderInstanceUsageManager

        root = cls.root
        if root is None:
            return []
        usages = ProviderInstanceUsageManager(
            model_registry=root.model_registry, requester_id=env("ROOT_ID")
        )
        totals: dict[str, dict[str, Any]] = {}
        for instance in cls.instances_of():
            entry = {**instance, "input_tokens": 0, "output_tokens": 0}
            for row in usages.list(provider_instance_id=instance["id"]):
                if row.key in ("input_tokens", "output_tokens"):
                    entry[row.key] += row.value or 0
            totals[instance["id"]] = entry
        return list(totals.values())
