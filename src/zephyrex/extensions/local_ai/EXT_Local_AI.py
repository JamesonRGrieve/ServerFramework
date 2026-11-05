# SPDX-License-Identifier: AGPL-3.0-or-later
"""Local AI: models run on this server's own hardware.

This is the base the model formats build on (``local_ai_gguf`` for
llama.cpp's GGUF files, ``local_ai_torch`` for PyTorch / transformers
models): model declarations pinned to a commit and per-file SHA-256,
downloads from allowed sources only, the models directory, and the
models held in memory (``LocalModels``); the provider shape and the
abilities each format offers (``LocalAI``). Its own abilities report the
hardware and the models in memory across every format.

Configuration (environment):

- ``LOCAL_AI_MODELS_DIR``: where model files are kept (default
  ``$HF_HOME/zephyrex-models``).
- ``LOCAL_AI_MODEL_SOURCES``: comma-separated sources models may be
  downloaded from (default ``https://huggingface.co``).
- ``LOCAL_AI_MAX_LOADED_MODELS``: how many models may be in memory at
  once (default 2).
"""

import asyncio
from typing import Any, ClassVar, Dict, List, Set

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractStaticExtension,
    ability,
)
from zephyrex.extensions.local_ai import Hardware
from zephyrex.extensions.local_ai.LocalModels import LOADED_MODELS
from zephyrex.lib.Dependencies import Dependencies, EXT_Dependency, PIP_Dependency


class EXT_Local_AI(AbstractStaticExtension):
    name: ClassVar[str] = "local_ai"
    version: ClassVar[str] = "3.0.0"
    description: ClassVar[str] = (
        "Models run on this server's own hardware: pinned, checksum-verified "
        "downloads and the models held in memory"
    )

    _env: ClassVar[Dict[str, Any]] = {
        "LOCAL_AI_MODELS_DIR": "",
        "LOCAL_AI_MODEL_SOURCES": "https://huggingface.co",
        "LOCAL_AI_MAX_LOADED_MODELS": "2",
    }
    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            EXT_Dependency(
                name="ai",
                friendly_name="AI",
                reason="The provider shape and neutral chat and embedding answers",
            ),
            PIP_Dependency(
                name="psutil",
                friendly_name="psutil",
                semver=">=5.9.0",
                reason="The processor and memory this server has",
            ),
        ]
    )
    _abilities: ClassVar[Set[str]] = {
        "detect_hardware",
        "loaded_models",
        "unload_all_models",
    }

    @classmethod
    def on_stop(cls) -> None:
        for loaded in LOADED_MODELS.summaries():
            LOADED_MODELS.unload(loaded["id"])

    @classmethod
    @ability("detect_hardware")
    async def detect_hardware(cls) -> Dict[str, Any]:
        """This server's processor, memory and GPUs."""
        return await asyncio.to_thread(Hardware.detect)

    @classmethod
    @ability("loaded_models")
    async def loaded_models(cls) -> List[Dict[str, Any]]:
        """The models in memory, of every format: since when, last used
        when, and how many calls each has answered."""
        return LOADED_MODELS.summaries()

    @classmethod
    @ability("unload_all_models")
    async def unload_all_models(cls) -> List[Dict[str, Any]]:
        """Release every model's memory; each loads again when next used.
        The models that were unloaded."""
        unloaded = LOADED_MODELS.summaries()
        for loaded in unloaded:
            await asyncio.to_thread(LOADED_MODELS.unload, loaded["id"])
        return unloaded
