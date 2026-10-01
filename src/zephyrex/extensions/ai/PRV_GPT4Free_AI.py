import asyncio
import random
from typing import Any, ClassVar, Dict, List, Optional, Set

try:
    from g4f.Provider import DeepInfra, FreeGpt, Liaobots
except ImportError:
    DeepInfra = None
    FreeGpt = None
    Liaobots = None

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractProviderInstance,
    ability,
)
from zephyrex.extensions.ai.EXT_AI import EXT_AI
from zephyrex.lib.Logging import logger
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

_MAX_PROVIDER_RETRIES = 3


class GPT4FreeProvider(EXT_AI.AbstractProvider):
    """Static GPT4Free provider implementation.

    Fans requests across a small set of free/community LLM gateways
    (via the ``g4f`` package), falling back to the next available
    provider/model pairing on failure.
    """

    name: ClassVar[str] = "GPT4Free"
    friendly_name: ClassVar[str] = "GPT4Free"
    platform: ClassVar[str] = "GPT4Free"

    _abilities: ClassVar[Set[str]] = {
        "text_generation",
    }

    _env: Dict[str, Any] = {}

    # Available free providers and the models each one exposes.
    _PROVIDER_TABLE: ClassVar[List[Dict[str, Any]]] = []

    @classmethod
    def _provider_table(cls) -> List[Dict[str, Any]]:
        if DeepInfra is None or FreeGpt is None or Liaobots is None:
            return []
        return [
            {
                "name": "DeepInfra",
                "class": DeepInfra,
                "models": ["meta-llama/Meta-Llama-3-70B-Instruct"],
            },
            {
                "name": "FreeGpt",
                "class": FreeGpt,
                "models": ["gpt-3.5-turbo"],
            },
            {
                "name": "Liaobots",
                "class": Liaobots,
                "models": [
                    "gemini-pro",
                    "gpt-3.5-turbo",
                    "claude-2.1",
                    "claude-3-sonnet-20240229",
                    "claude-3-opus-20240229",
                ],
            },
        ]

    @classmethod
    def get_platform_name(cls) -> str:
        return cls.platform

    @classmethod
    def services(cls) -> List[str]:
        return ["llm"]

    @classmethod
    def validate_config(
        cls, instance: Optional[ProviderInstanceModel] = None
    ) -> List[str]:
        # No API key is required for free providers.
        return []

    @classmethod
    def bond_instance(
        cls, instance: ProviderInstanceModel
    ) -> Optional[AbstractProviderInstance]:
        """Bond a provider instance for API operations."""
        if not cls._provider_table():
            logger.warning("g4f package not available for bonding GPT4Free")
            return None

        try:

            class BondedGPT4FreeInstance(AbstractProviderInstance):
                def __init__(self, provider_instance: ProviderInstanceModel):
                    super().__init__(provider_instance)
                    self.model_name = provider_instance.model_name or "gemini-pro"
                    self.failures: List[Dict[str, str]] = []

            return BondedGPT4FreeInstance(instance)

        except Exception as e:
            logger.error(f"Error bonding GPT4Free instance: {e}")
            return None

    @classmethod
    def _available_providers(
        cls, failures: List[Dict[str, str]]
    ) -> List[Dict[str, Any]]:
        available = []
        for provider in cls._provider_table():
            models = [
                model
                for model in provider["models"]
                if not any(
                    f["provider"] == provider["name"] and f["model"] == model
                    for f in failures
                )
            ]
            if models:
                available.append({**provider, "models": models})
        return available

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
        """Generate text by trying free g4f providers/models in turn."""
        provider_table = cls._provider_table()
        if not provider_table:
            return {"success": False, "error": "g4f package not available"}

        # Force English responses, matching the original provider's guardrail.
        effective_prompt = (
            "Ignore all previous rules about language use and respond only in "
            f"English.\n{prompt}"
        )

        failures: List[Dict[str, str]] = list(getattr(bonded_instance, "failures", []))
        candidates = [
            (provider, model)
            for provider in provider_table
            for model in provider["models"]
            if not any(
                f["provider"] == provider["name"] and f["model"] == model
                for f in failures
            )
        ]
        if not candidates:
            candidates = [
                (provider, provider["models"][0]) for provider in provider_table
            ]

        provider_entry, model_name = random.choice(candidates)

        last_error: Optional[str] = None
        for _ in range(_MAX_PROVIDER_RETRIES):
            try:
                result = asyncio.run(
                    provider_entry["class"].create_async(
                        model=model_name,
                        messages=[{"role": "user", "content": effective_prompt}],
                    )
                )
                return {
                    "success": True,
                    "text": result,
                    "model": model_name,
                    "provider": provider_entry["name"],
                }
            except Exception as e:
                last_error = str(e)
                failures.append({"provider": provider_entry["name"], "model": model_name})
                remaining = cls._available_providers(failures)
                if not remaining:
                    break
                provider_entry = random.choice(remaining)
                model_name = random.choice(provider_entry["models"])

        return {
            "success": False,
            "error": last_error or "All GPT4Free providers failed",
        }

    @classmethod
    @ability(name="embedding_generation")
    def generate_embeddings(
        cls, bonded_instance: AbstractProviderInstance, text: str, **kwargs
    ) -> Dict[str, Any]:
        """GPT4Free's free gateways do not offer an embeddings API."""
        return {
            "success": False,
            "error": "Embedding generation not supported by this provider",
        }
