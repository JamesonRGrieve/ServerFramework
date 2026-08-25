# SPDX-License-Identifier: AGPL-3.0-or-later
"""observability extension — env-driven metrics + error-reporter wiring."""

from typing import Any, ClassVar, Dict, List, Optional, Set, Tuple

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension
from zephyrex.lib.Environment import env
from zephyrex.lib.Logging import logger


class EXT_Observability(AbstractStaticExtension):
    """Auto-wire the metrics backend and error reporter from the environment.

    Metrics (#210): ``METRICS_BACKEND=prometheus|otel|noop`` selects and installs
    a backend into the core facade (``set_metrics_backend``). When Prometheus is
    active the host app exposes ``/metrics`` via ``render_metrics_exposition``.
    Errors (#215): ``SENTRY_DSN`` installs ``SentryErrorReporter``; otherwise
    ``ROLLBAR_TOKEN`` installs ``RollbarErrorReporter`` (``set_error_reporter``).

    Both concrete backend families and their optional pip deps live in this
    extension, so a deployment that doesn't enable it never carries
    ``prometheus_client`` / ``opentelemetry`` / ``sentry_sdk`` / ``rollbar`` and
    the metrics/error surfaces stay at their core no-op defaults.
    """

    name: ClassVar[str] = "observability"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Env-driven metrics (Prometheus/OTel) + error-reporter (Sentry/Rollbar) "
        "wiring, with the concrete backends moved out of core lib/"
    )

    _env: ClassVar[Dict[str, Any]] = {}
    _abilities: ClassVar[Set[str]] = {"metrics_export", "error_report"}
    _providers: ClassVar[List] = []

    @classmethod
    def on_initialize(cls) -> bool:
        logger.debug("Initializing observability extension")
        return True

    @classmethod
    def on_load(cls) -> None:
        cls._wire_metrics_backend()
        cls._wire_error_reporter()

    @classmethod
    def _wire_metrics_backend(cls) -> None:
        backend_name = (env("METRICS_BACKEND") or "noop").strip().lower()
        if backend_name in ("", "noop"):
            return
        from zephyrex.lib.Metrics import set_metrics_backend

        try:
            if backend_name == "prometheus":
                from zephyrex.extensions.observability.MetricsBackends import (
                    PrometheusMetricsBackend,
                )

                set_metrics_backend(PrometheusMetricsBackend())
            elif backend_name in ("otel", "opentelemetry"):
                from zephyrex.extensions.observability.MetricsBackends import (
                    OpenTelemetryMetricsBackend,
                )

                set_metrics_backend(OpenTelemetryMetricsBackend())
            else:
                logger.warning(
                    f"observability: unknown METRICS_BACKEND '{backend_name}'; "
                    "leaving the core no-op backend in place."
                )
                return
            logger.info(f"observability: metrics backend wired: {backend_name}")
        except ImportError as exc:
            logger.warning(
                f"observability: METRICS_BACKEND={backend_name} requested but "
                f"its dependency is unavailable ({exc}); leaving the no-op backend."
            )

    @classmethod
    def _wire_error_reporter(cls) -> None:
        from zephyrex.lib.Logging import set_error_reporter

        if env("SENTRY_DSN"):
            from zephyrex.extensions.observability.ErrorReporters import (
                SentryErrorReporter,
            )

            set_error_reporter(SentryErrorReporter())
            logger.info("observability: error reporter wired: sentry")
        elif env("ROLLBAR_TOKEN"):
            from zephyrex.extensions.observability.ErrorReporters import (
                RollbarErrorReporter,
            )

            set_error_reporter(RollbarErrorReporter())
            logger.info("observability: error reporter wired: rollbar")


def render_metrics_exposition() -> Optional[Tuple[bytes, str]]:
    """Return ``(payload, content_type)`` for a Prometheus scrape when the active
    metrics backend exposes one (Prometheus), else ``None`` so the host serves a
    404. Import-safe regardless of which backend is active; used by the host
    app's ``/metrics`` route.
    """
    from zephyrex.lib.Metrics import get_metrics_backend

    backend = get_metrics_backend()
    expose = getattr(backend, "expose", None)
    if expose is None:
        return None
    try:
        return expose()  # type: ignore[no-any-return]
    except Exception:
        return None
