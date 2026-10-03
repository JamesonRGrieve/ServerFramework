# SPDX-License-Identifier: AGPL-3.0-or-later
"""Long-term memories in the app database, recalled by meaning when an
embedding model is configured in the ai extension and by words otherwise
(see BLL_AI_Memories). The agents extension keeps and recalls its agents'
memories here.

Abilities act for the user named by ``requester_id``, whose memories they
are."""

from typing import Any, ClassVar, Dict, List, Optional, Set

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractStaticExtension,
    ability,
)
from zephyrex.extensions.ai_memories.BLL_AI_Memories import (
    MAX_MEMORY_CHARACTERS,
    MAX_RECALL,
    MemoryManager,
)
from zephyrex.extensions.ExternalErrors import InvalidInputExternalError
from zephyrex.lib.Dependencies import Dependencies, EXT_Dependency


def memory_entry(memory: Any) -> Dict[str, Any]:
    """A memory as an ability answers it (without its embedding)."""
    return {
        "id": memory.id,
        "agent_id": memory.agent_id,
        "key": memory.key,
        "content": memory.content,
        "source": memory.source,
        "conversation_id": memory.conversation_id,
        "created_at": memory.created_at.isoformat() if memory.created_at else None,
    }


def _content(content: str) -> str:
    if not content or not content.strip() or len(content) > MAX_MEMORY_CHARACTERS:
        raise InvalidInputExternalError(
            f"a memory is 1-{MAX_MEMORY_CHARACTERS} characters"
        )
    return content


def _limit(limit: int) -> int:
    if not 1 <= limit <= MAX_RECALL:
        raise InvalidInputExternalError(f"limit is 1-{MAX_RECALL}")
    return limit


class EXT_AI_Memories(AbstractStaticExtension):
    name: ClassVar[str] = "ai_memories"
    version: ClassVar[str] = "2.0.0"
    description: ClassVar[str] = (
        "Long-term memories in the app database, recalled by meaning or by words"
    )

    _env: ClassVar[Dict[str, Any]] = {}
    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            EXT_Dependency(
                name="ai",
                friendly_name="AI",
                optional=True,
                reason="Embeds memories so recall can rank them by meaning",
            )
        ]
    )
    # Named apart from the agents extension's own memorize/recall (which its
    # turn executor handles, and which keep memories here).
    _abilities: ClassVar[Set[str]] = {
        "keep_memory",
        "recall_memories",
        "recent_memories",
        "forget_memory",
    }

    @classmethod
    def memories(cls, requester_id: str) -> MemoryManager:
        manager: MemoryManager = cls.as_requester(MemoryManager, requester_id)
        return manager

    @classmethod
    @ability("keep_memory")
    async def keep_memory(
        cls,
        requester_id: str,
        agent_id: str,
        content: str,
        key: Optional[str] = None,
        conversation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Keep ``content`` as one of the agent's long-term memories."""
        kept = await cls.memories(requester_id).keep(
            agent_id, _content(content), key, conversation_id
        )
        return memory_entry(kept)

    @classmethod
    @ability("recall_memories")
    async def recall_memories(
        cls, requester_id: str, agent_id: str, query: str, limit: int = 5
    ) -> List[Dict[str, Any]]:
        """The agent's memories most related to ``query``."""
        found = await cls.memories(requester_id).recall(agent_id, query, _limit(limit))
        return [memory_entry(m) for m in found]

    @classmethod
    @ability("recent_memories")
    async def recent_memories(
        cls, requester_id: str, agent_id: str, limit: int = 10
    ) -> List[Dict[str, Any]]:
        found = cls.memories(requester_id).recent(agent_id, _limit(limit))
        return [memory_entry(m) for m in found]

    @classmethod
    @ability("forget_memory")
    async def forget_memory(cls, requester_id: str, memory_id: str) -> Dict[str, Any]:
        cls.memories(requester_id).delete(memory_id)
        return {"forgotten": memory_id}
