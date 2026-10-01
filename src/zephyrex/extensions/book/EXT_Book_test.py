from unittest.mock import MagicMock, patch

import pytest

from zephyrex.extensions.book.EXT_Book import EXT_Book


class TestBookExtension:
    """Test cases for Book Extension."""

    @pytest.fixture
    def extension(self):
        """Create a BookExtension instance for testing."""
        return EXT_Book()

    def test_extension_metadata(self, extension):
        """Test extension metadata is correctly set."""
        assert extension.name == "book"
        assert extension.version == "1.0.0"
        assert "book creation" in extension.description.lower()
        assert "publishing" in extension.description.lower()

    def test_dependencies(self, extension):
        """Test extension dependencies are properly defined."""
        # Check extension dependencies
        ext_deps = {dep.name for dep in extension.ext_dependencies}
        assert "core" in ext_deps
        assert "ai" in ext_deps
        assert "meta_labels" in ext_deps

        # Check pip dependencies
        pip_deps = {dep.name for dep in extension.pip_dependencies}
        assert "markdown" in pip_deps
        assert "pypdf" in pip_deps
        assert "jinja2" in pip_deps

        # Check sys dependencies
        assert isinstance(extension.sys_dependencies, list)

    def test_capabilities(self, extension):
        """Test extension capabilities are properly defined."""
        expected_capabilities = {
            "book_creation",
            "content_generation",
            "format_conversion",
            "chapter_management",
            "template_processing",
            "publishing_workflow",
            "metadata_management",
            "collaboration_tools",
        }
        assert set(extension.capabilities) == expected_capabilities

    def test_supported_formats(self, extension):
        """Test supported formats are properly defined."""
        expected_formats = {"pdf", "epub", "html", "markdown", "docx", "txt"}
        assert set(extension.SUPPORTED_FORMATS.keys()) == expected_formats

    def test_book_statuses(self, extension):
        """Test book statuses are properly defined."""
        expected_statuses = {
            "draft",
            "review",
            "editing",
            "finalized",
            "published",
            "archived",
        }
        assert set(extension.BOOK_STATUSES.keys()) == expected_statuses

    def test_initialization(self, extension):
        """Test extension initialization."""
        assert hasattr(extension, "bll_managers")
        assert hasattr(extension, "commands")
        assert hasattr(extension, "active_books")
        assert isinstance(extension.bll_managers, dict)
        assert isinstance(extension.commands, dict)
        assert isinstance(extension.active_books, dict)

    @patch("extensions.book.EXT_Book.logger")
    @patch("os.makedirs")
    def test_on_initialize_success(self, mock_makedirs, mock_logger, extension):
        """Test successful extension initialization."""
        with patch.object(extension, "_register_managers"), patch.object(
            extension, "_register_commands"
        ), patch.object(extension, "register_capability"):

            result = extension.on_initialize()
            assert result is True
            mock_logger.debug.assert_called()

    @patch("extensions.book.EXT_Book.logger")
    def test_on_initialize_failure(self, mock_logger, extension):
        """Test extension initialization failure."""
        with patch.object(
            extension, "_register_managers", side_effect=Exception("Test error")
        ):
            result = extension.on_initialize()
            assert result is False
            mock_logger.error.assert_called()

    def test_register_managers(self, extension):
        """Test manager registration."""
        with patch("builtins.__import__") as mock_import:
            mock_module = MagicMock()
            mock_import.return_value = mock_module

            extension._register_managers()

            assert mock_import.called

    def test_register_commands(self, extension):
        """Test command registration."""
        extension._register_commands()

        expected_commands = {
            "create_book",
            "update_book",
            "delete_book",
            "add_chapter",
            "update_chapter",
            "delete_chapter",
            "generate_content",
            "convert_format",
            "publish_book",
            "get_book_status",
        }
        assert set(extension.commands.keys()) == expected_commands

    @patch("os.makedirs")
    def test_setup_directories(self, mock_makedirs, extension):
        """Test directory setup."""
        extension._setup_directories()
        assert mock_makedirs.call_count == 2

    def test_capability_management(self, extension):
        """Test capability management methods."""
        # Test register_capability
        extension.register_capability("test_capability")
        assert "test_capability" in extension.capabilities

        # Test get_registered_capabilities
        capabilities = extension.get_registered_capabilities()
        assert isinstance(capabilities, set)
        assert "test_capability" in capabilities

        # Test get_capabilities
        capabilities = extension.get_capabilities()
        assert isinstance(capabilities, set)

        # Test has_capability
        assert extension.has_capability("test_capability") is True
        assert extension.has_capability("nonexistent_capability") is False

    @pytest.mark.asyncio
    async def test_create_book_success(self, extension):
        """Test successful book creation."""
        result = await extension.create_book(
            title="Test Book",
            author="Test Author",
            description="Test Description",
            genre="Fiction",
            metadata={"keywords": ["test"]},
        )

        assert result["success"] is True
        assert "book_id" in result
        assert result["book"]["title"] == "Test Book"
        assert result["book"]["author"] == "Test Author"
        assert result["book"]["status"] == "draft"
        assert len(result["book"]["chapters"]) == 0

        # Check that book was stored
        book_id = result["book_id"]
        assert book_id in extension.active_books

    @pytest.mark.asyncio
    async def test_update_book_success(self, extension):
        """Test successful book update."""
        # First create a book
        create_result = await extension.create_book(title="Original Title")
        book_id = create_result["book_id"]

        # Update the book
        result = await extension.update_book(
            book_id=book_id,
            title="Updated Title",
            status="review",
            metadata={"updated": True},
        )

        assert result["success"] is True
        assert result["book_id"] == book_id
        assert result["updates"]["title"] == "Updated Title"
        assert result["updates"]["status"] == "review"

        # Check that book was actually updated
        assert extension.active_books[book_id]["title"] == "Updated Title"
        assert extension.active_books[book_id]["status"] == "review"

    @pytest.mark.asyncio
    async def test_update_book_not_found(self, extension):
        """Test book update with non-existent book."""
        result = await extension.update_book("nonexistent_id", title="New Title")

        assert result["success"] is False
        assert "Book not found" in result["message"]

    @pytest.mark.asyncio
    async def test_delete_book_success(self, extension):
        """Test successful book deletion."""
        # First create a book
        create_result = await extension.create_book(title="Test Book")
        book_id = create_result["book_id"]

        # Delete the book
        result = await extension.delete_book(book_id)

        assert result["success"] is True
        assert result["book_id"] == book_id
        assert "deleted successfully" in result["message"]

        # Check that book was removed
        assert book_id not in extension.active_books

    @pytest.mark.asyncio
    async def test_delete_book_not_found(self, extension):
        """Test book deletion with non-existent book."""
        result = await extension.delete_book("nonexistent_id")

        assert result["success"] is False
        assert "Book not found" in result["message"]

    @pytest.mark.asyncio
    async def test_add_chapter_success(self, extension):
        """Test successful chapter addition."""
        # First create a book
        create_result = await extension.create_book(title="Test Book")
        book_id = create_result["book_id"]

        # Add a chapter
        result = await extension.add_chapter(
            book_id=book_id,
            title="Chapter 1",
            content="This is the first chapter content.",
            metadata={"section": "introduction"},
        )

        assert result["success"] is True
        assert result["book_id"] == book_id
        assert "chapter_id" in result
        assert result["chapter"]["title"] == "Chapter 1"
        assert result["chapter"]["order"] == 1
        assert result["chapter"]["word_count"] == 6

        # Check that chapter was added to book
        book = extension.active_books[book_id]
        assert len(book["chapters"]) == 1
        assert book["word_count"] == 6

    @pytest.mark.asyncio
    async def test_add_chapter_book_not_found(self, extension):
        """Test chapter addition with non-existent book."""
        result = await extension.add_chapter("nonexistent_id", "Chapter 1")

        assert result["success"] is False
        assert "Book not found" in result["message"]

    @pytest.mark.asyncio
    async def test_update_chapter_success(self, extension):
        """Test successful chapter update."""
        # Create book and chapter
        book_result = await extension.create_book(title="Test Book")
        book_id = book_result["book_id"]

        chapter_result = await extension.add_chapter(
            book_id, "Original Title", "Original content"
        )
        chapter_id = chapter_result["chapter_id"]

        # Update the chapter
        result = await extension.update_chapter(
            book_id=book_id,
            chapter_id=chapter_id,
            title="Updated Title",
            content="Updated content with more words here",
            order=2,
        )

        assert result["success"] is True
        assert result["chapter"]["title"] == "Updated Title"
        assert result["chapter"]["order"] == 2
        assert result["chapter"]["word_count"] == 6

    @pytest.mark.asyncio
    async def test_update_chapter_not_found(self, extension):
        """Test chapter update with non-existent chapter."""
        book_result = await extension.create_book(title="Test Book")
        book_id = book_result["book_id"]

        result = await extension.update_chapter(
            book_id, "nonexistent_chapter", title="New Title"
        )

        assert result["success"] is False
        assert "Chapter not found" in result["message"]

    @pytest.mark.asyncio
    async def test_delete_chapter_success(self, extension):
        """Test successful chapter deletion."""
        # Create book and chapter
        book_result = await extension.create_book(title="Test Book")
        book_id = book_result["book_id"]

        chapter_result = await extension.add_chapter(book_id, "Chapter 1", "Content")
        chapter_id = chapter_result["chapter_id"]

        # Delete the chapter
        result = await extension.delete_chapter(book_id, chapter_id)

        assert result["success"] is True
        assert result["chapter_id"] == chapter_id

        # Check that chapter was removed
        book = extension.active_books[book_id]
        assert len(book["chapters"]) == 0
        assert book["word_count"] == 0

    @pytest.mark.asyncio
    async def test_delete_chapter_not_found(self, extension):
        """Test chapter deletion with non-existent chapter."""
        book_result = await extension.create_book(title="Test Book")
        book_id = book_result["book_id"]

        result = await extension.delete_chapter(book_id, "nonexistent_chapter")

        assert result["success"] is False
        assert "Chapter not found" in result["message"]

    @pytest.mark.asyncio
    async def test_generate_content_success(self, extension):
        """Test successful content generation."""
        # Create a book
        book_result = await extension.create_book(title="Test Book")
        book_id = book_result["book_id"]

        # Generate outline content
        result = await extension.generate_content(
            book_id=book_id, content_type="outline", prompt="Science fiction novel"
        )

        assert result["success"] is True
        assert result["content_type"] == "outline"
        assert "Chapter 1" in result["generated_content"]
        assert "Introduction" in result["generated_content"]

        # Generate chapter content
        result = await extension.generate_content(
            book_id=book_id, content_type="chapter", prompt="alien invasion"
        )

        assert result["success"] is True
        assert result["content_type"] == "chapter"
        assert "alien invasion" in result["generated_content"]

    @pytest.mark.asyncio
    async def test_generate_content_book_not_found(self, extension):
        """Test content generation with non-existent book."""
        result = await extension.generate_content("nonexistent_id", "chapter")

        assert result["success"] is False
        assert "Book not found" in result["message"]

    @pytest.mark.asyncio
    async def test_convert_format_success(self, extension):
        """Test successful format conversion."""
        # Create a book
        book_result = await extension.create_book(title="Test Book")
        book_id = book_result["book_id"]

        # Convert to PDF
        result = await extension.convert_format(
            book_id=book_id, target_format="pdf", options={"quality": "high"}
        )

        assert result["success"] is True
        assert result["target_format"] == "pdf"
        assert "result" in result
        assert result["result"]["format"] == "pdf"
        assert "output_path" in result["result"]

    @pytest.mark.asyncio
    async def test_convert_format_unsupported(self, extension):
        """Test format conversion with unsupported format."""
        book_result = await extension.create_book(title="Test Book")
        book_id = book_result["book_id"]

        result = await extension.convert_format(book_id, "unsupported_format")

        assert result["success"] is False
        assert "Unsupported format" in result["message"]

    @pytest.mark.asyncio
    async def test_publish_book_success(self, extension):
        """Test successful book publishing."""
        # Create a book
        book_result = await extension.create_book(title="Test Book")
        book_id = book_result["book_id"]

        # Publish to multiple platforms
        result = await extension.publish_book(
            book_id=book_id,
            platforms=["local", "amazon", "google"],
            publish_options={"marketing": True},
        )

        assert result["success"] is True
        assert len(result["platforms"]) == 3
        assert len(result["results"]) == 3
        assert all(r["status"] == "success" for r in result["results"])

        # Check that book status was updated
        book = extension.active_books[book_id]
        assert book["status"] == "published"
        assert "published_at" in book

    @pytest.mark.asyncio
    async def test_get_book_status_success(self, extension):
        """Test successful book status retrieval."""
        # Create a book with chapters
        book_result = await extension.create_book(title="Test Book")
        book_id = book_result["book_id"]

        await extension.add_chapter(book_id, "Chapter 1", "Content here")
        await extension.add_chapter(book_id, "Chapter 2", "More content")

        # Get book status
        result = await extension.get_book_status(book_id)

        assert result["success"] is True
        assert result["book_id"] == book_id
        assert result["chapter_count"] == 2
        assert result["total_word_count"] == 4  # 2 + 2 words

    @pytest.mark.asyncio
    async def test_get_book_status_not_found(self, extension):
        """Test book status retrieval with non-existent book."""
        result = await extension.get_book_status("nonexistent_id")

        assert result["success"] is False
        assert "Book not found" in result["message"]

    def test_lifecycle_methods(self, extension):
        """Test extension lifecycle methods."""
        # Test on_start
        assert extension.on_start() is True

        # Test on_stop with auto_save
        extension.auto_save = True
        extension.active_books["test"] = {"title": "Test Book"}

        assert extension.on_stop() is True
        assert len(extension.active_books) == 0

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
            assert any("Markdown" in issue for issue in issues)

    def test_validate_config_invalid_format(self, extension):
        """Test configuration validation with invalid default format."""
        extension.default_format = "invalid_format"

        issues = extension.validate_config()
        assert any("Unsupported default format" in issue for issue in issues)

    @patch("os.path.exists", return_value=False)
    @patch("os.makedirs", side_effect=OSError("Permission denied"))
    def test_validate_config_directory_creation_failure(
        self, mock_makedirs, mock_exists, extension
    ):
        """Test configuration validation when directory creation fails."""
        issues = extension.validate_config()
        assert any("Cannot create" in issue for issue in issues)

    def test_get_required_permissions(self, extension):
        """Test required permissions."""
        permissions = extension.get_required_permissions()
        assert isinstance(permissions, list)
        assert len(permissions) > 0

        expected_permissions = [
            "books:create",
            "books:read",
            "books:update",
            "books:delete",
            "books:publish",
            "files:read",
            "files:write",
            "content:generate",
        ]
        assert set(permissions) == set(expected_permissions)

    def test_utility_methods(self, extension):
        """Test utility methods."""
        # Test get_supported_formats
        formats = extension.get_supported_formats()
        assert isinstance(formats, dict)
        assert "pdf" in formats

        # Test get_book_statuses
        statuses = extension.get_book_statuses()
        assert isinstance(statuses, dict)
        assert "draft" in statuses

        # Test get_active_books
        extension.active_books["test"] = {"title": "Test"}
        active_books = extension.get_active_books()
        assert isinstance(active_books, dict)
        assert "test" in active_books

    def test_get_book_statistics(self, extension):
        """Test book statistics."""
        # Add some test books
        extension.active_books = {
            "book1": {
                "status": "draft",
                "chapters": [{"word_count": 100}, {"word_count": 150}],
                "word_count": 250,
            },
            "book2": {
                "status": "published",
                "chapters": [{"word_count": 200}],
                "word_count": 200,
            },
        }

        stats = extension.get_book_statistics()

        assert stats["total_books"] == 2
        assert stats["total_chapters"] == 3
        assert stats["total_words"] == 450
        assert stats["status_distribution"]["draft"] == 1
        assert stats["status_distribution"]["published"] == 1
        assert "pdf" in stats["supported_formats"]

    @pytest.mark.asyncio
    async def test_error_handling(self, extension):
        """Test error handling in abilities."""
        with patch.object(
            extension, "active_books", side_effect=Exception("Test error")
        ):
            result = await extension.create_book("Test Book")
            assert result["success"] is False
            assert "Error creating book" in result["message"]

    @pytest.mark.asyncio
    async def test_multiple_chapters_ordering(self, extension):
        """Test chapter ordering with multiple chapters."""
        # Create a book
        book_result = await extension.create_book(title="Test Book")
        book_id = book_result["book_id"]

        # Add chapters with specific orders
        await extension.add_chapter(book_id, "Chapter 3", "Content 3", order=3)
        await extension.add_chapter(book_id, "Chapter 1", "Content 1", order=1)
        await extension.add_chapter(book_id, "Chapter 2", "Content 2", order=2)

        # Check chapters were added with correct orders
        book = extension.active_books[book_id]
        assert len(book["chapters"]) == 3

        # Find chapters by order
        chapter_orders = {ch["order"]: ch["title"] for ch in book["chapters"]}
        assert chapter_orders[1] == "Chapter 1"
        assert chapter_orders[2] == "Chapter 2"
        assert chapter_orders[3] == "Chapter 3"

    @pytest.mark.asyncio
    async def test_word_count_updates(self, extension):
        """Test that word counts are properly updated."""
        # Create a book
        book_result = await extension.create_book(title="Test Book")
        book_id = book_result["book_id"]

        # Add chapters with content
        await extension.add_chapter(
            book_id, "Chapter 1", "This has five words exactly"
        )  # 5 words
        await extension.add_chapter(
            book_id, "Chapter 2", "This chapter has seven words in it"
        )  # 7 words

        # Check total word count
        book = extension.active_books[book_id]
        assert book["word_count"] == 12

        # Update a chapter with new content
        chapter_id = book["chapters"][0]["id"]
        await extension.update_chapter(
            book_id, chapter_id, content="Only three words"
        )  # 3 words

        # Check updated word count
        book = extension.active_books[book_id]
        assert book["word_count"] == 10  # 3 + 7


if __name__ == "__main__":
    pytest.main([__file__])
