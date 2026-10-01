from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from zephyrex.extensions.ai_memories.EXT_AI_Memories import EXT_AI_Memories


class TestAIMemoriesExtension:
    """Test cases for AI Memories Extension."""

    @pytest.fixture
    def extension(self):
        """Create an EXT_AI_Memories instance for testing."""
        return EXT_AI_Memories()

    def test_extension_metadata(self, extension):
        """Test extension metadata is correctly set."""
        assert extension.name == "ai_memories"
        assert extension.version == "1.0.0"
        assert "memory" in extension.description.lower()
        assert "conversation" in extension.description.lower()

    def test_dependencies(self, extension):
        """Test extension dependencies are properly defined."""
        # Check extension dependencies
        ext_deps = {dep.name for dep in extension.ext_dependencies}
        assert "core" in ext_deps
        assert "ai_agents" in ext_deps
        assert "ai_conversations" in ext_deps

        # Check pip dependencies
        pip_deps = {dep.name for dep in extension.pip_dependencies}
        assert "sentence-transformers" in pip_deps
        assert "numpy" in pip_deps
        assert "chromadb" in pip_deps
        assert "faiss-cpu" in pip_deps

        # Check sys dependencies
        assert isinstance(extension.sys_dependencies, list)

    def test_capabilities(self, extension):
        """Test extension capabilities are properly defined."""
        expected_capabilities = {
            "memory_storage",
            "conversation_history",
            "memory_retrieval",
            "knowledge_management",
            "memory_consolidation",
            "similarity_search",
            "memory_linking",
        }
        assert set(extension.capabilities) == expected_capabilities

    def test_initialization(self, extension):
        """Test extension initialization."""
        assert hasattr(extension, "active_conversations")
        assert hasattr(extension, "memory_cache")
        assert hasattr(extension, "knowledge_graph")
        assert isinstance(extension.active_conversations, dict)
        assert isinstance(extension.memory_cache, dict)
        assert isinstance(extension.knowledge_graph, dict)

    def test_memory_constants(self, extension):
        """Test memory type constants are properly defined."""
        assert "episodic" in extension.MEMORY_TYPES
        assert "semantic" in extension.MEMORY_TYPES
        assert "procedural" in extension.MEMORY_TYPES

        assert 1 in extension.IMPORTANCE_LEVELS
        assert 5 in extension.IMPORTANCE_LEVELS

        assert "active" in extension.MEMORY_STATUSES
        assert "archived" in extension.MEMORY_STATUSES

    @patch("zephyrex.extensions.ai_memories.EXT_AI_Memories.logger")
    def test_on_initialize_success(self, mock_logger, extension):
        """Test successful extension initialization."""
        with patch.object(extension, "_initialize_embedding_model"), patch.object(
            extension, "_initialize_vector_store"
        ), patch.object(extension, "_register_memory_hooks"), patch.object(
            extension, "_initialize_knowledge_graph"
        ), patch.object(
            extension, "_schedule_memory_consolidation"
        ):

            result = extension.on_initialize()
            assert result is True
            mock_logger.debug.assert_called()

    @patch("zephyrex.extensions.ai_memories.EXT_AI_Memories.logger")
    def test_on_initialize_failure(self, mock_logger, extension):
        """Test extension initialization failure."""
        with patch.object(
            extension,
            "_initialize_embedding_model",
            side_effect=Exception("Test error"),
        ):
            result = extension.on_initialize()
            assert result is False
            mock_logger.error.assert_called()

    def test_initialize_embedding_model(self, extension):
        """Test embedding model initialization."""
        with patch("sentence_transformers.SentenceTransformer") as mock_transformer:
            mock_model = MagicMock()
            mock_transformer.return_value = mock_model

            extension._initialize_embedding_model()

            assert extension.embedding_encoder == mock_model
            mock_transformer.assert_called_with(extension.embedding_model)

    def test_initialize_embedding_model_import_error(self, extension):
        """Test embedding model initialization with import error."""
        with patch(
            "sentence_transformers.SentenceTransformer",
            side_effect=ImportError("Module not found"),
        ):
            extension._initialize_embedding_model()

            assert extension.embedding_encoder is None
            assert extension.enable_vector_search is False

    def test_validate_memory_content(self, extension):
        """Test memory content validation."""
        assert extension._validate_memory_content("Valid content") is True
        assert extension._validate_memory_content("") is False
        assert extension._validate_memory_content("   ") is False
        assert extension._validate_memory_content(None) is False

    def test_generate_embedding(self, extension):
        """Test embedding generation."""
        mock_encoder = MagicMock()
        mock_encoder.encode.return_value.tolist.return_value = [0.1, 0.2, 0.3]
        extension.embedding_encoder = mock_encoder

        result = extension._generate_embedding("test text")

        assert result == [0.1, 0.2, 0.3]
        mock_encoder.encode.assert_called_with("test text")

    def test_generate_embedding_no_encoder(self, extension):
        """Test embedding generation without encoder."""
        extension.embedding_encoder = None

        result = extension._generate_embedding("test text")

        assert result is None

    @pytest.mark.asyncio
    async def test_store_memory_success(self, extension):
        """Test successful memory storage."""
        with patch.object(
            extension, "_validate_memory_content", return_value=True
        ), patch.object(
            extension, "_generate_embedding", return_value=[0.1, 0.2]
        ), patch.object(
            extension, "_store_in_vector_db"
        ), patch.object(
            extension, "_generate_memory_summary", return_value="Summary"
        ):

            result = await extension.store_memory(
                content="Test memory content",
                memory_type="episodic",
                importance=4,
                metadata={"key": "value"},
            )

            assert result["success"] is True
            assert "memory_id" in result
            assert result["memory"]["content"] == "Test memory content"
            assert result["memory"]["memory_type"] == "episodic"
            assert result["memory"]["importance"] == 4

    @pytest.mark.asyncio
    async def test_store_memory_invalid_content(self, extension):
        """Test memory storage with invalid content."""
        with patch.object(extension, "_validate_memory_content", return_value=False):
            result = await extension.store_memory(content="")

            assert result["success"] is False
            assert "Invalid memory content" in result["message"]

    @pytest.mark.asyncio
    async def test_retrieve_memories_vector_search(self, extension):
        """Test memory retrieval with vector search."""
        extension.enable_vector_search = True
        extension.embedding_encoder = MagicMock()

        mock_memories = [
            {"id": "1", "content": "Test memory 1", "similarity": 0.9},
            {"id": "2", "content": "Test memory 2", "similarity": 0.8},
        ]

        with patch.object(
            extension, "_generate_embedding", return_value=[0.1, 0.2]
        ), patch.object(
            extension, "_search_similar_memories", return_value=mock_memories
        ):

            result = await extension.retrieve_memories("test query", limit=5)

            assert result["success"] is True
            assert len(result["memories"]) == 2
            assert result["memories"][0]["similarity"] == 0.9

    @pytest.mark.asyncio
    async def test_retrieve_memories_text_search(self, extension):
        """Test memory retrieval with text search fallback."""
        extension.enable_vector_search = False

        mock_memories = [{"id": "1", "content": "Test memory 1", "similarity": 0.5}]

        with patch.object(
            extension, "_search_memories_by_text", return_value=mock_memories
        ):
            result = await extension.retrieve_memories("test query")

            assert result["success"] is True
            assert len(result["memories"]) == 1

    @pytest.mark.asyncio
    async def test_update_memory_success(self, extension):
        """Test successful memory update."""
        memory_id = "test_memory_1"
        original_memory = {
            "content": "Original content",
            "importance": 3,
            "status": "active",
            "metadata": {"key": "value"},
        }
        extension.memory_cache[memory_id] = original_memory

        result = await extension.update_memory(
            memory_id=memory_id,
            content="Updated content",
            importance=5,
            status="archived",
        )

        assert result["success"] is True
        assert extension.memory_cache[memory_id]["content"] == "Updated content"
        assert extension.memory_cache[memory_id]["importance"] == 5
        assert extension.memory_cache[memory_id]["status"] == "archived"

    @pytest.mark.asyncio
    async def test_update_memory_not_found(self, extension):
        """Test memory update with non-existent memory."""
        result = await extension.update_memory("nonexistent_id", content="New content")

        assert result["success"] is False
        assert "Memory not found" in result["message"]

    @pytest.mark.asyncio
    async def test_delete_memory_soft_delete(self, extension):
        """Test soft delete memory."""
        memory_id = "test_memory_1"
        extension.memory_cache[memory_id] = {"status": "active"}

        result = await extension.delete_memory(memory_id, soft_delete=True)

        assert result["success"] is True
        assert result["soft_delete"] is True
        assert extension.memory_cache[memory_id]["status"] == "forgotten"

    @pytest.mark.asyncio
    async def test_delete_memory_hard_delete(self, extension):
        """Test hard delete memory."""
        memory_id = "test_memory_1"
        extension.memory_cache[memory_id] = {"status": "active"}

        result = await extension.delete_memory(memory_id, soft_delete=False)

        assert result["success"] is True
        assert result["soft_delete"] is False
        assert memory_id not in extension.memory_cache

    @pytest.mark.asyncio
    async def test_consolidate_memories(self, extension):
        """Test memory consolidation."""
        # Add test memories
        extension.memory_cache = {
            "mem1": {
                "content": "Similar content about topic A",
                "created_at": datetime.utcnow().isoformat(),
                "status": "active",
            },
            "mem2": {
                "content": "Similar content about topic A",
                "created_at": datetime.utcnow().isoformat(),
                "status": "active",
            },
        }

        with patch.object(
            extension, "_group_similar_memories"
        ) as mock_group, patch.object(
            extension, "_merge_memories"
        ) as mock_merge, patch.object(
            extension, "store_memory"
        ) as mock_store:

            mock_group.return_value = [
                [
                    ("mem1", extension.memory_cache["mem1"]),
                    ("mem2", extension.memory_cache["mem2"]),
                ]
            ]
            mock_merge.return_value = {
                "content": "Consolidated memory",
                "importance": 4,
                "metadata": {},
            }
            mock_store.return_value = {"success": True}

            result = await extension.consolidate_memories()

            assert result["success"] is True
            assert result["consolidated_groups"] == 1

    @pytest.mark.asyncio
    async def test_get_conversation_history(self, extension):
        """Test conversation history retrieval."""
        conversation_id = "conv_1"

        # Add test memories
        extension.memory_cache = {
            "mem1": {
                "conversation_id": conversation_id,
                "status": "active",
                "created_at": "2024-01-01T00:00:00Z",
                "content": "Memory 1",
            },
            "mem2": {
                "conversation_id": conversation_id,
                "status": "active",
                "created_at": "2024-01-02T00:00:00Z",
                "content": "Memory 2",
            },
            "mem3": {
                "conversation_id": "other_conv",
                "status": "active",
                "created_at": "2024-01-03T00:00:00Z",
                "content": "Memory 3",
            },
        }

        with patch.object(extension, "_generate_conversation_context") as mock_context:
            mock_context.return_value = {"summary": "Test context"}

            result = await extension.get_conversation_history(conversation_id)

            assert result["success"] is True
            assert len(result["memories"]) == 2
            assert result["conversation_id"] == conversation_id
            assert result["context"] == {"summary": "Test context"}

    def test_are_memories_similar(self, extension):
        """Test memory similarity checking."""
        memory1 = {
            "content": "This is about artificial intelligence and machine learning"
        }
        memory2 = {
            "content": "This discusses artificial intelligence and machine learning"
        }
        memory3 = {"content": "Completely different topic about cooking"}

        assert extension._are_memories_similar(memory1, memory2) is True
        assert extension._are_memories_similar(memory1, memory3) is False

    @pytest.mark.asyncio
    async def test_merge_memories(self, extension):
        """Test memory merging."""
        memory_group = [
            (
                "mem1",
                {"content": "First memory", "importance": 3, "metadata": {"tag": "A"}},
            ),
            (
                "mem2",
                {"content": "Second memory", "importance": 5, "metadata": {"tag": "B"}},
            ),
        ]

        result = await extension._merge_memories(memory_group)

        assert "First memory Second memory" in result["content"]
        assert result["importance"] == 5  # Should use highest importance
        assert "original_memory_ids" in result["metadata"]
        assert result["metadata"]["original_memory_ids"] == ["mem1", "mem2"]

    @pytest.mark.asyncio
    async def test_generate_memory_summary(self, extension):
        """Test memory summary generation."""
        short_content = "Short content"
        long_content = " ".join(["word"] * 30)

        short_summary = await extension._generate_memory_summary(short_content)
        long_summary = await extension._generate_memory_summary(long_content)

        assert short_summary == short_content
        assert len(long_summary.split()) <= 21  # 20 words + "..."

    @pytest.mark.asyncio
    async def test_generate_conversation_context(self, extension):
        """Test conversation context generation."""
        memories = [
            {
                "memory_type": "episodic",
                "importance": 3,
                "created_at": "2024-01-01T00:00:00Z",
            },
            {
                "memory_type": "episodic",
                "importance": 4,
                "created_at": "2024-01-02T00:00:00Z",
            },
            {
                "memory_type": "semantic",
                "importance": 5,
                "created_at": "2024-01-03T00:00:00Z",
            },
        ]

        result = await extension._generate_conversation_context(memories)

        assert result["total_memories"] == 3
        assert result["memory_types"]["episodic"] == 2
        assert result["memory_types"]["semantic"] == 1
        assert result["average_importance"] == 4.0

    @pytest.mark.asyncio
    async def test_search_memories_by_text(self, extension):
        """Test text-based memory search."""
        extension.memory_cache = {
            "mem1": {"content": "This contains the search term", "status": "active"},
            "mem2": {"content": "This does not contain it", "status": "active"},
            "mem3": {"content": "Another search term match", "status": "forgotten"},
        }

        result = await extension._search_memories_by_text(
            "search term", None, None, None, 10
        )

        assert len(result) == 1  # Only active memories with matching text
        assert result[0]["content"] == "This contains the search term"

    def test_lifecycle_methods(self, extension):
        """Test extension lifecycle methods."""
        # Test on_start
        assert extension.on_start() is True

        # Test on_stop
        extension.memory_cache["test"] = {"data": "test"}
        extension.active_conversations["conv1"] = {"data": "test"}
        extension.knowledge_graph["entity1"] = {"data": "test"}

        assert extension.on_stop() is True
        assert len(extension.memory_cache) == 0
        assert len(extension.active_conversations) == 0
        assert len(extension.knowledge_graph) == 0

        # Test on_startup and on_shutdown
        extension.on_startup()  # Should not raise exception
        extension.on_shutdown()  # Should not raise exception

    def test_validate_config(self, extension):
        """Test configuration validation."""
        with patch("builtins.__import__") as mock_import:
            # Test successful validation
            issues = extension.validate_config()
            assert isinstance(issues, list)

            # Test missing dependencies
            mock_import.side_effect = ImportError("Module not found")
            issues = extension.validate_config()
            assert len(issues) > 0

    def test_get_required_permissions(self, extension):
        """Test required permissions."""
        permissions = extension.get_required_permissions()
        assert isinstance(permissions, list)
        assert "memories:create" in permissions
        assert "memories:read" in permissions
        assert "conversations:read" in permissions

    def test_utility_methods(self, extension):
        """Test utility methods."""
        # Test get_memory_types
        memory_types = extension.get_memory_types()
        assert isinstance(memory_types, dict)
        assert "episodic" in memory_types

        # Test get_importance_levels
        importance_levels = extension.get_importance_levels()
        assert isinstance(importance_levels, dict)
        assert 1 in importance_levels

        # Test get_memory_statuses
        statuses = extension.get_memory_statuses()
        assert isinstance(statuses, dict)
        assert "active" in statuses

        # Test has_capability
        assert extension.has_capability("memory_storage") is True
        assert extension.has_capability("nonexistent_capability") is False

    def test_get_memory_stats(self, extension):
        """Test memory statistics."""
        extension.memory_cache = {
            "mem1": {"status": "active", "memory_type": "episodic", "importance": 3},
            "mem2": {"status": "active", "memory_type": "semantic", "importance": 5},
            "mem3": {"status": "forgotten", "memory_type": "episodic", "importance": 2},
        }
        extension.active_conversations = {"conv1": {}, "conv2": {}}

        stats = extension.get_memory_stats()

        assert stats["total_memories"] == 3
        assert stats["active_memories"] == 2
        assert stats["memory_types"]["episodic"] == 2
        assert stats["memory_types"]["semantic"] == 1
        assert stats["importance_distribution"][3] == 1
        assert stats["importance_distribution"][5] == 1
        assert stats["active_conversations"] == 2

    @pytest.mark.asyncio
    async def test_error_handling(self, extension):
        """Test error handling in abilities."""
        with patch.object(
            extension, "_validate_memory_content", side_effect=Exception("Test error")
        ):
            result = await extension.store_memory("test content")
            assert result["success"] is False
            assert "Failed to store memory" in result["message"]


if __name__ == "__main__":
    pytest.main([__file__])
