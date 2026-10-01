from typing import Any, ClassVar, Dict, List, Optional, Set

try:
    from openai import AzureOpenAI
except ImportError:
    AzureOpenAI = None

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractProviderInstance,
    ability,
)
from zephyrex.extensions.ai.EXT_AI import EXT_AI
from zephyrex.lib.Environment import env
from zephyrex.lib.Logging import logger
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

_DEFAULT_API_VERSION = "2024-02-01"


class AzureProvider(EXT_AI.AbstractProvider):
    """Static Azure OpenAI Provider implementation."""

    name: ClassVar[str] = "Azure"
    friendly_name: ClassVar[str] = "Azure OpenAI"
    platform: ClassVar[str] = "Azure"

    _abilities: ClassVar[Set[str]] = {
        "text_generation",
    }

    _env: Dict[str, Any] = {
        "AZURE_API_KEY": "",
        "AZURE_API_URI": "",
    }

    @classmethod
    def get_platform_name(cls) -> str:
        return cls.platform

    @classmethod
    def services(cls) -> List[str]:
        return ["llm", "vision"]

    @classmethod
    def validate_config(
        cls, instance: Optional[ProviderInstanceModel] = None
    ) -> List[str]:
        issues = []
        if instance:
            if not instance.api_key:
                issues.append("API key is required")
        else:
            if not env("AZURE_API_KEY"):
                issues.append("AZURE_API_KEY environment variable not set")
            if not env("AZURE_API_URI"):
                issues.append("AZURE_API_URI environment variable not set")
        return issues

    @classmethod
    def bond_instance(
        cls, instance: ProviderInstanceModel
    ) -> Optional[AbstractProviderInstance]:
        """Bond a provider instance for API operations."""
        if AzureOpenAI is None:
            logger.warning("openai package not available for bonding Azure")
            return None

        try:
            issues = cls.validate_config(instance)
            if issues:
                logger.error(f"Azure instance validation failed: {issues}")
                return None

            class BondedAzureInstance(AbstractProviderInstance):
                def __init__(self, provider_instance: ProviderInstanceModel):
                    super().__init__(provider_instance)
                    self.api_key = provider_instance.api_key
                    self.api_uri = (
                        provider_instance.api_uri
                        if getattr(provider_instance, "api_uri", None)
                        else env("AZURE_API_URI")
                    )
                    if not self.api_uri.endswith("/"):
                        self.api_uri += "/"
                    self.model_name = provider_instance.model_name or "gpt-4o"

                    settings = (
                        provider_instance.settings_json
                        if hasattr(provider_instance, "settings_json")
                        and provider_instance.settings_json
                        else {}
                    )
                    self.max_tokens = int(settings.get("max_tokens", 4096))
                    self.temperature = float(settings.get("temperature", 0.7))
                    self.top_p = float(settings.get("top_p", 0.7))
                    self.api_version = settings.get(
                        "api_version", _DEFAULT_API_VERSION
                    )

                @property
                def client(self):
                    """Get configured Azure OpenAI client."""
                    return AzureOpenAI(
                        api_key=self.api_key,
                        api_version=self.api_version,
                        azure_endpoint=self.api_uri,
                        azure_deployment=self.model_name,
                    )

            return BondedAzureInstance(instance)

        except Exception as e:
            logger.error(f"Error bonding Azure instance: {e}")
            return None

    @classmethod
    def _prepare_messages(cls, prompt: str, images: List[str]) -> List[Dict[str, Any]]:
        if not images:
            return [{"role": "user", "content": prompt}]

        content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
        for image in images:
            if image.startswith("http"):
                content.append({"type": "image_url", "image_url": {"url": image}})
            else:
                import base64

                file_type = image.split(".")[-1]
                with open(image, "rb") as f:
                    image_base64 = base64.b64encode(f.read()).decode()
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/{file_type};base64,{image_base64}"
                        },
                    }
                )
        return [{"role": "user", "content": content}]

    @classmethod
    @ability(name="text_generation")
    def generate_text(
        cls,
        bonded_instance: AbstractProviderInstance,
        prompt: str,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Generate text using Azure OpenAI."""
        try:
            client = bonded_instance.client
            images = kwargs.get("images", [])
            messages = cls._prepare_messages(prompt, images)

            response = client.chat.completions.create(
                model=bonded_instance.model_name,
                messages=messages,
                temperature=(
                    temperature if temperature is not None else bonded_instance.temperature
                ),
                max_tokens=max_tokens or bonded_instance.max_tokens,
                top_p=kwargs.get("top_p", bonded_instance.top_p),
                n=1,
                stream=False,
            )
            content = response.choices[0].message.content
            return {
                "success": True,
                "text": content,
                "model": bonded_instance.model_name,
            }
        except Exception as e:
            logger.error(f"Error generating text with Azure: {e}")
            return {"success": False, "error": str(e)}

    @classmethod
    @ability(name="embedding_generation")
    def generate_embeddings(
        cls, bonded_instance: AbstractProviderInstance, text: str, **kwargs
    ) -> Dict[str, Any]:
        """Generate text embeddings using Azure OpenAI."""
        try:
            client = bonded_instance.client
            model = kwargs.get("model", "text-embedding-3-small")
            response = client.embeddings.create(model=model, input=text)
            embedding = response.data[0].embedding
            return {
                "success": True,
                "embedding": embedding,
                "dimensions": len(embedding),
                "model": model,
            }
        except Exception as e:
            logger.error(f"Error generating embeddings with Azure: {e}")
            return {"success": False, "error": str(e)}
