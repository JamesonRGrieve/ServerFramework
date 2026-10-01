# SPDX-License-Identifier: AGPL-3.0-or-later
from typing import List, Optional, Union

import pytest

from zephyrex.lib.TypeUnions import (
    is_optional,
    is_union,
    non_none_args,
    unwrap_optional,
)


@pytest.mark.parametrize(
    "annotation",
    [Optional[int], Union[int, str], int | None, int | str, Optional[List[int]]],
)
def test_both_union_spellings_are_unions(annotation):
    assert is_union(annotation)


@pytest.mark.parametrize("annotation", [int, List[int], dict, None])
def test_non_unions(annotation):
    assert not is_union(annotation)


def test_optional_requires_none_member():
    assert is_optional(Optional[int])
    assert is_optional(int | None)
    assert not is_optional(int | str)
    assert not is_optional(Union[int, str])


def test_unwrap_optional_only_unwraps_single_member_optionals():
    assert unwrap_optional(Optional[int]) is int
    assert unwrap_optional(int | None) is int
    assert unwrap_optional(Optional[Union[int, str]]) == Optional[Union[int, str]]
    assert unwrap_optional(int | str) == int | str
    assert unwrap_optional(int) is int


def test_non_none_args_strips_only_none():
    """Members, not their order: typing caches unions by set equality, so
    ``Optional[Union[int, str]]`` can come back as an earlier-built
    ``Union[str, int, None]`` (the pre-commit suite hit exactly that)."""
    assert non_none_args(int | None) == (int,)
    for union in (Optional[Union[int, str]], int | str):
        members = non_none_args(union)
        assert len(members) == 2
        assert set(members) == {int, str}
