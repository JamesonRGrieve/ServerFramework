# SPDX-License-Identifier: AGPL-3.0-or-later
"""OpenAI: chat (with tools and images), embeddings, image generation,
transcription and speech."""

from typing import Any, ClassVar, Dict, Set, Tuple

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.ai.EXT_AI import (
    CHAT,
    EMBEDDINGS,
    IMAGES,
    SPEECH,
    TRANSCRIPTION,
)
from zephyrex.extensions.ai.OpenAICompatible import OpenAICompatibleProvider


class PRV_OpenAI_AI(OpenAICompatibleProvider):
    name: ClassVar[str] = "openai"
    friendly_name: ClassVar[str] = "OpenAI"
    description: ClassVar[str] = "OpenAI's models"
    _abilities: ClassVar[Set[str]] = {CHAT, EMBEDDINGS, IMAGES, TRANSCRIPTION, SPEECH}
    _env: ClassVar[Dict[str, Any]] = {"OPENAI_API_KEY": ""}
    # Reasoning models refuse max_tokens.
    max_tokens_field: ClassVar[str] = "max_completion_tokens"
    embedding_model: ClassVar[str] = "text-embedding-3-small"
    image_model: ClassVar[str] = "gpt-image-1"
    transcription_model: ClassVar[str] = "gpt-4o-mini-transcribe"
    speech_model: ClassVar[str] = "gpt-4o-mini-tts"
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting(
            "api_key", "API key", env="OPENAI_API_KEY", secret=True, field="api_key"
        ),
        InstanceSetting(
            "model", "Chat model", default="gpt-5-mini", field="model_name"
        ),
        InstanceSetting("base_url", "API address", default="https://api.openai.com/v1"),
        InstanceSetting("embedding_model", "Embedding model"),
        InstanceSetting("image_model", "Image model"),
        InstanceSetting("transcription_model", "Transcription model"),
        InstanceSetting("speech_model", "Speech model"),
    )
