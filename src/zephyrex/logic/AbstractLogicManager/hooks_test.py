# SPDX-License-Identifier: AGPL-3.0-or-later
import inspect
from typing import Any, Dict, Iterator, List, Optional

import pytest

from zephyrex.logic.AbstractLogicManager.hooks import (
    HookContext,
    HookRegistry,
    wrap_method_with_hooks,
)


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


class _Guarded:
    """A hooked method whose hook condition reads a kwarg the call may omit."""

    _hook_registry: HookRegistry
    calls: List[str]

    def __init__(self) -> None:
        self.calls = []

    def create(self, **kwargs: Any) -> Dict[str, Any]:
        self.calls.append("create")
        return {"created": True, **kwargs}


# The hook machinery is typed for AbstractBLLManager subclasses; this plain
# class has every attribute it reads.
_GUARDED_MANAGER: Any = _Guarded
setattr(_Guarded, "create", wrap_method_with_hooks(_GUARDED_MANAGER, "create"))


@pytest.fixture
def guarded() -> Iterator[_Guarded]:
    _Guarded._hook_registry = HookRegistry()
    yield _Guarded()
    _Guarded._hook_registry = HookRegistry()


def _register_team_guard(
    manager: _Guarded, timing: str, blocking: Optional[bool]
) -> List[str]:
    """Register a hook whose condition raises KeyError without ``team_id``;
    returns the list the hook body appends to when it runs."""
    ran: List[str] = []

    def team_guard(ctx: HookContext) -> None:
        ran.append("team_guard")

    manager._hook_registry.register_hook(
        _GUARDED_MANAGER,
        "create",
        timing,
        team_guard,
        condition=lambda ctx: ctx.kwargs["team_id"] in {"t1"},
        blocking=blocking,
    )
    return ran


@pytest.mark.unit
def test_blocking_before_hook_fails_closed_when_its_condition_raises(
    guarded: _Guarded,
) -> None:
    # BEFORE hooks block by default: a raising condition must stop the
    # operation, not skip the validation and let the operation proceed.
    ran = _register_team_guard(guarded, "before", blocking=None)

    with pytest.raises(KeyError, match="team_id"):
        guarded.create(name="x")

    assert ran == []
    assert guarded.calls == []


@pytest.mark.unit
def test_non_blocking_before_hook_is_skipped_when_its_condition_raises(
    guarded: _Guarded,
) -> None:
    ran = _register_team_guard(guarded, "before", blocking=False)

    assert guarded.create(name="x") == {"created": True, "name": "x"}

    assert ran == []
    assert guarded.calls == ["create"]


@pytest.mark.unit
def test_after_hook_is_skipped_when_its_condition_raises(guarded: _Guarded) -> None:
    # AFTER hooks are non-blocking by default.
    ran = _register_team_guard(guarded, "after", blocking=None)

    assert guarded.create(name="x") == {"created": True, "name": "x"}

    assert ran == []


@pytest.mark.unit
def test_blocking_after_hook_fails_closed_when_its_condition_raises(
    guarded: _Guarded,
) -> None:
    ran = _register_team_guard(guarded, "after", blocking=True)

    with pytest.raises(KeyError, match="team_id"):
        guarded.create(name="x")

    assert ran == []
