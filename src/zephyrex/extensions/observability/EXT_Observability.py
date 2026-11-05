# SPDX-License-Identifier: AGPL-3.0-or-later
"""observability extension — env-driven metrics + error-reporter wiring."""

from typing import Any, ClassVar, Dict, List, Optional, Set, Tuple

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension
from zephyrex.lib.Dependencies import Dependencies, PIP_Dependency
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
    # Each backend is used only when its setting selects it; the extra
    # installs all four so any can be switched on.
    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            PIP_Dependency(
                name="prometheus-client",
                friendly_name="Prometheus client",
                semver=">=0.20.0",
                reason="METRICS_BACKEND=prometheus",
            ),
            PIP_Dependency(
                name="opentelemetry-api",
                friendly_name="OpenTelemetry API",
                semver=">=1.20.0",
                reason="METRICS_BACKEND=otel",
            ),
            PIP_Dependency(
                name="sentry-sdk",
                friendly_name="Sentry SDK",
                semver=">=2.0.0",
                reason="SENTRY_DSN error reporting",
            ),
            PIP_Dependency(
                name="rollbar",
                friendly_name="Rollbar",
                semver=">=1.0.0",
                reason="ROLLBAR_TOKEN error reporting",
            ),
        ]
    )
    _abilities: ClassVar[Set[str]] = {"metrics_export", "error_report"}
    _providers: ClassVar[List] = []

    @classmethod
    def on_initialize(cls) -> bool:
        cls._wire_metrics_backend()
        cls._wire_error_reporter()
        return True

    @classmethod
    def _wire_metrics_backend(cls) -> None:
        backend_name = (env("METRICS_BACKEND") or "noop").strip().lower()
        if backend_name in ("", "noop"):
            return
        from zephyrex.extensions.observability.MetricsBackends import (
            OpenTelemetryMetricsBackend,
            PrometheusMetricsBackend,
        )
        from zephyrex.lib.Metrics import (
            MetricsBackend,
            get_metrics_backend,
            set_metrics_backend,
        )

        backend_class: type[MetricsBackend]
        if backend_name == "prometheus":
            backend_class = PrometheusMetricsBackend
        elif backend_name in ("otel", "opentelemetry"):
            backend_class = OpenTelemetryMetricsBackend
        else:
            logger.warning(
                f"observability: unknown METRICS_BACKEND '{backend_name}'; "
                "leaving the core no-op backend in place."
            )
            return
        # Every app build re-initializes the extension. A backend of the chosen
        # kind is kept: a second one would re-register its collectors under
        # the same names in the process-global Prometheus registry, which
        # rejects them, and every metric would be dropped.
        if isinstance(get_metrics_backend(), backend_class):
            return
        try:
            set_metrics_backend(backend_class())
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
