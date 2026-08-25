# SPDX-License-Identifier: AGPL-3.0-or-later
"""observability extension — metrics + error-reporter backends and wiring."""

from zephyrex.extensions.observability.ErrorReporters import (
    RollbarErrorReporter,
    SentryErrorReporter,
)
from zephyrex.extensions.observability.EXT_Observability import (
    EXT_Observability,
    render_metrics_exposition,
)
from zephyrex.extensions.observability.MetricsBackends import (
    OpenTelemetryMetricsBackend,
    PrometheusMetricsBackend,
)

__all__ = [
    "EXT_Observability",
    "render_metrics_exposition",
    "PrometheusMetricsBackend",
    "OpenTelemetryMetricsBackend",
    "SentryErrorReporter",
    "RollbarErrorReporter",
]
