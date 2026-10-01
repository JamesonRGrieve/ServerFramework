"""
Concrete PyTorch model provider implementation.
This provider is dynamically created for each discovered PyTorch model.
"""

from abc import abstractmethod
from typing import Any, ClassVar, Dict, List, Optional, Union

from zephyrex.extensions.AbstractExtensionProvider import AbstractProviderInstance, ability
from zephyrex.extensions.local_ai.EXT_Local_AI import AbstractLocalAIProvider
from zephyrex.lib.Logging import logger
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

# Try importing required libraries
try:
    import torch
    from transformers import (
        AutoModel,
        AutoModelForCausalLM,
        AutoTokenizer,
        pipeline,
        AutoModelForSeq2SeqLM,
        AutoModelForSequenceClassification,
    )

    DEPS_AVAILABLE = True
except ImportError as e:
    logger.warning(f"PyTorch provider dependencies not available: {e}")
    DEPS_AVAILABLE = False


class PRV_PyTorch_Model(AbstractLocalAIProvider):
    """
    Concrete PyTorch model provider for AI inference.

    This provider class is instantiated for each discovered PyTorch model
    and provides the actual AI transformation abilities (text_to_text, text_to_embedding, etc).

    The provider works with the Provider Rotation System:
    - Registered as a Provider in the database with AI abilities
    - Creates ProviderInstances for different configurations
    - Handles mounting/unmounting of models
    - Tracks usage for billing/analytics
    """

    # Provider metadata - typically set dynamically when creating providers
    extension_type: ClassVar[str] = "local_ai_pytorch"
    name: ClassVar[str] = "pytorch_model"  # Will be overridden per model
    friendly_name: ClassVar[str] = "PyTorch Model"  # Will be overridden per model
    description: ClassVar[str] = "PyTorch model provider for local AI inference"

    # Provider abilities - AI transformations
    _abilities: ClassVar[set] = {
        "text_to_text",
        "text_to_embedding",
        # Additional abilities can be added based on model type
        # "image_to_text", "text_to_image", "audio_to_text"
    }

    @classmethod
    def services(cls) -> List[str]:
        """Return a list of services provided by this provider."""
        return ["text_generation", "embedding", "chat_completion"]

    @classmethod
    def get_platform_name(cls) -> str:
        """Get the name of the AI platform this provider interacts with."""
        return "PyTorch/Transformers"

    # Model instance cache
    _model_cache: ClassVar[Dict[str, Any]] = {}

    @classmethod
    def bond_instance(cls, instance: ProviderInstanceModel) -> AbstractProviderInstance:
        """
        Bond a provider instance - load the PyTorch model into memory.

        Args:
            instance: Provider instance model containing configuration

        Returns:
            AbstractProviderInstance for use with abilities
        """
        if not DEPS_AVAILABLE:
            logger.error("PyTorch dependencies not available for bonding")
            raise RuntimeError("PyTorch dependencies not available")

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

            # Determine model architecture
            model_class = config.get("model_class", "AutoModelForCausalLM")

            # Load tokenizer
            tokenizer = AutoTokenizer.from_pretrained(
                model_path,
                trust_remote_code=config.get("trust_remote_code", False),
            )

            # Load model based on architecture
            if model_class == "AutoModelForCausalLM":
                model = AutoModelForCausalLM.from_pretrained(
                    model_path,
                    torch_dtype=getattr(torch, config.get("torch_dtype", "float16")),
                    device_map=config.get("device_map", "auto"),
                    low_cpu_mem_usage=config.get("low_cpu_mem_usage", True),
                    trust_remote_code=config.get("trust_remote_code", False),
                )
            elif model_class == "AutoModelForSeq2SeqLM":
                model = AutoModelForSeq2SeqLM.from_pretrained(
                    model_path,
                    torch_dtype=getattr(torch, config.get("torch_dtype", "float16")),
                    device_map=config.get("device_map", "auto"),
                    low_cpu_mem_usage=config.get("low_cpu_mem_usage", True),
                    trust_remote_code=config.get("trust_remote_code", False),
                )
            else:
                # Default to AutoModel
                model = AutoModel.from_pretrained(
                    model_path,
                    torch_dtype=getattr(torch, config.get("torch_dtype", "float16")),
                    device_map=config.get("device_map", "auto"),
                    low_cpu_mem_usage=config.get("low_cpu_mem_usage", True),
                    trust_remote_code=config.get("trust_remote_code", False),
                )

            # Cache the model and tokenizer
            bonded_instance = AbstractProviderInstance(
                instance_id=instance_id, provider_class=cls, config=config
            )

            cls._model_cache[instance_id] = {
                "model": model,
                "tokenizer": tokenizer,
                "config": config,
                "model_path": model_path,
                "bonded_instance": bonded_instance,
            }

            logger.info(f"Successfully bonded PyTorch model for instance {instance_id}")
            return bonded_instance

        except Exception as e:
            logger.error(f"Failed to bond PyTorch model: {e}")
            raise

    @classmethod
    def unbond_instance(cls, instance_id: str) -> bool:
        """
        Unbond a provider instance - unload the PyTorch model from memory.

        Args:
            instance_id: Provider instance ID

        Returns:
            True if unbonding successful, False otherwise
        """
        try:
            if instance_id in cls._model_cache:
                # Clean up the model
                model_data = cls._model_cache[instance_id]
                if "model" in model_data:
                    del model_data["model"]
                if "tokenizer" in model_data:
                    del model_data["tokenizer"]
                del cls._model_cache[instance_id]

                # Clear GPU cache if available
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()

                logger.info(
                    f"Successfully unbonded PyTorch model for instance {instance_id}"
                )
                return True
            else:
                logger.warning(f"No model found in cache for instance {instance_id}")
                return False

        except Exception as e:
            logger.error(f"Failed to unbond PyTorch model: {e}")
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
        Generate text using the PyTorch model.

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
                "error": "PyTorch dependencies not available",
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
            tokenizer = model_data["tokenizer"]

            # Use defaults from kwargs if not provided
            max_tokens = max_tokens or kwargs.get("max_tokens", 512)
            temperature = temperature or kwargs.get("temperature", 1.0)
            top_p = kwargs.get("top_p", 1.0)
            top_k = kwargs.get("top_k", 50)
            stop = kwargs.get("stop", [])

            # Prepare input (handle both string and chat format)
            chat_messages = kwargs.get("messages")
            if chat_messages and isinstance(chat_messages, list):
                # Chat format
                text = tokenizer.apply_chat_template(chat_messages, tokenize=False)
            else:
                text = prompt

            # Tokenize input
            inputs = tokenizer(text, return_tensors="pt", truncation=True)

            # Move to same device as model
            device = next(model.parameters()).device
            inputs = {k: v.to(device) for k, v in inputs.items()}

            # Generate
            with torch.no_grad():
                outputs = model.generate(
                    **inputs,
                    max_new_tokens=max_tokens,
                    temperature=temperature,
                    top_p=top_p,
                    top_k=top_k,
                    do_sample=temperature > 0,
                    pad_token_id=tokenizer.pad_token_id,
                    eos_token_id=tokenizer.eos_token_id,
                )

            # Decode output
            generated_ids = outputs[0][inputs["input_ids"].shape[1] :]
            generated_text = tokenizer.decode(generated_ids, skip_special_tokens=True)

            # Apply stop sequences if provided
            if stop:
                for stop_seq in stop:
                    if stop_seq in generated_text:
                        generated_text = generated_text[
                            : generated_text.index(stop_seq)
                        ]
                        break

            return {
                "success": True,
                "text": generated_text,
                "usage": {
                    "prompt_tokens": inputs["input_ids"].shape[1],
                    "completion_tokens": len(generated_ids),
                    "total_tokens": inputs["input_ids"].shape[1] + len(generated_ids),
                },
                "model": model_data.get("model_path"),
                "finish_reason": "stop",
            }

        except Exception as e:
            logger.error(f"PyTorch text generation error: {e}")
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
        Generate embeddings using the PyTorch model.

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
                "error": "PyTorch dependencies not available",
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
            tokenizer = model_data["tokenizer"]
            normalize = kwargs.get("normalize", True)

            # Ensure text is a list
            if isinstance(text, str):
                texts = [text]
            else:
                texts = [text]  # Single text input

            # Tokenize input
            inputs = tokenizer(
                texts,
                return_tensors="pt",
                truncation=True,
                padding=True,
                max_length=512,
            )

            # Move to same device as model
            device = next(model.parameters()).device
            inputs = {k: v.to(device) for k, v in inputs.items()}

            # Generate embeddings
            with torch.no_grad():
                outputs = model(**inputs)

                # Extract embeddings (mean pooling over sequence)
                if hasattr(outputs, "last_hidden_state"):
                    embeddings = outputs.last_hidden_state.mean(dim=1)
                elif hasattr(outputs, "pooler_output"):
                    embeddings = outputs.pooler_output
                else:
                    # Fallback to first output
                    embeddings = outputs[0].mean(dim=1)

                # Normalize if requested
                if normalize:
                    embeddings = torch.nn.functional.normalize(embeddings, p=2, dim=1)

                # Convert to list
                embeddings_list = embeddings.cpu().numpy().tolist()

            # Return single embedding if single text was provided
            if isinstance(text, str):
                embeddings_list = embeddings_list[0]

            return {
                "success": True,
                "embedding": embeddings_list,
                "dimension": embeddings.shape[-1],
                "model": model_data.get("model_path"),
            }

        except Exception as e:
            logger.error(f"PyTorch embedding generation error: {e}")
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
        """Generate image from text prompt (supported with diffusion models)."""
        # This could be implemented for diffusion models
        return {
            "success": False,
            "error": "Image generation not implemented for this PyTorch model type",
        }

    @classmethod
    @ability(name="speech_to_text")
    async def transcribe_audio(
        cls,
        bonded_instance: AbstractProviderInstance,
        audio_data: bytes,
        **kwargs,
    ) -> Dict[str, Any]:
        """Transcribe audio to text (supported with Whisper models)."""
        # This could be implemented for Whisper models
        return {
            "success": False,
            "error": "Audio transcription not implemented for this PyTorch model type",
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
        """Synthesize speech from text (supported with TTS models)."""
        # This could be implemented for TTS models
        return {
            "success": False,
            "error": "Speech synthesis not implemented for this PyTorch model type",
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
            "torch_dtype": settings.get("TORCH_DTYPE", "float16"),
            "device_map": settings.get("DEVICE_MAP", "auto"),
            "low_cpu_mem_usage": settings.get("LOW_CPU_MEM_USAGE", "true").lower()
            == "true",
            "trust_remote_code": settings.get("TRUST_REMOTE_CODE", "false").lower()
            == "true",
            "model_class": settings.get("MODEL_CLASS", "AutoModelForCausalLM"),
        }

        # Apply quantization settings
        if settings.get("LOAD_IN_8BIT", "false").lower() == "true":
            config["load_in_8bit"] = True
        elif settings.get("LOAD_IN_4BIT", "false").lower() == "true":
            config["load_in_4bit"] = True

        # Apply advanced settings if present
        if (
            "USE_FLASH_ATTENTION" in settings
            and settings["USE_FLASH_ATTENTION"].lower() == "true"
        ):
            config["use_flash_attention_2"] = True

        return config
