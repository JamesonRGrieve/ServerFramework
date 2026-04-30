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

from decimal import Decimal

from extensions.CostModel import ConstantCostModel
from extensions.ExternalErrors import (
    AuthExternalError,
    InvalidInputExternalError,
    PermanentExternalError,
    QueuedForRetry,
    RateLimitExternalError,
    RotationPolicy,
    SilentDropped,
    TransientExternalError,
    fail_fast,
    queue_and_retry,
    silent_drop,
)
from logic.Outbox import InMemoryOutboxStore

# `lib.Dependencies` is being mutated by a parallel agent and currently
# fails to import in some sandboxed runs (`Optional[Any]` referenced
# without an `Any` import). When that happens we skip the whole module
# so our own contract tests don't get false-failed by an unrelated bug.
try:
    import logic.BLL_Providers as _bll_mod  # canonical reference for the
    # OLD module — `_scoped_import` from `lib/Pydantic_test.py` may
    # replace `sys.modules["logic.BLL_Providers"]` with a fresh module
    # at runtime, leaving this test file's `RotationManager` symbol
    # pointing at the OLD class that writes to the OLD
    # `_STICKY_SESSIONS` dict. Asserting through `_bll_mod._sticky_get`
    # (rather than re-importing from the live sys.modules) keeps reads
    # and writes pointed at the same module-globals dict.
    from logic.BLL_Providers import (
        ManagerContractError,
        RotationManager,
        RoutingHint,
        reset_auth_cooldowns,
        reset_sticky_sessions,
        validate_manager_constructors,
    )
except NameError as _exc:  # pragma: no cover - defensive
    pytest.skip(
        f"BLL_Providers import blocked by lib/Dependencies (other batch): {_exc}",
        allow_module_level=True,
    )


@pytest.fixture(autouse=True)
def _reset_auth_cooldowns():
    """Auth-cooldown state is module-level; reset between tests so that
    a provider marked unhealthy by one test does not skew the next."""
    reset_auth_cooldowns()
    reset_sticky_sessions()
    yield
    reset_auth_cooldowns()
    reset_sticky_sessions()


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

    The fake DB lookup honors the `id` kwarg so that skipping a provider
    (e.g. via auth cooldown) does not desynchronize the lookup queue from
    the RPI iteration order.
    """
    rm = RotationManager(model_registry=None, requester_id="r1")
    rm.target_id = "rotation-1"
    rm.requester = MagicMock(id="r1")

    # Replace the loader with a stub that yields fake RPIs.
    fake_rpis = [MagicMock(provider_instance_id=f"pi-{i}") for i in range(len(instances))]
    rm._get_ordered_rotation_provider_instances = lambda: fake_rpis  # type: ignore

    # Map pi-id -> instance so the fake lookup returns the right instance
    # regardless of which RPIs the rotation actually visits.
    pi_map: dict = {f"pi-{i}": inst for i, inst in enumerate(instances)}

    # Patch ProviderInstanceModel.DB(...).get to use the fake lookup.
    # Use the canonical _bll_mod reference (bound at test-file import
    # time) so the patched class is the same one the OLD wrapped rotate
    # method resolves through its function globals — even after another
    # test's `_scoped_import` has swapped sys.modules to a new module
    # object. Without this, the patch would land on the NEW class while
    # rotate keeps reading from the OLD class and the DB would not be
    # stubbed.
    bll_mod = _bll_mod
    original_db = bll_mod.ProviderInstanceModel.DB

    class _FakeDB:
        def __init__(self, *a, **k):
            pass

        def get(self, *a, **k):
            pi_id = k.get("id")
            return pi_map.get(str(pi_id))

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
    # Patch via the canonical _bll_mod reference rather than a string
    # import path. Other tests' `_scoped_import` calls (e.g. the seeder
    # tests' from_scoped_import on logic) replace the `logic.BLL_Providers`
    # entry in sys.modules with a new module object, so pytest's
    # monkeypatch string-resolver fails to walk
    # `logic.BLL_Providers.time.sleep` mid-suite. Patching the attribute
    # directly via the module reference we captured at import time
    # bypasses the sys.modules lookup entirely.
    monkeypatch.setattr(_bll_mod.time, "sleep", lambda *_: None)
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


@pytest.mark.unit
def test_auth_cooldown_skips_provider_on_subsequent_rotate():
    """After an auth failure on prov-A, a second rotate() within the
    cooldown window should skip prov-A and start at prov-B."""
    a = MagicMock()
    a.name = "prov-A"
    b = MagicMock()
    b.name = "prov-B"
    # First rotate sees a, b. Second rotate sees b only because
    # _make_rotation_manager_with_fake_instances pops as we use them;
    # we therefore build two separate RMs but share the cooldown state.
    rm1 = _make_rotation_manager_with_fake_instances([a, b])
    seen_first: list = []

    def call_first(inst):
        seen_first.append(inst.name)
        if inst.name == "prov-A":
            raise AuthExternalError("401")
        return "B-ok"

    try:
        assert rm1.rotate(call_first) == "B-ok"
        assert seen_first == ["prov-A", "prov-B"]
    finally:
        rm1._restore_db()

    # Same RPI ids -> same cooldown lookup. New RM but same RPIs.
    rm2 = _make_rotation_manager_with_fake_instances([a, b])
    seen_second: list = []

    def call_second(inst):
        seen_second.append(inst.name)
        return "B-ok"

    try:
        # The cooldown helper builds RPIs with provider_instance_id=pi-N
        # by index. Both RMs use the same indexes, so prov-A's pi-0 is in
        # cooldown. Second rotate should skip pi-0 entirely.
        assert rm2.rotate(call_second) == "B-ok"
        assert seen_second == ["prov-B"]
    finally:
        rm2._restore_db()


# ----- Item 51 sticky-session tests ----------------------------------------


@pytest.mark.unit
def test_sticky_pin_set_on_first_success():
    """First rotate() with a stickiness key — no prior pin — falls through
    to linear rotation and pins the winning provider."""
    a = MagicMock()
    a.name = "prov-A"
    rm = _make_rotation_manager_with_fake_instances([a])
    try:
        result = rm.rotate(lambda inst: "ok", routing_hint=RoutingHint(stickiness_key="conv-1"))
        assert result == "ok"
        assert _bll_mod._sticky_get("conv-1", None) == "pi-0"
    finally:
        rm._restore_db()


@pytest.mark.unit
def test_sticky_pin_reused_on_subsequent_rotate():
    """Second rotate() with the same stickiness key honors the pin and
    skips the linear chain — call goes straight to pi-0 even when the
    chain would otherwise enumerate other RPIs."""
    a = MagicMock()
    a.name = "prov-A"
    b = MagicMock()
    b.name = "prov-B"
    # Pin pi-0 (prov-A) up front so the second rotate exercises the
    # pinned-attempt path.
    rm1 = _make_rotation_manager_with_fake_instances([a, b])
    try:
        rm1.rotate(lambda inst: "ok-1", routing_hint=RoutingHint(stickiness_key="conv-2"))
    finally:
        rm1._restore_db()

    rm2 = _make_rotation_manager_with_fake_instances([a, b])
    seen: list = []

    def call(inst):
        seen.append(inst.name)
        return "ok-2"

    try:
        result = rm2.rotate(call, routing_hint=RoutingHint(stickiness_key="conv-2"))
        assert result == "ok-2"
        # Pin hit — only prov-A should be visited.
        assert seen == ["prov-A"]
    finally:
        rm2._restore_db()


@pytest.mark.unit
def test_sticky_pin_invalidated_on_pinned_failure_then_falls_through():
    """When the pinned provider fails (transient/auth/bare), the pin is
    invalidated and the linear chain is recomputed against the surviving
    chain (the failed pin is removed from the head)."""
    a = MagicMock()
    a.name = "prov-A"
    b = MagicMock()
    b.name = "prov-B"
    # Pin pi-0 first.
    rm1 = _make_rotation_manager_with_fake_instances([a, b])
    try:
        rm1.rotate(lambda inst: "ok", routing_hint=RoutingHint(stickiness_key="conv-3"))
    finally:
        rm1._restore_db()

    rm2 = _make_rotation_manager_with_fake_instances([a, b])
    visited: list = []

    def call(inst):
        visited.append(inst.name)
        if inst.name == "prov-A":
            raise AuthExternalError("401 on pinned")
        return "B-ok"

    try:
        result = rm2.rotate(call, routing_hint=RoutingHint(stickiness_key="conv-3"))
        assert result == "B-ok"
        # Pinned A failed → fall-through to surviving chain (B only).
        assert visited == ["prov-A", "prov-B"]
        # Pin invalidated; new pin should be B.
        assert _bll_mod._sticky_get("conv-3", None) == "pi-1"
    finally:
        rm2._restore_db()


@pytest.mark.unit
def test_sticky_invalid_input_propagates_through_pin():
    """`InvalidInputExternalError` from a pinned attempt re-raises rather
    than silently falling through — sticky pinning never masks a 4xx.
    Default rotation has the same semantics; the pin path must match."""
    a = MagicMock()
    a.name = "prov-A"
    b = MagicMock()
    b.name = "prov-B"
    rm1 = _make_rotation_manager_with_fake_instances([a, b])
    try:
        rm1.rotate(lambda inst: "ok", routing_hint=RoutingHint(stickiness_key="conv-4"))
    finally:
        rm1._restore_db()

    rm2 = _make_rotation_manager_with_fake_instances([a, b])

    def call(inst):
        raise InvalidInputExternalError("bad payload")

    try:
        with pytest.raises(InvalidInputExternalError):
            rm2.rotate(call, routing_hint=RoutingHint(stickiness_key="conv-4"))
    finally:
        rm2._restore_db()


@pytest.mark.unit
def test_sticky_no_routing_hint_no_pin():
    """Default behavior is unchanged when no routing hint is supplied —
    no pin is created on success, no pin is consulted on subsequent calls."""
    a = MagicMock()
    a.name = "prov-A"
    rm = _make_rotation_manager_with_fake_instances([a])
    try:
        rm.rotate(lambda inst: "ok")
        assert len(_bll_mod._STICKY_SESSIONS) == 0
    finally:
        rm._restore_db()


# ----- Item 48 degradation tests ------------------------------------------


@pytest.fixture(autouse=True)
def _reset_observability_counters():
    RotationManager.reset_observability_counters()
    RotationManager.set_outbox_store(None)
    yield
    RotationManager.reset_observability_counters()
    RotationManager.set_outbox_store(None)


@pytest.mark.unit
def test_exhausted_chain_fail_fast_raises_http_500():
    a = MagicMock()
    a.name = "prov-A"
    a.provider_class = type("P", (), {"degradation_policy": fail_fast()})
    rm = _make_rotation_manager_with_fake_instances([a])

    def call(_inst):
        raise TransientExternalError("503")

    a.provider_class.rotation_policy = RotationPolicy(
        transient_max_retries=0, transient_base_ms=1, transient_max_ms=1, transient_jitter=0.0
    )
    try:
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as ei:
            rm.rotate(call)
        assert ei.value.status_code == 500
    finally:
        rm._restore_db()


@pytest.mark.unit
def test_exhausted_chain_queue_and_retry_returns_sentinel_and_writes_outbox():
    a = MagicMock()
    a.name = "prov-A"
    a.provider_class = type(
        "P",
        (),
        {
            "degradation_policy": queue_and_retry(),
            "rotation_policy": RotationPolicy(
                transient_max_retries=0,
                transient_base_ms=1,
                transient_max_ms=1,
                transient_jitter=0.0,
            ),
        },
    )
    store = InMemoryOutboxStore()
    RotationManager.set_outbox_store(store)
    rm = _make_rotation_manager_with_fake_instances([a])

    def call(_inst):
        raise TransientExternalError("503")

    try:
        result = rm.rotate(call, ability="charge.create")
        assert isinstance(result, QueuedForRetry)
        assert result.tracking_id
        assert result.status == "accepted"
        # Outbox should have one entry.
        entry = store.find_by_idempotency_key_starts_with = None  # noqa: F841
        # We didn't expose a "list" helper but enqueue returns the id; verify
        # by claiming pending.
        claimed = store.claim_pending(limit=10)
        assert len(claimed) == 1
        assert claimed[0].target_provider == "prov-A"
        assert claimed[0].target_ability == "charge.create"
    finally:
        rm._restore_db()


@pytest.mark.unit
def test_exhausted_chain_silent_drop_returns_sentinel_and_increments_counter():
    a = MagicMock()
    a.name = "prov-A"
    a.provider_class = type(
        "P",
        (),
        {
            "degradation_policy": silent_drop(),
            "rotation_policy": RotationPolicy(
                transient_max_retries=0,
                transient_base_ms=1,
                transient_max_ms=1,
                transient_jitter=0.0,
            ),
        },
    )
    rm = _make_rotation_manager_with_fake_instances([a])

    def call(_inst):
        raise TransientExternalError("503")

    try:
        result = rm.rotate(call, ability="beacon.fire")
        assert isinstance(result, SilentDropped)
        assert result.provider == "prov-A"
        assert result.ability == "beacon.fire"
        counters = RotationManager.get_silent_drop_counter()
        assert counters[("prov-A", "beacon.fire")] == 1
    finally:
        rm._restore_db()


# ----- Item 84 cost-observability test -----------------------------------


@pytest.mark.unit
def test_successful_rotation_with_cost_model_increments_counter():
    a = MagicMock()
    a.name = "prov-A"
    a.provider_class = type(
        "P", (), {"cost_model": ConstantCostModel(per_call_usd=Decimal("0.25"))}
    )
    rm = _make_rotation_manager_with_fake_instances([a])
    rm.requester = MagicMock(id="user-1", team_id="team-7")

    try:
        result = rm.rotate(lambda inst: "ok", ability="charge.create")
        assert result == "ok"
        counters = RotationManager.get_cost_counter()
        assert counters[("team-7", "prov-A", "charge.create")] == Decimal("0.25")
    finally:
        rm._restore_db()


@pytest.mark.unit
def test_cost_model_failure_does_not_break_rotation():
    a = MagicMock()
    a.name = "prov-A"

    class _Boom:
        def __call__(self, request, response):
            raise RuntimeError("model busted")

    a.provider_class = type("P", (), {"cost_model": _Boom()})
    rm = _make_rotation_manager_with_fake_instances([a])

    try:
        # Cost model raising must NOT fail the rotation call.
        assert rm.rotate(lambda inst: "ok", ability="x") == "ok"
    finally:
        rm._restore_db()
