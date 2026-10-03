# SPDX-License-Identifier: AGPL-3.0-or-later
"""GGUF models on this server, run by llama.cpp: chat, raw text
completion and embeddings.

Each provider instance is one model: a ``gguf_chat`` instance chats and
completes text, a ``gguf_embedding`` instance embeds. Its files are
declared, pinned and checked as the ``local_ai`` base describes; the
first ``.gguf`` among them is loaded with the instance's
``context_length``, ``threads`` and ``gpu_layers``. Chat answers in the
``ai`` extension's neutral shape; a local model takes neither images nor
tools.
"""

from typing import Any, ClassVar, Dict, Set

from zephyrex.extensions.local_ai.LocalAI import AbstractLocalAIExtension
from zephyrex.extensions.local_ai_gguf.GGUF import GGUF_DEPENDENCIES
from zephyrex.lib.Dependencies import Dependencies, EXT_Dependency


class EXT_Local_AI_GGUF(AbstractLocalAIExtension):
    name: ClassVar[str] = "local_ai_gguf"
    version: ClassVar[str] = "3.0.0"
    description: ClassVar[str] = (
        "GGUF models on this server through llama.cpp: chat, text completion "
        "and embeddings"
    )

    _env: ClassVar[Dict[str, Any]] = {}
    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            EXT_Dependency(
                name="local_ai",
                friendly_name="Local AI",
                reason="Model declarations, verified downloads and memory",
            ),
            *GGUF_DEPENDENCIES.pip,
        ]
    )
    _abilities: ClassVar[Set[str]] = set(AbstractLocalAIExtension._abilities)
