# SPDX-License-Identifier: AGPL-3.0-or-later
"""Google's Gemini models (chat with tools, embeddings) through the Gemini
API's OpenAI-compatible endpoint."""

from typing import Any, ClassVar, Dict, Set, Tuple

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.ai.EXT_AI import CHAT, EMBEDDINGS
from zephyrex.extensions.ai.OpenAICompatible import OpenAICompatibleProvider


class PRV_Google_AI(OpenAICompatibleProvider):
    name: ClassVar[str] = "google"
    friendly_name: ClassVar[str] = "Google Gemini"
    description: ClassVar[str] = "Google's Gemini models"
    _abilities: ClassVar[Set[str]] = {CHAT, EMBEDDINGS}
    _env: ClassVar[Dict[str, Any]] = {"GEMINI_API_KEY": ""}
    # The Gemini API refuses a bad key with a 400.
    refused_key_markers: ClassVar[Tuple[str, ...]] = (
        "API key not valid",
        "valid API key",
        "API_KEY_INVALID",
    )
    embedding_model: ClassVar[str] = "gemini-embedding-001"
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting(
            "api_key", "API key", env="GEMINI_API_KEY", secret=True, field="api_key"
        ),
        InstanceSetting(
            "model", "Model", default="gemini-2.5-flash", field="model_name"
        ),
        InstanceSetting(
            "base_url",
            "API address",
            default="https://generativelanguage.googleapis.com/v1beta/openai",
        ),
        InstanceSetting("embedding_model", "Embedding model"),
    )
