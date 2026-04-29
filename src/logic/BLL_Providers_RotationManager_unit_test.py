"""
Unit tests for RotationManager constructor and rotation-policy dispatch
(Items 70 + 2 hookup). These are pure-utility tests so mocks are
acceptable per AGENTS.md `@pytest.mark.unit`.
"""

from __future__ import annotations

import warnings
from typing import Any, List, Optional
from unittest.mock import MagicMock

import pytest

from extensions.ExternalErrors import (
    AuthExternalError,
    InvalidInputExternalError,
    PermanentExternalError,
    RateLimitExternalError,
    RotationPolicy,
    TransientExternalError,
)

# `lib.Dependencies` is being mutated by a parallel agent and currently
# fails to import in some sandboxed runs (`Optional[Any]` referenced
# without an `Any` import). When that happens we skip the whole module
# so our own contract tests don't get false-failed by an unrelated bug.
try:
    from logic.BLL_Providers import (
        ManagerContractError,
        RotationManager,
        validate_manager_constructors,
    )
except NameError as _exc:  # pragma: no cover - defensive
    pytest.skip(
        f"BLL_Providers import blocked by lib/Dependencies (other batch): {_exc}",
        allow_module_level=True,
    )


# ----- Item 70 tests --------------------------------------------------------


@pytest.mark.unit
def test_constructor_accepts_model_registry_first():
    """RotationManager(model_registry=...) builds without raising."""
    rm = RotationManager(model_registry=None, requester_id=None)
    assert rm.model_registry is None


@pytest.mark.unit
def test_constructor_legacy_positional_emits_deprecation():
    """Legacy positional `requester_id` first emits DeprecationWarning."""
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        rm = RotationManager("user-id-string")
        assert any(
            issubclass(w.category, DeprecationWarning)
            and "model_registry first" in str(w.message)
            for w in captured
        )
    assert rm.requester_id == "user-id-string"
    assert rm.model_registry is None


@pytest.mark.unit
def test_validate_manager_constructors_passes_for_compliant():
    validate_manager_constructors(RotationManager)


@pytest.mark.unit
def test_validate_manager_constructors_rejects_violator():
    class BadManager:
        def __init__(self, requester_id, model_registry=None):
            pass

    with pytest.raises(ManagerContractError, match="model_registry"):
        validate_manager_constructors(BadManager)


@pytest.mark.unit
def test_validate_manager_constructors_rejects_empty_init():
    class Empty:
        def __init__(self):
            pass

    with pytest.raises(ManagerContractError, match="no parameters"):
        validate_manager_constructors(Empty)


# ----- Item 2 hookup tests --------------------------------------------------


def _make_rotation_manager_with_fake_instances(instances: List[Any]) -> RotationManager:
    """Build a RotationManager whose `_get_ordered_rotation_provider_instances`
    returns the supplied iterable, and whose model lookup returns the same
    object as the provider_instance dto. Bypasses DB entirely.
    """
    rm = RotationManager(model_registry=None, requester_id="r1")
    rm.target_id = "rotation-1"
    rm.requester = MagicMock(id="r1")

    # Replace the loader with a stub that yields fake RPIs.
    fake_rpis = [MagicMock(provider_instance_id=f"pi-{i}") for i in range(len(instances))]
    rm._get_ordered_rotation_provider_instances = lambda: fake_rpis  # type: ignore

    # Replace the model_registry-driven lookup with a queue.
    queue = list(instances)

    def fake_lookup(*a, **k):  # closure ignored, drains queue in order
        return queue.pop(0)

    # Patch ProviderInstanceModel.DB(...).get to use the fake lookup.
    import logic.BLL_Providers as bll_mod
    original_db = bll_mod.ProviderInstanceModel.DB

    class _FakeDB:
        def __init__(self, *a, **k):
            pass

        def get(self, *a, **k):
            return queue.pop(0)

    rm.model_registry = MagicMock()
    rm.model_registry.DB.Base = object()
    rm.model_registry.DB.get_session.return_value.__enter__ = lambda self_: None
    rm.model_registry.DB.get_session.return_value.__exit__ = lambda *a: None
    bll_mod.ProviderInstanceModel.DB = lambda base: _FakeDB()
    rm._restore_db = lambda: setattr(bll_mod.ProviderInstanceModel, "DB", original_db)
    return rm


@pytest.mark.unit
def test_invalid_input_error_reraises_immediately():
    instance = MagicMock(name="prov-A")
    instance.name = "prov-A"
    rm = _make_rotation_manager_with_fake_instances([instance])

    def call(_inst):
        raise InvalidInputExternalError("bad args")

    try:
        with pytest.raises(InvalidInputExternalError):
            rm.rotate(call)
    finally:
        rm._restore_db()


@pytest.mark.unit
def test_permanent_error_reraises_immediately():
    instance = MagicMock(name="prov-A")
    instance.name = "prov-A"
    rm = _make_rotation_manager_with_fake_instances([instance])

    def call(_inst):
        raise PermanentExternalError("never")

    try:
        with pytest.raises(PermanentExternalError):
            rm.rotate(call)
    finally:
        rm._restore_db()


@pytest.mark.unit
def test_auth_error_advances_to_next_provider():
    a = MagicMock()
    a.name = "prov-A"
    b = MagicMock()
    b.name = "prov-B"
    rm = _make_rotation_manager_with_fake_instances([a, b])

    seen = []

    def call(inst):
        seen.append(inst.name)
        if inst.name == "prov-A":
            raise AuthExternalError("401")
        return "B-ok"

    try:
        result = rm.rotate(call)
        assert result == "B-ok"
        assert seen == ["prov-A", "prov-B"]
    finally:
        rm._restore_db()


@pytest.mark.unit
def test_transient_error_retries_then_advances():
    a = MagicMock()
    a.name = "prov-A"
    a.provider_class = type(
        "P", (), {"rotation_policy": RotationPolicy(transient_max_retries=2, transient_base_ms=1, transient_max_ms=1, transient_jitter=0.0)},
    )
    b = MagicMock()
    b.name = "prov-B"
    # rotate() does ONE provider_instance lookup per outer for-loop
    # iteration; the inner while-True transient retries reuse that same
    # object. So the queue holds [a, b], not [a, a, a, b].
    rm = _make_rotation_manager_with_fake_instances([a, b])

    calls = {"a": 0, "b": 0}

    def call(inst):
        if inst.name == "prov-A":
            calls["a"] += 1
            raise TransientExternalError("503")
        calls["b"] += 1
        return "B-ok"

    try:
        result = rm.rotate(call)
        assert result == "B-ok"
        # Initial + 2 retries = 3 attempts on A, then 1 on B.
        assert calls["a"] == 3
        assert calls["b"] == 1
    finally:
        rm._restore_db()


@pytest.mark.unit
def test_rate_limit_error_does_not_advance(monkeypatch):
    monkeypatch.setattr("logic.BLL_Providers.time.sleep", lambda *_: None)
    a = MagicMock()
    a.name = "prov-A"
    a.provider_class = type(
        "P", (), {"rotation_policy": RotationPolicy(rate_limit_base_ms=1, rate_limit_max_ms=2)},
    )
    b = MagicMock()
    b.name = "prov-B"
    # rotate() does ONE provider_instance lookup per outer for-loop
    # iteration; rate-limit retries reuse the same object so the queue
    # holds [a, b], not [a, a, b].
    rm = _make_rotation_manager_with_fake_instances([a, b])

    counter = {"n": 0}

    def call(inst):
        counter["n"] += 1
        if inst.name == "prov-A" and counter["n"] == 1:
            raise RateLimitExternalError("429", retry_after_seconds=0)
        if inst.name == "prov-A":
            return "A-ok-after-backoff"
        return "B-ok"

    try:
        result = rm.rotate(call)
        # Should stay on A and recover.
        assert result == "A-ok-after-backoff"
    finally:
        rm._restore_db()


@pytest.mark.unit
def test_bare_exception_advances_for_back_compat():
    a = MagicMock()
    a.name = "prov-A"
    b = MagicMock()
    b.name = "prov-B"
    rm = _make_rotation_manager_with_fake_instances([a, b])

    def call(inst):
        if inst.name == "prov-A":
            raise RuntimeError("legacy")
        return "B-ok"

    try:
        assert rm.rotate(call) == "B-ok"
    finally:
        rm._restore_db()
