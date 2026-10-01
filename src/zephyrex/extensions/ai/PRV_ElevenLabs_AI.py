from typing import Any, ClassVar, Dict, List, Optional, Set

import requests

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractProviderInstance,
    ability,
)
from zephyrex.extensions.ai.EXT_AI import EXT_AI
from zephyrex.lib.Environment import env
from zephyrex.lib.Logging import logger
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

_DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"
_REQUEST_TIMEOUT_SECONDS = 60


class ElevenLabsProvider(EXT_AI.AbstractProvider):
    """Static ElevenLabs text-to-speech provider implementation."""

    name: ClassVar[str] = "ElevenLabs"
    friendly_name: ClassVar[str] = "ElevenLabs"
    platform: ClassVar[str] = "ElevenLabs"

    _abilities: ClassVar[Set[str]] = {
        "text_to_speech",
    }

    _env: Dict[str, Any] = {
        "ELEVENLABS_API_KEY": "",
    }

    @classmethod
    def get_platform_name(cls) -> str:
        return cls.platform

    @classmethod
    def services(cls) -> List[str]:
        return ["tts"]

    @classmethod
    def validate_config(
        cls, instance: Optional[ProviderInstanceModel] = None
    ) -> List[str]:
        issues = []
        if instance:
            if not instance.api_key:
                issues.append("API key is required")
        elif not env("ELEVENLABS_API_KEY"):
            issues.append("ELEVENLABS_API_KEY environment variable not set")
        return issues

    @classmethod
    def bond_instance(
        cls, instance: ProviderInstanceModel
    ) -> Optional[AbstractProviderInstance]:
        """Bond a provider instance for API operations."""
        try:
            issues = cls.validate_config(instance)
            if issues:
                logger.error(f"ElevenLabs instance validation failed: {issues}")
                return None

            class BondedElevenLabsInstance(AbstractProviderInstance):
                def __init__(self, provider_instance: ProviderInstanceModel):
                    super().__init__(provider_instance)
                    self.api_key = provider_instance.api_key
                    settings = (
                        provider_instance.settings_json
                        if hasattr(provider_instance, "settings_json")
                        and provider_instance.settings_json
                        else {}
                    )
                    self.voice = settings.get("voice") or _DEFAULT_VOICE_ID

            return BondedElevenLabsInstance(instance)

        except Exception as e:
            logger.error(f"Error bonding ElevenLabs instance: {e}")
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
        """ElevenLabs only supports text-to-speech, not text generation."""
        return {
            "success": False,
            "error": "Text generation not supported by this provider",
        }

    @classmethod
    @ability(name="embedding_generation")
    def generate_embeddings(
        cls, bonded_instance: AbstractProviderInstance, text: str, **kwargs
    ) -> Dict[str, Any]:
        """ElevenLabs does not offer an embeddings API."""
        return {
            "success": False,
            "error": "Embedding generation not supported by this provider",
        }

    @classmethod
    @ability(name="text_to_speech")
    def text_to_speech(
        cls, bonded_instance: AbstractProviderInstance, text: str, **kwargs
    ) -> Dict[str, Any]:
        """Convert text to speech using ElevenLabs."""
        headers = {"xi-api-key": bonded_instance.api_key}
        voice_id = kwargs.get("voice") or bonded_instance.voice or _DEFAULT_VOICE_ID

        try:
            response = requests.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
                headers=headers,
                json={"text": text},
                timeout=_REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
        except requests.RequestException:
            # Fall back to the default voice if the requested one fails.
            voice_id = _DEFAULT_VOICE_ID
            try:
                response = requests.post(
                    f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
                    headers=headers,
                    json={"text": text},
                    timeout=_REQUEST_TIMEOUT_SECONDS,
                )
            except requests.RequestException as e:
                logger.error(f"Error generating speech with ElevenLabs: {e}")
                return {"success": False, "error": str(e)}

        if response.status_code == 200:
            return {
                "success": True,
                "audio": response.content,
                "format": "mp3",
                "voice": voice_id,
            }
        return {
            "success": False,
            "error": f"Failed to generate audio (status {response.status_code})",
        }
