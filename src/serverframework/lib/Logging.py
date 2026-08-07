"""Structured application logging contract (Item 85).

This module is the single entry point for application-level logging. It
configures `loguru` with a redaction patcher (so sensitive keys never
escape into stdout, JSON sinks, or downstream aggregators), injects a
per-request correlation id into every record, and exposes a pluggable
:class:`ErrorReporter` ABC so deployments can wire Sentry, Rollbar, or a
custom error pipeline without the framework taking a hard dependency.

Log-level taxonomy
------------------
The framework commits to a fixed taxonomy. Service authors must pick the
level that matches the *operational* meaning of the event, not the
loudness of the message:

* ``DEBUG`` — developer-only diagnostics. Off by default in production.
  Examples: query plans, intermediate values inside a hot loop, "entered
  function X with args Y".
* ``INFO`` — operational milestones. The system is healthy; this line
  records that something normal happened. Examples: "worker started",
  "request handled", "scheduled job ran".
* ``WARNING`` — degraded behavior. The request or job continues, but an
  operator monitoring dashboards should see this. Examples: retry
  succeeded after a 5xx, fallback path engaged, deprecated header used.
* ``ERROR`` — handler-level failure. A request, job, or callback failed;
  the process continues but the user-visible action did not complete.
  An on-call operator should be able to find this in the structured-log
  stream and correlate via :func:`get_correlation_id`.
* ``CRITICAL`` — process-level failure. Reserved for the framework's own
  startup-validation failures and unrecoverable conditions where the
  process should not continue serving traffic.

Correlation id propagation
--------------------------
Every log record receives ``record["extra"]["correlation_id"]``
populated from :func:`lib.RequestContext.get_correlation_id`. Inbound
middleware sets the value at ingress (derived from the W3C
``traceparent`` header when present, otherwise minted as
``uuid4().hex``). Background workers, ``asyncio.to_thread`` callbacks,
and ``asyncio.create_task`` coroutines should be wrapped via
:func:`lib.RequestContext.wrap_in_context` so the id propagates across
boundary handoffs.

Pluggable error reporting
-------------------------
:class:`ErrorReporter` is an ABC with three concrete subclasses shipped
in this module: :class:`NoopErrorReporter` (the default), and
:class:`SentryErrorReporter` / :class:`RollbarErrorReporter` (which
import their respective SDKs lazily — the SDK is an optional dependency
and missing it must never crash the framework). Use
:func:`set_error_reporter` to install a different reporter at startup,
and call :func:`get_error_reporter` from exception handlers to route
uncaught exceptions to the configured backend.
"""
import re
import sys
from abc import ABC, abstractmethod
from typing import Any, Mapping, Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # type: ignore[no-redef]

from loguru import logger

from serverframework.lib.Environment import env

logger.remove()

log_level = env("LOG_LEVEL")
server_timezone = env("TZ")

# Names of `extra` keys (and headers) whose values must never appear in logs.
# Match is case-insensitive and applied recursively through nested dicts/lists.
_SENSITIVE_KEYS = frozenset(
    {
        "password",
        "current_password",
        "new_password",
        "old_password",
        "token",
        "jwt",
        "api_key",
        "apikey",
        "x-api-key",
        "secret",
        "client_secret",
        "refresh_token",
        "access_token",
        "id_token",
        "authorization",
        "auth",
        "session_key",
        "totp_secret",
        "private_key",
    }
)

_REDACTED = "[REDACTED]"

# Regex patterns for inline-secret scrubbing in message strings.
# Format: (compiled pattern, replacement). Each pattern captures the secret
# in group 'v' and rewrites it to [REDACTED].
_INLINE_PATTERNS = [
    re.compile(
        r"(?i)\b(" + "|".join(re.escape(k) for k in _SENSITIVE_KEYS) + r")"
        r"\s*[=:]\s*['\"]?(?P<v>[^\s'\"&,;}]+)['\"]?"
    ),
    re.compile(r"(?i)Bearer\s+(?P<v>[A-Za-z0-9._\-+/=]+)"),
    re.compile(r"(?i)Basic\s+(?P<v>[A-Za-z0-9+/=]+)"),
]


def _scrub_value(value):
    """Recursively redact sensitive entries in dict/list/str structures."""
    if isinstance(value, dict):
        return {
            k: (_REDACTED if k.lower() in _SENSITIVE_KEYS else _scrub_value(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_scrub_value(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_scrub_value(v) for v in value)
    if isinstance(value, str):
        return _scrub_string(value)
    return value


def _scrub_string(text):
    """Apply inline-secret regex scrubbing to a message string."""
    if not text:
        return text
    for pattern in _INLINE_PATTERNS:
        text = pattern.sub(
            lambda m: m.group(0).replace(m.group("v"), _REDACTED), text
        )
    return text


def _redaction_patcher(record):
    """Loguru patcher: scrub sensitive keys/values and inject the
    per-request ``correlation_id`` before any sink sees the record.
    """
    extras = record.get("extra")
    if extras is None:
        extras = {}
        record["extra"] = extras
    for key in list(extras.keys()):
        if key.lower() in _SENSITIVE_KEYS:
            extras[key] = _REDACTED
        else:
            extras[key] = _scrub_value(extras[key])
    # Item 85 — every log line carries the current correlation id when one
    # is set. Imported lazily to avoid a circular import (RequestContext
    # is a leaf module, but importing it eagerly at module-load time
    # could surface ordering issues during bootstrap).
    try:
        from serverframework.lib.RequestContext import get_correlation_id

        cid = get_correlation_id()
        if cid is not None and "correlation_id" not in extras:
            extras["correlation_id"] = cid
    except ImportError:
        pass
    # Scrub the rendered message too — catches f-strings like
    # f"login attempt with password={canary}".
    record["message"] = _scrub_string(record.get("message", ""))


logger = logger.patch(_redaction_patcher)


def format_with_timezone(record):
    """Format log record with server timezone"""
    if server_timezone != "UTC":
        # Convert UTC time to server timezone for display
        utc_time = record["time"].replace(tzinfo=ZoneInfo("UTC"))
        local_time = utc_time.astimezone(ZoneInfo(server_timezone))
        record["time"] = local_time
    return record


LOG_LEVEL_MAP = {
    "CRITICAL": 50,
    "ERROR": 40,
    "WARNING": 30,
    "INFO": 20,
    "DEBUG": 10,
    "VERBOSE": 5,
    "SQL": 3,
    "NOTSET": 0,
}
logger.level("VERBOSE", no=5, color="<blue>")
logger.level("SQL", no=3, color="<magenta>")

logger.add(sys.stdout, level=log_level, filter=format_with_timezone)


# ---------------------------------------------------------------------------
# Item 85 — Pluggable error reporter
# ---------------------------------------------------------------------------


class ErrorReporter(ABC):
    """Pluggable error-reporting sink.

    Concrete subclasses route exceptions to a backend (Sentry, Rollbar,
    in-house pipeline) without the framework taking a hard dependency on
    that backend. The default install is :class:`NoopErrorReporter`;
    deployments override at startup via :func:`set_error_reporter`.

    The framework's exception handlers always call
    ``get_error_reporter().report(exc, {"path": ..., "method": ...})``;
    the helper attaches ``correlation_id``, ``request_user``, and
    ``traceparent`` from the active request context automatically before
    delegating to the subclass.
    """

    @abstractmethod
    def report(self, exception: BaseException, context: Mapping[str, Any]) -> None:
        """Send ``exception`` (with its traceback) to the underlying
        reporting backend. ``context`` is a free-form mapping of extra
        fields the caller wants attached (e.g. request path, user id).
        Implementations must never raise — they should swallow backend
        errors so the framework's exception path is not derailed by a
        misconfigured reporter."""


class NoopErrorReporter(ErrorReporter):
    """Default reporter — silently drops every report. Used in tests
    and in deployments that have not configured an external backend."""

    def report(self, exception: BaseException, context: Mapping[str, Any]) -> None:
        return None


class SentryErrorReporter(ErrorReporter):
    """Routes exceptions to Sentry via ``sentry_sdk``. The SDK is an
    optional dependency: if it is not installed the reporter degrades to
    a no-op (with a single ``debug`` log line on construction) rather
    than crashing the framework at import time.

    M-5 — initialises the SDK with ``send_default_pii=False`` and
    ``include_local_variables=False`` so request-handler local vars
    (``password``, ``authorization``, etc.) do not exit the process. A
    ``before_send`` hook applies the `privacy` extension's PII filter
    when loaded.
    """

    def __init__(self) -> None:
        try:
            import sentry_sdk  # type: ignore[import-not-found]

            self._sentry_sdk = sentry_sdk
            # Best-effort hardening if `sentry_sdk.init` was not called
            # explicitly elsewhere. We don't override an already-initialised
            # client (Hub.current.client check) — the deployment's own init
            # wins.
            try:
                if sentry_sdk.Hub.current.client is None:  # type: ignore[union-attr]
                    sentry_sdk.init(
                        send_default_pii=False,
                        include_local_variables=False,
                        before_send=_sentry_before_send,
                    )
            except Exception:
                # SDK pre-1.0 / non-standard layout — leave caller's init alone.
                pass
        except ImportError:
            self._sentry_sdk = None
            logger.debug(
                "SentryErrorReporter: sentry_sdk not installed; "
                "reporter will no-op until the dependency is available."
            )

    def report(self, exception: BaseException, context: Mapping[str, Any]) -> None:
        if self._sentry_sdk is None:
            return None
        try:
            with self._sentry_sdk.push_scope() as scope:  # type: ignore[union-attr]
                for key, value in context.items():
                    try:
                        scope.set_extra(key, value)
                    except Exception:
                        # Never let scope-tagging derail error reporting.
                        continue
                self._sentry_sdk.capture_exception(exception)  # type: ignore[union-attr]
        except Exception as e:
            logger.warning(
                "SentryErrorReporter.report: capture_exception failed; "
                f"swallowing to avoid masking the original error ({e})"
            )


def _sentry_before_send(event, hint):
    """Sentry `before_send` hook (M-5). Scrubs known PII patterns from
    the event message + breadcrumb data using the privacy extension's
    filter when loaded; falls back to the secrets-only redactor otherwise.
    """
    try:
        from serverframework.lib.Credentials import redact

        msg = event.get("message")
        if isinstance(msg, str):
            event["message"] = redact(msg)
        for crumb in event.get("breadcrumbs", {}).get("values", []) or []:
            m = crumb.get("message")
            if isinstance(m, str):
                crumb["message"] = redact(m)
    except Exception:
        # Never let scrubbing drop the event silently.
        return event
    # Delegate richer PII pattern coverage to the ``privacy`` extension
    # via the ``_pii_hooks["log_filter"]`` registry. Core never imports
    # from the extension; the extension registers a callable on
    # ``on_load`` and we consult it. Without the extension, only the
    # registered-secret redaction above fires.
    from serverframework.logic.BLL_Auth import _pii_hooks

    pii_filter = _pii_hooks["log_filter"]
    if pii_filter is not None:
        try:
            pii_filter(event)
        except Exception:
            # Never let an extension's pattern bug drop the event.
            return event
    return event


class RollbarErrorReporter(ErrorReporter):
    """Routes exceptions to Rollbar via ``rollbar``. The SDK is an
    optional dependency: missing it degrades the reporter to a no-op."""

    def __init__(self) -> None:
        try:
            import rollbar  # type: ignore[import-not-found]

            self._rollbar = rollbar
        except ImportError:
            self._rollbar = None
            logger.debug(
                "RollbarErrorReporter: rollbar not installed; "
                "reporter will no-op until the dependency is available."
            )

    def report(self, exception: BaseException, context: Mapping[str, Any]) -> None:
        if self._rollbar is None:
            return None
        try:
            self._rollbar.report_exc_info(  # type: ignore[union-attr]
                extra_data=dict(context)
            )
        except Exception as e:
            logger.warning(
                "RollbarErrorReporter.report: report_exc_info failed; "
                f"swallowing to avoid masking the original error ({e})"
            )


_active_reporter: ErrorReporter = NoopErrorReporter()


def set_error_reporter(reporter: ErrorReporter) -> None:
    """Install ``reporter`` as the framework-wide error sink. Called once
    at startup from deployment-specific bootstrap code."""
    global _active_reporter
    if not isinstance(reporter, ErrorReporter):
        raise TypeError(
            "set_error_reporter requires an ErrorReporter subclass; "
            f"got {type(reporter).__name__}"
        )
    _active_reporter = reporter


def get_error_reporter() -> ErrorReporter:
    """Return the active reporter, attaching request-scoped fields
    (``correlation_id``, ``request_user``, ``traceparent``) on every
    :meth:`ErrorReporter.report` call so callers do not have to remember
    to forward them. Returns a thin wrapper around the configured
    reporter rather than the reporter itself."""
    return _ContextEnrichingReporter(_active_reporter)


class _ContextEnrichingReporter(ErrorReporter):
    """Decorator around an :class:`ErrorReporter` that, on every
    :meth:`report` call, copies the current ``correlation_id``,
    ``request_user``, and ``traceparent`` into the caller's context
    mapping before delegating. Exposed only via
    :func:`get_error_reporter` — deployments that want raw access to
    their concrete reporter should keep their own reference."""

    def __init__(self, inner: ErrorReporter) -> None:
        self._inner = inner

    def report(self, exception: BaseException, context: Mapping[str, Any]) -> None:
        enriched: dict[str, Any] = dict(context) if context else {}
        try:
            from serverframework.lib.RequestContext import get_correlation_id, get_request_user

            cid = get_correlation_id()
            if cid is not None and "correlation_id" not in enriched:
                enriched["correlation_id"] = cid
            user = get_request_user()
            if user is not None and "request_user" not in enriched:
                enriched["request_user"] = user
        except ImportError:
            pass
        try:
            from serverframework.lib.ProviderHTTPClient import get_traceparent

            tp = get_traceparent()
            if tp is not None and "traceparent" not in enriched:
                enriched["traceparent"] = tp
        except ImportError:
            pass
        return self._inner.report(exception, enriched)
