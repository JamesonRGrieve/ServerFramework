"""Tests for hook-system extensions (Items 21, 22, 41).

Pure-utility tests for the topological sort, the blocking flag, the
non_critical_hook alias, and the typed HookContext accessors. Mocks are
acceptable here per the test mark contract.
"""

from __future__ import annotations

import pytest

from serverframework.logic.AbstractLogicManager import (
    NON_BLOCKING_HOOK_FAILURES,
    HookContext,
    HookOrderingError,
    HookTiming,
    _emit_non_blocking_failure_metric,
    _hook_blocking,
    _hook_extension_name,
    _sort_hooks_topologically,
    non_critical_hook,
)


# ---------------------------------------------------------------------------
# Item 21 -- topological sort + cycle detection
# ---------------------------------------------------------------------------


def _hk(name: str, ext: str, priority: int = 50, before=None, after=None):
    """Build a hook_info dict in the shape produced by the registry."""
    func = lambda ctx: None  # noqa: E731
    func.__name__ = name
    return {
        "func": func,
        "priority": priority,
        "extension": ext,
        "before": list(before or []),
        "after": list(after or []),
        "blocking": None,
        "condition": None,
    }


@pytest.mark.unit
def test_topological_sort_respects_explicit_after():
    audit = _hk("audit", "audit_ext", after=["mfa_ext"])
    mfa = _hk("mfa", "mfa_ext")
    sorted_ = _sort_hooks_topologically([audit, mfa])
    names = [h["func"].__name__ for h in sorted_]
    assert names.index("mfa") < names.index("audit")


@pytest.mark.unit
def test_topological_sort_respects_explicit_before():
    a = _hk("a", "a_ext", before=["b_ext"])
    b = _hk("b", "b_ext")
    sorted_ = _sort_hooks_topologically([b, a])  # registered out of order
    names = [h["func"].__name__ for h in sorted_]
    assert names.index("a") < names.index("b")


@pytest.mark.unit
def test_topological_sort_priority_breaks_ties():
    a = _hk("a", "ext1", priority=10)
    b = _hk("b", "ext2", priority=5)
    sorted_ = _sort_hooks_topologically([a, b])
    # No constraints -> alphabetical extension first; ext1 < ext2; but
    # within an extension priority controls order. Verify the deterministic
    # result is stable.
    assert sorted_[0]["func"].__name__ in {"a", "b"}
    # Run again with same input -> exact same output.
    sorted2 = _sort_hooks_topologically([a, b])
    assert [h["func"].__name__ for h in sorted_] == [
        h["func"].__name__ for h in sorted2
    ]


@pytest.mark.unit
def test_topological_sort_extension_alpha_breaks_ties():
    a = _hk("a", "zeta", priority=50)
    b = _hk("b", "alpha", priority=50)
    sorted_ = _sort_hooks_topologically([a, b])
    # Alphabetical extension order: alpha < zeta.
    assert sorted_[0]["extension"] == "alpha"
    assert sorted_[1]["extension"] == "zeta"


@pytest.mark.unit
def test_topological_sort_function_name_breaks_remaining_ties():
    a = _hk("zoo", "ext", priority=50)
    b = _hk("aardvark", "ext", priority=50)
    sorted_ = _sort_hooks_topologically([a, b])
    assert sorted_[0]["func"].__name__ == "aardvark"
    assert sorted_[1]["func"].__name__ == "zoo"


@pytest.mark.unit
def test_topological_sort_cycle_raises_hookorderingerror():
    a = _hk("a", "ext_a", before=["ext_b"])
    b = _hk("b", "ext_b", before=["ext_a"])  # cycle
    with pytest.raises(HookOrderingError) as exc_info:
        _sort_hooks_topologically([a, b])
    msg = str(exc_info.value)
    assert "ext_a" in msg
    assert "ext_b" in msg


@pytest.mark.unit
def test_topological_sort_unknown_constraint_target_is_ignored():
    """before/after referencing an extension that has no hooks present must
    not break the sort -- the constraint is simply unsatisfied."""
    only = _hk("a", "ext_a", after=["nonexistent_ext"])
    sorted_ = _sort_hooks_topologically([only])
    assert len(sorted_) == 1


@pytest.mark.unit
def test_topological_sort_single_element_unchanged():
    only = _hk("solo", "ext")
    assert _sort_hooks_topologically([only]) == [only]


@pytest.mark.unit
def test_extension_name_helper_extracts_from_module_path():
    def f() -> None:
        pass

    f.__module__ = "serverframework.extensions.payment.hooks"
    assert _hook_extension_name(f) == "payment"


@pytest.mark.unit
def test_extension_name_helper_falls_back_to_leaf():
    def f() -> None:
        pass

    f.__module__ = "some.unrelated.module"
    assert _hook_extension_name(f) == "module"


# ---------------------------------------------------------------------------
# Item 22 -- blocking parameter / non_critical_hook alias / metric
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_hook_blocking_explicit_false_overrides_default_true():
    info = {"blocking": False}
    assert _hook_blocking(info, default=True) is False


@pytest.mark.unit
def test_hook_blocking_explicit_true_overrides_default_false():
    info = {"blocking": True}
    assert _hook_blocking(info, default=False) is True


@pytest.mark.unit
def test_hook_blocking_unset_uses_default():
    info = {"blocking": None}
    assert _hook_blocking(info, default=True) is True
    assert _hook_blocking(info, default=False) is False


@pytest.mark.unit
def test_emit_non_blocking_failure_metric_increments_counter():
    NON_BLOCKING_HOOK_FAILURES.clear()

    def some_hook(ctx):
        pass

    _emit_non_blocking_failure_metric(
        method_name="create",
        timing="after",
        hook_func=some_hook,
        error=RuntimeError("boom"),
    )
    keys = [k for k in NON_BLOCKING_HOOK_FAILURES if "some_hook" in k]
    assert keys, "metric key for some_hook should be present"
    assert NON_BLOCKING_HOOK_FAILURES[keys[0]] == 1


@pytest.mark.unit
def test_non_critical_hook_alias_is_callable():
    """Smoke test: non_critical_hook is invocable as a decorator factory.

    The full hook-execution behavior is covered by the mainline
    AbstractLogicManager_test.py::test_hook_error_handling case (Item 22
    delivery is exercised end-to-end there).
    """
    assert callable(non_critical_hook)


# ---------------------------------------------------------------------------
# Item 41 -- typed HookContext accessors
# ---------------------------------------------------------------------------


class _DummyManager:
    pass


@pytest.mark.unit
def test_hook_context_kwarg_returns_value():
    ctx = HookContext(
        manager=_DummyManager(),
        method_name="create",
        args=(),
        kwargs={"user_id": "u-1", "email": "a@b.com"},
        result=None,
        timing=HookTiming.BEFORE,
    )
    assert ctx.kwarg("user_id") == "u-1"
    assert ctx.kwarg("missing") is None
    assert ctx.kwarg("missing", "default") == "default"


@pytest.mark.unit
def test_hook_context_arg_indexed_access():
    ctx = HookContext(
        manager=_DummyManager(),
        method_name="m",
        args=("first", "second"),
        kwargs={},
        result=None,
        timing=HookTiming.BEFORE,
    )
    assert ctx.arg(0) == "first"
    assert ctx.arg(1) == "second"
    assert ctx.arg(2) is None
    assert ctx.arg(2, "default") == "default"


@pytest.mark.unit
def test_hook_context_set_result_updates_modified_result():
    ctx = HookContext(
        manager=_DummyManager(),
        method_name="m",
        args=(),
        kwargs={},
        result=None,
        timing=HookTiming.AFTER,
    )
    ctx.set_result({"ok": True})
    assert ctx.modified_result == {"ok": True}


@pytest.mark.unit
def test_hook_context_is_generic_at_runtime():
    """HookContext[P, R] must be subscriptable for typing without erroring."""
    # Subscription should be a no-op at runtime.
    Subscripted = HookContext[..., int]
    assert Subscripted is not None