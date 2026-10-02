# SPDX-License-Identifier: AGPL-3.0-or-later
"""Books over the API and through the abilities: chapters keep their own
word count, export downloads each format, another user sees nothing, and
an ability acts as the user it names (the same rows the API shows them).
The old extension kept books in process memory and lost them."""

from typing import Any, Dict

import pytest
from fastapi import HTTPException

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.book.EXT_Book import EXT_Book
from zephyrex.pydantic2.registry import ModelRegistry


def auth(user) -> Dict[str, str]:
    return {"Authorization": f"Bearer {user.jwt}"}


class TestBooks(ExtensionServerMixin):
    extension_class = EXT_Book

    @pytest.fixture
    def book(self, server, admin_a) -> Dict[str, Any]:
        response = server.post(
            "/v1/book",
            json={"book": {"title": "Notes on the Engine", "author": "Ada"}},
            headers=auth(admin_a),
        )
        assert response.status_code == 201, response.text
        created: Dict[str, Any] = response.json()["book"]
        return created

    @pytest.fixture
    def attached(self, server, monkeypatch) -> None:
        """Abilities resolve the running app: this test server's."""
        registry = server.app.state.model_registry
        monkeypatch.setattr(
            ModelRegistry, "attached", classmethod(lambda cls: registry)
        )

    def _chapter(self, server, user, book_id: str, position: int, content: str):
        response = server.post(
            "/v1/book_chapter",
            json={
                "book_chapter": {
                    "book_id": book_id,
                    "title": f"Chapter {position}",
                    "content": content,
                    "position": position,
                }
            },
            headers=auth(user),
        )
        assert response.status_code == 201, response.text
        return response.json()["book_chapter"]

    def test_a_word_count_sent_by_a_client_is_replaced(self, server, admin_a, book):
        response = server.post(
            "/v1/book_chapter",
            json={
                "book_chapter": {
                    "book_id": book["id"],
                    "title": "Padded",
                    "content": "two words",
                    "position": 1,
                    "word_count": 999,
                }
            },
            headers=auth(admin_a),
        )
        assert response.status_code == 201, response.text
        chapter = response.json()["book_chapter"]
        assert chapter["word_count"] == 2
        updated = server.put(
            f"/v1/book_chapter/{chapter['id']}",
            json={"book_chapter": {"word_count": 999}},
            headers=auth(admin_a),
        )
        assert updated.json()["book_chapter"]["word_count"] == 2

    def test_a_chapter_counts_its_own_words(self, server, admin_a, book):
        chapter = self._chapter(server, admin_a, book["id"], 1, "one two three")
        assert chapter["word_count"] == 3
        updated = server.put(
            f"/v1/book_chapter/{chapter['id']}",
            json={"book_chapter": {"content": "just two"}},
            headers=auth(admin_a),
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["book_chapter"]["word_count"] == 2

    @pytest.mark.parametrize(
        "format, media_type",
        [
            ("markdown", "text/markdown"),
            ("html", "text/html"),
            ("epub", "application/epub+zip"),
        ],
    )
    def test_export(self, server, admin_a, book, format, media_type):
        self._chapter(server, admin_a, book["id"], 2, "Second.")
        self._chapter(server, admin_a, book["id"], 1, "First.")
        response = server.get(
            f"/v1/book/{book['id']}/export",
            params={"format": format},
            headers=auth(admin_a),
        )
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith(media_type)
        assert "attachment" in response.headers["content-disposition"]
        if format == "markdown":
            text = response.text
            assert text.index("First.") < text.index("Second.")

    def test_an_unknown_export_format(self, server, admin_a, book):
        response = server.get(
            f"/v1/book/{book['id']}/export",
            params={"format": "docx"},
            headers=auth(admin_a),
        )
        assert response.status_code == 400

    def test_another_user_sees_nothing(self, server, admin_a, admin_b, book):
        assert (
            server.get(f"/v1/book/{book['id']}", headers=auth(admin_b)).status_code
            == 404
        )
        assert (
            server.get(
                f"/v1/book/{book['id']}/export", headers=auth(admin_b)
            ).status_code
            == 404
        )

    async def test_abilities_act_as_the_named_user(
        self, server, admin_a, admin_b, attached
    ):
        created = await EXT_Book.create_book(admin_a.id, "Written by an agent")
        await EXT_Book.add_chapter(admin_a.id, created["id"], "Opening", "Once upon")
        await EXT_Book.add_chapter(admin_a.id, created["id"], "Middle", "a time")
        book = await EXT_Book.get_book(admin_a.id, created["id"])
        assert [c["position"] for c in book["chapters"]] == [1, 2]
        assert book["word_count"] == 4

        # The user sees the agent's book through the API...
        seen = server.get(f"/v1/book/{created['id']}", headers=auth(admin_a))
        assert seen.status_code == 200, seen.text
        # ...and another user's agent cannot reach it.
        with pytest.raises(HTTPException) as raised:
            await EXT_Book.get_book(admin_b.id, created["id"])
        assert raised.value.status_code == 404

        exported = await EXT_Book.export_book(admin_a.id, created["id"], "epub")
        assert exported["filename"].endswith(".epub") and exported["content_base64"]

        await EXT_Book.delete_book(admin_a.id, created["id"])
        gone = server.get(f"/v1/book/{created['id']}", headers=auth(admin_a))
        assert gone.status_code == 404

    async def test_an_ability_needs_a_requester(self, attached):
        with pytest.raises(HTTPException) as raised:
            await EXT_Book.create_book("", "Nobody's book")
        assert raised.value.status_code == 400
