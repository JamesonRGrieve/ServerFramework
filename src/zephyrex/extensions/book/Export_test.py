# SPDX-License-Identifier: AGPL-3.0-or-later
"""Book export: Markdown, HTML (markup typed into a chapter is shown, not
run) and EPUB (a valid container with each chapter), filenames and word
counts."""

import io
import zipfile

import pytest

from zephyrex.extensions.book.Export import (
    Chapter,
    Manuscript,
    filename,
    render,
    word_count,
)

BOOK = Manuscript(
    id="b1",
    title="Ada & the <Engine>",
    author="Ada Lovelace",
    language="en",
    description="Notes.",
    chapters=[
        Chapter("One", "The *first* chapter."),
        Chapter(
            "Two <b>", 'Raw <script>alert("x")</script> and <img src=x onerror=1>.'
        ),
    ],
)


def test_markdown():
    rendered = render(BOOK, "markdown")
    text = rendered.body.decode()
    assert text.startswith("# Ada & the <Engine>\n\n*Ada Lovelace*")
    assert "## One\n\nThe *first* chapter." in text
    assert rendered.media_type.startswith("text/markdown")
    assert rendered.filename == "Ada-the-Engine.md"


def test_html_shows_markup_instead_of_running_it():
    page = render(BOOK, "html").body.decode()
    assert "<title>Ada &amp; the &lt;Engine&gt;</title>" in page
    assert "<em>first</em>" in page
    assert "<script>" not in page and "<img" not in page
    assert "&lt;script&gt;" in page
    assert "<h2>Two &lt;b&gt;</h2>" in page


def test_epub_is_a_valid_container_with_every_chapter():
    rendered = render(BOOK, "epub")
    assert rendered.media_type == "application/epub+zip"
    with zipfile.ZipFile(io.BytesIO(rendered.body)) as archive:
        names = archive.namelist()
        assert names[0] == "mimetype"
        assert archive.read("mimetype") == b"application/epub+zip"
        chapters = [name for name in names if name.endswith("chapter-2.xhtml")]
        assert chapters
        body = archive.read(chapters[0]).decode()
    assert "&lt;script&gt;" in body and "<script>" not in body


def test_an_unknown_format():
    with pytest.raises(ValueError):
        render(BOOK, "docx")


@pytest.mark.parametrize(
    "title, name",
    [
        ("My Book", "My-Book.txt"),
        ("../../etc/passwd", "etc-passwd.txt"),
        ("", "book.txt"),
    ],
)
def test_filenames_are_safe(title, name):
    assert filename(title, "txt") == name


def test_word_count():
    assert word_count("one  two\nthree") == 3
    assert word_count("") == 0 and word_count(None) == 0
