# SPDX-License-Identifier: AGPL-3.0-or-later
"""Any server that speaks the OpenAI API (Ollama, vLLM, LM Studio,
llama.cpp's server, LiteLLM): chat and embeddings at ``base_url`` (such as
``http://192.168.1.20:11434/v1``), with an optional key. A server on a
private network must be named in ``EGRESS_ALLOWED_HOSTS``."""

from typing import Any, ClassVar, Dict, Set, Tuple

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.ai.EXT_AI import CHAT, EMBEDDINGS
from zephyrex.extensions.ai.OpenAICompatible import OpenAICompatibleProvider


class PRV_OpenAICompatible_AI(OpenAICompatibleProvider):
    name: ClassVar[str] = "openai_compatible"
    friendly_name: ClassVar[str] = "OpenAI-compatible server"
    description: ClassVar[str] = "A model server that speaks the OpenAI API"
    _abilities: ClassVar[Set[str]] = {CHAT, EMBEDDINGS}
    _env: ClassVar[Dict[str, Any]] = {}
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting("base_url", "API address, ending /v1"),
        InstanceSetting("api_key", "API key, if any", secret=True, field="api_key"),
        InstanceSetting("model", "Chat model", field="model_name"),
        InstanceSetting("embedding_model", "Embedding model"),
    )
