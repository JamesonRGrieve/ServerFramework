"""
AI Memories extension for AGInfrastructure.

Provides comprehensive memory management abilities including conversation history,
long-term memory storage, knowledge retrieval, and memory consolidation.
"""

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractStaticExtension,
    AbstractStaticProvider,
)
from zephyrex.lib.Dependencies import Dependencies, EXT_Dependency, PIP_Dependency
from zephyrex.lib.Logging import logger


class EXT_AI_Memories(AbstractStaticExtension):
    """
    AI Memories extension for AGInfrastructure.

    Provides comprehensive memory management abilities including conversation history,
    long-term memory storage, knowledge retrieval, and memory consolidation.

    The extension focuses on:
    - Conversation history management
    - Long-term memory storage and retrieval
    - Memory consolidation and organization
    - Vector-based memory search and similarity
    - Database schema extensions for memory data
    """

    # Extension metadata (class attributes)
    name: str = "ai_memories"
    friendly_name: str = "AI Memory Management"
    version: str = "1.0.0"
    description: str = (
        "AI Memories extension providing comprehensive memory management and "
        "conversation history capabilities via long-term storage, retrieval, "
        "and consolidation"
    )

    # Unified dependencies using the Dependencies class. ``ext_dependencies``,
    # ``pip_dependencies`` and ``sys_dependencies`` below are derived views
    # onto this single canonical list, so there is one source of truth.
    dependencies: Dependencies = Dependencies(
        [
            EXT_Dependency(
                name="core",
                friendly_name="Core Extension",
                optional=False,
                reason="Required for base memory functionality and database operations",
            ),
            EXT_Dependency(
                name="ai_agents",
                friendly_name="AI Agents Extension",
                optional=True,
                reason="Allows agents to store long-term memories including tied to projects",
            ),
            EXT_Dependency(
                name="ai_conversations",
                friendly_name="AI Conversations Extension",
                optional=True,
                reason="Allows agents to associate memories with conversations and memorize artifacts",
            ),
            PIP_Dependency(
                name="sentence-transformers",
                friendly_name="Sentence Transformers",
                optional=False,
                reason="Required for generating memory embeddings and similarity search",
                semver=">=2.2.0",
            ),
            PIP_Dependency(
                name="numpy",
                friendly_name="NumPy",
                optional=False,
                reason="Required for vector operations and memory embeddings",
                semver=">=1.24.0",
            ),
            PIP_Dependency(
                name="chromadb",
                friendly_name="ChromaDB",
                optional=True,
                reason="Vector database for efficient memory storage and retrieval",
                semver=">=0.4.0",
            ),
            PIP_Dependency(
                name="faiss-cpu",
                friendly_name="FAISS",
                optional=True,
                reason="Alternative vector search library for memory similarity",
                semver=">=1.7.0",
            ),
            PIP_Dependency(
                name="nltk",
                friendly_name="Natural Language Toolkit",
                optional=True,
                reason="Text processing for memory content analysis",
                semver=">=3.8.0",
            ),
            PIP_Dependency(
                name="spacy",
                friendly_name="spaCy",
                optional=True,
                reason="Advanced NLP for memory entity extraction",
                semver=">=3.6.0",
            ),
        ]
    )

    @property
    def ext_dependencies(self) -> List[EXT_Dependency]:
        """Extension-on-extension dependencies, derived from ``dependencies``."""
        return self.dependencies.ext

    @property
    def pip_dependencies(self) -> List[PIP_Dependency]:
        """PIP dependencies, derived from ``dependencies``."""
        return self.dependencies.pip

    @property
    def sys_dependencies(self) -> List[Any]:
        """System dependencies, derived from ``dependencies``."""
        return self.dependencies.sys

    # What capabilities this extension provides
    capabilities: List[str] = [
        "memory_storage",
        "conversation_history",
        "memory_retrieval",
        "knowledge_management",
        "memory_consolidation",
        "similarity_search",
        "memory_linking",
    ]

    # Memory types
    MEMORY_TYPES = {
        "episodic": "Specific events and experiences",
        "semantic": "Facts and general knowledge",
        "procedural": "Skills and procedures",
        "working": "Temporary information for current task",
        "declarative": "Explicit facts and events",
        "associative": "Connections between concepts",
    }

    # Memory importance levels
    IMPORTANCE_LEVELS = {
        1: "Very Low - Can be forgotten quickly",
        2: "Low - Short-term retention",
        3: "Medium - Standard retention",
        4: "High - Long-term retention",
        5: "Critical - Permanent retention",
    }

    # Memory statuses
    MEMORY_STATUSES = {
        "active": "Currently accessible and relevant",
        "archived": "Stored but not actively used",
        "consolidated": "Merged with other memories",
        "forgotten": "Marked for deletion",
        "pending": "Waiting for processing",
    }

    def __init__(self, **kwargs):
        """Initialize the AI Memories extension."""
        super().__init__(**kwargs)
        self.active_conversations: Dict[str, Any] = {}
        self.memory_cache: Dict[str, Any] = {}
        self.knowledge_graph: Dict[str, Any] = {}

        self.embedding_model: str = "all-MiniLM-L6-v2"
        self.embedding_encoder: Any = None
        self.enable_vector_search: bool = True
        self.vector_store_client: Any = None

    # -- Initialization ------------------------------------------------

    def on_initialize(self) -> bool:
        """Initialize the AI Memories extension."""
        logger.debug("Initializing AI Memories Extension...")

        try:
            self._initialize_embedding_model()
            self._initialize_vector_store()
            self._register_memory_hooks()
            self._initialize_knowledge_graph()
            self._schedule_memory_consolidation()

            logger.debug("AI Memories extension initialized successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to initialize AI Memories extension: {str(e)}")
            return False

    def _initialize_embedding_model(self) -> None:
        """Initialize the sentence-transformers embedding encoder."""
        try:
            from sentence_transformers import SentenceTransformer

            self.embedding_encoder = SentenceTransformer(self.embedding_model)
            logger.debug(f"Embedding model '{self.embedding_model}' initialized")
        except ImportError as e:
            logger.warning(f"sentence-transformers not available: {e}")
            self.embedding_encoder = None
            self.enable_vector_search = False

    def _initialize_vector_store(self) -> None:
        """Initialize the (optional) vector database client."""
        try:
            import chromadb

            client = chromadb.Client()
            self.vector_store_client = client.get_or_create_collection("ai_memories")
            logger.debug("Vector store initialized using ChromaDB")
        except ImportError:
            self.vector_store_client = None
            logger.debug("ChromaDB not available; vector store disabled")
        except Exception as e:
            self.vector_store_client = None
            logger.error(f"Failed to initialize vector store: {e}")

    def _register_memory_hooks(self) -> None:
        """Register hooks used to capture memories as they occur."""
        logger.debug("Registering memory hooks")

    def _initialize_knowledge_graph(self) -> None:
        """Initialize the in-memory knowledge graph."""
        if self.knowledge_graph is None:
            self.knowledge_graph = {}
        logger.debug("Knowledge graph initialized")

    def _schedule_memory_consolidation(self) -> None:
        """Schedule periodic memory consolidation (no-op placeholder)."""
        logger.debug("Memory consolidation scheduled")

    # -- Validation / embeddings -----------------------------------------

    def _validate_memory_content(self, content: Optional[str]) -> bool:
        """Validate that memory content is a usable, non-blank string."""
        if not content:
            return False
        if not content.strip():
            return False
        return True

    def _generate_embedding(self, text: str) -> Optional[List[float]]:
        """Generate a vector embedding for ``text`` using the encoder, if any."""
        if self.embedding_encoder is None:
            return None
        embedding = self.embedding_encoder.encode(text)
        return embedding.tolist()

    @staticmethod
    def _cosine_similarity(vec_a: List[float], vec_b: List[float]) -> float:
        """Pure-python cosine similarity between two equal-length vectors."""
        if not vec_a or not vec_b or len(vec_a) != len(vec_b):
            return 0.0
        dot = sum(a * b for a, b in zip(vec_a, vec_b))
        norm_a = sum(a * a for a in vec_a) ** 0.5
        norm_b = sum(b * b for b in vec_b) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    # -- Storage -----------------------------------------------------------

    async def store_memory(
        self,
        content: str,
        memory_type: str = "episodic",
        importance: int = 3,
        metadata: Optional[Dict[str, Any]] = None,
        conversation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Store a new memory in the cache (and vector store, if configured)."""
        try:
            if not self._validate_memory_content(content):
                return {
                    "success": False,
                    "message": "Invalid memory content provided",
                }

            embedding = self._generate_embedding(content)
            summary = await self._generate_memory_summary(content)

            memory_id = str(uuid.uuid4())
            memory = {
                "content": content,
                "memory_type": memory_type,
                "importance": importance,
                "metadata": metadata or {},
                "status": "active",
                "summary": summary,
                "embedding": embedding,
                "conversation_id": conversation_id,
                "created_at": datetime.utcnow().isoformat(),
            }
            self.memory_cache[memory_id] = memory

            if embedding is not None:
                await self._store_in_vector_db(memory_id, embedding, memory)

            return {
                "success": True,
                "memory_id": memory_id,
                "memory": memory,
            }

        except Exception as e:
            logger.error(f"Failed to store memory: {e}")
            return {"success": False, "message": f"Failed to store memory: {str(e)}"}

    async def _store_in_vector_db(
        self, memory_id: str, embedding: List[float], memory: Dict[str, Any]
    ) -> None:
        """Persist the embedding into the configured vector store, if any."""
        if self.vector_store_client is None:
            return
        try:
            self.vector_store_client.add(
                ids=[memory_id],
                embeddings=[embedding],
                metadatas=[{"memory_type": memory.get("memory_type", "episodic")}],
            )
        except Exception as e:
            logger.error(f"Failed to store embedding in vector store: {e}")

    # -- Retrieval -----------------------------------------------------------

    async def retrieve_memories(
        self,
        query: str,
        limit: int = 10,
        memory_type: Optional[str] = None,
        conversation_id: Optional[str] = None,
        min_importance: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Retrieve memories relevant to ``query``, via vector or text search."""
        try:
            if self.enable_vector_search and self.embedding_encoder is not None:
                query_embedding = self._generate_embedding(query)
                memories = self._search_similar_memories(
                    query_embedding,
                    limit=limit,
                    memory_type=memory_type,
                    conversation_id=conversation_id,
                )
            else:
                memories = await self._search_memories_by_text(
                    query, memory_type, conversation_id, min_importance, limit
                )

            return {"success": True, "memories": memories}

        except Exception as e:
            logger.error(f"Failed to retrieve memories: {e}")
            return {
                "success": False,
                "message": f"Failed to retrieve memories: {str(e)}",
            }

    def _search_similar_memories(
        self,
        query_embedding: Optional[List[float]],
        limit: int = 10,
        memory_type: Optional[str] = None,
        conversation_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Rank cached memories by cosine similarity to ``query_embedding``."""
        scored: List[Dict[str, Any]] = []
        for memory_id, memory in self.memory_cache.items():
            if memory.get("status") != "active":
                continue
            if memory_type and memory.get("memory_type") != memory_type:
                continue
            if conversation_id and memory.get("conversation_id") != conversation_id:
                continue
            embedding = memory.get("embedding")
            if embedding is None or query_embedding is None:
                continue
            similarity = self._cosine_similarity(query_embedding, embedding)
            scored.append({**memory, "id": memory_id, "similarity": similarity})

        scored.sort(key=lambda m: m["similarity"], reverse=True)
        return scored[:limit]

    async def _search_memories_by_text(
        self,
        query: str,
        memory_type: Optional[str] = None,
        conversation_id: Optional[str] = None,
        min_importance: Optional[int] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Fallback substring search over active memories when no encoder is set."""
        query_lower = query.lower()
        results: List[Dict[str, Any]] = []
        for memory_id, memory in self.memory_cache.items():
            if memory.get("status") != "active":
                continue
            if query_lower not in memory.get("content", "").lower():
                continue
            if memory_type and memory.get("memory_type") != memory_type:
                continue
            if conversation_id and memory.get("conversation_id") != conversation_id:
                continue
            if min_importance is not None and memory.get("importance", 0) < min_importance:
                continue
            results.append({**memory, "id": memory_id})

        return results[:limit]

    # -- Update / delete -----------------------------------------------------

    async def update_memory(
        self,
        memory_id: str,
        content: Optional[str] = None,
        importance: Optional[int] = None,
        status: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Update fields on an existing cached memory."""
        try:
            if memory_id not in self.memory_cache:
                return {"success": False, "message": "Memory not found"}

            memory = self.memory_cache[memory_id]
            if content is not None:
                memory["content"] = content
                memory["embedding"] = self._generate_embedding(content)
            if importance is not None:
                memory["importance"] = importance
            if status is not None:
                memory["status"] = status
            if metadata is not None:
                memory.setdefault("metadata", {}).update(metadata)
            memory["updated_at"] = datetime.utcnow().isoformat()

            return {"success": True, "memory": memory}

        except Exception as e:
            logger.error(f"Failed to update memory: {e}")
            return {"success": False, "message": f"Failed to update memory: {str(e)}"}

    async def delete_memory(
        self, memory_id: str, soft_delete: bool = True
    ) -> Dict[str, Any]:
        """Delete a memory, either marking it forgotten or removing it outright."""
        try:
            if memory_id not in self.memory_cache:
                return {"success": False, "message": "Memory not found"}

            if soft_delete:
                self.memory_cache[memory_id]["status"] = "forgotten"
            else:
                del self.memory_cache[memory_id]

            return {"success": True, "soft_delete": soft_delete}

        except Exception as e:
            logger.error(f"Failed to delete memory: {e}")
            return {"success": False, "message": f"Failed to delete memory: {str(e)}"}

    # -- Consolidation ---------------------------------------------------

    async def consolidate_memories(self) -> Dict[str, Any]:
        """Group similar active memories together and merge each group."""
        try:
            active_memories = [
                (mid, mem)
                for mid, mem in self.memory_cache.items()
                if mem.get("status") == "active"
            ]
            groups = await self._group_similar_memories(active_memories)

            consolidated_groups = 0
            for group in groups:
                merged = await self._merge_memories(group)
                result = await self.store_memory(
                    content=merged["content"],
                    importance=merged.get("importance", 3),
                    metadata=merged.get("metadata", {}),
                )
                if result.get("success"):
                    consolidated_groups += 1
                    for original_id, _ in group:
                        if original_id in self.memory_cache:
                            self.memory_cache[original_id]["status"] = "consolidated"

            return {"success": True, "consolidated_groups": consolidated_groups}

        except Exception as e:
            logger.error(f"Failed to consolidate memories: {e}")
            return {
                "success": False,
                "message": f"Failed to consolidate memories: {str(e)}",
            }

    async def _group_similar_memories(
        self, memories: List[Tuple[str, Dict[str, Any]]]
    ) -> List[List[Tuple[str, Dict[str, Any]]]]:
        """Group memories whose content is similar to one another."""
        groups: List[List[Tuple[str, Dict[str, Any]]]] = []
        used: set = set()
        for i, (mem_id, memory) in enumerate(memories):
            if mem_id in used:
                continue
            group = [(mem_id, memory)]
            used.add(mem_id)
            for other_id, other_memory in memories[i + 1 :]:
                if other_id in used:
                    continue
                if self._are_memories_similar(memory, other_memory):
                    group.append((other_id, other_memory))
                    used.add(other_id)
            if len(group) > 1:
                groups.append(group)
        return groups

    def _are_memories_similar(
        self,
        memory1: Dict[str, Any],
        memory2: Dict[str, Any],
        threshold: float = 0.3,
    ) -> bool:
        """Word-overlap (Jaccard) similarity check between two memories' content."""
        words1 = set(memory1.get("content", "").lower().split())
        words2 = set(memory2.get("content", "").lower().split())
        if not words1 or not words2:
            return False

        intersection = words1 & words2
        union = words1 | words2
        similarity = len(intersection) / len(union) if union else 0.0
        return similarity >= threshold

    async def _merge_memories(
        self, memory_group: List[Tuple[str, Dict[str, Any]]]
    ) -> Dict[str, Any]:
        """Merge a group of similar memories into a single consolidated memory."""
        contents = [memory.get("content", "") for _, memory in memory_group]
        merged_content = " ".join(contents)
        importance = max(
            (memory.get("importance", 1) for _, memory in memory_group), default=1
        )
        original_ids = [mem_id for mem_id, _ in memory_group]

        return {
            "content": merged_content,
            "importance": importance,
            "metadata": {"original_memory_ids": original_ids},
        }

    async def _generate_memory_summary(self, content: str, max_words: int = 20) -> str:
        """Summarize memory content, truncating long content to ``max_words``."""
        words = content.split()
        if len(words) <= max_words:
            return content
        return " ".join(words[:max_words]) + "..."

    # -- Conversation history ---------------------------------------------

    async def get_conversation_history(
        self, conversation_id: str, limit: Optional[int] = None
    ) -> Dict[str, Any]:
        """Return active memories tied to ``conversation_id``, oldest first."""
        try:
            memories = [
                {**memory, "id": memory_id}
                for memory_id, memory in self.memory_cache.items()
                if memory.get("conversation_id") == conversation_id
                and memory.get("status") == "active"
            ]
            memories.sort(key=lambda m: m.get("created_at", ""))
            if limit:
                memories = memories[:limit]

            context = await self._generate_conversation_context(memories)

            return {
                "success": True,
                "memories": memories,
                "conversation_id": conversation_id,
                "context": context,
            }

        except Exception as e:
            logger.error(f"Failed to get conversation history: {e}")
            return {
                "success": False,
                "message": f"Failed to get conversation history: {str(e)}",
            }

    async def _generate_conversation_context(
        self, memories: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Summarize a list of memories into aggregate conversation context."""
        memory_types: Dict[str, int] = {}
        total_importance = 0
        for memory in memories:
            memory_type = memory.get("memory_type", "unknown")
            memory_types[memory_type] = memory_types.get(memory_type, 0) + 1
            total_importance += memory.get("importance", 0)

        average_importance = total_importance / len(memories) if memories else 0.0

        return {
            "total_memories": len(memories),
            "memory_types": memory_types,
            "average_importance": average_importance,
        }

    # -- Lifecycle -----------------------------------------------------------

    def on_start(self) -> bool:
        """Start the AI Memories extension."""
        try:
            logger.debug("AI Memories extension started successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to start AI Memories extension: {e}")
            return False

    def on_stop(self) -> bool:
        """Stop the AI Memories extension and clear in-memory state."""
        try:
            self.memory_cache.clear()
            self.active_conversations.clear()
            self.knowledge_graph.clear()
            logger.debug("AI Memories extension stopped successfully")
            return True
        except Exception as e:
            logger.error(f"Error stopping AI Memories extension: {e}")
            return False

    def on_startup(self) -> None:
        """Called during application startup."""
        logger.debug("AI Memories extension startup hook called")

    def on_shutdown(self) -> None:
        """Called during application shutdown."""
        logger.debug("AI Memories extension shutdown hook called")

    def validate_config(self) -> List[str]:
        """Validate the extension's runtime configuration."""
        issues: List[str] = []

        try:
            import sentence_transformers  # noqa: F401
        except ImportError:
            issues.append(
                "sentence-transformers library not installed - embedding generation will not work"
            )

        try:
            import numpy  # noqa: F401
        except ImportError:
            issues.append(
                "NumPy library not installed - vector operations will not work"
            )

        try:
            import chromadb  # noqa: F401
        except ImportError:
            logger.debug("ChromaDB not available - vector database features disabled")

        try:
            import faiss  # noqa: F401
        except ImportError:
            logger.debug("FAISS not available - alternative vector search disabled")

        return issues

    def get_required_permissions(self) -> List[str]:
        """Return the list of permissions required by this extension."""
        return [
            "memories:create",
            "memories:read",
            "memories:update",
            "memories:delete",
            "memories:consolidate",
            "conversations:read",
        ]

    # -- Utility methods -----------------------------------------------------

    def get_memory_types(self) -> Dict[str, str]:
        """Return the supported memory types."""
        return dict(self.MEMORY_TYPES)

    def get_importance_levels(self) -> Dict[int, str]:
        """Return the supported memory importance levels."""
        return dict(self.IMPORTANCE_LEVELS)

    def get_memory_statuses(self) -> Dict[str, str]:
        """Return the supported memory statuses."""
        return dict(self.MEMORY_STATUSES)

    def has_capability(self, capability: str) -> bool:
        """Check if this extension has a specific capability."""
        return capability in self.capabilities

    def get_memory_stats(self) -> Dict[str, Any]:
        """Return aggregate statistics about cached memories."""
        total_memories = len(self.memory_cache)
        active_memories = sum(
            1 for memory in self.memory_cache.values() if memory.get("status") == "active"
        )

        memory_types: Dict[str, int] = {}
        importance_distribution: Dict[int, int] = {}
        for memory in self.memory_cache.values():
            memory_type = memory.get("memory_type")
            if memory_type:
                memory_types[memory_type] = memory_types.get(memory_type, 0) + 1

            importance = memory.get("importance")
            if importance is not None:
                importance_distribution[importance] = (
                    importance_distribution.get(importance, 0) + 1
                )

        return {
            "total_memories": total_memories,
            "active_memories": active_memories,
            "memory_types": memory_types,
            "importance_distribution": importance_distribution,
            "active_conversations": len(self.active_conversations),
        }


class AbstractAIMemoriesExtensionProvider(AbstractStaticProvider):
    """
    Abstract base class for AI memory service providers.
    Defines the common interface for all AI memory providers with static functionality.
    All AI memory providers should be static/abstract classes with no instantiation required.
    """

    extension = EXT_AI_Memories
