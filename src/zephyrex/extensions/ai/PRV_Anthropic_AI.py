import base64
from typing import Any, ClassVar, Dict, List, Optional, Set

import httpx

try:
    import anthropic
except ImportError:
    anthropic = None

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractProviderInstance,
    ability,
)
from zephyrex.extensions.ai.EXT_AI import EXT_AI
from zephyrex.lib.Environment import env
from zephyrex.lib.Logging import logger
from zephyrex.logic.BLL_Providers import ProviderInstanceModel


class AnthropicProvider(EXT_AI.AbstractProvider):
    """Static Anthropic (Claude) AI Provider implementation."""

    # Provider metadata
    name: ClassVar[str] = "Anthropic"
    friendly_name: ClassVar[str] = "Anthropic Claude"
    platform: ClassVar[str] = "Anthropic"

    # Provider capabilities
    _abilities: ClassVar[Set[str]] = {
        "text_generation",
        "embedding_generation",
    }

    # Environment variables this provider needs
    _env: Dict[str, Any] = {
        "ANTHROPIC_API_KEY": "",
    }

    @classmethod
    def get_platform_name(cls) -> str:
        """Get the name of the AI platform this provider interacts with."""
        return cls.platform

    @classmethod
    def services(cls) -> List[str]:
        """Return a list of services provided by this provider."""
        return ["llm", "vision"]

    @classmethod
    def validate_config(
        cls, instance: Optional[ProviderInstanceModel] = None
    ) -> List[str]:
        """Validate provider configuration."""
        issues = []

        if instance:
            if not instance.api_key:
                issues.append("API key is required")
        elif not env("ANTHROPIC_API_KEY"):
            issues.append("ANTHROPIC_API_KEY environment variable not set")

        return issues

    @classmethod
    def bond_instance(
        cls, instance: ProviderInstanceModel
    ) -> Optional[AbstractProviderInstance]:
        """Bond a provider instance for API operations."""
        if anthropic is None:
            logger.warning("anthropic package not available for bonding")
            return None

        try:
            issues = cls.validate_config(instance)
            if issues:
                logger.error(f"Anthropic instance validation failed: {issues}")
                return None

            class BondedAnthropicInstance(AbstractProviderInstance):
                def __init__(self, provider_instance: ProviderInstanceModel):
                    super().__init__(provider_instance)
                    self.api_key = provider_instance.api_key or env(
                        "ANTHROPIC_API_KEY"
                    )
                    self.model_name = (
                        provider_instance.model_name or "claude-3-5-sonnet-20240620"
                    )

                    settings = (
                        provider_instance.settings_json
                        if hasattr(provider_instance, "settings_json")
                        and provider_instance.settings_json
                        else {}
                    )
                    self.max_tokens = int(settings.get("max_tokens", 4096))
                    self.temperature = float(settings.get("temperature", 0.7))

                @property
                def client(self):
                    """Get configured Anthropic client."""
                    return anthropic.Client(api_key=self.api_key)

            return BondedAnthropicInstance(instance)

        except Exception as e:
            logger.error(f"Error bonding Anthropic instance: {e}")
            return None

    @classmethod
    def _prepare_messages(cls, prompt: str, images: List[str]) -> List[Dict[str, Any]]:
        """Prepare messages (with optional images) for the Anthropic Messages API."""
        if not images:
            return [{"role": "user", "content": [{"type": "text", "text": prompt}]}]

        content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
        for image in images:
            if image.startswith("http"):
                image_base64 = base64.b64encode(httpx.get(image).content).decode(
                    "utf-8"
                )
            else:
                with open(image, "rb") as f:
                    image_base64 = base64.b64encode(f.read()).decode("utf-8")

            file_type = image.split(".")[-1] or "jpeg"
            if file_type == "jpg":
                file_type = "jpeg"

            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": f"image/{file_type}",
                        "data": image_base64,
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
        """Generate text using Anthropic Claude."""
        try:
            client = bonded_instance.client
            images = kwargs.get("images", [])
            messages = cls._prepare_messages(prompt, images)

            response = client.messages.create(
                model=bonded_instance.model_name,
                messages=messages,
                max_tokens=max_tokens or bonded_instance.max_tokens,
                temperature=(
                    temperature if temperature is not None else bonded_instance.temperature
                ),
            )

            text = response.content[0].text
            usage = getattr(response, "usage", None)
            return {
                "success": True,
                "text": text,
                "usage": {
                    "prompt_tokens": getattr(usage, "input_tokens", 0) if usage else 0,
                    "completion_tokens": (
                        getattr(usage, "output_tokens", 0) if usage else 0
                    ),
                },
                "model": bonded_instance.model_name,
            }
        except Exception as e:
            logger.error(f"Error generating text with Anthropic: {e}")
            return {"success": False, "error": str(e)}

    @classmethod
    @ability(name="embedding_generation")
    def generate_embeddings(
        cls, bonded_instance: AbstractProviderInstance, text: str, **kwargs
    ) -> Dict[str, Any]:
        """Anthropic does not offer an embeddings API."""
        return {
            "success": False,
            "error": "Embedding generation not supported by this provider",
        }
