# SPDX-License-Identifier: AGPL-3.0-or-later
"""Include tokens: normalized to distinct names, and capped per request."""

import pytest
from fastapi import HTTPException
from pydantic import BaseModel

from zephyrex.pydantic2.fastapi.query import (
    MAX_INCLUDE_TOKENS,
    _normalize_projection_values,
    _normalize_query_list,
    _validate_includes,
)


class ShelvedItemModel(BaseModel):
    id: str
    shelf_id: str


@pytest.mark.parametrize(
    "value",
    ["shelf, shelf,shelves,shelf", ["shelf", "shelf ", "shelves", "shelf"]],
    ids=["csv-string", "list"],
)
def test_normalize_projection_values_keeps_each_token_once(value):
    assert _normalize_projection_values(value) == ["shelf", "shelves"]


@pytest.mark.parametrize(
    "value",
    ["shelf,shelf, shelves", ["shelf,shelf", "shelves", "shelf"]],
    ids=["csv-string", "list-of-csv"],
)
def test_normalize_query_list_keeps_each_token_once(value):
    assert _normalize_query_list(value) == ["shelf", "shelves"]


def test_repeated_include_is_one_token_and_passes_validation():
    """Hundreds of copies of one valid include collapse to one token, which
    is neither over the cap nor invalid."""
    tokens = _normalize_query_list(["shelf"] * 300)
    assert tokens == ["shelf"]
    _validate_includes(tokens, ShelvedItemModel, "shelved_item")


def test_more_distinct_includes_than_the_cap_is_a_422():
    tokens = [f"relation_{i}" for i in range(MAX_INCLUDE_TOKENS + 1)]
    with pytest.raises(HTTPException) as exc:
        _validate_includes(tokens, ShelvedItemModel, "shelved_item")
    assert exc.value.status_code == 422
    assert exc.value.detail["max_includes"] == MAX_INCLUDE_TOKENS


def test_the_cap_counts_distinct_includes():
    """Duplicates of tokens within the cap are not counted again, so the
    request is judged on what it names: here one invalid include."""
    tokens = ["shelf", "bogus"] * MAX_INCLUDE_TOKENS
    with pytest.raises(HTTPException) as exc:
        _validate_includes(tokens, ShelvedItemModel, "shelved_item")
    assert exc.value.status_code == 422
    assert exc.value.detail["invalid_includes"] == ["bogus"]
