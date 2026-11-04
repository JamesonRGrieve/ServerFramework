# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reverse-relationship includes resolve each relation once per request."""

from types import SimpleNamespace
from typing import Any, ClassVar, Dict, List

from pydantic import BaseModel
from sqlalchemy import column

from zephyrex.pydantic2.fastapi.resource import _populate_reverse_includes


class _BookTable:
    """An in-memory stand-in for the book table: answers ``list`` with the
    rows whose ``shelf_id`` is in the filter, and records each query."""

    shelf_id = column("shelf_id")
    rows: ClassVar[List[Dict[str, Any]]] = [
        {"id": "b1", "shelf_id": "s1"},
        {"id": "b2", "shelf_id": "s1"},
        {"id": "b3", "shelf_id": "s2"},
    ]
    queries: ClassVar[List[List[Any]]] = []

    @classmethod
    def list(
        cls,
        requester_id: str,
        model_registry: Any,
        filters: List[Any],
        return_type: str,
    ) -> List[Dict[str, Any]]:
        cls.queries.append(filters)
        (shelf_filter,) = filters
        shelf_ids = set(shelf_filter.right.value)
        return [row for row in cls.rows if row["shelf_id"] in shelf_ids]


class ShelfModel(BaseModel):
    id: str


class BookModel(BaseModel):
    id: str
    shelf_id: str

    @classmethod
    def DB(cls, base: Any) -> type[_BookTable]:
        return _BookTable


def test_a_relation_named_many_ways_is_queried_once() -> None:
    """``books`` repeated and ``book`` (its singular alias) are one relation:
    one child query, its result shared between both keys."""
    _BookTable.queries.clear()
    registry = SimpleNamespace(
        bound_models=[ShelfModel, BookModel],
        DB=SimpleNamespace(manager=SimpleNamespace(Base=object())),
    )
    items: List[Dict[str, Any]] = [{"id": "s1"}, {"id": "s2"}, {"id": "s3"}]

    _populate_reverse_includes(
        items,
        ["books", "books", "book", "books"],
        ShelfModel,
        registry,
        requester_id="requester",
    )

    assert len(_BookTable.queries) == 1
    assert [book["id"] for book in items[0]["books"]] == ["b1", "b2"]
    assert items[0]["book"]["id"] == "b1"
    assert [book["id"] for book in items[1]["books"]] == ["b3"]
    assert items[2]["books"] == []
    assert items[2]["book"] is None
