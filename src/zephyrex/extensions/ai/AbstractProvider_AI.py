from abc import abstractmethod
from typing import Any, Dict, List, Optional, Union

import numpy as np

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractProviderInstance,
    AbstractStaticProvider,
)
from zephyrex.logic.BLL_Providers import ProviderInstanceModel


class AbstractAIProviderInstance(AbstractProviderInstance):
    """
    Abstract base class for AI provider instances.
    Provides common AI functionality for bonded provider instances.
    """

    def __init__(self, provider_instance: ProviderInstanceModel):
        """
        Initialize AI provider instance.

        Args:
            provider_instance: The provider instance configuration
        """
        self.provider_instance = provider_instance
        self.ai_model = getattr(provider_instance, "model_name", "")
        self.ai_max_tokens = getattr(provider_instance, "max_tokens", 4096)
        self.ai_temperature = getattr(provider_instance, "temperature", 0.7)
        self.ai_top_p = getattr(provider_instance, "top_p", 0.7)

    @abstractmethod
    async def inference(
        self, prompt: str, tokens: int = 0, images: List[str] = []
    ) -> str:
        """
        Perform inference using the provider's model.

        Args:
            prompt: Input text prompt
            tokens: Number of tokens in the input (0 if unknown)
            images: List of image paths or URLs for multimodal models

        Returns:
            Generated text response
        """
        pass

    async def text_to_speech(self, text: str) -> Union[str, bytes]:
        """
        Convert text to speech.

        Args:
            text: Input text to convert to speech

        Returns:
            Either audio bytes or a URL to the audio file

        Raises:
            NotImplementedError: If the provider doesn't support TTS
        """
        raise NotImplementedError("text_to_speech not implemented by this provider")

    async def generate_image(self, prompt: str, **kwargs) -> str:
        """
        Generate an image from a text prompt.

        Args:
            prompt: Text description of the image to generate
            **kwargs: Additional image generation parameters

        Returns:
            Path or URL to the generated image

        Raises:
            NotImplementedError: If the provider doesn't support image generation
        """
        raise NotImplementedError("generate_image not implemented by this provider")

    async def transcribe_audio(self, audio_path: str) -> str:
        """
        Transcribe audio to text.

        Args:
            audio_path: Path to the audio file to transcribe

        Returns:
            Transcribed text

        Raises:
            NotImplementedError: If the provider doesn't support transcription
        """
        raise NotImplementedError("transcribe_audio not implemented by this provider")

    async def translate_audio(self, audio_path: str) -> str:
        """
        Translate audio to text.

        Args:
            audio_path: Path to the audio file to translate

        Returns:
            Translated text

        Raises:
            NotImplementedError: If the provider doesn't support audio translation
        """
        raise NotImplementedError("translate_audio not implemented by this provider")

    def embeddings(self, input: str) -> np.ndarray:
        """
        Generate embeddings for text input.

        Args:
            input: Text to generate embeddings for

        Returns:
            Embedding vector as numpy array

        Raises:
            NotImplementedError: If the provider doesn't support embeddings
        """
        raise NotImplementedError("embeddings not implemented by this provider")


# AbstractAIProvider has been moved to EXT_AI.py as EXT_AI.AbstractProvider
# This file now only contains the AbstractAIProviderInstance base class
