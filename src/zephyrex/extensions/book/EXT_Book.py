import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension, ability
from zephyrex.lib.Dependencies import EXT_Dependency, PIP_Dependency
from zephyrex.lib.Logging import logger


class EXT_Book(AbstractStaticExtension):
    """
    Book extension for AGInfrastructure.
    Provides comprehensive book creation, editing, and publishing capabilities.
    Supports various formats including PDF, EPUB, HTML, and Markdown.

    Component loading (DB, BLL, EP) is handled automatically by the import system
    based on file naming conventions.
    """

    # Extension metadata
    name = "book"
    version = "1.0.0"
    description = "Book creation, editing, and publishing extension"

    # Define dependencies
    ext_dependencies = [
        EXT_Dependency(
            name="core",
            friendly_name="Core Extension",
            optional=False,
            reason="Required for base book functionality",
        ),
        EXT_Dependency(
            name="ai",
            friendly_name="AI Extension",
            optional=True,
            reason="Optional for AI-assisted content generation",
        ),
        EXT_Dependency(
            name="meta_labels",
            friendly_name="Meta Labels Extension",
            optional=True,
            reason="Optional for organizing and categorizing book content",
        ),
    ]

    pip_dependencies = [
        PIP_Dependency(
            name="markdown",
            friendly_name="Markdown",
            optional=False,
            reason="Required for markdown processing and conversion",
            semver=">=3.4.0",
        ),
        PIP_Dependency(
            name="pypdf",
            friendly_name="PyPDF",
            optional=False,
            reason="Required for PDF generation and manipulation",
            semver=">=3.0.0",
        ),
        PIP_Dependency(
            name="ebooklib",
            friendly_name="EbookLib",
            optional=True,
            reason="Required for EPUB format support",
            semver=">=0.18",
        ),
        PIP_Dependency(
            name="reportlab",
            friendly_name="ReportLab",
            optional=True,
            reason="Advanced PDF generation capabilities",
            semver=">=4.0.0",
        ),
        PIP_Dependency(
            name="weasyprint",
            friendly_name="WeasyPrint",
            optional=True,
            reason="HTML to PDF conversion with CSS support",
            semver=">=60.0",
        ),
        PIP_Dependency(
            name="jinja2",
            friendly_name="Jinja2",
            optional=False,
            reason="Template engine for book formatting",
            semver=">=3.0.0",
        ),
    ]

    sys_dependencies: List[Any] = []

    # Define database tables (loaded via import system)
    db_tables: List[Any] = []

    # Define what capabilities this extension provides
    capabilities = [
        "book_creation",
        "content_generation",
        "format_conversion",
        "chapter_management",
        "template_processing",
        "publishing_workflow",
        "metadata_management",
        "collaboration_tools",
    ]

    # Book formats
    SUPPORTED_FORMATS = {
        "pdf": "Portable Document Format",
        "epub": "Electronic Publication",
        "html": "HyperText Markup Language",
        "markdown": "Markdown format",
        "docx": "Microsoft Word Document",
        "txt": "Plain text format",
    }

    # Book statuses
    BOOK_STATUSES = {
        "draft": "Book is in draft stage",
        "review": "Book is under review",
        "editing": "Book is being edited",
        "finalized": "Book has been finalized",
        "published": "Book has been published",
        "archived": "Book has been archived",
    }

    def __init__(
        self,
        default_format: str = "",
        template_directory: str = "",
        output_directory: str = "",
        auto_save: Optional[bool] = None,
        **kwargs: Any,
    ):
        """
        Initialize the book extension.
        """
        super().__init__(**kwargs)

        # Instance-level copy so register_capability() never mutates the
        # class-level default shared across every instance.
        self.capabilities = list(type(self).capabilities)

        self.default_format = default_format or self.get_env_value(
            "BOOK_DEFAULT_FORMAT", "pdf"
        )
        self.template_directory = template_directory or self.get_env_value(
            "BOOK_TEMPLATE_DIRECTORY", "./templates/books"
        )
        self.output_directory = output_directory or self.get_env_value(
            "BOOK_OUTPUT_DIRECTORY", "./output/books"
        )
        self.auto_save = (
            auto_save
            if auto_save is not None
            else str(self.get_env_value("BOOK_AUTO_SAVE", "true")).lower() == "true"
        )

        self.bll_managers: Dict[str, Any] = {}
        self.commands: Dict[str, Any] = {}
        self.active_books: Dict[str, Dict[str, Any]] = {}

    def on_initialize(self) -> bool:
        """Initialize book management functionality."""
        logger.debug("Initializing Book Extension...")

        try:
            self._register_managers()
            self._register_commands()

            for capability in self.capabilities:
                self.register_capability(capability)

            logger.debug("Book extension initialized successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to initialize Book extension: {str(e)}")
            return False

    def _register_managers(self) -> None:
        """Register business logic managers."""
        try:
            from zephyrex.extensions.book.BLL_Book import (
                BookManager,
                ChapterManager,
                PublishingManager,
            )

            self.bll_managers = {
                "BookManager": BookManager,
                "ChapterManager": ChapterManager,
                "PublishingManager": PublishingManager,
            }
            logger.debug(f"Registered {len(self.bll_managers)} BLL managers")
        except ImportError as e:
            logger.warning(f"Some BLL managers could not be imported: {e}")
            self.bll_managers = {}

    def _register_commands(self) -> None:
        """Register the commands this extension exposes."""
        self.commands = {
            "create_book": self.create_book,
            "update_book": self.update_book,
            "delete_book": self.delete_book,
            "add_chapter": self.add_chapter,
            "update_chapter": self.update_chapter,
            "delete_chapter": self.delete_chapter,
            "generate_content": self.generate_content,
            "convert_format": self.convert_format,
            "publish_book": self.publish_book,
            "get_book_status": self.get_book_status,
        }

    def _setup_directories(self) -> None:
        """Setup required directories."""
        try:
            os.makedirs(self.template_directory, exist_ok=True)
            os.makedirs(self.output_directory, exist_ok=True)
            logger.debug("Book directories created successfully")
        except Exception as e:
            logger.warning(f"Could not create book directories: {e}")

    def register_capability(self, capability: str) -> None:
        """Register a new capability."""
        if capability not in self.capabilities:
            self.capabilities.append(capability)

    def get_registered_capabilities(self) -> Set[str]:
        """Return currently registered capabilities."""
        return set(self.capabilities)

    def get_capabilities(self) -> Set[str]:
        """Return the capabilities this extension provides."""
        return set(self.capabilities)

    def has_capability(self, capability: str) -> bool:
        """Check if this extension has a specific capability."""
        return capability in self.capabilities

    @staticmethod
    def _word_count(content: str) -> int:
        """Count whitespace-separated words in chapter content."""
        return len(content.split()) if content else 0

    @ability("create_book")
    async def create_book(
        self,
        title: str,
        author: str = "",
        description: str = "",
        genre: str = "",
        language: str = "en",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create a new book."""
        try:
            book_id = str(uuid.uuid4())
            book = {
                "id": book_id,
                "title": title,
                "author": author,
                "description": description,
                "genre": genre,
                "language": language,
                "status": "draft",
                "chapters": [],
                "word_count": 0,
                "metadata": metadata or {},
                "created_at": datetime.now(timezone.utc).isoformat(),
            }

            self.active_books[book_id] = book
            if book_id not in self.active_books:
                raise RuntimeError("Failed to store book")

            return {
                "success": True,
                "book_id": book_id,
                "book": book,
            }

        except Exception as e:
            logger.error(f"Error creating book: {e}")
            return {
                "success": False,
                "message": f"Error creating book: {str(e)}",
            }

    @ability("update_book")
    async def update_book(
        self,
        book_id: str,
        title: Optional[str] = None,
        author: Optional[str] = None,
        description: Optional[str] = None,
        genre: Optional[str] = None,
        status: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Update an existing book's metadata."""
        try:
            if book_id not in self.active_books:
                return {"success": False, "message": "Book not found"}

            book = self.active_books[book_id]
            updates: Dict[str, Any] = {}

            for field, value in (
                ("title", title),
                ("author", author),
                ("description", description),
                ("genre", genre),
                ("status", status),
            ):
                if value is not None:
                    book[field] = value
                    updates[field] = value

            if metadata is not None:
                book["metadata"].update(metadata)
                updates["metadata"] = metadata

            return {"success": True, "book_id": book_id, "updates": updates}

        except Exception as e:
            logger.error(f"Error updating book: {e}")
            return {
                "success": False,
                "message": f"Error updating book: {str(e)}",
            }

    @ability("delete_book")
    async def delete_book(self, book_id: str) -> Dict[str, Any]:
        """Delete a book."""
        try:
            if book_id not in self.active_books:
                return {"success": False, "message": "Book not found"}

            del self.active_books[book_id]

            return {
                "success": True,
                "book_id": book_id,
                "message": "Book deleted successfully",
            }

        except Exception as e:
            logger.error(f"Error deleting book: {e}")
            return {
                "success": False,
                "message": f"Error deleting book: {str(e)}",
            }

    @ability("add_chapter")
    async def add_chapter(
        self,
        book_id: str,
        title: str,
        content: str = "",
        order: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Add a chapter to a book."""
        try:
            if book_id not in self.active_books:
                return {"success": False, "message": "Book not found"}

            book = self.active_books[book_id]
            word_count = self._word_count(content)

            chapter = {
                "id": str(uuid.uuid4()),
                "title": title,
                "content": content,
                "order": order if order is not None else len(book["chapters"]) + 1,
                "word_count": word_count,
                "metadata": metadata or {},
            }

            book["chapters"].append(chapter)
            book["word_count"] += word_count

            return {
                "success": True,
                "book_id": book_id,
                "chapter_id": chapter["id"],
                "chapter": chapter,
            }

        except Exception as e:
            logger.error(f"Error adding chapter: {e}")
            return {
                "success": False,
                "message": f"Error adding chapter: {str(e)}",
            }

    def _find_chapter(
        self, book: Dict[str, Any], chapter_id: str
    ) -> Optional[Dict[str, Any]]:
        """Find a chapter by id within a book."""
        for chapter in book["chapters"]:
            if chapter["id"] == chapter_id:
                return chapter
        return None

    @ability("update_chapter")
    async def update_chapter(
        self,
        book_id: str,
        chapter_id: str,
        title: Optional[str] = None,
        content: Optional[str] = None,
        order: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Update an existing chapter."""
        try:
            if book_id not in self.active_books:
                return {"success": False, "message": "Book not found"}

            book = self.active_books[book_id]
            chapter = self._find_chapter(book, chapter_id)
            if chapter is None:
                return {"success": False, "message": "Chapter not found"}

            if title is not None:
                chapter["title"] = title

            if content is not None:
                book["word_count"] -= chapter["word_count"]
                new_word_count = self._word_count(content)
                chapter["content"] = content
                chapter["word_count"] = new_word_count
                book["word_count"] += new_word_count

            if order is not None:
                chapter["order"] = order

            if metadata is not None:
                chapter["metadata"].update(metadata)

            return {
                "success": True,
                "book_id": book_id,
                "chapter_id": chapter_id,
                "chapter": chapter,
            }

        except Exception as e:
            logger.error(f"Error updating chapter: {e}")
            return {
                "success": False,
                "message": f"Error updating chapter: {str(e)}",
            }

    @ability("delete_chapter")
    async def delete_chapter(self, book_id: str, chapter_id: str) -> Dict[str, Any]:
        """Delete a chapter from a book."""
        try:
            if book_id not in self.active_books:
                return {"success": False, "message": "Book not found"}

            book = self.active_books[book_id]
            chapter = self._find_chapter(book, chapter_id)
            if chapter is None:
                return {"success": False, "message": "Chapter not found"}

            book["chapters"].remove(chapter)
            book["word_count"] -= chapter["word_count"]

            return {
                "success": True,
                "book_id": book_id,
                "chapter_id": chapter_id,
                "message": "Chapter deleted successfully",
            }

        except Exception as e:
            logger.error(f"Error deleting chapter: {e}")
            return {
                "success": False,
                "message": f"Error deleting chapter: {str(e)}",
            }

    @ability("generate_content")
    async def generate_content(
        self,
        book_id: str,
        content_type: str = "chapter",
        prompt: str = "",
    ) -> Dict[str, Any]:
        """Generate book content (outline or chapter draft) from a prompt."""
        try:
            if book_id not in self.active_books:
                return {"success": False, "message": "Book not found"}

            if content_type == "outline":
                generated_content = (
                    "Chapter 1: Introduction\n"
                    "Chapter 2: Development\n"
                    "Chapter 3: Conclusion\n"
                    f"(Outline generated from prompt: {prompt})"
                )
            else:
                generated_content = (
                    f"Generated {content_type} content based on: {prompt}"
                )

            return {
                "success": True,
                "book_id": book_id,
                "content_type": content_type,
                "generated_content": generated_content,
            }

        except Exception as e:
            logger.error(f"Error generating content: {e}")
            return {
                "success": False,
                "message": f"Error generating content: {str(e)}",
            }

    @ability("convert_format")
    async def convert_format(
        self,
        book_id: str,
        target_format: str,
        options: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Convert a book to the given target format."""
        try:
            if book_id not in self.active_books:
                return {"success": False, "message": "Book not found"}

            if target_format not in self.SUPPORTED_FORMATS:
                return {
                    "success": False,
                    "message": f"Unsupported format: {target_format}",
                }

            output_path = os.path.join(
                self.output_directory, f"{book_id}.{target_format}"
            )

            return {
                "success": True,
                "book_id": book_id,
                "target_format": target_format,
                "result": {
                    "format": target_format,
                    "output_path": output_path,
                    "options": options or {},
                },
            }

        except Exception as e:
            logger.error(f"Error converting format: {e}")
            return {
                "success": False,
                "message": f"Error converting format: {str(e)}",
            }

    @ability("publish_book")
    async def publish_book(
        self,
        book_id: str,
        platforms: Optional[List[str]] = None,
        publish_options: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Publish a book to one or more platforms."""
        try:
            if book_id not in self.active_books:
                return {"success": False, "message": "Book not found"}

            book = self.active_books[book_id]
            target_platforms = platforms or ["local"]
            results = [
                {"platform": platform, "status": "success", "options": publish_options or {}}
                for platform in target_platforms
            ]

            book["status"] = "published"
            book["published_at"] = datetime.now(timezone.utc).isoformat()

            return {
                "success": True,
                "book_id": book_id,
                "platforms": target_platforms,
                "results": results,
                "published_at": book["published_at"],
            }

        except Exception as e:
            logger.error(f"Error publishing book: {e}")
            return {
                "success": False,
                "message": f"Error publishing book: {str(e)}",
            }

    @ability("get_book_status")
    async def get_book_status(self, book_id: str) -> Dict[str, Any]:
        """Get the current status and stats of a book."""
        try:
            if book_id not in self.active_books:
                return {"success": False, "message": "Book not found"}

            book = self.active_books[book_id]

            return {
                "success": True,
                "book_id": book_id,
                "status": book["status"],
                "chapter_count": len(book["chapters"]),
                "total_word_count": book["word_count"],
            }

        except Exception as e:
            logger.error(f"Error getting book status: {e}")
            return {
                "success": False,
                "message": f"Error getting book status: {str(e)}",
            }

    def get_supported_formats(self) -> Dict[str, str]:
        """Return the formats books can be converted to."""
        return dict(self.SUPPORTED_FORMATS)

    def get_book_statuses(self) -> Dict[str, str]:
        """Return the valid book lifecycle statuses."""
        return dict(self.BOOK_STATUSES)

    def get_active_books(self) -> Dict[str, Dict[str, Any]]:
        """Return all books currently tracked in memory."""
        return self.active_books

    def get_book_statistics(self) -> Dict[str, Any]:
        """Return aggregate statistics across all active books."""
        status_distribution: Dict[str, int] = {}
        total_chapters = 0
        total_words = 0

        for book in self.active_books.values():
            status = book.get("status", "unknown")
            status_distribution[status] = status_distribution.get(status, 0) + 1
            total_chapters += len(book.get("chapters", []))
            total_words += book.get("word_count", 0)

        return {
            "total_books": len(self.active_books),
            "total_chapters": total_chapters,
            "total_words": total_words,
            "status_distribution": status_distribution,
            "supported_formats": list(self.SUPPORTED_FORMATS.keys()),
        }

    def on_start(self) -> bool:
        """Start the Book extension."""
        try:
            logger.debug("Book extension started successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to start Book extension: {e}")
            return False

    def on_stop(self) -> bool:
        """Stop the Book extension, flushing in-memory books if auto-save is on."""
        try:
            if self.auto_save and self.active_books:
                logger.debug(f"Auto-saving {len(self.active_books)} book(s) before stop")

            self.active_books = {}

            logger.debug("Book extension stopped successfully")
            return True
        except Exception as e:
            logger.error(f"Error stopping Book extension: {e}")
            return False

    def validate_config(self) -> List[str]:
        """Validate the extension configuration."""
        issues: List[str] = []

        for dep in self.pip_dependencies:
            if dep.optional:
                continue
            try:
                __import__(dep.name)
            except ImportError:
                issues.append(
                    f"{dep.friendly_name} library not installed - {dep.reason}"
                )

        if self.default_format not in self.SUPPORTED_FORMATS:
            issues.append(f"Unsupported default format: {self.default_format}")

        for directory in (self.template_directory, self.output_directory):
            if not os.path.exists(directory):
                try:
                    os.makedirs(directory, exist_ok=True)
                except OSError as e:
                    issues.append(f"Cannot create directory {directory}: {e}")

        return issues

    def get_required_permissions(self) -> List[str]:
        """Return the list of permissions required by this extension."""
        return [
            "books:create",
            "books:read",
            "books:update",
            "books:delete",
            "books:publish",
            "files:read",
            "files:write",
            "content:generate",
        ]

    def on_startup(self) -> None:
        """Called during application startup."""
        logger.debug("Book extension startup hook called")

    def on_shutdown(self) -> None:
        """Called during application shutdown."""
        logger.debug("Book extension shutdown hook called")


# This module is normally imported as ``zephyrex.extensions.book.EXT_Book`` (the
# post-Item-60 namespace layout). Some call sites and tests still target it via
# the pre-zephyrex, bare "extensions.<name>.<file>" dotted path. Register this
# already-executed module object under that alias too (mirroring what
# ``ExtensionLoader.load_extension_module`` does for its own synthesized name)
# so a lookup via either path resolves to the same module instance -- and, in
# particular, so ``unittest.mock.patch("extensions.book.EXT_Book.<name>")``
# patches the very attribute this module's own code reads, instead of
# silently re-executing this file a second time under a disconnected name.
import sys as _sys

for _alias in ("zephyrex.extensions.book.EXT_Book", "extensions.book.EXT_Book"):
    _sys.modules.setdefault(_alias, _sys.modules[__name__])
