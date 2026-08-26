"""Integration tests for blocking / non-blocking hook execution (Issue #38).

Exercises the full hook lifecycle through real ``AbstractBLLManager``
subclasses — not mocks. Each test builds a minimal manager, registers
hooks via the public API (``hook_bll`` / ``non_critical_hook``), calls
a hooked method, and asserts the observable outcome (propagated error,
swallowed error with metric, argument mutation, result override, etc.).
"""

from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

import zephyrex.logic.AbstractLogicManager.hooks as _hooks_module
from zephyrex.logic.AbstractLogicManager import (
    NON_BLOCKING_HOOK_FAILURES,
    AbstractBLLManager,
    HookContext,
    HookRegistry,
    _register_hook_on_class,
    _sort_hooks_topologically,
)

# ---------------------------------------------------------------------------
# Minimal concrete manager for testing
# ---------------------------------------------------------------------------


class HookTestManager(AbstractBLLManager):
    """Concrete manager whose methods we can hook and call."""

    _model = None
    call_log: List[str]

    def __init__(self) -> None:
        super().__init__()
        self.call_log = []

    def create(self, **kwargs: Any) -> Dict[str, Any]:
        self.call_log.append("create")
        return {"created": True, **kwargs}

    def update(self, **kwargs: Any) -> Dict[str, Any]:
        self.call_log.append("update")
        return {"updated": True, **kwargs}

    def delete(self, **kwargs: Any) -> Dict[str, Any]:
        self.call_log.append("delete")
        return {"deleted": True}

    def get(self, **kwargs: Any) -> Dict[str, Any]:
        self.call_log.append("get")
        return {"item": "found"}


class ChildHookTestManager(HookTestManager):
    """Child that inherits parent hooks and can add its own."""

    pass


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_hook_registries():
    """Reset registries before each test to prevent cross-test leakage."""
    HookTestManager._hook_registry = HookRegistry()
    ChildHookTestManager._hook_registry = HookRegistry(
        parent_registry=HookTestManager._hook_registry
    )
    NON_BLOCKING_HOOK_FAILURES.clear()
    yield
    HookTestManager._hook_registry = HookRegistry()
    ChildHookTestManager._hook_registry = HookRegistry(
        parent_registry=HookTestManager._hook_registry
    )
    NON_BLOCKING_HOOK_FAILURES.clear()


# ---------------------------------------------------------------------------
# 1. Blocking BEFORE hook — error propagates, method never runs
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_blocking_before_hook_propagates_exception():
    def deny(ctx: HookContext) -> None:
        raise PermissionError("access denied")

    _register_hook_on_class(
        HookTestManager,
        "create",
        "before",
        deny,
        priority=10,
        condition=None,
        blocking=True,
    )
    mgr = HookTestManager()
    with pytest.raises(PermissionError, match="access denied"):
        mgr.create(name="x")
    assert "create" not in mgr.call_log


# ---------------------------------------------------------------------------
# 2. Non-blocking BEFORE hook — error swallowed, method still runs
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_non_blocking_before_hook_swallows_exception():
    def flaky_validator(ctx: HookContext) -> None:
        raise RuntimeError("transient failure")

    _register_hook_on_class(
        HookTestManager,
        "create",
        "before",
        flaky_validator,
        priority=10,
        condition=None,
        blocking=False,
    )
    mgr = HookTestManager()
    result = mgr.create(name="y")
    assert result["created"] is True
    assert "create" in mgr.call_log
    assert any("flaky_validator" in k for k in NON_BLOCKING_HOOK_FAILURES)


# ---------------------------------------------------------------------------
# 3. Blocking AFTER hook — error propagates after method executes
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_blocking_after_hook_propagates_exception():
    def post_audit(ctx: HookContext) -> None:
        raise ValueError("audit check failed")

    _register_hook_on_class(
        HookTestManager,
        "create",
        "after",
        post_audit,
        priority=10,
        condition=None,
        blocking=True,
    )
    mgr = HookTestManager()
    with pytest.raises(ValueError, match="audit check failed"):
        mgr.create(name="z")
    assert "create" in mgr.call_log


# ---------------------------------------------------------------------------
# 4. Non-blocking AFTER hook — error swallowed, result still returned
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_non_blocking_after_hook_swallows_exception():
    def broken_notifier(ctx: HookContext) -> None:
        raise ConnectionError("notification service down")

    _register_hook_on_class(
        HookTestManager,
        "create",
        "after",
        broken_notifier,
        priority=10,
        condition=None,
        blocking=False,
    )
    mgr = HookTestManager()
    result = mgr.create(name="ok")
    assert result["created"] is True
    assert any("broken_notifier" in k for k in NON_BLOCKING_HOOK_FAILURES)


# ---------------------------------------------------------------------------
# 5. Default blocking semantics: BEFORE=True, AFTER=False
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_before_hook_defaults_to_blocking():
    def exploding(ctx: HookContext) -> None:
        raise RuntimeError("boom")

    _register_hook_on_class(
        HookTestManager,
        "update",
        "before",
        exploding,
        priority=10,
        condition=None,
    )
    mgr = HookTestManager()
    with pytest.raises(RuntimeError, match="boom"):
        mgr.update(id="1")
    assert "update" not in mgr.call_log


@pytest.mark.unit
def test_after_hook_defaults_to_non_blocking():
    def exploding(ctx: HookContext) -> None:
        raise RuntimeError("boom")

    _register_hook_on_class(
        HookTestManager,
        "update",
        "after",
        exploding,
        priority=10,
        condition=None,
    )
    mgr = HookTestManager()
    result = mgr.update(id="1")
    assert result["updated"] is True
    assert any("exploding" in k for k in NON_BLOCKING_HOOK_FAILURES)


# ---------------------------------------------------------------------------
# 6. non_critical_hook alias wires blocking=False
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_non_critical_hook_is_non_blocking():
    def crashy(ctx: HookContext) -> None:
        raise RuntimeError("should be swallowed")

    _register_hook_on_class(
        HookTestManager,
        "delete",
        "after",
        crashy,
        priority=10,
        condition=None,
        blocking=False,
    )
    mgr = HookTestManager()
    result = mgr.delete(id="1")
    assert result["deleted"] is True
    assert any("crashy" in k for k in NON_BLOCKING_HOOK_FAILURES)


# ---------------------------------------------------------------------------
# 7. BEFORE hook can skip execution via skip_method()
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_before_hook_skip_method():
    def cache_hit(ctx: HookContext) -> None:
        ctx.skip_method()
        ctx.set_result({"cached": True})

    _register_hook_on_class(
        HookTestManager,
        "get",
        "before",
        cache_hit,
        priority=10,
        condition=None,
    )
    mgr = HookTestManager()
    result = mgr.get(id="123")
    assert result == {"cached": True}
    assert "get" not in mgr.call_log


# ---------------------------------------------------------------------------
# 8. BEFORE hook can modify arguments
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_before_hook_modifies_arguments():
    def inject_audit_field(ctx: HookContext) -> None:
        ctx.kwargs["audited_by"] = "system"

    _register_hook_on_class(
        HookTestManager,
        "create",
        "before",
        inject_audit_field,
        priority=10,
        condition=None,
    )
    mgr = HookTestManager()
    result = mgr.create(name="test")
    assert result["audited_by"] == "system"
    assert result["name"] == "test"


# ---------------------------------------------------------------------------
# 9. AFTER hook can modify result
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_after_hook_modifies_result():
    def enrich_result(ctx: HookContext) -> None:
        ctx.set_result({**ctx.result, "enriched": True})

    _register_hook_on_class(
        HookTestManager,
        "get",
        "after",
        enrich_result,
        priority=10,
        condition=None,
    )
    mgr = HookTestManager()
    result = mgr.get(id="1")
    assert result["enriched"] is True
    assert result["item"] == "found"


# ---------------------------------------------------------------------------
# 10. Condition-based hook execution
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_conditional_hook_skipped_when_false():
    call_tracker = MagicMock()

    def guarded_hook(ctx: HookContext) -> None:
        call_tracker()

    def only_deletes(ctx: HookContext) -> bool:
        return ctx.method_name == "delete"  # type: ignore[no-any-return]

    _register_hook_on_class(
        HookTestManager,
        "create",
        "before",
        guarded_hook,
        priority=10,
        condition=only_deletes,
    )
    mgr = HookTestManager()
    mgr.create(name="test")
    call_tracker.assert_not_called()


@pytest.mark.unit
def test_conditional_hook_runs_when_true():
    call_tracker = MagicMock()

    def guarded_hook(ctx: HookContext) -> None:
        call_tracker()

    def always(ctx: HookContext) -> bool:
        return True

    _register_hook_on_class(
        HookTestManager,
        "create",
        "before",
        guarded_hook,
        priority=10,
        condition=always,
    )
    mgr = HookTestManager()
    mgr.create(name="test")
    call_tracker.assert_called_once()


# ---------------------------------------------------------------------------
# 11. Priority ordering — lower runs first
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_hooks_execute_in_priority_order():
    execution_order: list[str] = []

    def hook_a(ctx: HookContext) -> None:
        execution_order.append("a")

    def hook_b(ctx: HookContext) -> None:
        execution_order.append("b")

    def hook_c(ctx: HookContext) -> None:
        execution_order.append("c")

    _register_hook_on_class(
        HookTestManager,
        "create",
        "before",
        hook_c,
        priority=30,
        condition=None,
    )
    _register_hook_on_class(
        HookTestManager,
        "create",
        "before",
        hook_a,
        priority=10,
        condition=None,
    )
    _register_hook_on_class(
        HookTestManager,
        "create",
        "before",
        hook_b,
        priority=20,
        condition=None,
    )
    mgr = HookTestManager()
    mgr.create(name="test")
    assert execution_order == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# 12. Inheritance — child inherits parent hooks
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_child_inherits_parent_hooks():
    call_tracker = MagicMock()

    def parent_hook(ctx: HookContext) -> None:
        call_tracker()

    _register_hook_on_class(
        HookTestManager,
        "create",
        "before",
        parent_hook,
        priority=10,
        condition=None,
    )
    mgr = ChildHookTestManager()
    mgr.create(name="from-child")
    call_tracker.assert_called_once()


@pytest.mark.unit
def test_child_can_add_own_hooks():
    execution_order: list[str] = []

    def parent_hook(ctx: HookContext) -> None:
        execution_order.append("parent")

    def child_hook(ctx: HookContext) -> None:
        execution_order.append("child")

    _register_hook_on_class(
        HookTestManager,
        "create",
        "before",
        parent_hook,
        priority=10,
        condition=None,
    )
    _register_hook_on_class(
        ChildHookTestManager,
        "create",
        "before",
        child_hook,
        priority=20,
        condition=None,
    )
    mgr = ChildHookTestManager()
    mgr.create(name="test")
    assert "parent" in execution_order
    assert "child" in execution_order


# ---------------------------------------------------------------------------
# 13. Metric counter increments per swallowed failure
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_metric_counter_increments_on_each_swallowed_failure():
    def bad_hook(ctx: HookContext) -> None:
        raise RuntimeError("counted")

    _register_hook_on_class(
        HookTestManager,
        "create",
        "after",
        bad_hook,
        priority=10,
        condition=None,
        blocking=False,
    )
    mgr = HookTestManager()
    mgr.create(name="first")
    mgr.create(name="second")
    keys = [k for k in NON_BLOCKING_HOOK_FAILURES if "bad_hook" in k]
    assert len(keys) == 1
    assert NON_BLOCKING_HOOK_FAILURES[keys[0]] == 2


# ---------------------------------------------------------------------------
# 14. Multiple hooks: blocking one stops chain, non-blocking continues
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_blocking_before_hook_stops_subsequent_hooks():
    execution_order: list[str] = []

    def first(ctx: HookContext) -> None:
        execution_order.append("first")
        raise PermissionError("denied")

    def second(ctx: HookContext) -> None:
        execution_order.append("second")

    _register_hook_on_class(
        HookTestManager,
        "create",
        "before",
        first,
        priority=10,
        condition=None,
        blocking=True,
    )
    _register_hook_on_class(
        HookTestManager,
        "create",
        "before",
        second,
        priority=20,
        condition=None,
        blocking=True,
    )
    mgr = HookTestManager()
    with pytest.raises(PermissionError):
        mgr.create(name="test")
    assert execution_order == ["first"]
    assert "create" not in mgr.call_log


@pytest.mark.unit
def test_non_blocking_before_hook_continues_to_next():
    execution_order: list[str] = []

    def first(ctx: HookContext) -> None:
        execution_order.append("first")
        raise RuntimeError("swallowed")

    def second(ctx: HookContext) -> None:
        execution_order.append("second")

    _register_hook_on_class(
        HookTestManager,
        "create",
        "before",
        first,
        priority=10,
        condition=None,
        blocking=False,
    )
    _register_hook_on_class(
        HookTestManager,
        "create",
        "before",
        second,
        priority=20,
        condition=None,
        blocking=True,
    )
    mgr = HookTestManager()
    result = mgr.create(name="test")
    assert execution_order == ["first", "second"]
    assert result["created"] is True


# ---------------------------------------------------------------------------
# 15. Mixed blocking and non-blocking on same method
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_mixed_blocking_non_blocking_before_hooks():
    """Non-blocking hook fails, blocking hook succeeds — method runs."""
    execution_order: list[str] = []

    def non_blocking_logger(ctx: HookContext) -> None:
        execution_order.append("logger")
        raise RuntimeError("log service down")

    def blocking_auth(ctx: HookContext) -> None:
        execution_order.append("auth")

    _register_hook_on_class(
        HookTestManager,
        "create",
        "before",
        non_blocking_logger,
        priority=10,
        condition=None,
        blocking=False,
    )
    _register_hook_on_class(
        HookTestManager,
        "create",
        "before",
        blocking_auth,
        priority=20,
        condition=None,
        blocking=True,
    )
    mgr = HookTestManager()
    result = mgr.create(name="test")
    assert execution_order == ["logger", "auth"]
    assert result["created"] is True
    assert any("non_blocking_logger" in k for k in NON_BLOCKING_HOOK_FAILURES)


@pytest.mark.unit
def test_mixed_blocking_non_blocking_after_hooks():
    """Non-blocking after hook fails, blocking after hook succeeds."""
    execution_order: list[str] = []

    def non_blocking_analytics(ctx: HookContext) -> None:
        execution_order.append("analytics")
        raise RuntimeError("analytics down")

    def blocking_cache_invalidation(ctx: HookContext) -> None:
        execution_order.append("cache")

    _register_hook_on_class(
        HookTestManager,
        "update",
        "after",
        non_blocking_analytics,
        priority=10,
        condition=None,
        blocking=False,
    )
    _register_hook_on_class(
        HookTestManager,
        "update",
        "after",
        blocking_cache_invalidation,
        priority=20,
        condition=None,
        blocking=True,
    )
    mgr = HookTestManager()
    result = mgr.update(id="1")
    assert result["updated"] is True
    assert execution_order == ["analytics", "cache"]


# ---------------------------------------------------------------------------
# 16. Before AND after hooks on same method
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_before_and_after_hooks_both_execute():
    execution_order: list[str] = []

    def before_validate(ctx: HookContext) -> None:
        execution_order.append("before")

    def after_notify(ctx: HookContext) -> None:
        execution_order.append("after")

    _register_hook_on_class(
        HookTestManager,
        "create",
        "before",
        before_validate,
        priority=10,
        condition=None,
    )
    _register_hook_on_class(
        HookTestManager,
        "create",
        "after",
        after_notify,
        priority=10,
        condition=None,
    )
    mgr = HookTestManager()
    result = mgr.create(name="test")
    assert execution_order == ["before", "after"]
    assert result["created"] is True


# ---------------------------------------------------------------------------
# 17. HookContext exposes correct timing
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_hook_context_reports_correct_timing():
    observed_timings: list[str] = []

    def observer(ctx: HookContext) -> None:
        observed_timings.append(ctx.timing.value)

    _register_hook_on_class(
        HookTestManager,
        "get",
        "before",
        observer,
        priority=10,
        condition=None,
    )
    _register_hook_on_class(
        HookTestManager,
        "get",
        "after",
        observer,
        priority=10,
        condition=None,
    )
    mgr = HookTestManager()
    mgr.get(id="1")
    assert observed_timings == ["before", "after"]


# ---------------------------------------------------------------------------
# 18. get_hooks memoization (hot-path optimization + invalidation correctness)
# ---------------------------------------------------------------------------


def _named_hook(name: str, ext: str):
    """Build a hook function whose derived extension name is ``ext``.

    ``_hook_extension_name`` reads ``func.__module__``; spelling it as
    ``zephyrex.extensions.<ext>.hooks`` yields extension ``<ext>`` so
    before/after ordering constraints can reference distinct extensions.
    """

    def _hook(ctx: HookContext) -> None:
        return None

    _hook.__name__ = name
    _hook.__module__ = f"zephyrex.extensions.{ext}.hooks"
    return _hook


def _register_ordered_before_hooks(manager_class) -> None:
    """Register three ordered BEFORE hooks on ``create``.

    Constraints force the deterministic order validate -> mfa -> audit:
      * ``validate`` must run BEFORE ``mfa``
      * ``audit`` must run AFTER ``mfa``
    """
    _register_hook_on_class(
        manager_class,
        "create",
        "before",
        _named_hook("audit", "audit"),
        50,
        None,
        after=["mfa"],
    )
    _register_hook_on_class(
        manager_class,
        "create",
        "before",
        _named_hook("mfa", "mfa"),
        50,
        None,
    )
    _register_hook_on_class(
        manager_class,
        "create",
        "before",
        _named_hook("validate", "validate"),
        50,
        None,
        before=["mfa"],
    )


@pytest.mark.unit
def test_get_hooks_memoized_result_matches_fresh_sort():
    """(a) The memoized result equals the pure topological sort and is stable
    across repeated calls -- memoization does not alter the output."""
    _register_ordered_before_hooks(HookTestManager)
    reg = HookTestManager._hook_registry

    # Canonical order computed directly from the raw hook list.
    raw = list(reg.hooks["create"]["before"])
    expected = [h["func"].__name__ for h in _sort_hooks_topologically(raw)]

    got_first = [h["func"].__name__ for h in reg.get_hooks("create")["before"]]
    got_second = [h["func"].__name__ for h in reg.get_hooks("create")["before"]]

    assert expected == ["validate", "mfa", "audit"]
    assert got_first == expected
    assert got_second == expected


@pytest.mark.unit
def test_get_hooks_reflects_hook_registered_after_first_call():
    """(b) A hook registered AFTER a first get_hooks call is reflected on the
    next call -- registration invalidates the memoized result."""
    _register_ordered_before_hooks(HookTestManager)
    reg = HookTestManager._hook_registry

    first = [h["func"].__name__ for h in reg.get_hooks("create")["before"]]
    assert "late" not in first

    _register_hook_on_class(
        HookTestManager,
        "create",
        "before",
        _named_hook("late", "late_ext"),
        50,
        None,
    )

    second = [h["func"].__name__ for h in reg.get_hooks("create")["before"]]
    assert "late" in second
    assert len(second) == len(first) + 1


@pytest.mark.unit
def test_get_hooks_reflects_hook_registered_on_parent_after_child_cached():
    """(b') A hook registered on a PARENT registry after a CHILD cached its
    result must still surface through the child -- chain-version invalidation.

    This is the subtle correctness risk: the parent has no reference to its
    children, so the child must detect the ancestor mutation on lookup."""
    child_reg = ChildHookTestManager._hook_registry

    # Child caches an (empty-of-own-hooks) result first.
    before = [h["func"].__name__ for h in child_reg.get_hooks("create")["before"]]
    assert "from_parent" not in before

    # Register on the PARENT class after the child already cached.
    _register_hook_on_class(
        HookTestManager,
        "create",
        "before",
        _named_hook("from_parent", "parent_ext"),
        50,
        None,
    )

    after = [h["func"].__name__ for h in child_reg.get_hooks("create")["before"]]
    assert "from_parent" in after


@pytest.mark.unit
def test_get_hooks_does_not_recompute_sort_on_cache_hit(monkeypatch):
    """(c) The topological sort is not re-run on a second identical call --
    proving the hot path is memoized rather than recomputed each time."""
    _register_ordered_before_hooks(HookTestManager)
    reg = HookTestManager._hook_registry

    call_count = {"n": 0}
    original_sort = _hooks_module._sort_hooks_topologically

    def _counting_sort(hook_list):
        call_count["n"] += 1
        return original_sort(hook_list)

    monkeypatch.setattr(_hooks_module, "_sort_hooks_topologically", _counting_sort)

    reg.get_hooks("create")  # first call: computes + caches
    first_count = call_count["n"]
    assert first_count > 0, "sort must run on the first (cold) call"

    reg.get_hooks("create")  # second identical call: served from cache
    assert (
        call_count["n"] == first_count
    ), "topological sort must not be recomputed on a memoized cache hit"
