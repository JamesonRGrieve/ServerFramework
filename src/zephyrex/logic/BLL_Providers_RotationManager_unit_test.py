"""
Unit tests for RotationManager constructor and rotation-policy dispatch
(Items 70 + 2 hookup). These are pure-utility tests so mocks are
acceptable per AGENTS.md `@pytest.mark.unit`.
"""

from __future__ import annotations

import asyncio
import warnings
from unittest.mock import MagicMock

import pytest

from decimal import Decimal

from zephyrex.extensions.billing.BLL_CostModel import ConstantCostModel
from zephyrex.lib.Metrics import (
    InMemoryMetricsBackend,
    reset_metrics_backend,
    set_metrics_backend,
)
from zephyrex.extensions.ExternalErrors import (
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
from zephyrex.logic.Outbox import InMemoryOutboxStore
from zephyrex.logic.RotationTestDoubles import fake_rotation_manager

from zephyrex.logic import BLL_Providers as _bll_mod  # canonical module reference

# `_scoped_import` from `lib/Pydantic_test.py` may replace
# `sys.modules["zephyrex.logic.BLL_Providers"]` with a fresh module at runtime,
# leaving this file's `RotationManager` symbol pointing at the OLD class that
# writes to the OLD `_STICKY_SESSIONS` dict. Asserting through
# `_bll_mod._sticky_get` (rather than re-importing from the live sys.modules)
# keeps reads and writes pointed at the same module-globals dict.
#
# The import is intentionally NOT wrapped in `except NameError: pytest.skip(...)`:
# a genuine import break in BLL_Providers must fail this module loudly rather
# than silently delete every RotationManager contract test from the run.
from zephyrex.logic.BLL_Providers import (
    ManagerContractError,
    RotationManager,
    RoutingHint,
    reset_auth_cooldowns,
    reset_sticky_sessions,
    validate_manager_constructors,
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


@pytest.mark.unit
def test_invalid_input_error_reraises_immediately(monkeypatch):
    instance = MagicMock(name="prov-A")
    instance.name = "prov-A"
    rm = fake_rotation_manager(monkeypatch, [instance])

    def call(_inst):
        raise InvalidInputExternalError("bad args")

    with pytest.raises(InvalidInputExternalError):
        rm.rotate(call)


@pytest.mark.unit
def test_permanent_error_reraises_immediately(monkeypatch):
    instance = MagicMock(name="prov-A")
    instance.name = "prov-A"
    rm = fake_rotation_manager(monkeypatch, [instance])

    def call(_inst):
        raise PermanentExternalError("never")

    with pytest.raises(PermanentExternalError):
        rm.rotate(call)


@pytest.mark.unit
def test_auth_error_advances_to_next_provider(monkeypatch):
    a = MagicMock()
    a.name = "prov-A"
    b = MagicMock()
    b.name = "prov-B"
    rm = fake_rotation_manager(monkeypatch, [a, b])

    seen = []

    def call(inst):
        seen.append(inst.name)
        if inst.name == "prov-A":
            raise AuthExternalError("401")
        return "B-ok"

    result = rm.rotate(call)
    assert result == "B-ok"
    assert seen == ["prov-A", "prov-B"]


@pytest.mark.unit
def test_transient_error_retries_then_advances(monkeypatch):
    a = MagicMock()
    a.name = "prov-A"
    a.provider_class = type(
        "P",
        (),
        {
            "rotation_policy": RotationPolicy(
                transient_max_retries=2,
                transient_base_ms=1,
                transient_max_ms=1,
                transient_jitter=0.0,
            )
        },
    )
    b = MagicMock()
    b.name = "prov-B"
    # rotate() does ONE provider_instance lookup per outer for-loop
    # iteration; the inner while-True transient retries reuse that same
    # object. So the queue holds [a, b], not [a, a, a, b].
    rm = fake_rotation_manager(monkeypatch, [a, b])

    calls = {"a": 0, "b": 0}

    def call(inst):
        if inst.name == "prov-A":
            calls["a"] += 1
            raise TransientExternalError("503")
        calls["b"] += 1
        return "B-ok"

    result = rm.rotate(call)
    assert result == "B-ok"
    # Initial + 2 retries = 3 attempts on A, then 1 on B.
    assert calls["a"] == 3
    assert calls["b"] == 1


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
        "P",
        (),
        {"rotation_policy": RotationPolicy(rate_limit_base_ms=1, rate_limit_max_ms=2)},
    )
    b = MagicMock()
    b.name = "prov-B"
    # rotate() does ONE provider_instance lookup per outer for-loop
    # iteration; rate-limit retries reuse the same object so the queue
    # holds [a, b], not [a, a, b].
    rm = fake_rotation_manager(monkeypatch, [a, b])

    counter = {"n": 0}

    def call(inst):
        counter["n"] += 1
        if inst.name == "prov-A" and counter["n"] == 1:
            raise RateLimitExternalError("429", retry_after_seconds=0)
        if inst.name == "prov-A":
            return "A-ok-after-backoff"
        return "B-ok"

    result = rm.rotate(call)
    # Should stay on A and recover.
    assert result == "A-ok-after-backoff"


@pytest.mark.unit
def test_bare_exception_advances_for_back_compat(monkeypatch):
    a = MagicMock()
    a.name = "prov-A"
    b = MagicMock()
    b.name = "prov-B"
    rm = fake_rotation_manager(monkeypatch, [a, b])

    def call(inst):
        if inst.name == "prov-A":
            raise RuntimeError("legacy")
        return "B-ok"

    assert rm.rotate(call) == "B-ok"


@pytest.mark.unit
def test_auth_cooldown_skips_provider_on_subsequent_rotate(monkeypatch):
    """After an auth failure on prov-A, a second rotate() within the
    cooldown window should skip prov-A and start at prov-B."""
    a = MagicMock()
    a.name = "prov-A"
    b = MagicMock()
    b.name = "prov-B"
    # First rotate sees a, b. Second rotate sees b only because
    # fake_rotation_manager pops as we use them;
    # we therefore build two separate RMs but share the cooldown state.
    rm1 = fake_rotation_manager(monkeypatch, [a, b])
    seen_first: list = []

    def call_first(inst):
        seen_first.append(inst.name)
        if inst.name == "prov-A":
            raise AuthExternalError("401")
        return "B-ok"

    assert rm1.rotate(call_first) == "B-ok"
    assert seen_first == ["prov-A", "prov-B"]

    # Same RPI ids -> same cooldown lookup. New RM but same RPIs.
    rm2 = fake_rotation_manager(monkeypatch, [a, b])
    seen_second: list = []

    def call_second(inst):
        seen_second.append(inst.name)
        return "B-ok"

    # The cooldown helper builds RPIs with provider_instance_id=pi-N
    # by index. Both RMs use the same indexes, so prov-A's pi-0 is in
    # cooldown. Second rotate should skip pi-0 entirely.
    assert rm2.rotate(call_second) == "B-ok"
    assert seen_second == ["prov-B"]


# ----- Item 51 sticky-session tests ----------------------------------------


@pytest.mark.unit
def test_sticky_pin_set_on_first_success(monkeypatch):
    """First rotate() with a stickiness key — no prior pin — falls through
    to linear rotation and pins the winning provider."""
    a = MagicMock()
    a.name = "prov-A"
    rm = fake_rotation_manager(monkeypatch, [a])
    result = rm.rotate(
        lambda inst: "ok", routing_hint=RoutingHint(stickiness_key="conv-1")
    )
    assert result == "ok"
    assert _bll_mod._sticky_get("conv-1", None) == "pi-0"


@pytest.mark.unit
def test_sticky_pin_reused_on_subsequent_rotate(monkeypatch):
    """Second rotate() with the same stickiness key honors the pin and
    skips the linear chain — call goes straight to pi-0 even when the
    chain would otherwise enumerate other RPIs."""
    a = MagicMock()
    a.name = "prov-A"
    b = MagicMock()
    b.name = "prov-B"
    # Pin pi-0 (prov-A) up front so the second rotate exercises the
    # pinned-attempt path.
    rm1 = fake_rotation_manager(monkeypatch, [a, b])
    rm1.rotate(lambda inst: "ok-1", routing_hint=RoutingHint(stickiness_key="conv-2"))

    rm2 = fake_rotation_manager(monkeypatch, [a, b])
    seen: list = []

    def call(inst):
        seen.append(inst.name)
        return "ok-2"

    result = rm2.rotate(call, routing_hint=RoutingHint(stickiness_key="conv-2"))
    assert result == "ok-2"
    # Pin hit — only prov-A should be visited.
    assert seen == ["prov-A"]


@pytest.mark.unit
def test_sticky_pin_invalidated_on_pinned_failure_then_falls_through(monkeypatch):
    """When the pinned provider fails (transient/auth/bare), the pin is
    invalidated and the linear chain is recomputed against the surviving
    chain (the failed pin is removed from the head)."""
    a = MagicMock()
    a.name = "prov-A"
    b = MagicMock()
    b.name = "prov-B"
    # Pin pi-0 first.
    rm1 = fake_rotation_manager(monkeypatch, [a, b])
    rm1.rotate(lambda inst: "ok", routing_hint=RoutingHint(stickiness_key="conv-3"))

    rm2 = fake_rotation_manager(monkeypatch, [a, b])
    visited: list = []

    def call(inst):
        visited.append(inst.name)
        if inst.name == "prov-A":
            raise AuthExternalError("401 on pinned")
        return "B-ok"

    result = rm2.rotate(call, routing_hint=RoutingHint(stickiness_key="conv-3"))
    assert result == "B-ok"
    # Pinned A failed → fall-through to surviving chain (B only).
    assert visited == ["prov-A", "prov-B"]
    # Pin invalidated; new pin should be B.
    assert _bll_mod._sticky_get("conv-3", None) == "pi-1"


@pytest.mark.unit
def test_sticky_invalid_input_propagates_through_pin(monkeypatch):
    """`InvalidInputExternalError` from a pinned attempt re-raises rather
    than silently falling through — sticky pinning never masks a 4xx.
    Default rotation has the same semantics; the pin path must match."""
    a = MagicMock()
    a.name = "prov-A"
    b = MagicMock()
    b.name = "prov-B"
    rm1 = fake_rotation_manager(monkeypatch, [a, b])
    rm1.rotate(lambda inst: "ok", routing_hint=RoutingHint(stickiness_key="conv-4"))

    rm2 = fake_rotation_manager(monkeypatch, [a, b])

    def call(inst):
        raise InvalidInputExternalError("bad payload")

    with pytest.raises(InvalidInputExternalError):
        rm2.rotate(call, routing_hint=RoutingHint(stickiness_key="conv-4"))


@pytest.mark.unit
def test_sticky_no_routing_hint_no_pin(monkeypatch):
    """Default behavior is unchanged when no routing hint is supplied —
    no pin is created on success, no pin is consulted on subsequent calls."""
    a = MagicMock()
    a.name = "prov-A"
    rm = fake_rotation_manager(monkeypatch, [a])
    rm.rotate(lambda inst: "ok")
    assert len(_bll_mod._STICKY_SESSIONS) == 0


# ----- Item 48 degradation tests ------------------------------------------


@pytest.fixture(autouse=True)
def _reset_observability_counters():
    RotationManager.reset_observability_counters()
    RotationManager.set_outbox_store(None)
    yield
    RotationManager.reset_observability_counters()
    RotationManager.set_outbox_store(None)


@pytest.mark.unit
def test_exhausted_chain_fail_fast_raises_http_500(monkeypatch):
    a = MagicMock()
    a.name = "prov-A"
    a.provider_class = type("P", (), {"degradation_policy": fail_fast()})
    rm = fake_rotation_manager(monkeypatch, [a])

    def call(_inst):
        raise TransientExternalError("503")

    a.provider_class.rotation_policy = RotationPolicy(
        transient_max_retries=0,
        transient_base_ms=1,
        transient_max_ms=1,
        transient_jitter=0.0,
    )
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as ei:
        rm.rotate(call)
    assert ei.value.status_code == 500


@pytest.mark.unit
def test_exhausted_chain_queue_and_retry_returns_sentinel_and_writes_outbox(
    monkeypatch,
):
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
    rm = fake_rotation_manager(monkeypatch, [a])

    def call(_inst):
        raise TransientExternalError("503")

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


@pytest.mark.unit
def test_exhausted_chain_silent_drop_returns_sentinel_and_increments_counter(
    monkeypatch,
):
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
    rm = fake_rotation_manager(monkeypatch, [a])

    def call(_inst):
        raise TransientExternalError("503")

    result = rm.rotate(call, ability="beacon.fire")
    assert isinstance(result, SilentDropped)
    assert result.provider == "prov-A"
    assert result.ability == "beacon.fire"
    counters = RotationManager.get_silent_drop_counter()
    assert counters[("prov-A", "beacon.fire")] == 1


# ----- Item 84 cost-observability test -----------------------------------


@pytest.mark.unit
def test_successful_rotation_with_cost_model_increments_counter(monkeypatch):
    a = MagicMock()
    a.name = "prov-A"
    a.provider_class = type(
        "P", (), {"cost_model": ConstantCostModel(per_call_usd=Decimal("0.25"))}
    )
    rm = fake_rotation_manager(monkeypatch, [a])
    rm.requester = MagicMock(id="user-1", team_id="team-7")

    result = rm.rotate(lambda inst: "ok", ability="charge.create")
    assert result == "ok"
    counters = RotationManager.get_cost_counter()
    assert counters[("team-7", "prov-A", "charge.create")] == Decimal("0.25")


@pytest.mark.unit
def test_cost_model_failure_does_not_break_rotation(monkeypatch):
    a = MagicMock()
    a.name = "prov-A"

    class _Boom:
        def __call__(self, request, response):
            raise RuntimeError("model busted")

    a.provider_class = type("P", (), {"cost_model": _Boom()})
    rm = fake_rotation_manager(monkeypatch, [a])

    # Cost model raising must NOT fail the rotation call.
    assert rm.rotate(lambda inst: "ok", ability="x") == "ok"


# ----- Item 34 — distributed-tracing + provider-call metrics -------------


@pytest.mark.unit
def test_rotation_emits_attempt_spans_and_metrics_for_multi_provider_chain(
    monkeypatch,
):
    """Item 34: a 3-provider rotation where the first 2 fail with
    `TransientExternalError` (zero retries configured) and the 3rd
    succeeds should record:

      - 1 parent rotation span enclosing 3 attempt-children,
      - `rotation.attempt.failure` == 2,
      - `rotation.attempt.success` == 1,
      - `rotation.success_total` == 1.

    Uses the real `InMemoryMetricsBackend` — no mocks per the no-mock
    pillar in AGENTS.md.
    """
    backend = InMemoryMetricsBackend()
    set_metrics_backend(backend)
    try:
        zero_retry_policy = RotationPolicy(
            transient_max_retries=0,
            transient_base_ms=1,
            transient_max_ms=1,
            transient_jitter=0.0,
        )
        a = MagicMock()
        a.name = "prov-A"
        a.provider_class = type("PA", (), {"rotation_policy": zero_retry_policy})
        b = MagicMock()
        b.name = "prov-B"
        b.provider_class = type("PB", (), {"rotation_policy": zero_retry_policy})
        c = MagicMock()
        c.name = "prov-C"
        c.provider_class = type("PC", (), {"rotation_policy": zero_retry_policy})

        rm = fake_rotation_manager(monkeypatch, [a, b, c])
        seen: list = []

        def call(inst):
            seen.append(inst.name)
            if inst.name in ("prov-A", "prov-B"):
                raise TransientExternalError("503")
            return "C-ok"

        result = rm.rotate(call, ability="charge.create")
        assert result == "C-ok"
        assert seen == ["prov-A", "prov-B", "prov-C"]

        # ----- span tree -------------------------------------------------
        rotation_spans = [s for s in backend.spans if s["name"] == "rotation.rotate"]
        attempt_spans = [s for s in backend.spans if s["name"] == "rotation.attempt"]
        assert len(rotation_spans) == 1
        assert len(attempt_spans) == 3
        # Every attempt should point at the rotation span as parent.
        parent_id = id(rotation_spans[0])
        for s in attempt_spans:
            assert s["parent"] == parent_id
        # attempt_index increments 0/1/2; provider names tagged.
        assert [s["tags"]["attempt_index"] for s in attempt_spans] == [0, 1, 2]
        assert [s["tags"]["provider"] for s in attempt_spans] == [
            "prov-A",
            "prov-B",
            "prov-C",
        ]

        # ----- counters --------------------------------------------------
        # Failure counter: 1 per failed provider, labelled by provider+error_class.
        a_fail_key = (
            "rotation.attempt.failure",
            frozenset(
                {
                    ("provider", "prov-A"),
                    ("ability", "charge.create"),
                    ("error_class", "TransientExternalError"),
                }
            ),
        )
        b_fail_key = (
            "rotation.attempt.failure",
            frozenset(
                {
                    ("provider", "prov-B"),
                    ("ability", "charge.create"),
                    ("error_class", "TransientExternalError"),
                }
            ),
        )
        assert backend.counters[a_fail_key] == 1.0
        assert backend.counters[b_fail_key] == 1.0
        total_failures = sum(
            v
            for (n, _), v in backend.counters.items()
            if n == "rotation.attempt.failure"
        )
        assert total_failures == 2.0

        # Success counter: 1 for prov-C.
        c_success_key = (
            "rotation.attempt.success",
            frozenset({("provider", "prov-C"), ("ability", "charge.create")}),
        )
        assert backend.counters[c_success_key] == 1.0

        # rotation.success_total: 1 for the whole rotation.
        success_total_key = (
            "rotation.success_total",
            frozenset({("ability", "charge.create")}),
        )
        assert backend.counters[success_total_key] == 1.0

        # No exhaustion counter — chain succeeded on the 3rd provider.
        assert not any(n == "rotation.exhausted_total" for (n, _) in backend.counters)
    finally:
        reset_metrics_backend()


@pytest.mark.unit
def test_rotation_emits_exhausted_counter_when_chain_exhausted(monkeypatch):
    """Item 34: when every provider in the chain fails and the
    degradation policy raises HTTPException, `rotation.exhausted_total`
    increments exactly once."""
    backend = InMemoryMetricsBackend()
    set_metrics_backend(backend)
    try:
        zero_retry_policy = RotationPolicy(
            transient_max_retries=0,
            transient_base_ms=1,
            transient_max_ms=1,
            transient_jitter=0.0,
        )
        a = MagicMock()
        a.name = "prov-A"
        a.provider_class = type(
            "PA",
            (),
            {
                "rotation_policy": zero_retry_policy,
                "degradation_policy": fail_fast(),
            },
        )
        rm = fake_rotation_manager(monkeypatch, [a])

        def call(_inst):
            raise TransientExternalError("503")

        from fastapi import HTTPException

        with pytest.raises(HTTPException):
            rm.rotate(call, ability="x")

        exhausted_key = (
            "rotation.exhausted_total",
            frozenset({("ability", "x")}),
        )
        assert backend.counters[exhausted_key] == 1.0
    finally:
        reset_metrics_backend()


@pytest.mark.unit
def test_rotation_telemetry_failure_does_not_break_rotation(monkeypatch):
    """Item 34: a misbehaving metrics backend must NEVER fail the
    rotation. Install a backend whose every method raises and assert
    rotation still returns the callable's success value."""
    from zephyrex.lib.Metrics import MetricsBackend

    class BoomBackend(MetricsBackend):
        def counter(self, *a, **k):
            raise RuntimeError("boom")

        def gauge(self, *a, **k):
            raise RuntimeError("boom")

        def histogram(self, *a, **k):
            raise RuntimeError("boom")

        def span(self, *a, **k):
            raise RuntimeError("boom")

    set_metrics_backend(BoomBackend())
    try:
        a = MagicMock()
        a.name = "prov-A"
        rm = fake_rotation_manager(monkeypatch, [a])
        assert rm.rotate(lambda inst: "ok", ability="x") == "ok"
    finally:
        reset_metrics_backend()


# ---------------------------------------------------------------------------
# arotate: async callables get the same policy
#
# Every async ability (database, email) used to ``await rm.rotate(...)``:
# the sync rotation returned the un-run coroutine as the "successful" result,
# so a provider failing inside it escaped retry and failover entirely.
# ---------------------------------------------------------------------------


def _two_providers():
    a = MagicMock()
    a.name = "prov-A"
    a.provider_class = type(
        "P",
        (),
        {
            "rotation_policy": RotationPolicy(
                transient_max_retries=2,
                transient_base_ms=1,
                transient_max_ms=1,
                transient_jitter=0.0,
            )
        },
    )
    b = MagicMock()
    b.name = "prov-B"
    return a, b


@pytest.mark.unit
def test_arotate_advances_when_an_awaited_attempt_fails(monkeypatch):
    a, b = _two_providers()
    rm = fake_rotation_manager(monkeypatch, [a, b])
    seen = []

    async def call(inst):
        seen.append(inst.name)
        if inst.name == "prov-A":
            raise AuthExternalError("401")
        return "B-ok"

    assert asyncio.run(rm.arotate(call)) == "B-ok"
    assert seen == ["prov-A", "prov-B"]


@pytest.mark.unit
def test_arotate_retries_transient_failures_without_blocking_the_loop(monkeypatch):
    a, b = _two_providers()
    rm = fake_rotation_manager(monkeypatch, [a, b])
    waits = []

    async def recording_sleep(seconds):
        waits.append(seconds)

    def blocking_sleep(_seconds):
        raise AssertionError("arotate blocked the event loop with time.sleep")

    monkeypatch.setattr(_bll_mod.asyncio, "sleep", recording_sleep)
    monkeypatch.setattr(_bll_mod.time, "sleep", blocking_sleep)
    calls = {"a": 0, "b": 0}

    async def call(inst):
        if inst.name == "prov-A":
            calls["a"] += 1
            raise TransientExternalError("503")
        calls["b"] += 1
        return "B-ok"

    assert asyncio.run(rm.arotate(call)) == "B-ok"
    assert calls == {"a": 3, "b": 1}
    assert len(waits) == 2


@pytest.mark.unit
def test_arotate_reraises_invalid_input_raised_by_the_coroutine(monkeypatch):
    a, _ = _two_providers()
    rm = fake_rotation_manager(monkeypatch, [a])

    async def call(_inst):
        raise InvalidInputExternalError("bad args")

    with pytest.raises(InvalidInputExternalError):
        asyncio.run(rm.arotate(call))


@pytest.mark.unit
def test_arotate_accepts_a_sync_callable(monkeypatch):
    a, _ = _two_providers()
    rm = fake_rotation_manager(monkeypatch, [a])
    assert asyncio.run(rm.arotate(lambda inst: inst.name)) == "prov-A"
