# SPDX-License-Identifier: AGPL-3.0-or-later
"""Books: writing them chapter by chapter and exporting them as Markdown,
HTML or EPUB.

Books and chapters are records (``BLL_Book``) a user reaches through the
API. The abilities do the same for an agent acting for a user: each
takes the ``requester_id`` of that user and works under their
permissions, on exactly the books they can see.
"""

import base64
from typing import Any, ClassVar, Dict, Optional, Set

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractStaticExtension,
    ability,
)
from zephyrex.extensions.book.BLL_Book import BookChapterManager, BookManager
from zephyrex.extensions.book.Export import FORMATS, render
from zephyrex.extensions.ExternalErrors import InvalidInputExternalError
from zephyrex.lib.Dependencies import Dependencies, PIP_Dependency


def _fields(**values: Any) -> Dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


class EXT_Book(AbstractStaticExtension):
    name: ClassVar[str] = "book"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Books written chapter by chapter, exported as Markdown, HTML or EPUB"
    )

    _env: ClassVar[Dict[str, Any]] = {}
    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            PIP_Dependency(
                name="markdown-it-py",
                friendly_name="markdown-it-py",
                semver=">=3.0",
                reason="Rendering chapters' Markdown for HTML and EPUB",
            ),
            PIP_Dependency(
                name="ebooklib",
                friendly_name="EbookLib",
                semver=">=0.20",
                reason="Writing EPUB",
            ),
        ]
    )
    _abilities: ClassVar[Set[str]] = {
        "create_book",
        "update_book",
        "delete_book",
        "get_book",
        "add_chapter",
        "update_chapter",
        "delete_chapter",
        "export_book",
    }

    @classmethod
    def _books(cls, requester_id: str) -> BookManager:
        manager: BookManager = cls.as_requester(BookManager, requester_id)
        return manager

    @classmethod
    def _chapters(cls, requester_id: str) -> BookChapterManager:
        manager: BookChapterManager = cls.as_requester(BookChapterManager, requester_id)
        return manager

    @classmethod
    @ability("create_book")
    async def create_book(
        cls,
        requester_id: str,
        title: str,
        author: Optional[str] = None,
        description: Optional[str] = None,
        genre: Optional[str] = None,
        language: str = "en",
    ) -> Dict[str, Any]:
        """Start a book (as a draft)."""
        created = cls._books(requester_id).create(
            **_fields(
                title=title,
                author=author,
                description=description,
                genre=genre,
                language=language,
            )
        )
        return dict(created.model_dump(mode="json"))

    @classmethod
    @ability("update_book")
    async def update_book(
        cls,
        requester_id: str,
        book_id: str,
        title: Optional[str] = None,
        author: Optional[str] = None,
        description: Optional[str] = None,
        genre: Optional[str] = None,
        language: Optional[str] = None,
        status: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Change a book's details or status."""
        changes = _fields(
            title=title,
            author=author,
            description=description,
            genre=genre,
            language=language,
            status=status,
        )
        if not changes:
            raise InvalidInputExternalError("nothing to change")
        updated = cls._books(requester_id).update(book_id, **changes)
        return dict(updated.model_dump(mode="json"))

    @classmethod
    @ability("delete_book")
    async def delete_book(cls, requester_id: str, book_id: str) -> Dict[str, Any]:
        """Delete a book and its chapters."""
        books, chapters = cls._books(requester_id), cls._chapters(requester_id)
        books.get(id=book_id)
        for chapter in chapters.in_order(book_id):
            chapters.delete(chapter.id)
        books.delete(book_id)
        return {"id": book_id, "deleted": True}

    @classmethod
    @ability("get_book")
    async def get_book(cls, requester_id: str, book_id: str) -> Dict[str, Any]:
        """A book with its chapters in order and its word count."""
        books = cls._books(requester_id)
        book = books.get(id=book_id).model_dump(mode="json")
        chapters = [
            chapter.model_dump(mode="json")
            for chapter in cls._chapters(requester_id).in_order(book_id)
        ]
        return {
            **book,
            "chapters": chapters,
            "word_count": sum(chapter["word_count"] for chapter in chapters),
        }

    @classmethod
    @ability("add_chapter")
    async def add_chapter(
        cls,
        requester_id: str,
        book_id: str,
        title: str,
        content: str = "",
        position: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Add a chapter, after the last one unless ``position`` says where."""
        chapters = cls._chapters(requester_id)
        cls._books(requester_id).get(id=book_id)
        if position is None:
            existing = chapters.in_order(book_id)
            position = existing[-1].position + 1 if existing else 1
        created = chapters.create(
            book_id=book_id, title=title, content=content, position=position
        )
        return dict(created.model_dump(mode="json"))

    @classmethod
    @ability("update_chapter")
    async def update_chapter(
        cls,
        requester_id: str,
        chapter_id: str,
        title: Optional[str] = None,
        content: Optional[str] = None,
        position: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Change a chapter's title, text or place."""
        changes = _fields(title=title, content=content, position=position)
        if not changes:
            raise InvalidInputExternalError("nothing to change")
        updated = cls._chapters(requester_id).update(chapter_id, **changes)
        return dict(updated.model_dump(mode="json"))

    @classmethod
    @ability("delete_chapter")
    async def delete_chapter(cls, requester_id: str, chapter_id: str) -> Dict[str, Any]:
        cls._chapters(requester_id).delete(chapter_id)
        return {"id": chapter_id, "deleted": True}

    @classmethod
    @ability("export_book")
    async def export_book(
        cls, requester_id: str, book_id: str, format: str = "markdown"
    ) -> Dict[str, Any]:
        """The book as Markdown or HTML text, or as an EPUB in base64."""
        if format not in FORMATS:
            raise InvalidInputExternalError(
                f"format must be one of {', '.join(FORMATS)}, not {format!r}"
            )
        rendered = render(cls._books(requester_id).manuscript(book_id), format)
        exported: Dict[str, Any] = {
            "format": format,
            "media_type": rendered.media_type,
            "filename": rendered.filename,
        }
        if format == "epub":
            exported["content_base64"] = base64.b64encode(rendered.body).decode("ascii")
        else:
            exported["content"] = rendered.body.decode("utf-8")
        return exported
