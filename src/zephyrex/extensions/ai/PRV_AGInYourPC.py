# SPDX-License-Identifier: AGPL-3.0-or-later
"""An AGInYourPC server: chat, embeddings, images, transcription and
speech through its OpenAI-compatible API (``base_url``, else
``AGINYOURPC_API_URI``)."""

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


class PRV_AGInYourPC_AI(OpenAICompatibleProvider):
    name: ClassVar[str] = "aginyourpc"
    friendly_name: ClassVar[str] = "AGInYourPC"
    description: ClassVar[str] = "A self-hosted AGInYourPC model server"
    _abilities: ClassVar[Set[str]] = {CHAT, EMBEDDINGS, IMAGES, TRANSCRIPTION, SPEECH}
    _env: ClassVar[Dict[str, Any]] = {
        "AGINYOURPC_API_URI": "",
        "AGINYOURPC_API_KEY": "",
    }
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting(
            "base_url", "API address, ending /v1", env="AGINYOURPC_API_URI"
        ),
        InstanceSetting(
            "api_key",
            "API key",
            env="AGINYOURPC_API_KEY",
            secret=True,
            field="api_key",
        ),
        InstanceSetting("model", "Chat model", field="model_name"),
        InstanceSetting("embedding_model", "Embedding model"),
        InstanceSetting("image_model", "Image model"),
        InstanceSetting("transcription_model", "Transcription model"),
        InstanceSetting("speech_model", "Speech model"),
    )
