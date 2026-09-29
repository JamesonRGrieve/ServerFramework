# SPDX-License-Identifier: AGPL-3.0-or-later
import inspect
from typing import List, Optional

from zephyrex.logic.AbstractLogicManager.hooks import wrap_method_with_hooks


class _Manager:
    def members(self, id: str, include: Optional[List[str]] = None) -> str:
        """The members of ``id``."""
        return f"{id}:{include}"


_Manager.members.marker = "kept"  # type: ignore[attr-defined]


def test_wrapped_method_looks_like_the_original():
    wrapped = wrap_method_with_hooks(_Manager, "members")

    assert wrapped.__name__ == "members"
    assert wrapped.__doc__ == "The members of ``id``."
    assert wrapped.marker == "kept"
    # The route layer decides what to pass from this signature.
    assert list(inspect.signature(wrapped).parameters) == ["self", "id", "include"]
    assert wrapped(_Manager(), "t1", include=["role"]) == "t1:['role']"
