# SPDX-License-Identifier: AGPL-3.0-or-later
"""A batch ``create(entities=[...])`` runs the create hooks once per entity,
each seeing that entity's fields, as single creates would. It ran them once
for the whole call, with ``kwargs == {"entities": [...]}``, so a hook that
checks or fills one record's fields saw none of them: it refused the batch,
or (guarding on ``entities``) skipped its check."""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from zephyrex.logic.AbstractLogicManager import (
    AbstractBLLManager,
    HookContext,
    HookRegistry,
    _register_hook_on_class,
)


class BatchHookManager(AbstractBLLManager):
    """The base ``create`` (single and batch) over an in-memory write."""

    _model = None
    written: List[Dict[str, Any]]

    def __init__(self) -> None:
        super().__init__()
        self.written = []

    def _create_single_entity(self, **kwargs: Any) -> Any:
        self.written.append(dict(kwargs))
        return {"id": f"r{len(self.written)}", **kwargs}


class OverridingManager(BatchHookManager):
    """A manager whose own ``create`` handles the list first (a batch-wide
    check), as the LDAP and SAML managers do, then hands it to the base."""

    def create(self, **kwargs: Any) -> Any:
        if isinstance(kwargs.get("entities"), list):
            names = [entity["name"] for entity in kwargs["entities"]]
            if len(set(names)) != len(names):
                raise ValueError("names repeat")
        return super().create(**kwargs)


@pytest.fixture(autouse=True)
def _fresh_registries():
    for manager in (BatchHookManager, OverridingManager):
        manager._hook_registry = HookRegistry()
    yield
    for manager in (BatchHookManager, OverridingManager):
        manager._hook_registry = HookRegistry()


def _hook(manager: type, timing: str, func: Any) -> None:
    _register_hook_on_class(
        manager, "create", timing, func, priority=10, condition=None
    )


@pytest.mark.parametrize("manager_class", [BatchHookManager, OverridingManager])
def test_each_entity_runs_the_hooks_with_its_own_fields(manager_class):
    seen_before: List[Dict[str, Any]] = []
    seen_after: List[Any] = []

    def one_record(ctx: HookContext) -> None:
        if ctx.kwarg("name") is None:
            raise ValueError("a record names itself")
        seen_before.append(dict(ctx.kwargs))
        ctx.kwargs["checked"] = True

    def observed(ctx: HookContext) -> None:
        seen_after.append(ctx.result)

    _hook(manager_class, "before", one_record)
    _hook(manager_class, "after", observed)

    manager = manager_class()
    results = manager.create(entities=[{"name": "a"}, {"name": "b"}], team="t")

    assert [entry["name"] for entry in seen_before] == ["a", "b"]
    assert all(entry["team"] == "t" for entry in seen_before)
    assert all(row["checked"] for row in manager.written)  # hook edits kept
    assert seen_after == results and [r["name"] for r in results] == ["a", "b"]


def test_a_refusing_hook_stops_at_its_record():
    def refuse_b(ctx: HookContext) -> None:
        if ctx.kwarg("name") == "b":
            raise ValueError("no b")

    _hook(BatchHookManager, "before", refuse_b)
    manager = BatchHookManager()
    with pytest.raises(ValueError, match="no b"):
        manager.create(entities=[{"name": "a"}, {"name": "b"}, {"name": "c"}])
    assert [row["name"] for row in manager.written] == ["a"]


def test_an_override_keeps_its_batch_wide_check():
    calls: List[str] = []
    _hook(OverridingManager, "before", lambda ctx: calls.append(ctx.kwarg("name")))
    with pytest.raises(ValueError, match="names repeat"):
        OverridingManager().create(entities=[{"name": "a"}, {"name": "a"}])
    assert calls == []  # refused before any record's hooks ran


def test_a_single_create_runs_the_hooks_once():
    calls: List[Any] = []
    _hook(BatchHookManager, "before", lambda ctx: calls.append(ctx.kwarg("name")))
    BatchHookManager().create(name="solo")
    assert calls == ["solo"]
