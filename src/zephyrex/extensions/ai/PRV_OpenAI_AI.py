"""
OpenAI provider for AGInfrastructure AI extension.
Provides comprehensive AI capabilities through OpenAI's API.
Fully static implementation compatible with the Provider Rotation System.
"""

from typing import Any, Dict, List, Optional, Set

try:
    import openai
except ImportError:
    openai = None
    import warnings

    warnings.warn(
        "OpenAI package currently missing, but in PIP_Dependencies, will likely install on run",
        ImportWarning,
    )

from zephyrex.extensions.AbstractExtensionProvider import AbstractProviderInstance
from zephyrex.extensions.ai.EXT_AI import EXT_AI
from zephyrex.lib.Dependencies import Dependencies, PIP_Dependency
from zephyrex.lib.Environment import env
from zephyrex.lib.Logging import logger
from zephyrex.logic.BLL_Providers import ProviderInstanceModel


class OpenAIProviderInstance(AbstractProviderInstance):
    """OpenAI-specific provider instance with SDK integration."""

    def __init__(self, sdk, provider_instance: ProviderInstanceModel):
        """
        Initialize OpenAI provider instance.

        Args:
            sdk: OpenAI client instance
            provider_instance: Provider instance configuration
        """
        self._sdk = sdk
        self.provider_instance = provider_instance

    @property
    def sdk(self):
        return self._sdk

    def generate_text(
        self, prompt: str, max_tokens: int = 4096, temperature: float = 0.7, **kwargs
    ) -> str:
        """
        Generate text using OpenAI's API.

        Args:
            prompt: Input text prompt
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature
            **kwargs: Additional generation parameters

        Returns:
            Generated text response
        """
        try:
            messages = [{"role": "user", "content": prompt}]

            # Handle multimodal input if images are provided
            images = kwargs.get("images", [])
            model_name = kwargs.get(
                "model", self.provider_instance.model_name or "gpt-4o"
            )

            if images and model_name in [
                "gpt-4-vision-preview",
                "gpt-4o",
                "gpt-4o-mini",
            ]:
                content = [{"type": "text", "text": prompt}]
                for image_url in images:
                    content.append(
                        {"type": "image_url", "image_url": {"url": image_url}}
                    )
                messages = [{"role": "user", "content": content}]

            response = self.sdk.chat.completions.create(
                model=model_name,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=kwargs.get("top_p", 0.9),
            )

            return response.choices[0].message.content

        except Exception as e:
            logger.error(f"OpenAI text generation error: {e}")
            raise

    def create_embedding(self, text: str, **kwargs) -> List[float]:
        """
        Generate embeddings using OpenAI's embedding API.

        Args:
            text: Text to generate embeddings for
            **kwargs: Additional embedding parameters

        Returns:
            Embedding vector as list of floats
        """
        try:
            model = kwargs.get("model", "text-embedding-3-small")
            response = self.sdk.embeddings.create(
                model=model, input=text, encoding_format="float"
            )

            return response.data[0].embedding

        except Exception as e:
            logger.error(f"OpenAI embeddings error: {e}")
            raise

    def generate_image(self, prompt: str, size: str = "1024x1024", **kwargs) -> str:
        """
        Generate an image using OpenAI's DALL-E.

        Args:
            prompt: Text description of the image to generate
            size: Image size specification
            **kwargs: Additional image generation parameters

        Returns:
            URL to the generated image
        """
        try:
            quality = kwargs.get("quality", "standard")
            model = kwargs.get("model", "dall-e-3")

            response = self.sdk.images.generate(
                model=model,
                prompt=prompt,
                size=size,
                quality=quality,
                n=1,
            )

            return response.data[0].url

        except Exception as e:
            logger.error(f"OpenAI image generation error: {e}")
            raise

    def transcribe_audio(self, audio_data: bytes, **kwargs) -> str:
        """
        Transcribe audio using OpenAI's Whisper API.

        Args:
            audio_data: Audio data as bytes
            **kwargs: Additional transcription parameters

        Returns:
            Transcribed text
        """
        try:
            import io

            audio_file = io.BytesIO(audio_data)
            audio_file.name = "audio.wav"  # OpenAI needs a filename

            transcript = self.sdk.audio.transcriptions.create(
                model="whisper-1", file=audio_file
            )

            return transcript.text

        except Exception as e:
            logger.error(f"OpenAI transcription error: {e}")
            raise

    def text_to_speech(self, text: str, voice: str = "alloy", **kwargs) -> bytes:
        """
        Convert text to speech using OpenAI's TTS API.

        Args:
            text: Input text to convert to speech
            voice: Voice to use for synthesis
            **kwargs: Additional TTS parameters

        Returns:
            Audio bytes
        """
        try:
            model = kwargs.get("model", "tts-1")
            response = self.sdk.audio.speech.create(
                model=model, voice=voice, input=text
            )
            return response.content

        except Exception as e:
            logger.error(f"OpenAI TTS error: {e}")
            raise


class PRV_OpenAI_AI(EXT_AI.AbstractProvider):
    """
    OpenAI provider for AGInfrastructure AI extension.
    Supports text generation, image generation, TTS, transcription, and embeddings.
    All functionality is provided through static class methods.
    Fully compatible with the Provider Rotation System.
    """

    # Static provider metadata - MUST have proper name for discovery
    name = "OpenAI"  # This is critical for provider discovery
    version = "2.0.0"
    description = "OpenAI AI provider with comprehensive capabilities"

    # Unified dependencies for OpenAI provider
    dependencies = Dependencies(
        [
            PIP_Dependency(
                name="openai",
                friendly_name="OpenAI Python Library",
                semver=">=1.0.0",
                reason="OpenAI API access",
                optional=False,
            ),
        ]
    )

    # Static abilities provided by this provider
    _abilities: Set[str] = {
        "text_generation",
        "embedding_generation",
        "image_generation",
        "audio_transcription",
        "text_to_speech",
        "vision_analysis",
    }

    @classmethod
    def bond_instance(
        cls, instance: ProviderInstanceModel
    ) -> Optional[OpenAIProviderInstance]:
        """
        Bond a provider instance with proper OpenAI SDK configuration.

        Args:
            instance: ProviderInstanceModel with API credentials

        Returns:
            Bonded instance with configured OpenAI SDK or None if openai not available
        """
        if openai is None:
            logger.warning("OpenAI library not available for bonding")
            return None

        try:
            # Get API key from instance or fallback to environment
            api_key = (
                instance.api_key
                if hasattr(instance, "api_key") and instance.api_key
                else env("OPENAI_API_KEY")
            )

            if not api_key:
                logger.error("No API key available for OpenAI provider instance")
                return None

            # Create OpenAI client
            client = openai.OpenAI(api_key=api_key)

            # Return bonded instance with the SDK
            return OpenAIProviderInstance(client, instance)

        except Exception as e:
            logger.error(f"Failed to bond OpenAI provider instance: {e}")
            return None

    @classmethod
    def get_platform_name(cls) -> str:
        """Get the platform name."""
        return "OpenAI"

    @classmethod
    def services(cls) -> List[str]:
        """Return list of services provided."""
        return ["text", "image", "audio", "embeddings", "vision"]

    @classmethod
    def generate_text(
        cls,
        bonded_instance: OpenAIProviderInstance,
        prompt: str,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Generate text using OpenAI provider."""
        try:
            result = bonded_instance.generate_text(
                prompt=prompt,
                max_tokens=max_tokens or 4096,
                temperature=temperature or 0.7,
                **kwargs,
            )
            return {
                "success": True,
                "text": result,
                "model": kwargs.get(
                    "model", bonded_instance.provider_instance.model_name or "gpt-4o"
                ),
                "tokens_used": len(result.split()),  # Approximate
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @classmethod
    def generate_embeddings(
        cls, bonded_instance: OpenAIProviderInstance, text: str, **kwargs
    ) -> Dict[str, Any]:
        """Create text embedding using OpenAI provider."""
        try:
            result = bonded_instance.create_embedding(text=text, **kwargs)
            return {
                "success": True,
                "embeddings": result,
                "dimensions": len(result) if result else 0,
                "model": kwargs.get("model", "text-embedding-3-small"),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @classmethod
    def generate_image(
        cls, bonded_instance: OpenAIProviderInstance, prompt: str, **kwargs
    ) -> Dict[str, Any]:
        """Generate image using OpenAI provider."""
        try:
            size = kwargs.get("size", "1024x1024")
            result = bonded_instance.generate_image(prompt=prompt, size=size, **kwargs)
            return {
                "success": True,
                "image_url": result,
                "model": kwargs.get("model", "dall-e-3"),
                "size": size,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @classmethod
    def transcribe_audio(
        cls, bonded_instance: OpenAIProviderInstance, audio_path: str, **kwargs
    ) -> Dict[str, Any]:
        """Transcribe audio using OpenAI provider."""
        try:
            # Read audio file to bytes
            with open(audio_path, "rb") as f:
                audio_data = f.read()
            result = bonded_instance.transcribe_audio(audio_data=audio_data, **kwargs)
            return {
                "success": True,
                "text": result,
                "model": "whisper-1",
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @classmethod
    def text_to_speech(
        cls, bonded_instance: OpenAIProviderInstance, text: str, **kwargs
    ) -> Dict[str, Any]:
        """Convert text to speech using OpenAI provider."""
        try:
            voice = kwargs.get("voice", "alloy")
            result = bonded_instance.text_to_speech(text=text, voice=voice, **kwargs)
            return {
                "success": True,
                "audio_data": result,
                "model": kwargs.get("model", "tts-1"),
                "voice": voice,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @classmethod
    def validate_config(cls) -> bool:
        """Validate OpenAI provider configuration."""
        api_key = env("OPENAI_API_KEY")
        return bool(api_key and openai is not None)

    @classmethod
    def get_models(cls) -> List[str]:
        """Get list of available models for this provider."""
        return [
            "gpt-4o",
            "gpt-4o-mini",
            "gpt-4-turbo",
            "gpt-3.5-turbo",
            "dall-e-3",
            "dall-e-2",
            "whisper-1",
            "tts-1",
            "tts-1-hd",
            "text-embedding-3-small",
            "text-embedding-3-large",
        ]

    @classmethod
    def get_capabilities(cls) -> Set[str]:
        """Return the capabilities this provider offers."""
        return cls._abilities.copy()
