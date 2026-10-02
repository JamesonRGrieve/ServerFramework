# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Bifrost LLM gateway (https://github.com/maximhq/bifrost): chat and
embeddings through its OpenAI-compatible API, the model routed by name
(``openai/gpt-5-mini``, ``anthropic/claude-sonnet-4-5``)."""

from typing import Any, ClassVar, Dict, Set, Tuple

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.ai.EXT_AI import CHAT, EMBEDDINGS
from zephyrex.extensions.ai.OpenAICompatible import OpenAICompatibleProvider


class PRV_Bifrost_AI(OpenAICompatibleProvider):
    name: ClassVar[str] = "bifrost"
    friendly_name: ClassVar[str] = "Bifrost"
    description: ClassVar[str] = "A Bifrost LLM gateway"
    _abilities: ClassVar[Set[str]] = {CHAT, EMBEDDINGS}
    _env: ClassVar[Dict[str, Any]] = {"BIFROST_API_URI": "", "BIFROST_API_KEY": ""}
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting(
            "base_url", "Gateway address, ending /v1", env="BIFROST_API_URI"
        ),
        InstanceSetting(
            "api_key",
            "Virtual key, if the gateway wants one",
            env="BIFROST_API_KEY",
            secret=True,
            field="api_key",
        ),
        InstanceSetting("model", "Model, as provider/model", field="model_name"),
        InstanceSetting("embedding_model", "Embedding model, as provider/model"),
    )
