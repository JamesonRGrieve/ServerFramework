"""
Concrete GGUF model provider implementation.
This provider is dynamically created for each discovered GGUF model.
"""

from abc import abstractmethod
from typing import Any, ClassVar, Dict, List, Optional

from zephyrex.extensions.AbstractExtensionProvider import AbstractProviderInstance, ability
from zephyrex.extensions.local_ai.EXT_Local_AI import AbstractLocalAIProvider
from zephyrex.lib.Logging import logger
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

# Try importing required libraries
try:
    import psutil
    from llama_cpp import Llama

    DEPS_AVAILABLE = True
except ImportError as e:
    logger.warning(f"GGUF provider dependencies not available: {e}")
    DEPS_AVAILABLE = False


class PRV_GGUF_Model(AbstractLocalAIProvider):
    """
    Concrete GGUF model provider for AI inference.

    This provider class is instantiated for each discovered GGUF model
    and provides the actual AI transformation abilities (text_to_text, text_to_embedding).

    The provider works with the Provider Rotation System:
    - Registered as a Provider in the database with AI abilities
    - Creates ProviderInstances for different configurations
    - Handles mounting/unmounting of models
    - Tracks usage for billing/analytics
    """

    # Provider metadata - typically set dynamically when creating providers
    extension_type: ClassVar[str] = "local_ai_gguf"
    name: ClassVar[str] = "gguf_model"  # Will be overridden per model
    friendly_name: ClassVar[str] = "GGUF Model"  # Will be overridden per model
    description: ClassVar[str] = "GGUF model provider for local AI inference"

    # Provider abilities - AI transformations
    _abilities: ClassVar[set] = {
        "text_to_text",
        "text_to_embedding",
    }

    @classmethod
    def services(cls) -> List[str]:
        """Return a list of services provided by this provider."""
        return ["text_generation", "embedding"]

    @classmethod
    def get_platform_name(cls) -> str:
        """Get the name of the AI platform this provider interacts with."""
        return "llama.cpp (GGUF)"

    # Model instance cache
    _model_cache: ClassVar[Dict[str, Any]] = {}

    @classmethod
    def bond_instance(cls, instance: ProviderInstanceModel) -> AbstractProviderInstance:
        """
        Bond a provider instance - load the GGUF model into memory.

        Args:
            instance: Provider instance model containing configuration

        Returns:
            AbstractProviderInstance for use with abilities
        """
        if not DEPS_AVAILABLE:
            logger.error("GGUF dependencies not available for bonding")
            raise RuntimeError("GGUF dependencies not available")

        try:
            instance_id = instance.id
            model_path = getattr(instance, "model_path", None) or getattr(
                instance, "name", None
            )

            if not model_path:
                logger.error(f"No model path provided for instance {instance_id}")
                raise ValueError("No model path provided")

            # Get configuration from instance settings
            config = cls._build_config_from_instance(instance)

            # Load the model
            model = Llama(
                model_path=model_path,
                n_gpu_layers=config.get("n_gpu_layers", 0),
                n_ctx=config.get("context_window", 4096),
                n_batch=config.get("batch_size", 512),
                use_mmap=config.get("use_mmap", True),
                use_mlock=config.get("use_mlock", False),
                verbose=False,
            )

            # Cache the model
            bonded_instance = AbstractProviderInstance(
                instance_id=instance_id, provider_class=cls, config=config
            )

            cls._model_cache[instance_id] = {
                "model": model,
                "config": config,
                "model_path": model_path,
                "bonded_instance": bonded_instance,
            }

            logger.info(f"Successfully bonded GGUF model for instance {instance_id}")
            return bonded_instance

        except Exception as e:
            logger.error(f"Failed to bond GGUF model: {e}")
            raise

    @classmethod
    def unbond_instance(cls, instance_id: str) -> bool:
        """
        Unbond a provider instance - unload the GGUF model from memory.

        Args:
            instance_id: Provider instance ID

        Returns:
            True if unbonding successful, False otherwise
        """
        try:
            if instance_id in cls._model_cache:
                # Clean up the model
                del cls._model_cache[instance_id]
                logger.info(
                    f"Successfully unbonded GGUF model for instance {instance_id}"
                )
                return True
            else:
                logger.warning(f"No model found in cache for instance {instance_id}")
                return False

        except Exception as e:
            logger.error(f"Failed to unbond GGUF model: {e}")
            return False

    @classmethod
    @ability(name="text_to_text")
    async def generate_text(
        cls,
        bonded_instance: AbstractProviderInstance,
        prompt: str,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Generate text using the GGUF model.

        Args:
            bonded_instance: Bonded provider instance
            prompt: Input text prompt
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature
            **kwargs: Additional generation parameters

        Returns:
            Dictionary with generated text and metadata
        """
        if not DEPS_AVAILABLE:
            return {
                "success": False,
                "error": "GGUF dependencies not available",
            }

        try:
            # Get the model from cache
            instance_id = bonded_instance.instance_id
            model_data = cls._model_cache.get(instance_id)
            if not model_data:
                return {
                    "success": False,
                    "error": f"Model not loaded for instance {instance_id}",
                }

            model = model_data["model"]

            # Use defaults from kwargs if not provided
            max_tokens = max_tokens or kwargs.get("max_tokens", 512)
            temperature = temperature or kwargs.get("temperature", 0.7)
            top_p = kwargs.get("top_p", 0.9)
            stop = kwargs.get("stop", [])

            # Generate text
            response = model(
                prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
                stop=stop or [],
                echo=False,
            )

            generated_text = response["choices"][0]["text"]
            usage = response.get("usage", {})

            return {
                "success": True,
                "text": generated_text,
                "usage": {
                    "prompt_tokens": usage.get("prompt_tokens", 0),
                    "completion_tokens": usage.get("completion_tokens", 0),
                    "total_tokens": usage.get("total_tokens", 0),
                },
                "model": model_data.get("model_path"),
                "finish_reason": response["choices"][0].get("finish_reason"),
            }

        except Exception as e:
            logger.error(f"GGUF text generation error: {e}")
            return {
                "success": False,
                "error": str(e),
            }

    @classmethod
    @ability(name="text_to_embedding")
    async def generate_embeddings(
        cls,
        bonded_instance: AbstractProviderInstance,
        text: str,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Generate embeddings using the GGUF model.

        Args:
            bonded_instance: Bonded provider instance
            text: Input text to embed
            **kwargs: Additional parameters

        Returns:
            Dictionary with embeddings and metadata
        """
        if not DEPS_AVAILABLE:
            return {
                "success": False,
                "error": "GGUF dependencies not available",
            }

        try:
            # Get the model from cache
            instance_id = bonded_instance.instance_id
            model_data = cls._model_cache.get(instance_id)
            if not model_data:
                return {
                    "success": False,
                    "error": f"Model not loaded for instance {instance_id}",
                }

            model = model_data["model"]

            # Generate embeddings
            embeddings = model.embed(text)

            return {
                "success": True,
                "embedding": embeddings,
                "dimension": len(embeddings),
                "model": model_data.get("model_path"),
            }

        except Exception as e:
            logger.error(f"GGUF embedding generation error: {e}")
            return {
                "success": False,
                "error": str(e),
            }

    # Abstract method stubs for other abilities required by AbstractLocalAIProvider
    @classmethod
    @ability(name="text_to_image")
    async def generate_image(
        cls,
        bonded_instance: AbstractProviderInstance,
        prompt: str,
        width: int = 512,
        height: int = 512,
        **kwargs,
    ) -> Dict[str, Any]:
        """Generate image from text prompt (not supported by GGUF)."""
        return {
            "success": False,
            "error": "Image generation not supported by GGUF models",
        }

    @classmethod
    @ability(name="speech_to_text")
    async def transcribe_audio(
        cls,
        bonded_instance: AbstractProviderInstance,
        audio_data: bytes,
        **kwargs,
    ) -> Dict[str, Any]:
        """Transcribe audio to text (not supported by GGUF)."""
        return {
            "success": False,
            "error": "Audio transcription not supported by GGUF models",
        }

    @classmethod
    @ability(name="text_to_speech")
    async def synthesize_speech(
        cls,
        bonded_instance: AbstractProviderInstance,
        text: str,
        voice: str = "default",
        **kwargs,
    ) -> Dict[str, Any]:
        """Synthesize speech from text (not supported by GGUF)."""
        return {
            "success": False,
            "error": "Speech synthesis not supported by GGUF models",
        }

    # =====================================================================
    # Helper Methods
    # =====================================================================

    @classmethod
    def _build_config_from_instance(
        cls, instance: ProviderInstanceModel
    ) -> Dict[str, Any]:
        """Build configuration from provider instance data."""
        # Get settings from instance - this would come from ProviderInstanceSettings in real implementation
        settings = getattr(instance, "settings", {}) or {}

        config = {
            "n_gpu_layers": int(settings.get("N_GPU_LAYERS", 0)),
            "context_window": int(settings.get("MAX_CONTEXT", 4096)),
            "batch_size": int(settings.get("BATCH_SIZE", 512)),
            "use_mmap": settings.get("USE_MMAP", "true").lower() == "true",
            "use_mlock": settings.get("USE_MLOCK", "false").lower() == "true",
        }

        # Apply advanced settings if present
        if "TENSOR_SPLIT" in settings:
            import json

            try:
                config["tensor_split"] = json.loads(settings["TENSOR_SPLIT"])
            except json.JSONDecodeError:
                logger.warning("Invalid TENSOR_SPLIT configuration")

        if "ROPE_SCALING" in settings:
            import json

            try:
                config["rope_scaling"] = json.loads(settings["ROPE_SCALING"])
            except json.JSONDecodeError:
                logger.warning("Invalid ROPE_SCALING configuration")

        return config
