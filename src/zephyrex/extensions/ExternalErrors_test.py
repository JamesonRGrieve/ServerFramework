"""End-to-end tests for the typed external error hierarchy and rotation policy."""

from __future__ import annotations

import pytest

from zephyrex.extensions.ExternalErrors import (
    SILENT_DROPPED_STATUS,
    AuthExternalError,
    BaseExternalError,
    DegradationMode,
    InvalidInputExternalError,
    InvalidPaginationError,
    NavigationNotIncludedError,
    NormalizedSentinel,
    PermanentExternalError,
    QueuedForRetry,
    RateLimitExternalError,
    RotationPolicy,
    SilentDropped,
    TransientExternalError,
    UnsupportedOperatorError,
    default_degradation_policy,
    default_rotation_policy,
    extract_degradation_sentinel,
    fail_fast,
    queue_and_retry,
    silent_drop,
)


class TestErrorHierarchy:
    def test_all_subclasses_inherit_from_base(self):
        assert issubclass(TransientExternalError, BaseExternalError)
        assert issubclass(AuthExternalError, BaseExternalError)
        assert issubclass(InvalidInputExternalError, BaseExternalError)
        assert issubclass(RateLimitExternalError, BaseExternalError)
        assert issubclass(PermanentExternalError, BaseExternalError)

    def test_specialized_inputs_inherit_from_invalid_input(self):
        assert issubclass(InvalidPaginationError, InvalidInputExternalError)
        assert issubclass(UnsupportedOperatorError, InvalidInputExternalError)
        assert issubclass(NavigationNotIncludedError, InvalidInputExternalError)

    def test_runbook_url_present_on_each(self):
        assert TransientExternalError.runbook_url is not None
        assert AuthExternalError.runbook_url is not None
        assert InvalidInputExternalError.runbook_url is not None
        assert RateLimitExternalError.runbook_url is not None
        assert PermanentExternalError.runbook_url is not None

    def test_constructor_carries_metadata(self):
        exc = TransientExternalError(
            "upstream 503",
            provider="stripe",
            ability="charge.create",
            upstream_status=503,
            upstream_payload={"err": "x"},
        )
        assert exc.message == "upstream 503"
        assert exc.provider == "stripe"
        assert exc.ability == "charge.create"
        assert exc.upstream_status == 503
        assert exc.upstream_payload == {"err": "x"}
        assert "stripe" in str(exc)

    def test_rate_limit_carries_retry_after(self):
        exc = RateLimitExternalError("slow down", retry_after_seconds=12.5)
        assert exc.retry_after_seconds == 12.5

    def test_can_be_raised_and_caught_polymorphically(self):
        with pytest.raises(BaseExternalError) as ei:
            raise AuthExternalError("bad token")
        assert isinstance(ei.value, AuthExternalError)


class TestRotationPolicy:
    def test_defaults(self):
        p = RotationPolicy()
        assert p.transient_max_retries == 3
        assert p.transient_base_ms == 100
        assert p.transient_max_ms == 5000
        assert p.transient_jitter == 0.1
        assert p.rate_limit_base_ms == 1000
        assert p.rate_limit_max_ms == 60000
        assert p.auth_cooldown_seconds == 300
        assert p.header_parser is None

    def test_factory_returns_default(self):
        p = default_rotation_policy()
        assert isinstance(p, RotationPolicy)
        assert p.transient_max_retries == 3

    def test_overrides(self):
        p = RotationPolicy(transient_max_retries=5, auth_cooldown_seconds=60)
        assert p.transient_max_retries == 5
        assert p.auth_cooldown_seconds == 60

    def test_header_parser_is_callable(self):
        def parser(response):
            return {"retry_after_seconds": 5}

        p = RotationPolicy(header_parser=parser)
        assert p.header_parser({"x": 1}) == {"retry_after_seconds": 5}


# ---- Item 48: DegradationPolicy tests ----------------------------------------


class TestDegradationPolicy:
    def test_default_mode_is_fail_fast(self):
        p = default_degradation_policy()
        assert p.mode == DegradationMode.FAIL_FAST

    def test_fail_fast_constructor(self):
        p = fail_fast()
        assert p.mode == DegradationMode.FAIL_FAST

    def test_queue_and_retry_constructor_defaults(self):
        p = queue_and_retry()
        assert p.mode == DegradationMode.QUEUE_AND_RETRY
        assert p.outbox_retention_days == 7
        assert p.outbox_max_attempts == 5

    def test_queue_and_retry_constructor_overrides(self):
        p = queue_and_retry(outbox_retention_days=30, outbox_max_attempts=10)
        assert p.outbox_retention_days == 30
        assert p.outbox_max_attempts == 10

    def test_silent_drop_constructor(self):
        # The dangerous mode requires the explicit constructor for grep-ability.
        p = silent_drop()
        assert p.mode == DegradationMode.SILENT_DROP

    def test_dataclass_frozen(self):
        # Frozen dataclass — immutable after construction. Catches accidental
        # mutation that would silently change degradation behavior across
        # callers that share the same policy instance.
        p = fail_fast()
        with pytest.raises(Exception):  # FrozenInstanceError on >=3.7
            p.mode = DegradationMode.SILENT_DROP  # type: ignore[misc]

    def test_mode_string_value_round_trip(self):
        # Modes are str-typed enums so they round-trip through OpenAPI
        # / audit logs / GraphQL introspection without a custom serializer.
        assert DegradationMode.FAIL_FAST.value == "fail_fast"
        assert DegradationMode.QUEUE_AND_RETRY.value == "queue_and_retry"
        assert DegradationMode.SILENT_DROP.value == "silent_drop"

    def test_provider_default_classvar_overridable(self):
        # AbstractStaticProvider declares `degradation_policy: ClassVar` so
        # concrete providers can attach a default. Verify the override
        # mechanism by subclassing inline (avoids importing the full
        # provider machinery in this unit test).
        class _Provider:
            degradation_policy = queue_and_retry()

        assert _Provider.degradation_policy.mode == DegradationMode.QUEUE_AND_RETRY


# ---- Item 48: shared degradation-sentinel projection (SSOT) ------------------


class TestExtractDegradationSentinel:
    """`extract_degradation_sentinel` is the single source of truth for the
    sentinel field set. Both the FastAPI and Strawberry emitters derive their
    transport output from the `NormalizedSentinel` it returns."""

    def test_queued_for_retry_normalizes_status_and_tracking_id(self):
        norm = extract_degradation_sentinel(QueuedForRetry(tracking_id="abc"))
        assert isinstance(norm, NormalizedSentinel)
        assert norm.kind == "queued"
        assert norm.status == "accepted"
        assert norm.tracking_id == "abc"
        # Queued projection carries no provider/ability.
        assert norm.provider is None
        assert norm.ability is None

    def test_queued_projection_honors_sentinel_status_attribute(self):
        # Divergence resolution: the projection reads the sentinel's real
        # `status` attribute (not a hard-coded literal), so a non-default
        # status flows through both transports consistently.
        norm = extract_degradation_sentinel(
            QueuedForRetry(tracking_id="t1", status="queued")
        )
        assert norm is not None
        assert norm.kind == "queued"
        assert norm.status == "queued"
        assert norm.tracking_id == "t1"

    def test_silent_dropped_normalizes_provider_and_ability(self):
        norm = extract_degradation_sentinel(
            SilentDropped(provider="email_provider", ability="send_email")
        )
        assert isinstance(norm, NormalizedSentinel)
        assert norm.kind == "silent_dropped"
        assert norm.status == SILENT_DROPPED_STATUS == "silent_dropped"
        assert norm.provider == "email_provider"
        assert norm.ability == "send_email"
        # Silent-drop projection carries no outbox tracking handle.
        assert norm.tracking_id is None

    def test_silent_dropped_defaults(self):
        norm = extract_degradation_sentinel(SilentDropped())
        assert norm is not None
        assert norm.kind == "silent_dropped"
        assert norm.status == SILENT_DROPPED_STATUS
        assert norm.provider is None
        assert norm.ability is None

    @pytest.mark.parametrize("value", [None, 42, "widget", {"id": "w1"}, object()])
    def test_returns_none_for_non_sentinel(self, value):
        assert extract_degradation_sentinel(value) is None
