# SPDX-License-Identifier: AGPL-3.0-or-later
"""xAI's Grok models (chat, with tools)."""

from typing import Any, ClassVar, Dict, Set, Tuple

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.ai.EXT_AI import CHAT
from zephyrex.extensions.ai.OpenAICompatible import OpenAICompatibleProvider


class PRV_XAI_AI(OpenAICompatibleProvider):
    name: ClassVar[str] = "xai"
    friendly_name: ClassVar[str] = "xAI"
    description: ClassVar[str] = "xAI's Grok models"
    _abilities: ClassVar[Set[str]] = {CHAT}
    _env: ClassVar[Dict[str, Any]] = {"XAI_API_KEY": ""}
    # xAI refuses a bad key with a 400.
    refused_key_markers: ClassVar[Tuple[str, ...]] = ("Incorrect API key",)
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting(
            "api_key", "API key", env="XAI_API_KEY", secret=True, field="api_key"
        ),
        InstanceSetting("model", "Model", default="grok-4", field="model_name"),
        InstanceSetting("base_url", "API address", default="https://api.x.ai/v1"),
    )
