# SPDX-License-Identifier: AGPL-3.0-or-later
"""Azure OpenAI (Azure AI Foundry): chat and embeddings through a
resource's v1 API (``https://<resource>.openai.azure.com``), the model
named by its deployment."""

from typing import Any, ClassVar, Dict, Set, Tuple

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.ai.EXT_AI import CHAT, EMBEDDINGS
from zephyrex.extensions.ai.OpenAICompatible import OpenAICompatibleProvider
from zephyrex.extensions.ExternalErrors import TransientExternalError
from zephyrex.logic.BLL_Providers import ProviderInstanceModel


class PRV_Azure_AI(OpenAICompatibleProvider):
    name: ClassVar[str] = "azure_openai"
    friendly_name: ClassVar[str] = "Azure OpenAI"
    description: ClassVar[str] = "OpenAI models deployed on Azure"
    _abilities: ClassVar[Set[str]] = {CHAT, EMBEDDINGS}
    _env: ClassVar[Dict[str, Any]] = {
        "AZURE_OPENAI_ENDPOINT": "",
        "AZURE_OPENAI_API_KEY": "",
    }
    max_tokens_field: ClassVar[str] = "max_completion_tokens"
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting(
            "endpoint",
            "Resource endpoint (https://<resource>.openai.azure.com)",
            env="AZURE_OPENAI_ENDPOINT",
        ),
        InstanceSetting(
            "api_key",
            "API key",
            env="AZURE_OPENAI_API_KEY",
            secret=True,
            field="api_key",
        ),
        InstanceSetting("model", "Chat deployment name", field="model_name"),
        InstanceSetting("embedding_model", "Embedding deployment name"),
    )

    @classmethod
    def base(cls, instance: ProviderInstanceModel) -> str:
        endpoint = str(cls.setting(instance, "endpoint") or "").rstrip("/")
        if not endpoint:
            raise TransientExternalError(
                "Azure OpenAI endpoint not configured", provider=cls.name
            )
        return f"{endpoint}/openai/v1"

    @classmethod
    def auth_headers(cls, instance: ProviderInstanceModel) -> Dict[str, str]:
        key = cls.setting(instance, "api_key")
        return {"api-key": str(key)} if key else {}
