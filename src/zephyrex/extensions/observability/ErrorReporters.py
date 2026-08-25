# SPDX-License-Identifier: AGPL-3.0-or-later
"""Concrete error reporters for the ``observability`` extension.

The reporter *facade* (``ErrorReporter`` ABC, ``set_error_reporter`` /
``get_error_reporter``, the ``NoopErrorReporter`` default) stays in core
``lib/Logging``. The concrete Sentry / Rollbar adapters live here so the
framework wheel never carries ``sentry_sdk`` / ``rollbar``; the extension wires
one from ``SENTRY_DSN`` / ``ROLLBAR_TOKEN`` at ``on_load``.
"""

from __future__ import annotations

from typing import Any, Mapping

from zephyrex.lib.Logging import ErrorReporter, logger


def _sentry_before_send(event: Any, hint: Any) -> Any:
    """Sentry ``before_send`` hook (M-5). Scrubs known PII from the event
    message + breadcrumbs using the ``privacy`` extension's filter when loaded,
    falling back to the secrets-only redactor otherwise."""
    try:
        from zephyrex.lib.Credentials import redact

        msg = event.get("message")
        if isinstance(msg, str):
            event["message"] = redact(msg)
        for crumb in event.get("breadcrumbs", {}).get("values", []) or []:
            m = crumb.get("message")
            if isinstance(m, str):
                crumb["message"] = redact(m)
    except Exception:
        return event
    # Delegate richer PII pattern coverage to the ``privacy`` extension via the
    # ``_pii_hooks["log_filter"]`` registry (core never imports the extension).
    from zephyrex.lib.Hooks import _pii_hooks

    pii_filter = _pii_hooks["log_filter"]
    if pii_filter is not None:
        try:
            pii_filter(event)
        except Exception:
            return event
    return event


class SentryErrorReporter(ErrorReporter):
    """Routes exceptions to Sentry via ``sentry_sdk``. The SDK is an optional
    dependency: if it is not installed the reporter degrades to a no-op (with a
    single ``debug`` log line on construction) rather than crashing at import.

    M-5 — initialises the SDK with ``send_default_pii=False`` and
    ``include_local_variables=False`` so request-handler local vars
    (``password``, ``authorization``, …) do not exit the process. A
    ``before_send`` hook applies the ``privacy`` extension's PII filter when
    loaded.
    """

    def __init__(self) -> None:
        try:
            import sentry_sdk  # type: ignore[import-not-found]

            self._sentry_sdk = sentry_sdk
            try:
                if sentry_sdk.Hub.current.client is None:  # type: ignore[union-attr]
                    sentry_sdk.init(
                        send_default_pii=False,
                        include_local_variables=False,
                        before_send=_sentry_before_send,
                    )
            except Exception:
                pass
        except ImportError:
            self._sentry_sdk = None
            logger.debug(
                "SentryErrorReporter: sentry_sdk not installed; reporter will "
                "no-op until the dependency is available."
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
                        continue
                self._sentry_sdk.capture_exception(exception)  # type: ignore[union-attr]
        except Exception as e:
            logger.warning(
                "SentryErrorReporter.report: capture_exception failed; "
                f"swallowing to avoid masking the original error ({e})"
            )


class RollbarErrorReporter(ErrorReporter):
    """Routes exceptions to Rollbar via ``rollbar``. The SDK is an optional
    dependency: missing it degrades the reporter to a no-op."""

    def __init__(self) -> None:
        try:
            import rollbar  # type: ignore[import-not-found]

            self._rollbar = rollbar
        except ImportError:
            self._rollbar = None
            logger.debug(
                "RollbarErrorReporter: rollbar not installed; reporter will "
                "no-op until the dependency is available."
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
