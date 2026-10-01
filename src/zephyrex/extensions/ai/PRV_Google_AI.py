import os
import uuid
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Optional, Set

try:
    import google.generativeai as genai
except ImportError:
    genai = None

try:
    import gtts
except ImportError:
    gtts = None

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractProviderInstance,
    ability,
)
from zephyrex.extensions.ai.EXT_AI import EXT_AI
from zephyrex.lib.Environment import env
from zephyrex.lib.Logging import logger
from zephyrex.logic.BLL_Providers import ProviderInstanceModel


class GoogleProvider(EXT_AI.AbstractProvider):
    """Static Google Generative AI (Gemini) provider implementation."""

    name: ClassVar[str] = "Google"
    friendly_name: ClassVar[str] = "Google Gemini"
    platform: ClassVar[str] = "Google"

    _abilities: ClassVar[Set[str]] = {
        "text_generation",
        "text_to_speech",
    }

    _env: Dict[str, Any] = {
        "GOOGLE_API_KEY": "",
    }

    @classmethod
    def get_platform_name(cls) -> str:
        return cls.platform

    @classmethod
    def services(cls) -> List[str]:
        return ["llm", "tts", "vision"]

    @classmethod
    def validate_config(
        cls, instance: Optional[ProviderInstanceModel] = None
    ) -> List[str]:
        issues = []
        if instance:
            if not instance.api_key:
                issues.append("API key is required")
        elif not env("GOOGLE_API_KEY"):
            issues.append("GOOGLE_API_KEY environment variable not set")
        return issues

    @classmethod
    def bond_instance(
        cls, instance: ProviderInstanceModel
    ) -> Optional[AbstractProviderInstance]:
        """Bond a provider instance for API operations."""
        if genai is None:
            logger.warning("google-generativeai package not available for bonding")
            return None

        try:
            issues = cls.validate_config(instance)
            if issues:
                logger.error(f"Google instance validation failed: {issues}")
                return None

            class BondedGoogleInstance(AbstractProviderInstance):
                def __init__(self, provider_instance: ProviderInstanceModel):
                    super().__init__(provider_instance)
                    self.api_key = provider_instance.api_key
                    self.model_name = (
                        provider_instance.model_name or "gemini-2.0-flash-exp"
                    )
                    settings = (
                        provider_instance.settings_json
                        if hasattr(provider_instance, "settings_json")
                        and provider_instance.settings_json
                        else {}
                    )
                    self.temperature = float(settings.get("temperature", 0.7))

            return BondedGoogleInstance(instance)

        except Exception as e:
            logger.error(f"Error bonding Google instance: {e}")
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
        """Generate text using Google Gemini."""
        try:
            genai.configure(api_key=bonded_instance.api_key)
            generation_config = genai.types.GenerationConfig(
                temperature=(
                    temperature if temperature is not None else bonded_instance.temperature
                )
            )
            model = genai.GenerativeModel(
                model_name=bonded_instance.model_name,
                generation_config=generation_config,
            )

            content: Any = prompt
            images = kwargs.get("images", [])
            if images:
                parts: List[Any] = []
                for image in images:
                    file_extension = Path(image).suffix.lstrip(".")
                    parts.append(
                        {
                            "mime_type": f"image/{file_extension}",
                            "data": Path(image).read_bytes(),
                        }
                    )
                parts.append(prompt)
                content = parts

            response = model.generate_content(
                contents=content, generation_config=generation_config
            )

            if response.parts:
                generated_text = "".join(part.text for part in response.parts)
            else:
                generated_text = "".join(
                    part.text for part in response.candidates[0].content.parts
                )

            return {
                "success": True,
                "text": generated_text,
                "model": bonded_instance.model_name,
            }
        except Exception as e:
            logger.error(f"Error generating text with Google Gemini: {e}")
            return {"success": False, "error": str(e)}

    @classmethod
    @ability(name="embedding_generation")
    def generate_embeddings(
        cls, bonded_instance: AbstractProviderInstance, text: str, **kwargs
    ) -> Dict[str, Any]:
        """Generate text embeddings using Google's embedding model."""
        if genai is None:
            return {
                "success": False,
                "error": "google-generativeai package not available",
            }
        try:
            genai.configure(api_key=bonded_instance.api_key)
            model = kwargs.get("model", "models/text-embedding-004")
            result = genai.embed_content(model=model, content=text)
            embedding = result["embedding"]
            return {
                "success": True,
                "embedding": embedding,
                "dimensions": len(embedding),
                "model": model,
            }
        except Exception as e:
            logger.error(f"Error generating embeddings with Google: {e}")
            return {"success": False, "error": str(e)}

    @classmethod
    @ability(name="text_to_speech")
    def text_to_speech(
        cls, bonded_instance: AbstractProviderInstance, text: str, **kwargs
    ) -> Dict[str, Any]:
        """Convert text to speech using gTTS and persist it under WORKSPACE."""
        if gtts is None:
            return {
                "success": False,
                "error": "gTTS package not available for text-to-speech",
            }
        try:
            tts = gtts.gTTS(text)
            filename = f"{uuid.uuid4()}.mp3"
            workspace = os.path.join(os.getcwd(), "WORKSPACE")
            os.makedirs(workspace, exist_ok=True)
            mp3_path = os.path.join(workspace, filename)
            tts.save(mp3_path)
            aginfrastructure_uri = env("AGINFRASTRUCTURE_URI") or ""
            return {
                "success": True,
                "url": f"{aginfrastructure_uri}/outputs/{filename}",
                "format": "mp3",
            }
        except Exception as e:
            logger.error(f"Error generating speech with Google (gTTS): {e}")
            return {"success": False, "error": str(e)}
