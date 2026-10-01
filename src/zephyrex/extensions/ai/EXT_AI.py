"""
AI extension for AGInfrastructure.
Implements the Provider Rotation System for AI model capabilities.
"""

from abc import abstractmethod
from typing import Any, ClassVar, Dict, List, Optional, Set, Type

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractProviderInstance,
    AbstractStaticExtension,
    AbstractStaticProvider,
    ability,
)
from zephyrex.lib.Dependencies import Dependencies, EXT_Dependency, PIP_Dependency
from zephyrex.lib.Logging import logger
from zephyrex.pydantic2.registry import classproperty
from zephyrex.logic.BLL_Providers import ProviderInstanceModel


class AbstractAIProvider(AbstractStaticProvider):
    """
    Abstract base class for AI service providers.
    Defines the common interface for all AI providers with static functionality.
    All AI providers should be static/abstract classes with no instantiation required.
    Integrates with the Provider Rotation System for failover and load balancing.
    """

    extension_type: ClassVar[str] = "ai"

    @classmethod
    @abstractmethod
    def bond_instance(
        cls, instance: ProviderInstanceModel
    ) -> Optional[AbstractProviderInstance]:
        """
        Bond a provider instance for API operations.

        Args:
            instance: ProviderInstanceModel with API credentials

        Returns:
            Bonded instance with configured SDK or None if bonding fails
        """
        pass

    @classmethod
    @abstractmethod
    def get_platform_name(cls) -> str:
        """Get the name of the AI platform this provider interacts with."""
        pass

    @classmethod
    def get_extension_info(cls) -> Dict[str, Any]:
        """Get information about the AI extension."""
        return {
            "name": "AI",
            "description": f"AI extension for {cls.get_platform_name()}",
            "platform": cls.get_platform_name(),
        }

    @classmethod
    @abstractmethod
    def services(cls) -> List[str]:
        """Return a list of services provided by this provider."""
        pass

    # Abstract abilities - must be implemented by providers
    @classmethod
    @abstractmethod
    @ability(name="text_generation")
    def generate_text(
        cls,
        bonded_instance: AbstractProviderInstance,
        prompt: str,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Generate text using this AI provider."""
        pass

    @classmethod
    @abstractmethod
    @ability(name="embedding_generation")
    def generate_embeddings(
        cls, bonded_instance: AbstractProviderInstance, text: str, **kwargs
    ) -> Dict[str, Any]:
        """Generate text embeddings using this AI provider."""
        pass

    @classmethod
    def chat(
        cls,
        bonded_instance: AbstractProviderInstance,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Run a single chat turn over a provider-neutral message list.

        This is the transport the agent-turn loop uses to talk to a model —
        distinct from ``generate_text`` (a single string prompt). ``chat``
        carries a whole conversation (system/user/assistant/tool messages) and,
        for tool-capable providers, a ``tools`` catalog, and can return the
        model's ``tool_calls`` so the caller can execute them and continue the
        loop.

        Message format is provider-neutral (providers translate to their own
        wire format)::

            {"role": "system"|"user"|"assistant"|"tool",
             "content": Optional[str],
             "tool_calls": [{"id": str, "name": str, "arguments": str}],  # assistant
             "tool_call_id": str}                                          # tool

        Tool format is the OpenAI function-tool shape (the de-facto standard;
        non-OpenAI providers translate)::

            {"type": "function",
             "function": {"name": str, "description": str,
                          "parameters": {<json-schema>}}}

        Returns::

            {"success": True,
             "message": {"role": "assistant", "content": Optional[str],
                         "tool_calls": Optional[List[{"id","name","arguments"}]]},
             "finish_reason": Optional[str], "model": str}

        or ``{"success": False, "error": str}`` on failure.

        This base implementation is a text-only fallback for providers that do
        not natively support chat/tools: it flattens ``messages`` into one
        prompt, calls ``generate_text``, and returns the reply as an assistant
        message with no ``tool_calls``. ``tools`` are ignored — a text-only
        model cannot call them, so this is honest capability degradation, not a
        silently-dropped tool call. Tool-capable providers override this method.
        """
        prompt = cls._flatten_messages_to_prompt(messages)
        result = cls.generate_text(
            bonded_instance,
            prompt=prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            **kwargs,
        )
        if not result.get("success"):
            return result
        return {
            "success": True,
            "message": {
                "role": "assistant",
                "content": result.get("text", ""),
                "tool_calls": None,
            },
            "finish_reason": "stop",
            "model": result.get("model", getattr(bonded_instance, "model_name", "")),
        }

    @staticmethod
    def _flatten_messages_to_prompt(messages: List[Dict[str, Any]]) -> str:
        """Flatten a provider-neutral message list into one labelled prompt.

        Used only by the text-only ``chat`` fallback. Role structure is kept as
        ``Role: content`` lines so a plain completion model still sees the
        conversation, and tool-call / tool-result turns are rendered textually
        so nothing is silently dropped.
        """
        lines: List[str] = []
        for msg in messages:
            role = str(msg.get("role", "user")).capitalize()
            content = msg.get("content")
            if content:
                text = content if isinstance(content, str) else str(content)
                lines.append(f"{role}: {text}")
            for tool_call in msg.get("tool_calls") or []:
                lines.append(
                    f"{role} (tool call): "
                    f"{tool_call.get('name')}({tool_call.get('arguments')})"
                )
        return "\n".join(lines)

    @classmethod
    @ability(name="image_generation")
    def generate_image(
        cls, bonded_instance: AbstractProviderInstance, prompt: str, **kwargs
    ) -> Dict[str, Any]:
        """Generate image using this AI provider (optional)."""
        return {
            "success": False,
            "error": "Image generation not supported by this provider",
        }

    @classmethod
    @ability(name="transcription")
    def transcribe_audio(
        cls, bonded_instance: AbstractProviderInstance, audio_path: str, **kwargs
    ) -> Dict[str, Any]:
        """Transcribe audio to text (optional)."""
        return {
            "success": False,
            "error": "Audio transcription not supported by this provider",
        }

    @classmethod
    @ability(name="text_to_speech")
    def text_to_speech(
        cls, bonded_instance: AbstractProviderInstance, text: str, **kwargs
    ) -> Dict[str, Any]:
        """Convert text to speech (optional)."""
        return {
            "success": False,
            "error": "Text-to-speech not supported by this provider",
        }


class EXT_AI(AbstractStaticExtension):
    """
    AI extension for AGInfrastructure.

    Provides comprehensive AI model functionality including text generation, embeddings,
    transcription, image generation, and more through the Provider Rotation System.
    This extension serves as the base framework for AI capabilities that can be
    extended by specific AI implementations.

    The extension focuses on:
    - Text generation and chat capabilities
    - Embedding generation for semantic search
    - Image generation and vision processing
    - Audio transcription and text-to-speech
    - Multi-modal AI interactions
    - Provider rotation for high availability
    - AI model management and optimization
    - Request tracking and analytics

    Usage:
        # Generate text using any available AI provider
        result = EXT_AI.root.rotate(
            EXT_AI.generate_text,
            prompt="What is artificial intelligence?",
            max_tokens=100,
            temperature=0.7
        )
    """

    # Extension metadata
    name: ClassVar[str] = "ai"
    friendly_name: ClassVar[str] = "AI Framework"
    version: ClassVar[str] = "2.0.0"
    description: ClassVar[str] = (
        "AI extension providing comprehensive AI model capabilities via Provider Rotation System"
    )

    # Environment variables that this extension needs
    _env: ClassVar[Dict[str, Any]] = {
        "AI_DEFAULT_MODEL": "gpt-4",
        "AI_MAX_TOKENS": "4096",
        "AI_TEMPERATURE": "0.7",
        "AI_TOP_P": "0.9",
        "AI_REQUEST_TIMEOUT": "60",
        "AI_MAX_RETRIES": "3",
        "AI_ENABLE_TRACKING": "true",
        "AI_COST_TRACKING": "true",
    }

    # Unified dependencies using the Dependencies class
    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            EXT_Dependency(
                name="conversations",
                friendly_name="Conversation Framework",
                reason="Required for conversing with agents",
                optional=False,
            ),
            PIP_Dependency(
                name="tiktoken",
                friendly_name="TikToken - OpenAI tokenizer library",
                optional=False,
                reason="Required for token counting across AI providers",
                semver=">=0.5.0",
            ),
            PIP_Dependency(
                name="openai",
                friendly_name="OpenAI Python client",
                optional=True,
                reason="Required for OpenAI provider functionality",
                semver=">=1.0.0",
            ),
            PIP_Dependency(
                name="anthropic",
                friendly_name="Anthropic Python client",
                optional=True,
                reason="Required for Anthropic/Claude provider functionality",
                semver=">=0.8.0",
            ),
        ]
    )

    # Meta abilities provided by this extension for managing AI operations
    _abilities: ClassVar[set] = {
        "manage_ai_providers",
        "configure_ai_models",
        "track_ai_usage",
        "optimize_model_selection",
    }

    @classproperty
    def pip_dependencies(cls):
        """Get PIP dependencies for backward compatibility."""
        return cls.dependencies.pip

    @classproperty
    def ext_dependencies(cls):
        """Get extension dependencies for backward compatibility."""
        return cls.dependencies.ext

    @classproperty
    def sys_dependencies(cls):
        """Get system dependencies for backward compatibility."""
        return cls.dependencies.sys

    @classmethod
    def has_ability(cls, ability: str) -> bool:
        """Check if this extension has a specific ability."""
        return ability in cls._abilities

    @ability
    @classmethod
    def manage_ai_providers(cls, **kwargs) -> Dict[str, Any]:
        """
        Meta ability: Manage AI provider configurations and status.

        Returns:
            Dict containing provider management information
        """
        try:
            providers = []
            for provider_class in cls.providers:
                providers.append(
                    {
                        "name": getattr(
                            provider_class, "name", provider_class.__name__
                        ),
                        "abilities": list(getattr(provider_class, "_abilities", set())),
                        "status": "available",
                    }
                )

            return {"success": True, "providers": providers, "count": len(providers)}
        except Exception as e:
            logger.error(f"Error managing AI providers: {e}")
            return {"success": False, "error": str(e)}

    @ability
    @classmethod
    def configure_ai_models(
        cls, provider_name: str, settings: Dict[str, Any], **kwargs
    ) -> Dict[str, Any]:
        """
        Meta ability: Configure AI model settings for a specific provider.

        Args:
            provider_name: Name of the provider to configure
            settings: Configuration settings

        Returns:
            Configuration result
        """
        try:
            return {
                "success": True,
                "provider": provider_name,
                "settings_applied": settings,
                "message": f"Configuration updated for {provider_name}",
            }
        except Exception as e:
            logger.error(f"Error configuring AI models: {e}")
            return {"success": False, "error": str(e)}

    @ability
    @classmethod
    def track_ai_usage(cls, **kwargs) -> Dict[str, Any]:
        """
        Meta ability: Track AI usage statistics across providers.

        Returns:
            Usage statistics
        """
        try:
            # In real implementation, would query ProviderInstanceUsage records
            return {
                "success": True,
                "total_requests": 0,
                "total_tokens": 0,
                "by_provider": {},
                "message": "Usage tracking data",
            }
        except Exception as e:
            logger.error(f"Error tracking AI usage: {e}")
            return {"success": False, "error": str(e)}

    @ability
    @classmethod
    def optimize_model_selection(
        cls, task_type: str, requirements: Dict[str, Any], **kwargs
    ) -> Dict[str, Any]:
        """
        Meta ability: Optimize model selection based on task requirements.

        Args:
            task_type: Type of AI task (text_generation, embedding, etc.)
            requirements: Task requirements (speed, quality, cost, etc.)

        Returns:
            Recommended model configuration
        """
        try:
            # Simple recommendation logic
            recommendations = []

            if task_type == "text_generation":
                if requirements.get("quality_priority", False):
                    recommendations.append(
                        {
                            "provider": "openai",
                            "model": "gpt-4",
                            "reason": "Highest quality text generation",
                        }
                    )
                elif requirements.get("cost_priority", False):
                    recommendations.append(
                        {
                            "provider": "openai",
                            "model": "gpt-3.5-turbo",
                            "reason": "Cost-effective text generation",
                        }
                    )

            return {
                "success": True,
                "task_type": task_type,
                "recommendations": recommendations,
                "requirements": requirements,
            }
        except Exception as e:
            logger.error(f"Error optimizing model selection: {e}")
            return {"success": False, "error": str(e)}

    @classmethod
    def get_seed_data(cls) -> List[Dict[str, Any]]:
        """
        Return seed data for AI providers and instances.
        """
        from zephyrex.lib.Environment import env

        providers_data = []
        instances_data = []

        # AGInYourPC Provider
        if env("AGINYOURPC_API_KEY") and env("AGINYOURPC_API_URI"):
            providers_data.append(
                {
                    "name": "AGInYourPC",
                    "friendly_name": "AGInYourPC AI Service",
                    "system": True,
                }
            )
            instances_data.append(
                {
                    "name": "Root_AGInYourPC",
                    "_provider_name": "AGInYourPC",
                    "api_key": env("AGINYOURPC_API_KEY"),
                    "api_uri": env("AGINYOURPC_API_URI"),
                    "model_name": "aginyourpc",
                    "enabled": True,
                }
            )
            logger.debug("Registering AGInYourPC provider via AI extension")

        # OpenAI Provider (if configured)
        if env("OPENAI_API_KEY"):
            providers_data.append(
                {
                    "name": "OpenAI",
                    "friendly_name": "OpenAI API Service",
                    "system": True,
                }
            )
            instances_data.append(
                {
                    "name": "Root_OpenAI",
                    "_provider_name": "OpenAI",
                    "api_key": env("OPENAI_API_KEY"),
                    "model_name": env("OPENAI_MODEL", "gpt-4"),
                    "enabled": True,
                }
            )
            logger.debug("Registering OpenAI provider via AI extension")

        return {"providers": providers_data, "instances": instances_data}

    @classmethod
    def transcribe_audio(cls, audio_file, **kwargs):
        try:
            temp_audio_file_path = "audio.mp3"
            if cls.root:
                result = cls.root.rotate(
                    provider_callback,
                    audio_file=audio_file,
                    audio_file_path=temp_audio_file_path,
                )
                return result
        except Exception as e:
            logger.error(f"Failed to transcribe audio: {e}")
        finally:
            import os

            if os.path.exists(temp_audio_file_path):
                os.remove(temp_audio_file_path)
                logger.debug(f"File '{temp_audio_file_path}' deleted successfully.")
            else:
                logger.debug(
                    f"File '{temp_audio_file_path}' does not exist, nothing to delete."
                )

    # Bind the module-level abstract provider base class so
    # ``EXT_AI.AbstractProvider`` resolves per the AbstractStaticExtension
    # contract (see zephyrex/extensions/email/EXT_EMail.py for the same
    # pattern).
    AbstractProvider = AbstractAIProvider


def provider_callback(provider_instance, **kwargs):
    providers = EXT_AI.providers
    for provider in providers:
        if provider.name.lower() == provider_instance.model_name.lower():
            bond = provider.bond_instance(provider_instance)
            if not bond:
                return {
                    "success": False,
                    "error": "Provider could not be bonded",
                }

            import base64

            audio_file = kwargs["audio_file"]
            audio_file_path = kwargs["audio_file_path"]

            decoded_data = base64.b64decode(audio_file)

            with open(audio_file_path, "wb") as file:
                file.write(decoded_data)

            return provider.transcribe_audio(bond, audio_path=audio_file_path, **kwargs)

    return {
        "success": False,
        "error": f"No provider found for model '{provider_instance.model_name}'",
    }
