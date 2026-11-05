# SPDX-License-Identifier: AGPL-3.0-or-later
"""Union detection that covers both spellings of a union annotation.

``Optional[X]`` / ``Union[X, Y]`` have origin ``typing.Union``; PEP 604
``X | None`` has origin ``types.UnionType``. Checking only ``typing.Union``
silently treats every ``X | None`` field as an opaque type.
"""

from types import UnionType
from typing import Any, Union, get_args, get_origin

UNION_ORIGINS = frozenset({Union, UnionType})


def is_union(annotation: Any) -> bool:
    """True for ``Union[...]``, ``Optional[...]`` and ``X | Y``."""
    return get_origin(annotation) in UNION_ORIGINS


def is_optional(annotation: Any) -> bool:
    """True for a union that admits ``None``."""
    return is_union(annotation) and type(None) in get_args(annotation)


def non_none_args(annotation: Any) -> tuple[Any, ...]:
    """The members of a union other than ``None``."""
    return tuple(arg for arg in get_args(annotation) if arg is not type(None))


def unwrap_optional(annotation: Any) -> Any:
    """``X`` for ``Optional[X]`` / ``X | None``; any other annotation as is."""
    if is_optional(annotation):
        members = non_none_args(annotation)
        if len(members) == 1:
            return members[0]
    return annotation
