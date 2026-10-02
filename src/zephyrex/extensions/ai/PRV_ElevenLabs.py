# SPDX-License-Identifier: AGPL-3.0-or-later
"""ElevenLabs: speech from text and transcription (Scribe)."""

import base64
from typing import Any, ClassVar, Dict, Optional, Set, Tuple

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.ai.EXT_AI import SPEECH, TRANSCRIPTION, AbstractAIProvider
from zephyrex.lib.ProviderHTTPClient import path_segment
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

API_URL = "https://api.elevenlabs.io/v1"


class PRV_ElevenLabs_AI(AbstractAIProvider):
    name: ClassVar[str] = "elevenlabs"
    friendly_name: ClassVar[str] = "ElevenLabs"
    description: ClassVar[str] = "ElevenLabs speech and transcription"
    _abilities: ClassVar[Set[str]] = {SPEECH, TRANSCRIPTION}
    _env: ClassVar[Dict[str, Any]] = {"ELEVENLABS_API_KEY": ""}
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting(
            "api_key",
            "API key",
            env="ELEVENLABS_API_KEY",
            secret=True,
            field="api_key",
        ),
        InstanceSetting(
            "model",
            "Speech model",
            default="eleven_multilingual_v2",
            field="model_name",
        ),
        InstanceSetting("voice", "Default voice id", default="21m00Tcm4TlvDq8ikWAM"),
        InstanceSetting(
            "transcription_model", "Transcription model", default="scribe_v1"
        ),
        InstanceSetting("base_url", "API address", default=API_URL),
    )

    @classmethod
    def base(cls, instance: ProviderInstanceModel) -> str:
        return str(cls.setting(instance, "base_url")).rstrip("/")

    @classmethod
    def headers(cls, instance: ProviderInstanceModel) -> Dict[str, str]:
        return {"xi-api-key": str(cls.setting(instance, "api_key") or "")}

    @classmethod
    async def speak(
        cls, instance: ProviderInstanceModel, text: str, voice: Optional[str]
    ) -> Dict[str, Any]:
        model = cls.model(instance)
        chosen = voice or str(cls.setting(instance, "voice"))
        response = await cls.http().post(
            f"{cls.base(instance)}/text-to-speech/{path_segment(chosen, 'voice')}",
            params={"output_format": "mp3_44100_128"},
            json={"text": text, "model_id": model},
            headers=cls.headers(instance),
            raw=True,
        )
        return {
            "audio_base64": base64.b64encode(response.content).decode(),
            "content_type": response.headers.get("content-type", "audio/mpeg"),
            "model": model,
            "voice": chosen,
        }

    @classmethod
    async def transcribe(
        cls,
        instance: ProviderInstanceModel,
        audio: bytes,
        filename: str,
        language: Optional[str],
    ) -> Dict[str, Any]:
        model = str(cls.setting(instance, "transcription_model"))
        form = {"model_id": model}
        if language:
            form["language_code"] = language
        answer = await cls.http().post(
            f"{cls.base(instance)}/speech-to-text",
            data=form,
            files={"file": (filename, audio, "application/octet-stream")},
            headers=cls.headers(instance),
        )
        return {"text": str(answer.get("text", "")), "model": model}
