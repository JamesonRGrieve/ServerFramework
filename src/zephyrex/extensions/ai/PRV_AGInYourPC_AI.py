import logging
import os
import re
import uuid
from typing import Any, ClassVar, Dict, List, Optional, Set

import numpy as np
import requests

try:
    import openai
except ImportError:
    openai = None

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractProviderInstance,
    ability,
)
from zephyrex.extensions.ai.EXT_AI import EXT_AI
from zephyrex.lib.Environment import env
from zephyrex.lib.Logging import logger
from zephyrex.logic.BLL_Providers import ProviderInstanceModel


class AGInYourPCProvider(EXT_AI.AbstractProvider):
    """Static AGInYourPC AI Provider implementation."""

    # Provider metadata
    name: ClassVar[str] = "AGInYourPC"
    friendly_name: ClassVar[str] = "AGInYourPC AI Service"
    platform: ClassVar[str] = "AGInYourPC"

    # Provider capabilities
    _abilities: ClassVar[Set[str]] = {
        "text_generation",
        "embedding_generation",
        "image_generation",
        "transcription",
        "text_to_speech",
    }

    # Environment variables this provider needs
    _env: Dict[str, Any] = {
        "AGINYOURPC_API_KEY": "",
        "AGINYOURPC_API_URI": "",
    }

    @classmethod
    def get_platform_name(cls) -> str:
        """Get the name of the AI platform this provider interacts with."""
        return cls.platform

    @classmethod
    def services(cls) -> List[str]:
        """Return a list of services provided by this provider."""
        return [
            "llm",
            "tts",
            "image",
            "transcription",
            "translation",
            "vision",
            "embeddings",
        ]

    @classmethod
    def validate_config(
        cls, instance: Optional[ProviderInstanceModel] = None
    ) -> List[str]:
        """Validate provider configuration."""
        issues = []

        if instance:
            if not instance.api_key:
                issues.append("API key is required")
            # if not instance.api_uri:
            #     issues.append("API URI is required")
        else:
            # Check environment variables
            if not env("AGINYOURPC_API_KEY"):
                issues.append("AGINYOURPC_API_KEY environment variable not set")
            if not env("AGINYOURPC_API_URI"):
                issues.append("AGINYOURPC_API_URI environment variable not set")

        return issues

    @classmethod
    def bond_instance(
        cls, instance: ProviderInstanceModel
    ) -> Optional[AbstractProviderInstance]:
        """Bond a provider instance for API operations."""
        if openai is None:
            logger.warning("openai package not available for bonding AGInYourPC")
            return None

        try:
            # Validate the instance
            issues = cls.validate_config(instance)
            if issues:
                logger.error(f"AGInYourPC instance validation failed: {issues}")
                return None

            # Create bonded instance
            class BondedAGInYourPCInstance(AbstractProviderInstance):
                def __init__(self, provider_instance: ProviderInstanceModel):
                    super().__init__(provider_instance)
                    self.api_key = provider_instance.api_key
                    self.api_uri = (
                        provider_instance.api_uri
                        if hasattr(provider_instance, "api_uri")
                        else env("AGINYOURPC_API_URI")
                    )
                    self.model_name = provider_instance.model_name or "aginyourpc"

                    # Configure API URI
                    if not self.api_uri.endswith("/"):
                        self.api_uri += "/"
                    if "v1/" not in self.api_uri:
                        self.api_uri += "v1/"

                    # Set up outputs URL
                    self.output_url = self.api_uri.replace("/v1/", "") + "/outputs/"

                    # Default settings from instance settings_json or environment
                    settings = (
                        provider_instance.settings_json
                        if hasattr(provider_instance, "settings_json")
                        and provider_instance.settings_json
                        else {}
                    )
                    self.max_tokens = int(settings.get("max_tokens", 8192))
                    self.temperature = float(settings.get("temperature", 1.33))
                    self.top_p = float(settings.get("top_p", 0.95))
                    self.voice = settings.get("voice", "HAL9000")
                    self.language = settings.get("language", "en")
                    self.transcription_model = settings.get(
                        "transcription_model", "base"
                    )

                    # Failure tracking
                    self.failure_count = 0

                @property
                def client(self):
                    """Get configured OpenAI client."""
                    client = openai.OpenAI(api_key=self.api_key, base_url=self.api_uri)
                    return client

            return BondedAGInYourPCInstance(instance)

        except Exception as e:
            logger.error(f"Error bonding AGInYourPC instance: {e}")
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
        """Generate text using AGInYourPC."""
        try:
            client = bonded_instance.client

            # Use instance defaults if not provided
            max_tokens = max_tokens or bonded_instance.max_tokens
            temperature = temperature or bonded_instance.temperature
            top_p = kwargs.get("top_p", bonded_instance.top_p)

            # Handle images if provided
            images = kwargs.get("images", [])
            messages = cls._prepare_messages(prompt, images)

            # Make API request
            response = client.chat.completions.create(
                model=bonded_instance.model_name,
                messages=messages,
                max_tokens=int(max_tokens),
                temperature=float(temperature),
                top_p=float(top_p),
                n=1,
                stream=False,
            )

            # Process response
            if hasattr(response, "choices") and response.choices:
                content = response.choices[0].message.content

                # Clean up response
                content = cls._clean_response(content, bonded_instance.output_url)

                return {
                    "success": True,
                    "text": content,
                    "usage": {
                        "prompt_tokens": (
                            response.usage.prompt_tokens
                            if hasattr(response, "usage")
                            else 0
                        ),
                        "completion_tokens": (
                            response.usage.completion_tokens
                            if hasattr(response, "usage")
                            else 0
                        ),
                        "total_tokens": (
                            response.usage.total_tokens
                            if hasattr(response, "usage")
                            else 0
                        ),
                    },
                    "model": bonded_instance.model_name,
                }
            else:
                return {"success": False, "error": "No response from model"}

        except Exception as e:
            logger.error(f"Error generating text with AGInYourPC: {e}")
            return {"success": False, "error": str(e)}

    @classmethod
    def _prepare_messages(cls, prompt: str, images: List[str]) -> List[Dict[str, Any]]:
        """Prepare messages for API request."""
        messages = []

        if images:
            content = [{"type": "text", "text": prompt}]

            for image in images:
                if image.startswith("http"):
                    content.append({"type": "image_url", "image_url": {"url": image}})
                else:
                    # Handle local files
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

            messages.append({"role": "user", "content": content})
        else:
            messages.append({"role": "user", "content": prompt})

        return messages

    @classmethod
    def _clean_response(cls, response: str, output_url: str) -> str:
        """Clean up the response text."""
        # Remove User: prefix if present
        if "User:" in response:
            # Split by User: and take everything after the first occurrence
            parts = response.split("User:", 1)
            if len(parts) > 1:
                # Take the part after "User:" and split by newline to get the response
                response_parts = parts[1].split("\n", 1)
                if len(response_parts) > 1:
                    response = response_parts[1]
                else:
                    response = parts[0]  # Fallback to original if no newline found

        # Clean up formatting
        response = response.lstrip()
        response = response.replace("<s>", "").replace("</s>", "")

        # Fix output URLs
        if "http://localhost:8091/outputs/" in response:
            response = response.replace("http://localhost:8091/outputs/", output_url)

        # Handle embedded media
        if output_url in response:
            urls = re.findall(f"{re.escape(output_url)}[^\"' ]+", response)
            if urls:
                urls = urls[0].split("\n\n")
                for url in urls:
                    file_type = url.split(".")[-1]
                    if file_type == "wav":
                        response = response.replace(
                            f'<audio controls><source src="{url}" type="audio/wav"></audio>',
                            url,
                        )

        return response

    @classmethod
    @ability(name="embedding_generation")
    def generate_embeddings(
        cls, bonded_instance: AbstractProviderInstance, text: str, **kwargs
    ) -> Dict[str, Any]:
        """Generate text embeddings using AGInYourPC."""
        try:
            client = bonded_instance.client

            response = client.embeddings.create(
                input=text,
                model="bge-m3",  # AGInYourPC's embedding model
            )

            if hasattr(response, "data") and response.data:
                embedding = response.data[0].embedding
                return {
                    "success": True,
                    "embedding": embedding,
                    "dimensions": len(embedding),
                    "model": "bge-m3",
                }
            else:
                return {"success": False, "error": "No embedding returned"}

        except Exception as e:
            logger.error(f"Error generating embeddings with AGInYourPC: {e}")
            return {"success": False, "error": str(e)}

    async def inference(
        self, prompt: str, tokens: int = 0, images: List[str] = []
    ) -> str:
        """Legacy inference method for backward compatibility."""
        # This method is kept for backward compatibility
        # In real usage, the static methods should be used instead
        logger.warning(
            "Using legacy inference method. Consider using generate_text instead."
        )
        raise NotImplementedError(
            "Legacy inference method not supported in static provider"
        )

    @classmethod
    @ability(name="transcription")
    def transcribe_audio(
        cls, bonded_instance: AbstractProviderInstance, audio_path: str, **kwargs
    ) -> Dict[str, Any]:
        """Transcribe audio to text."""
        try:
            client = bonded_instance.client

            with open(audio_path, "rb") as audio_file:
                transcription = client.audio.transcriptions.create(
                    model=bonded_instance.transcription_model, file=audio_file
                )

            return {
                "success": True,
                "text": transcription.text,
                "model": bonded_instance.transcription_model,
            }

        except Exception as e:
            logger.error(f"Error transcribing audio with AGInYourPC: {e}")
            return {"success": False, "error": str(e)}

    @classmethod
    def translate_audio(
        cls, bonded_instance: AbstractProviderInstance, audio_path: str, **kwargs
    ) -> Dict[str, Any]:
        """Translate audio to English."""
        try:
            client = bonded_instance.client

            with open(audio_path, "rb") as audio_file:
                translation = client.audio.translations.create(
                    model=bonded_instance.transcription_model, file=audio_file
                )

            return {
                "success": True,
                "text": translation.text,
                "model": bonded_instance.transcription_model,
            }

        except Exception as e:
            logger.error(f"Error translating audio with AGInYourPC: {e}")
            return {"success": False, "error": str(e)}

    @classmethod
    @ability(name="text_to_speech")
    def text_to_speech(
        cls, bonded_instance: AbstractProviderInstance, text: str, **kwargs
    ) -> Dict[str, Any]:
        """Convert text to speech."""
        try:
            client = bonded_instance.client

            tts_response = client.audio.speech.create(
                model="tts-1",
                voice=bonded_instance.voice,
                input=text,
                extra_body={"language": bonded_instance.language[:2].lower()},
            )

            return {
                "success": True,
                "audio": tts_response.content,
                "format": "wav",
                "voice": bonded_instance.voice,
            }

        except Exception as e:
            logger.error(f"Error generating speech with AGInYourPC: {e}")
            return {"success": False, "error": str(e)}

    @classmethod
    @ability(name="image_generation")
    def generate_image(
        cls, bonded_instance: AbstractProviderInstance, prompt: str, **kwargs
    ) -> Dict[str, Any]:
        """Generate image using AGInYourPC."""
        try:
            client = bonded_instance.client

            size = kwargs.get("size", "512x512")
            response_format = kwargs.get("response_format", "url")

            response = client.images.generate(
                model="dall-e-3",
                prompt=prompt,
                n=1,
                size=size,
                response_format=response_format,
            )

            if hasattr(response, "data") and response.data:
                url = response.data[0].url

                # Download and save image if needed
                if kwargs.get("save_local", False):
                    filename = f"{uuid.uuid4()}.png"
                    image_path = os.path.join("WORKSPACE", filename)

                    with open(image_path, "wb") as f:
                        f.write(requests.get(url).content)

                    aginfrastructure_uri = env("AGINFRASTRUCTURE_URI")
                    local_url = f"{aginfrastructure_uri}/outputs/{filename}"

                    return {
                        "success": True,
                        "url": local_url,
                        "original_url": url,
                        "filename": filename,
                        "model": "dall-e-3",
                    }
                else:
                    return {
                        "success": True,
                        "url": url,
                        "model": "dall-e-3",
                    }
            else:
                return {"success": False, "error": "No image generated"}

        except Exception as e:
            logger.error(f"Error generating image with AGInYourPC: {e}")
            return {"success": False, "error": str(e)}
