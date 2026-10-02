# SPDX-License-Identifier: AGPL-3.0-or-later
"""DeepSeek's chat models (with tools)."""

from typing import Any, ClassVar, Dict, Set, Tuple

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.ai.EXT_AI import CHAT
from zephyrex.extensions.ai.OpenAICompatible import OpenAICompatibleProvider


class PRV_DeepSeek_AI(OpenAICompatibleProvider):
    name: ClassVar[str] = "deepseek"
    friendly_name: ClassVar[str] = "DeepSeek"
    description: ClassVar[str] = "DeepSeek's models"
    _abilities: ClassVar[Set[str]] = {CHAT}
    _env: ClassVar[Dict[str, Any]] = {"DEEPSEEK_API_KEY": ""}
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting(
            "api_key", "API key", env="DEEPSEEK_API_KEY", secret=True, field="api_key"
        ),
        InstanceSetting("model", "Model", default="deepseek-chat", field="model_name"),
        InstanceSetting("base_url", "API address", default="https://api.deepseek.com"),
    )
