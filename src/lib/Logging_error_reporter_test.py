"""Item 85 — ErrorReporter ABC tests.

Covers the public surface added for the structured-logging contract:
``NoopErrorReporter``, ``SentryErrorReporter``, ``RollbarErrorReporter``,
the ``set_error_reporter`` / ``get_error_reporter`` helpers, and the
context-enrichment wrapper that auto-attaches ``correlation_id`` /
``request_user`` / ``traceparent`` to every report.

No mocks: the tests write a small real ``ErrorReporter`` subclass
(``RecordingReporter``) that captures calls into a list. This satisfies
AGENTS.md's no-mock pillar — the contract is exercised end-to-end
through real Python objects, not unittest.mock.Mock stand-ins.
"""

from __future__ import annotations

from typing import Any, List, Mapping, Tuple

import pytest

from lib.Logging import (
    ErrorReporter,
    NoopErrorReporter,
    RollbarErrorReporter,
    SentryErrorReporter,
    get_error_reporter,
    set_error_reporter,
)
from lib.RequestContext import (
    clear_request_context,
    set_correlation_id,
    set_request_user,
)


class RecordingReporter(ErrorReporter):
    """Real ErrorReporter subclass — captures calls into a list. No mocks."""

    def __init__(self) -> None:
        self.calls: List[Tuple[BaseException, Mapping[str, Any]]] = []

    def report(self, exception: BaseException, context: Mapping[str, Any]) -> None:
        self.calls.append((exception, dict(context)))


@pytest.fixture(autouse=True)
def _restore_default_reporter():
    """Every test starts with the default NoopErrorReporter and a clean
    request context, and restores both on teardown so test ordering can
    never leak state."""
    clear_request_context()
    set_error_reporter(NoopErrorReporter())
    yield
    set_error_reporter(NoopErrorReporter())
    clear_request_context()


# ----- NoopErrorReporter ----------------------------------------------------


@pytest.mark.unit
def test_noop_reporter_is_a_no_op():
    """The default reporter accepts any exception and any context dict
    without raising and returns ``None``."""
    reporter = NoopErrorReporter()
    result = reporter.report(RuntimeError("boom"), {"path": "/x"})
    assert result is None


@pytest.mark.unit
def test_default_active_reporter_is_noop():
    """Out of the box, ``get_error_reporter().report`` does nothing
    visible — but the call still succeeds."""
    # No exception, no observable side-effect.
    get_error_reporter().report(ValueError("noop"), {"path": "/health"})


# ----- set/get_error_reporter ----------------------------------------------


@pytest.mark.unit
def test_set_error_reporter_routes_calls_to_custom_subclass():
    rec = RecordingReporter()
    set_error_reporter(rec)

    err = RuntimeError("real failure")
    get_error_reporter().report(err, {"path": "/v1/users", "method": "POST"})

    assert len(rec.calls) == 1
    captured_exc, captured_ctx = rec.calls[0]
    assert captured_exc is err
    assert captured_ctx["path"] == "/v1/users"
    assert captured_ctx["method"] == "POST"


@pytest.mark.unit
def test_set_error_reporter_rejects_non_subclass():
    """Type discipline — ``set_error_reporter(object())`` is a programmer
    bug; we surface it at install time, not at the first ``report``."""
    with pytest.raises(TypeError):
        set_error_reporter("not-a-reporter")  # type: ignore[arg-type]


# ----- Context enrichment ---------------------------------------------------


@pytest.mark.unit
def test_get_error_reporter_attaches_correlation_id():
    rec = RecordingReporter()
    set_error_reporter(rec)
    set_correlation_id("cid-7")

    get_error_reporter().report(RuntimeError("x"), {"path": "/x"})

    _, ctx = rec.calls[0]
    assert ctx["correlation_id"] == "cid-7"
    assert ctx["path"] == "/x"


@pytest.mark.unit
def test_get_error_reporter_attaches_request_user():
    rec = RecordingReporter()
    set_error_reporter(rec)
    set_request_user({"user_id": "u-99", "email": "a@b.c"})

    get_error_reporter().report(RuntimeError("x"), {})

    _, ctx = rec.calls[0]
    assert ctx["request_user"] == {"user_id": "u-99", "email": "a@b.c"}


@pytest.mark.unit
def test_get_error_reporter_does_not_overwrite_caller_provided_keys():
    """Caller-supplied context wins over the auto-enrichment fields —
    a service that already knows the canonical correlation id keeps it."""
    rec = RecordingReporter()
    set_error_reporter(rec)
    set_correlation_id("from-context")

    get_error_reporter().report(
        RuntimeError("x"),
        {"correlation_id": "from-caller"},
    )

    _, ctx = rec.calls[0]
    assert ctx["correlation_id"] == "from-caller"


@pytest.mark.unit
def test_get_error_reporter_omits_correlation_id_when_unset():
    rec = RecordingReporter()
    set_error_reporter(rec)
    # No correlation_id set.
    get_error_reporter().report(RuntimeError("x"), {"path": "/y"})

    _, ctx = rec.calls[0]
    assert "correlation_id" not in ctx
    assert ctx["path"] == "/y"


@pytest.mark.unit
def test_get_error_reporter_attaches_traceparent_when_set():
    """When ProviderHTTPClient's ``set_traceparent`` is active, the
    enricher copies the value into the report context."""
    from lib.ProviderHTTPClient import set_traceparent, _traceparent

    rec = RecordingReporter()
    set_error_reporter(rec)
    token = set_traceparent("00-deadbeefdeadbeefdeadbeefdeadbeef-cafebabecafebabe-01")
    try:
        get_error_reporter().report(RuntimeError("x"), {})
    finally:
        _traceparent.reset(token)

    _, ctx = rec.calls[0]
    assert ctx["traceparent"].startswith("00-")


# ----- SentryErrorReporter / RollbarErrorReporter degrade gracefully --------


@pytest.mark.unit
def test_sentry_reporter_is_noop_when_sdk_missing():
    """When ``sentry_sdk`` is not installed, the reporter constructs
    cleanly and ``report`` is a no-op (never crashes the framework)."""
    reporter = SentryErrorReporter()
    # Whether or not sentry_sdk is installed in the test env, the report
    # call must complete without raising.
    reporter.report(RuntimeError("x"), {"path": "/x"})


@pytest.mark.unit
def test_rollbar_reporter_is_noop_when_sdk_missing():
    reporter = RollbarErrorReporter()
    reporter.report(RuntimeError("x"), {"path": "/x"})


@pytest.mark.unit
def test_sentry_and_rollbar_are_error_reporter_subclasses():
    """They satisfy the ABC contract and can be installed via
    ``set_error_reporter`` regardless of SDK availability."""
    set_error_reporter(SentryErrorReporter())
    set_error_reporter(RollbarErrorReporter())
    # No assertion needed — type check is the assertion.


# ----- ErrorReporter ABC enforcement ----------------------------------------


@pytest.mark.unit
def test_error_reporter_abc_cannot_be_instantiated_directly():
    """The ABC must not be instantiable — concrete subclasses must
    implement ``report``."""
    with pytest.raises(TypeError):
        ErrorReporter()  # type: ignore[abstract]


@pytest.mark.unit
def test_error_reporter_subclass_must_implement_report():
    class IncompleteReporter(ErrorReporter):
        pass

    with pytest.raises(TypeError):
        IncompleteReporter()  # type: ignore[abstract]
