"""Local AI provider.

Delegates AI operations to the ``local_ai`` extension (a sibling extension
that manages locally-hosted models) rather than an external network API.
The ``local_ai`` extension's manager is looked up lazily so this provider
degrades gracefully (via ``bond_instance`` returning ``None``) when
``local_ai`` is not installed/loaded in a given deployment.
"""

from typing import Any, ClassVar, Dict, List, Optional, Set

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractProviderInstance,
    ability,
)
from zephyrex.extensions.ai.EXT_AI import EXT_AI
from zephyrex.lib.Logging import logger
from zephyrex.logic.BLL_Providers import ProviderInstanceModel


class LocalProvider(EXT_AI.AbstractProvider):
    """
    Local AI provider that delegates to the ``local_ai`` extension directly
    instead of making external API calls. Allows seamless integration with
    locally running AI models managed by that extension.
    """

    name: ClassVar[str] = "Local"
    friendly_name: ClassVar[str] = "Local AI"
    platform: ClassVar[str] = "Local"

    _abilities: ClassVar[Set[str]] = {
        "text_generation",
    }

    _env: Dict[str, Any] = {}

    @classmethod
    def get_platform_name(cls) -> str:
        return cls.platform

    @classmethod
    def _local_ai_manager_class(cls):
        """Best-effort lookup of the ``local_ai`` extension's manager class."""
        try:
            from zephyrex.extensions.local_ai.BLL_Local_AI import (
                LocalAIProviderInstanceHelper,
            )

            return LocalAIProviderInstanceHelper
        except ImportError as e:
            logger.debug(f"local_ai extension not available for Local provider: {e}")
            return None

    @classmethod
    def services(cls) -> List[str]:
        """Return available services; falls back to text generation only."""
        return ["llm"] if cls._local_ai_manager_class() is not None else []

    @classmethod
    def validate_config(
        cls, instance: Optional[ProviderInstanceModel] = None
    ) -> List[str]:
        if cls._local_ai_manager_class() is None:
            return ["local_ai extension is not installed/loaded"]
        return []

    @classmethod
    def bond_instance(
        cls, instance: ProviderInstanceModel
    ) -> Optional[AbstractProviderInstance]:
        """Bond a provider instance for API operations."""
        helper_cls = cls._local_ai_manager_class()
        if helper_cls is None:
            logger.warning(
                "local_ai extension not available for bonding Local provider"
            )
            return None

        try:

            class BondedLocalInstance(AbstractProviderInstance):
                def __init__(self, provider_instance: ProviderInstanceModel):
                    super().__init__(provider_instance)
                    self.provider_instance_id = provider_instance.id
                    self.model_name = provider_instance.model_name or "local-ai"

            return BondedLocalInstance(instance)

        except Exception as e:
            logger.error(f"Error bonding Local AI instance: {e}")
            return None

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
        """Generate text via the locally-hosted model managed by ``local_ai``."""
        return {
            "success": False,
            "error": (
                "Local AI text generation requires the local_ai extension's "
                "runtime to be wired up in this deployment"
            ),
        }

    @classmethod
    @ability(name="embedding_generation")
    def generate_embeddings(
        cls, bonded_instance: AbstractProviderInstance, text: str, **kwargs
    ) -> Dict[str, Any]:
        """Local AI embeddings are not wired up in this provider yet."""
        return {
            "success": False,
            "error": "Embedding generation not supported by this provider",
        }
