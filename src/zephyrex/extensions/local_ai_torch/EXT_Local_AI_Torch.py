# SPDX-License-Identifier: AGPL-3.0-or-later
"""PyTorch / transformers models on this server: chat and text
completion with a causal language model, embeddings with an encoder,
and transcription with a speech model such as Whisper.

Each provider instance is one model directory, declared, pinned and
checked as the ``local_ai`` base describes, run on the instance's
``device`` at its ``dtype``. Models load from safetensors only and never
run a repository's own code. Chat answers in the ``ai`` extension's
neutral shape; a local model takes neither images nor tools.
Transcription reads WAV (PCM) audio.
"""

from typing import Any, ClassVar, Dict, Optional, Set

from zephyrex.extensions.AbstractExtensionProvider import ability
from zephyrex.extensions.ai.EXT_AI import TRANSCRIPTION, audio_bytes
from zephyrex.extensions.local_ai.LocalAI import AbstractLocalAIExtension
from zephyrex.extensions.local_ai_torch.Torch import TORCH_DEPENDENCIES
from zephyrex.lib.Dependencies import Dependencies, EXT_Dependency


class EXT_Local_AI_Torch(AbstractLocalAIExtension):
    name: ClassVar[str] = "local_ai_torch"
    version: ClassVar[str] = "3.0.0"
    description: ClassVar[str] = (
        "PyTorch models on this server through transformers: chat, text "
        "completion, embeddings and transcription"
    )

    _env: ClassVar[Dict[str, Any]] = {}
    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            EXT_Dependency(
                name="local_ai",
                friendly_name="Local AI",
                reason="Model declarations, verified downloads and memory",
            ),
            *TORCH_DEPENDENCIES.pip,
        ]
    )
    _abilities: ClassVar[Set[str]] = {
        *AbstractLocalAIExtension._abilities,
        "transcribe",
    }

    @classmethod
    @ability("transcribe")
    async def transcribe(
        cls,
        audio_base64: str,
        filename: str = "audio.wav",
        language: Optional[str] = None,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """The text spoken in WAV audio: ``{text, model}``."""
        result: Dict[str, Any] = await cls._call(
            model,
            TRANSCRIPTION,
            "transcribe",
            audio_bytes(audio_base64),
            filename,
            language,
        )
        return result
