# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hugging Face Inference Providers: open models through the router's
OpenAI-compatible chat API, the model named as on the Hub (optionally
``:provider``, such as ``openai/gpt-oss-120b:cerebras``)."""

from typing import Any, ClassVar, Dict, Set, Tuple

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.ai.EXT_AI import CHAT
from zephyrex.extensions.ai.OpenAICompatible import OpenAICompatibleProvider


class PRV_HuggingFace_AI(OpenAICompatibleProvider):
    name: ClassVar[str] = "huggingface"
    friendly_name: ClassVar[str] = "Hugging Face"
    description: ClassVar[str] = "Open models through Hugging Face Inference Providers"
    _abilities: ClassVar[Set[str]] = {CHAT}
    _env: ClassVar[Dict[str, Any]] = {"HF_TOKEN": ""}
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting(
            "api_key", "Access token", env="HF_TOKEN", secret=True, field="api_key"
        ),
        InstanceSetting(
            "model", "Model", default="openai/gpt-oss-120b", field="model_name"
        ),
        InstanceSetting(
            "base_url", "Router address", default="https://router.huggingface.co/v1"
        ),
    )
