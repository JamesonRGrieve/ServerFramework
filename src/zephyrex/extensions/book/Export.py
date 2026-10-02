# SPDX-License-Identifier: AGPL-3.0-or-later
"""A book's chapters (Markdown) rendered as one Markdown file, a standalone
HTML page, or an EPUB 3 ebook.

Chapter text is CommonMark with raw HTML disabled: markup typed into a
chapter is shown as text, never run, in the HTML and EPUB a reader
opens.
"""

import html
import io
import re
from dataclasses import dataclass
from typing import List, Optional, Sequence

from markdown_it import MarkdownIt

FORMATS = ("markdown", "html", "epub")
_MARKDOWN = MarkdownIt("commonmark", {"html": False})
_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True)
class Chapter:
    title: str
    content: str


@dataclass(frozen=True)
class Manuscript:
    id: str
    title: str
    author: str
    language: str
    description: str
    chapters: Sequence[Chapter]


@dataclass(frozen=True)
class Rendered:
    body: bytes
    media_type: str
    filename: str


def filename(title: str, extension: str) -> str:
    stem = _UNSAFE_FILENAME.sub("-", title).strip("-.") or "book"
    return f"{stem[:80]}.{extension}"


def chapter_html(chapter: Chapter) -> str:
    return f"<h2>{html.escape(chapter.title)}</h2>\n{_MARKDOWN.render(chapter.content)}"


def as_markdown(book: Manuscript) -> Rendered:
    parts = [f"# {book.title}"]
    if book.author:
        parts.append(f"*{book.author}*")
    if book.description:
        parts.append(book.description)
    parts.extend(
        f"## {chapter.title}\n\n{chapter.content}" for chapter in book.chapters
    )
    return Rendered(
        "\n\n".join(parts).encode("utf-8") + b"\n",
        "text/markdown; charset=utf-8",
        filename(book.title, "md"),
    )


def as_html(book: Manuscript) -> Rendered:
    title = html.escape(book.title)
    byline = f"<p><em>{html.escape(book.author)}</em></p>\n" if book.author else ""
    body = "\n".join(chapter_html(chapter) for chapter in book.chapters)
    page = (
        f'<!doctype html>\n<html lang="{html.escape(book.language)}">\n<head>\n'
        f'<meta charset="utf-8">\n<title>{title}</title>\n</head>\n<body>\n'
        f"<h1>{title}</h1>\n{byline}{body}\n</body>\n</html>\n"
    )
    return Rendered(
        page.encode("utf-8"), "text/html; charset=utf-8", filename(book.title, "html")
    )


def as_epub(book: Manuscript) -> Rendered:
    from ebooklib import epub

    document = epub.EpubBook()
    document.set_identifier(f"urn:zephyrex:book:{book.id}")
    document.set_title(book.title)
    document.set_language(book.language)
    if book.author:
        document.add_author(book.author)
    if book.description:
        document.add_metadata("DC", "description", book.description)
    pages: List[epub.EpubHtml] = []
    for number, chapter in enumerate(book.chapters, start=1):
        page = epub.EpubHtml(
            title=chapter.title, file_name=f"chapter-{number}.xhtml", lang=book.language
        )
        page.content = chapter_html(chapter)
        document.add_item(page)
        pages.append(page)
    document.toc = pages
    document.add_item(epub.EpubNcx())
    document.add_item(epub.EpubNav())
    document.spine = ["nav", *pages]
    buffer = io.BytesIO()
    epub.write_epub(buffer, document)
    return Rendered(
        buffer.getvalue(), "application/epub+zip", filename(book.title, "epub")
    )


def render(book: Manuscript, format: str) -> Rendered:
    renderer = {"markdown": as_markdown, "html": as_html, "epub": as_epub}.get(format)
    if renderer is None:
        raise ValueError(f"format must be one of {', '.join(FORMATS)}, not {format!r}")
    return renderer(book)


def word_count(content: Optional[str]) -> int:
    return len((content or "").split())
